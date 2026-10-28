#!/usr/bin/env python3
"""Weekly nowcast of OFR qualifying-hedge-fund leverage (separate live layer).

Target: OFR Hedge Fund Monitor, qualifying hedge funds, gross assets /
net assets (GAV/NAV), quarterly 2013Q1-2026Q2 (54 observations). The
official quarterly panel (data/panel_complete.csv) carries this series as
an observed/nowcast value on the quarterly cadence; this module is the
WEEKLY live layer for the same quantity and never writes to the panel.

Model (fixed by the pilot study, 2026-10-23; see docs/hf/index.html for
the full methodology note):
  ridge lambda=100 with a mean-drift intercept, predicting the CHANGE
  from the last published value. Features, quarter-to-date at the
  vintage Friday: S&P 500 cumulative log return, mean VIX, mean
  Baa-Treasury spread (BAA minus DGS10), change in 10y yield, change in
  2y yield, change in broad dollar index (log), weeks since the last
  publication (staleness), quarter fraction elapsed. A levels
  regression fails on this drifting series and unregularized OLS is
  unusable at this sample size; neither is shipped. Carry-forward and
  AR(1) are computed as benchmarks only.
  Publication rule: quarter Q posts at the end of week 10 of Q+1.
  Backtest (pseudo-real-time, Friday vintages 2019-2026, final values
  as targets): in-progress quarter RMSE 0.0758 vs carry-forward 0.0845;
  just-ended quarter 0.0509 vs 0.0557.

Outputs:
  docs/data/hf_nowcast.json   the site bundle (latest vintage)
  data/hf_nowcast_vintages.csv  one row per (vintage, target quarter),
                                upserted — a rerun for the same vintage
                                replaces, never duplicates

The SCOOS companion series (FRED EXHE2C1Q8NP, net percent of dealers
reporting increased use of leverage) is displayed alongside as a timely
cross-check. It is never modeled: if the current quarter's value has
not posted by mid-quarter, it is labeled "pending" and the last
available quarter is shown.

All sources are keyless public endpoints (OFR API, FRED graph CSVs).
Stdlib + polars + numpy only. No network at import.

Usage:
    python src/hf_nowcast.py [--vintage YYYY-MM-DD]
    (default vintage: the most recent Friday on or before today, UTC)
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
BUNDLE = HERE / "docs" / "data" / "hf_nowcast.json"
VINTAGES = HERE / "data" / "hf_nowcast_vintages.csv"

OFR_API = "https://data.financialresearch.gov/hf/v1/series/timeseries"
OFR_GAV = "FPF-ALLQHF_GAV_SUM"
OFR_NAV = "FPF-ALLQHF_NAV_SUM"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
PRED_IDS = ["SP500", "VIXCLS", "BAA", "DGS10", "DGS2", "DTWEXBGS"]
SCOOS_ID = "EXHE2C1Q8NP"
PUB_WEEK = 10           # quarter Q publishes end of week PUB_WEEK of Q+1
FEAT_KEYS = ["spx_ret", "vix", "oas", "d10", "d2", "usd"]
RIDGE_LAMBDA = 100.0
METHOD = "nowcast-ridge100-change"
BACKTEST = {
    "in_progress_rmse": 0.0758, "in_progress_carry_rmse": 0.0845,
    "just_ended_rmse": 0.0509, "just_ended_carry_rmse": 0.0557,
    "window": "Friday vintages 2019-01..2026-09, pseudo-real-time, "
              "final values as targets",
}
UA = {"User-Agent": "frb-heat-index weekly nowcast (public data fetch)"}


# ----------------------------------------------------------------------------
# calendar helpers
# ----------------------------------------------------------------------------

def q_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def qkey(y: int, q: int) -> str:
    return f"{y}Q{q}"


def q_add(y: int, q: int, n: int) -> tuple[int, int]:
    idx = y * 4 + (q - 1) + n
    return idx // 4, idx % 4 + 1


def q_start(y: int, q: int) -> date:
    return date(y, 3 * (q - 1) + 1, 1)


def q_end(y: int, q: int) -> date:
    return q_start(*q_add(y, q, 1)) - timedelta(days=1)


def pub_date(y: int, q: int) -> date:
    """Assumed publication date of quarter (y, q): end of week 10 of Q+1."""
    return q_start(*q_add(y, q, 1)) + timedelta(weeks=PUB_WEEK) \
        - timedelta(days=1)


def qnum(q: str) -> int:
    return int(q[:4]) * 4 + int(q[-1])


def last_friday(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 4) % 7)


# ----------------------------------------------------------------------------
# fetches (keyless public sources)
# ----------------------------------------------------------------------------

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_ofr_series(mnemonic: str) -> dict[date, float]:
    rows = json.loads(_get(f"{OFR_API}?mnemonic={mnemonic}"))
    out = {}
    for ds, v in rows:
        y, m, dd = (int(p) for p in ds.split("-"))
        out[date(y, m, dd)] = float(v)
    return out


def fetch_target() -> dict[str, float]:
    """Quarter -> GAV/NAV ratio, from the OFR API (quarter-end dates)."""
    gav = fetch_ofr_series(OFR_GAV)
    nav = fetch_ofr_series(OFR_NAV)
    out = {}
    for d, g in gav.items():
        if d in nav and nav[d]:
            out[qkey(*q_of(d))] = g / nav[d]
    return dict(sorted(out.items()))


def fetch_fred(series_id: str) -> pl.DataFrame:
    """FRED keyless graph CSV -> (date, value), nulls dropped."""
    text = _get(FRED_CSV + series_id).decode()
    df = pl.read_csv(io.StringIO(text), null_values=".")
    col = [c for c in df.columns if c != "observation_date"][0]
    df = df.rename({col: "value"}).with_columns(
        pl.col("observation_date").str.to_date())
    return df.filter(pl.col("value").is_not_null()).sort("observation_date")


def fetch_scoos() -> dict[str, float]:
    df = fetch_fred(SCOOS_ID)
    return {qkey(*q_of(r["observation_date"])): float(r["value"])
            for r in df.iter_rows(named=True)}


def scoos_status(latest_q: str, vintage: date) -> str:
    """'observed' when the latest SCOOS quarter is the current one (or the
    current quarter's print is not yet due); 'pending' when the current
    quarter is past mid-quarter and its value has not posted."""
    cur = qkey(*q_of(vintage))
    if qnum(latest_q) >= qnum(cur):
        return "observed"
    if (vintage - q_start(*q_of(vintage))).days >= 45:
        return "pending"
    return "observed"


# ----------------------------------------------------------------------------
# Friday panel + quarter-to-date features
# ----------------------------------------------------------------------------

def build_friday_panel(series: dict[str, pl.DataFrame],
                       first: date, last: date) -> pl.DataFrame:
    """Each series asof-sampled independently onto Fridays (last
    observation on or before the Friday)."""
    wk = pl.DataFrame({"friday": pl.date_range(first, last, interval="1w",
                                               eager=True)})
    for sid, d in series.items():
        d = d.rename({"value": sid.lower()})
        wk = wk.join_asof(d, left_on="friday", right_on="observation_date",
                          strategy="backward").drop("observation_date")
    return wk


def qtd_features(wk: pl.DataFrame, y: int, q: int, upto: date) -> dict | None:
    qs, qe = q_start(y, q), min(upto, q_end(y, q))
    if qe < qs:
        return None
    sub = wk.filter((pl.col("friday") >= qs) & (pl.col("friday") <= qe))
    if sub.height == 0:
        return None
    first = sub.row(0, named=True)
    last = sub.row(-1, named=True)

    def ok(v):
        return v is not None and not (isinstance(v, float) and np.isnan(v))

    def cmean(col):
        s = sub[col].drop_nulls()
        return float(s.mean()) if len(s) else np.nan

    baa_m, d10_m = cmean("baa"), cmean("dgs10")
    return {
        "spx_ret": (np.log(last["sp500"] / first["sp500"])
                    if ok(first["sp500"]) and ok(last["sp500"]) else np.nan),
        "vix": cmean("vixcls"),
        "oas": (baa_m - d10_m
                if baa_m == baa_m and d10_m == d10_m else np.nan),
        "d10": (last["dgs10"] - first["dgs10"])
               if ok(first["dgs10"]) and ok(last["dgs10"]) else np.nan,
        "d2": (last["dgs2"] - first["dgs2"])
              if ok(first["dgs2"]) and ok(last["dgs2"]) else np.nan,
        "usd": (np.log(last["dtwexbgs"] / first["dtwexbgs"])
                if ok(first["dtwexbgs"]) and ok(last["dtwexbgs"]) else np.nan),
    }


# ----------------------------------------------------------------------------
# ridge bridge (change prediction, mean-drift intercept)
# ----------------------------------------------------------------------------

def ridge_fit(X: np.ndarray, y: np.ndarray, lam: float):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Xs = (X - mu) / sd
    A = Xs.T @ Xs + lam * np.eye(X.shape[1])
    b = np.linalg.solve(A, Xs.T @ (y - y.mean()))
    return b, mu, sd, float(y.mean())


def ridge_pred(model, x: np.ndarray) -> float:
    b, mu, sd, ybar = model
    return float(ybar + b @ ((x - mu) / sd))


def _feature_vector(wk, target, quarters, yq, weeks_in_eff, upto):
    """(x, last_pub_q, last_pub) at an information date, or None."""
    pub = [q for q in quarters
           if pub_date(int(q[:4]), int(q[-1])) <= upto]
    if len(pub) < 16:
        return None
    feats = qtd_features(wk, *yq, upto=upto)
    if feats is None:
        return None
    last_pub_q = pub[-1]
    stale = (upto - pub_date(int(last_pub_q[:4]),
                             int(last_pub_q[-1]))).days / 7.0
    frac = min(1.0, weeks_in_eff / 13.0)
    x = np.array([feats[k] for k in FEAT_KEYS] + [stale, frac], dtype=float)
    if np.isnan(x).any():
        return None
    return x, last_pub_q, target[last_pub_q]


def _training_rows(wk, target, quarters, pub, weeks_in_eff):
    """Ragged training rows from published quarters: each published
    quarter's features truncated at weeks_in_eff, target = the quarter's
    change from the last value published at that information date."""
    Xtr, ytr = [], []
    for q in pub:
        qy, qq = int(q[:4]), int(q[-1])
        upto = min(q_start(qy, qq) + timedelta(weeks=weeks_in_eff + 1)
                   - timedelta(days=1), q_end(qy, qq))
        f = qtd_features(wk, qy, qq, upto=upto)
        info_dt = q_start(qy, qq) + timedelta(weeks=weeks_in_eff)
        prev_pub = [p for p in quarters
                    if pub_date(int(p[:4]), int(p[-1])) <= info_dt]
        if f is None or not prev_pub:
            continue
        lp = target[prev_pub[-1]]
        st = (info_dt - pub_date(int(prev_pub[-1][:4]),
                                 int(prev_pub[-1][-1]))).days / 7.0
        x = np.array([f[k] for k in FEAT_KEYS]
                     + [st, min(1.0, weeks_in_eff / 13.0)], dtype=float)
        if np.isnan(x).any():
            continue
        Xtr.append(x)
        ytr.append(target[q] - lp)
    return np.array(Xtr), np.array(ytr)


def nowcast_quarter(wk, target, quarters, vintage: date,
                    yq: tuple[int, int], weeks_in_eff: int,
                    rmse_band: float) -> dict | None:
    """Ridge-100 change nowcast for one target quarter at a vintage."""
    got = _feature_vector(wk, target, quarters, yq, weeks_in_eff, vintage)
    if got is None:
        return None
    xnow, last_pub_q, last_pub = got
    pub = [q for q in quarters
           if pub_date(int(q[:4]), int(q[-1])) <= vintage]
    Xtr, ytr = _training_rows(wk, target, quarters, pub, weeks_in_eff)
    if len(ytr) < 20:
        return None
    value = float(last_pub + ridge_pred(
        ridge_fit(Xtr, ytr, RIDGE_LAMBDA), xnow))
    return {
        "target_quarter": qkey(*yq),
        "value": round(value, 4),
        "method": METHOD,
        "carry_forward": round(float(last_pub), 4),
        "rmse_band": rmse_band,
        "status": "nowcast",
    }


def compute_bundle(vintage: date, target: dict[str, float],
                   wk: pl.DataFrame, scoos: dict[str, float]) -> dict:
    quarters = sorted(target.keys())
    pub = [q for q in quarters
           if pub_date(int(q[:4]), int(q[-1])) <= vintage]
    last_pub_q = pub[-1]
    yq = q_of(vintage)
    weeks_in = (vintage - q_start(*yq)).days // 7
    nowcasts = []
    je = nowcast_quarter(wk, target, quarters, vintage, q_add(*yq, -1), 13,
                         BACKTEST["just_ended_rmse"])
    if je is not None:
        nowcasts.append(je)
    ip = nowcast_quarter(wk, target, quarters, vintage, yq, weeks_in,
                         BACKTEST["in_progress_rmse"])
    if ip is not None:
        nowcasts.append(ip)
    scoos_q = sorted(scoos.keys())[-1] if scoos else None
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "vintage_friday": vintage.isoformat(),
        "last_published_quarter": last_pub_q,
        "last_published_value": round(float(target[last_pub_q]), 4),
        "nowcasts": nowcasts,
        "scoos": {
            "series_id": SCOOS_ID,
            "latest_quarter": scoos_q,
            "latest_value": scoos.get(scoos_q) if scoos_q else None,
            "status": scoos_status(scoos_q, vintage) if scoos_q else "pending",
        },
        "model": {
            "method": METHOD,
            "target": "OFR qualifying hedge funds, gross assets / net "
                      "assets (FPF-ALLQHF_GAV_SUM / FPF-ALLQHF_NAV_SUM)",
            "publication_rule": "quarter Q posts at end of week 10 of Q+1",
            "features": FEAT_KEYS + ["staleness_weeks", "quarter_fraction"],
            "backtest": BACKTEST,
        },
    }


def upsert_vintages(path: Path, bundle: dict) -> int:
    """Upsert this bundle's nowcast rows into the vintage log.
    Key: (vintage, target_quarter). Returns the row count written."""
    new_rows = [{
        "vintage": bundle["vintage_friday"],
        "target_quarter": n["target_quarter"],
        "nowcast_value": n["value"],
        "carry_forward": n["carry_forward"],
        "last_published_quarter": bundle["last_published_quarter"],
        "last_published_value": bundle["last_published_value"],
        "rmse_band": n["rmse_band"],
        "scoos_latest_quarter": bundle["scoos"]["latest_quarter"],
        "scoos_latest_value": bundle["scoos"]["latest_value"],
    } for n in bundle["nowcasts"]]
    keep = []
    if path.exists():
        old = pl.read_csv(path)
        new_keys = {(r["vintage"], r["target_quarter"]) for r in new_rows}
        keep = [r for r in old.iter_rows(named=True)
                if (r["vintage"], r["target_quarter"]) not in new_keys]
    all_rows = sorted(keep + new_rows,
                      key=lambda r: (r["vintage"], r["target_quarter"]))
    pl.DataFrame(all_rows).write_csv(path)
    return len(new_rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vintage", default=None,
                    help="vintage date YYYY-MM-DD (default: most recent "
                         "Friday on or before today, UTC)")
    args = ap.parse_args()
    vintage = (date.fromisoformat(args.vintage) if args.vintage
               else last_friday(datetime.now(timezone.utc).date()))
    print(f"vintage: {vintage} (Friday)")
    target = fetch_target()
    print(f"target: {len(target)} quarters, last published "
          f"{sorted(target)[-1]} = {target[sorted(target)[-1]]:.4f}")
    series = {sid: fetch_fred(sid) for sid in PRED_IDS}
    wk = build_friday_panel(series, date(2012, 12, 28), vintage)
    scoos = fetch_scoos()
    bundle = compute_bundle(vintage, target, wk, scoos)
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_text(json.dumps(bundle, indent=1) + "\n")
    n = upsert_vintages(VINTAGES, bundle)
    for nc in bundle["nowcasts"]:
        print(f"  {nc['target_quarter']}: nowcast {nc['value']:.2f} "
              f"(carry-forward {nc['carry_forward']:.2f}, "
              f"band ±{nc['rmse_band']})")
    print(f"  SCOOS {bundle['scoos']['latest_quarter']}: "
          f"{bundle['scoos']['latest_value']} "
          f"({bundle['scoos']['status']})")
    print(f"wrote {BUNDLE} and upserted {n} rows into {VINTAGES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
