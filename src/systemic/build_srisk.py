#!/usr/bin/env python3
"""Build the SRISK indicator: total SRISK relative to total assets.

Method (V-LAB "without simulation", publicly documented):
    beta_i,t   = 36m rolling OLS beta of firm log returns on S&P 500
    LRMES_i,t  = 1 - exp(log(1-0.40) * beta_i,t) = 1 - 0.6^beta
    SRISK_i,t  = max(0, k*DEBT_i,t - (1-k)*W_i,t*(1-LRMES_i,t)), k = 8%
    indicator_t = sum_i SRISK_i,t / sum_i book_assets_i,t

Inputs (all public):
- Y-9C firm-level: BHCK2170 (total assets), BHCK3217 (total equity),
  DEBT = assets - equity. $ thousands.
- Market cap W: quarter-end unadjusted close (Yahoo) x shares outstanding.
  Shares: SEC EDGAR XBRL companyfacts (2009Q3+); pre-2009 split-adjusted
  backward carry from earliest XBRL obs (spot-checked vs 10-Ks).
- S&P 500 (^GSPC) for betas.

Output: work/raw/systemic/srisk_quarterly.csv (quarter, srisk_assets).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from systemic.universe import SRISK_FIRMS  # noqa: E402

HERE = Path(__file__).resolve().parent.parent.parent
YRAW = HERE / "work" / "raw" / "yahoo"
ERAW = HERE / "work" / "raw" / "edgar"
Y9C_DIRS = [
    Path("/home/hatch/workspace/fedheat_r2_work/y9c_raw"),   # 1990-2014
    Path("/home/hatch/workspace/fedheat_r2_work/y9c_new"),   # 2015-2021Q3
    HERE / "work" / "raw" / "y9c",                            # 2021Q4+
]
OUT = HERE / "work" / "raw" / "systemic" / "srisk_quarterly.csv"

K = 0.08          # prudential capital ratio (V-LAB default for US firms)
CRISIS_D = 0.40   # six-month market decline threshold
BETA_WIN = 36     # months for rolling beta
BETA_MIN = 24

# Manual book-value overrides ($ thousands) for firms/quarters lacking Y-9C.
# GS and MS became BHCs in Sep 2008 and first filed Y-9C in 2009Q1; their
# 2008Q4 books come from FY2008 10-Ks (fiscal year ended Nov 2008):
# - GS: 10-K (CIK 886982): total assets $884,547M,
#   total shareholders' equity $64,369M.
# - MS: 10-K (CIK 895421): total assets $658,812M,
#   total shareholders' equity $50,831M.
MANUAL_BOOK = {
    ("2008Q4", "GS"): (884_547_000.0, 64_369_000.0),
    ("2008Q4", "MS"): (658_812_000.0, 50_831_000.0),
}


def parse_q(s: str) -> tuple[int, int]:
    return int(s[:4]), int(s[5])


def quarters_between(a: str, b: str) -> list[str]:
    out, y, q = [], *parse_q(a)
    ey, eq = parse_q(b)
    while (y, q) <= (ey, eq):
        out.append(f"{y}Q{q}")
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def load_y9c_firms() -> pl.DataFrame:
    """Firm-level (rssd, quarter) book assets/equity for the panel RSSDs."""
    want = {r for r, _ in SRISK_FIRMS.values()}
    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for d in Y9C_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("bhcf*.csv")):
            stem = f.stem  # bhcfYYMM
            yy, mm = int(stem[4:6]), int(stem[6:8])
            # disambiguate century: files are 1990-2026
            year = 1900 + yy if yy >= 90 else 2000 + yy
            q = mm // 3
            label = f"{year}Q{q}"
            if (label, year) in seen:
                continue
            seen.add((label, year))
            try:
                hdr = pl.read_csv(f, n_rows=0, encoding="utf8-lossy").columns
            except Exception:
                continue
            cmap = {c.lower(): c for c in hdr}
            need = ["rssd9001", "bhck2170", "bhck3217"]
            if any(n not in cmap for n in need):
                continue
            df = pl.read_csv(
                f, columns=[cmap[n] for n in need], encoding="utf8-lossy",
                infer_schema_length=10000,
            )
            df = df.with_columns(pl.col(cmap["rssd9001"]).cast(pl.Int64, strict=False))
            df = df.drop_nulls(cmap["rssd9001"])
            df = df.filter(pl.col(cmap["rssd9001"]).is_in(list(want)))
            for r in df.iter_rows(named=True):
                try:
                    a = float(r[cmap["bhck2170"]])
                    e = float(r[cmap["bhck3217"]])
                except (TypeError, ValueError):
                    continue
                if a > 0:
                    rows.append({"quarter": label, "rssd": int(r[cmap["rssd9001"]]),
                                 "assets": a, "equity": e})
    return pl.DataFrame(rows).sort(["rssd", "quarter"])


def load_monthly(ticker: str) -> pl.DataFrame:
    """Monthly (date, close, adjclose, splits) from cached Yahoo JSON."""
    fn = YRAW / f"{ticker}.json"
    d = json.loads(fn.read_bytes())
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
    splits = r.get("events", {}).get("splits", {}) or {}
    import datetime
    dates = [datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()
             for t in ts]
    return pl.DataFrame({
        "date": dates,
        "close": q.get("close", [None] * len(ts)),
        "adjclose": adj if len(adj) == len(ts) else q.get("close"),
    }).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("close").cast(pl.Float64),
        pl.col("adjclose").cast(pl.Float64),
    ).drop_nulls()


def quarter_end_price(df: pl.DataFrame) -> pl.DataFrame:
    """Last monthly close within each calendar quarter."""
    return (df.with_columns([
        pl.col("date").dt.year().alias("y"),
        pl.col("date").dt.month().alias("m"),
    ]).with_columns(
        ((pl.col("m") - 1) // 3 + 1).alias("q")
    ).sort("date").group_by(["y", "q"]).tail(1)
     .select([pl.format("{}Q{}", "y", "q").alias("quarter"),
              pl.col("close").alias("px")]))


def load_anchors() -> dict[str, dict[int, float]]:
    """10-K cover shares anchors: {ticker: {fy: shares}}."""
    fn = ERAW / "shares_10k_anchors.csv"
    if not fn.exists():
        return {}
    df = pl.read_csv(fn)
    out: dict[str, dict[int, float]] = {}
    for r in df.iter_rows(named=True):
        out.setdefault(r["ticker"], {})[int(r["fy"])] = float(r["shares"])
    return out


def load_shares(ticker: str, anchors: dict[int, float] | None = None) -> pl.DataFrame:
    """Quarterly shares outstanding.

    2004-2010: 10-K cover anchors (linearly interpolated between year-ends)
    -- captures crisis-era issuances the backward carry would miss.
    2011+: SEC EDGAR XBRL companyfacts as-of join.
    Pre-2004: split-adjusted backward carry from the 2004 anchor (or
    earliest XBRL observation if no anchors exist for the firm).
    """
    import datetime
    fn = ERAW / f"shares_{ticker}.csv"
    xbrl = pl.read_csv(fn).with_columns(
        pl.col("end_date").str.to_date().alias("date")).sort("date")
    obs = [(r["date"], float(r["shares"])) for r in xbrl.iter_rows(named=True)]

    # split history from Yahoo monthly file
    d = json.loads((YRAW / f"{ticker}.json").read_bytes())
    splits = (d["chart"]["result"][0].get("events", {}).get("splits", {}) or {})
    sp = sorted(
        (datetime.datetime.fromtimestamp(int(v["date"]), datetime.timezone.utc).date(),
         float(v["numerator"]) / float(v["denominator"]))
        for v in splits.values())

    qends: dict[str, datetime.date] = {}
    for lab in quarters_between("1990Q1", "2026Q2"):
        y, q = parse_q(lab)
        qends[lab] = datetime.date(y, {1: 3, 2: 6, 3: 9, 4: 12}[q],
                                   {1: 31, 2: 30, 3: 30, 4: 31}[q])

    anchors = anchors or {}
    ayears = sorted(anchors)

    def anchored_value(y: int, q: int) -> float | None:
        if not ayears or y < ayears[0] or y > ayears[-1]:
            return None
        if q == 4:
            return anchors[y]
        lo = max((a for a in ayears if a < y), default=None)
        if lo is None:
            return anchors[y]
        # Qq of year y lies q/4 of the way from end(y-1) to end(y)
        return anchors[lo] + (anchors[y] - anchors[lo]) * (q / 4)

    # backward-carry base: earliest anchor if available, else earliest XBRL obs
    if ayears:
        base_date = datetime.date(ayears[0], 12, 31)
        base_shares = anchors[ayears[0]]
    else:
        base_date = obs[0][0]
        base_shares = obs[0][1]

    first_obs_date = obs[0][0]
    out: dict[str, float] = {}
    for lab, qe in qends.items():
        y, q = parse_q(lab)
        av = anchored_value(y, q)
        if av is not None:
            out[lab] = av
        elif qe >= first_obs_date - datetime.timedelta(days=1):
            # XBRL as-of: nearest observation to this quarter-end
            best = min(obs, key=lambda o: abs((o[0] - qe).days))
            out[lab] = best[1]
        else:
            # backward carry: base, divided by splits in (qe, base_date]
            f = 1.0
            for dt_, ratio in sp:
                if qe < dt_ <= base_date:
                    f *= ratio
            out[lab] = base_shares / f
    return pl.DataFrame({"quarter": list(out.keys()),
                         "shares": list(out.values())}).sort("quarter")


def rolling_beta(firm: pl.DataFrame, mkt: pl.DataFrame) -> pl.DataFrame:
    """36m rolling OLS beta of firm log returns on market log returns."""
    j = firm.join(mkt, on="date", how="inner", suffix="_m").sort("date")
    j = j.with_columns([
        (pl.col("adjclose") / pl.col("adjclose").shift(1)).log().alias("r"),
        (pl.col("adjclose_m") / pl.col("adjclose_m").shift(1)).log().alias("rm"),
    ]).drop_nulls()
    rows = j.rows(named=True)
    out = []
    for i in range(len(rows)):
        win = rows[max(0, i - BETA_WIN + 1): i + 1]
        if len(win) < BETA_MIN:
            continue
        import numpy as np
        r = np.array([w["r"] for w in win])
        rm = np.array([w["rm"] for w in win])
        if rm.std() == 0:
            continue
        beta = np.cov(r, rm, ddof=1)[0, 1] / np.var(rm, ddof=1)
        out.append({"date": rows[i]["date"], "beta": float(beta)})
    return pl.DataFrame(out)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print("loading firm-level Y-9C...")
    book = load_y9c_firms()
    print(f"  {book.height} firm-quarters, {book['quarter'].min()}..{book['quarter'].max()}")
    r2t = {r: t for t, (r, _) in SRISK_FIRMS.items()}
    rmap = pl.DataFrame({"rssd": list(r2t.keys()), "ticker": list(r2t.values())})
    book = book.join(rmap, on="rssd", how="inner")
    # manual overrides for pre-Y-9C quarters (GS/MS 2008Q4)
    if MANUAL_BOOK:
        ov = pl.DataFrame(
            [{"quarter": q, "ticker": t, "assets": a, "equity": e}
             for (q, t), (a, e) in MANUAL_BOOK.items()]
        )
        book = pl.concat([book, ov], how="diagonal")

    print("loading market data...")
    mkt = load_monthly("IDX_GSPC").select(["date", "adjclose"])
    anchors = load_anchors()
    print(f"  10-K anchors: {sum(len(v) for v in anchors.values())} "
          f"across {len(anchors)} firms")

    # per-firm market cap + beta panels
    caps, betas = [], []
    for ticker in sorted(SRISK_FIRMS):
        try:
            px = load_monthly(ticker)
        except Exception as e:  # noqa: BLE001
            print(f"  {ticker}: price load failed: {e}", file=sys.stderr)
            continue
        qpx = quarter_end_price(px).with_columns(pl.lit(ticker).alias("ticker"))
        try:
            sh = load_shares(ticker, anchors.get(ticker))
        except Exception as e:  # noqa: BLE001
            print(f"  {ticker}: shares failed: {e}", file=sys.stderr)
            continue
        cap = qpx.join(sh, on="quarter", how="inner").with_columns(
            (pl.col("px") * pl.col("shares") / 1000.0).alias("mcap")  # $ thousands
        ).select(["quarter", "ticker", "mcap"])
        caps.append(cap)
        b = rolling_beta(px.select(["date", "adjclose"]), mkt)
        b = b.with_columns([
            pl.col("date").dt.year().alias("y"),
            ((pl.col("date").dt.month() - 1) // 3 + 1).alias("q"),
            pl.lit(ticker).alias("ticker"),
        ]).with_columns(pl.format("{}Q{}", "y", "q").alias("quarter"))
        # one beta per quarter: the window ending in the quarter's last month
        b = b.sort("date").group_by(["quarter", "ticker"]).tail(1)
        betas.append(b.select(["quarter", "ticker", "beta"]))
        print(f"  {ticker}: mcap {cap.height}q, beta {b.height}q")
    cap = pl.concat(caps)
    beta = pl.concat(betas)

    # assemble SRISK
    df = book.join(cap, on=["quarter", "ticker"], how="inner")
    df = df.join(beta, on=["quarter", "ticker"], how="inner")
    df = df.with_columns([
        (pl.col("assets") - pl.col("equity")).alias("debt"),
        (1.0 - 0.6 ** pl.col("beta")).alias("lrmes"),
    ]).with_columns(
        (K * pl.col("debt") - (1 - K) * pl.col("mcap") * (1 - pl.col("lrmes"))).alias("srisk_raw")
    ).with_columns(
        pl.when(pl.col("srisk_raw") > 0).then(pl.col("srisk_raw")).otherwise(0.0).alias("srisk")
    )
    agg = (df.group_by("quarter").agg([
        pl.col("srisk").sum().alias("tot_srisk"),
        pl.col("assets").sum().alias("tot_assets"),
        pl.col("ticker").n_unique().alias("n_firms"),
    ]).sort("quarter").with_columns(
        (pl.col("tot_srisk") / pl.col("tot_assets")).alias("value")
    ))
    # Start in 2000Q1: pre-2000 share counts rely on long backward carries
    # through the 1990s buyback wave and are unreliable. (V-LAB's public
    # SRISK likewise starts ~2000.)
    agg = agg.filter(pl.col("quarter") >= "2000Q1")
    agg.select(["quarter", "value", "n_firms", "tot_srisk", "tot_assets"]).write_csv(OUT)
    print(f"wrote {OUT}: {agg.height} quarters")
    # sanity landmarks
    for q in ["2007Q4", "2008Q4", "2009Q4", "2019Q4"]:
        r = agg.filter(pl.col("quarter") == q)
        if r.height:
            print(f"  {q}: srisk/assets={r['value'][0]:.4f} "
                  f"tot_srisk=${r['tot_srisk'][0] / 1e6:.0f}B n={r['n_firms'][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
