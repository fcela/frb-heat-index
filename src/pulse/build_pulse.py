"""Build the market-pulse dataset.

Fetches every live indicator (status live / live-proxy / live-experimental),
validates, and writes:
  data/pulse/<segment>/<indicator>.csv   (date,value[,contract|bank])
  data/pulse/_meta.json                  (per-indicator sourcing metadata)
  docs/data/pulse.json                   (site bundle)

Indicators with multiple sub-series (cftc_cot, etf_dislocation,
earnings_calls) are stored in long format with a group column.

JSON outputs are sanitized: NaN/Inf -> null, strict parse check (the NaN
outage lesson). Run: python3 -m src.pulse.build_pulse [--incremental]
"""

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.pulse import finra, fred
from src.pulse import (
    sofr_iorb,
    on_rrp,
    dealer_fails,
    cftc_cot,
    treasury_basis,
    etf_dislocation,
    wti_calendar_spread,
    insider_selling,
    htm_unrealized,
    n_mfp,
    fhlb,
    earnings_calls,  # noqa: F401  (scoring framework; see _earnings_calls_seed)
    cds_bond_basis,
    xccy_basis,
)
from src.pulse.segments import SEGMENTS, FETCHABLE_STATUSES, all_indicators

REPO = Path(__file__).resolve().parents[2]
PULSE_DIR = REPO / "data" / "pulse"
SITE_JSON = REPO / "docs" / "data" / "pulse.json"


def sanitize_records(records: list[dict]) -> list[dict]:
    """Map NaN/Inf floats to None so JSON stays strictly valid."""
    out = []
    for r in records:
        v = r.get("value")
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            r = dict(r, value=None)
        out.append(r)
    return out


def strict_json(obj) -> str:
    """Serialize with NaN rejected loudly (fail-closed)."""
    return json.dumps(obj, allow_nan=False)


def write_indicator_csv(seg: str, key: str, df: pl.DataFrame) -> Path:
    d = PULSE_DIR / seg
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.csv"
    cols = ["date", "value"] + [c for c in df.columns if c not in ("date", "value")]
    df.select(cols).write_csv(p)
    return p


def load_existing(seg: str, key: str) -> pl.DataFrame | None:
    p = PULSE_DIR / seg / f"{key}.csv"
    if not p.exists():
        return None
    df = pl.read_csv(p)
    df = df.with_columns(pl.col("date").str.to_date())
    return df


def merge_incremental(old: pl.DataFrame | None, new: pl.DataFrame) -> pl.DataFrame:
    if old is None or old.height == 0:
        return new
    # Long-format frames (date,value + group column) must share column order.
    if set(old.columns) == set(new.columns):
        new = new.select(old.columns)
    group_cols = [c for c in new.columns if c not in ("date", "value")]
    combined = pl.concat([old, new]).unique(
        ["date"] + group_cols, keep="last"
    ).sort(["date"] + group_cols)
    return combined


def _earnings_calls_seed() -> pl.DataFrame:
    """v1 seed for the earnings-call credit-language indicator.

    The fetcher module ships the scoring framework (score_transcript) and a
    verified Q3-2026 calibration point in its docstring: JPM 7.6 / BAC 11.6 /
    WFC 8.9 per 10k words. No transcript harvester ships with v1, so the
    indicator builds with the verified seed until the harvester exists.
    """
    return pl.DataFrame({
        "date": ["2026-09-30", "2026-09-30", "2026-09-30"],
        "bank": ["JPM", "BAC", "WFC"],
        "value": [7.6, 11.6, 8.9],
    }).with_columns(pl.col("date").str.to_date())


def fetch_indicator(ind_key: str, ind: dict) -> pl.DataFrame:
    if "fred_id" in ind:
        return fred.fetch_fred_csv(ind["fred_id"])
    if ind_key == "mbs_spread":
        a = fred.fetch_fred_csv("OBMMIC30YF").rename({"value": "mort"})
        b = fred.fetch_fred_csv("DGS30").rename({"value": "tsy"})
        return a.join(b, on="date", how="inner").with_columns(
            (pl.col("mort") - pl.col("tsy")).alias("value")
        ).select("date", "value")
    if ind_key == "margin_debt":
        return finra.fetch_margin_debt()
    if ind_key == "sofr_iorb":
        return sofr_iorb.fetch_sofr_iorb_spread()
    if ind_key == "on_rrp":
        return on_rrp.fetch_on_rrp()
    if ind_key == "dealer_fails":
        return dealer_fails.fetch_fails_to_deliver()
    if ind_key == "fhlb":
        # quarters_back=5: the CFR URL pattern reaches back to 2025Q2;
        # older reports live under different paths the fetcher does not chase.
        return fhlb.fetch_advances(quarters_back=5)
    if ind_key == "treasury_basis":
        return treasury_basis.fetch_treasury_cash_futures_basis()
    if ind_key == "xccy_basis":
        return xccy_basis.fetch_xccy_basis()
    if ind_key == "cftc_cot":
        return cftc_cot.fetch_spec_net_zscores()
    if ind_key == "etf_dislocation":
        lqd = etf_dislocation.fetch_lqd_dislocation().with_columns(
            pl.lit("LQD").alias("contract")
        )
        hyg = etf_dislocation.fetch_hyg_dislocation().with_columns(
            pl.lit("HYG").alias("contract")
        )
        return pl.concat([lqd, hyg]).sort(["contract", "date"])
    if ind_key == "cds_bond_basis":
        return cds_bond_basis.fetch_ebp()
    if ind_key == "insider_selling":
        return insider_selling.fetch_bank_insider_selling()
    if ind_key == "htm_unrealized":
        return htm_unrealized.fetch_htm_unrealized()
    if ind_key == "n_mfp":
        return n_mfp.fetch_concentration()
    if ind_key == "earnings_calls":
        return _earnings_calls_seed()
    if ind_key == "wti_calendar_spread":
        return wti_calendar_spread.fetch_wti_calendar_spread()
    raise RuntimeError(f"no fetcher for {ind_key}")


def validate_frame(df: pl.DataFrame, ind_key: str) -> None:
    if "date" not in df.columns or "value" not in df.columns:
        raise RuntimeError(f"{ind_key}: fetcher must return date/value columns")
    if df.height == 0:
        raise RuntimeError(f"{ind_key}: fetcher returned no rows")
    bad = df.filter(~pl.col("value").is_finite())
    if bad.height:
        raise RuntimeError(f"{ind_key}: {bad.height} non-finite values")


def build(incremental: bool = False) -> dict:
    meta_indicators = {}
    site_segments = []
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    for seg_key, seg in SEGMENTS.items():
        site_inds = []
        for ind_key, ind in seg["indicators"].items():
            entry = {
                "key": ind_key,
                "title": ind["title"],
                "why": ind["why"],
                "source": ind["source"],
                "frequency": ind["frequency"],
                "status": ind["status"],
                "note": ind.get("note", ""),
                "unit": ind.get("unit", ""),
            }
            if ind["status"] not in FETCHABLE_STATUSES:
                entry.update({"last_observation": None, "n_observations": 0})
                meta_indicators[f"{seg_key}/{ind_key}"] = entry
                site_inds.append({**entry, "series": []})
                continue
            try:
                df = fetch_indicator(ind_key, ind)
                validate_frame(df, ind_key)
                if incremental:
                    df = merge_incremental(load_existing(seg_key, ind_key), df)
                    validate_frame(df, ind_key)
                write_indicator_csv(seg_key, ind_key, df)

                group_cols = [c for c in df.columns if c not in ("date", "value")]
                last_date = str(df["date"].max())
                first_date = str(df["date"].min())
                if group_cols:
                    gcol = group_cols[0]
                    labels = sorted(df[gcol].unique().to_list())
                    last_rows = df.filter(pl.col("date") == df["date"].max())
                    last_value = {
                        r[gcol]: r["value"] for r in
                        last_rows.select(gcol, "value").iter_rows(named=True)
                    }
                    entry.update({
                        "group_by": gcol,
                        "subseries": labels,
                        "last_observation": last_date,
                        "last_value": last_value,
                        "n_observations": df.height,
                        "first_observation": first_date,
                        "fetched_at_utc": now,
                    })
                    records = sanitize_records([
                        {"date": str(r["date"]), gcol: r[gcol], "value": r["value"]}
                        for r in df.iter_rows(named=True)
                    ])
                else:
                    last = df.tail(1).row(0)
                    entry.update({
                        "last_observation": str(last[0]),
                        "last_value": last[1],
                        "n_observations": df.height,
                        "first_observation": str(df.head(1).row(0)[0]),
                        "fetched_at_utc": now,
                    })
                    records = sanitize_records(
                        [{"date": str(d), "value": v} for d, v in df.iter_rows()]
                    )
                # Strict check on the actual payload.
                strict_json(records)
                site_inds.append({**entry, "series": records})
            except Exception as e:
                entry.update({
                    "status": "error",
                    "error": str(e)[:200],
                    "last_observation": None,
                    "n_observations": 0,
                })
                site_inds.append({**entry, "series": []})
            meta_indicators[f"{seg_key}/{ind_key}"] = entry

        site_segments.append({
            "key": seg_key,
            "title": seg["title"],
            "subtitle": seg["subtitle"],
            "indicators": site_inds,
        })

    meta = {
        "built_at_utc": now,
        "incremental": incremental,
        "n_indicators": len(meta_indicators),
        "n_live": sum(1 for v in meta_indicators.values()
                      if v["status"] in FETCHABLE_STATUSES),
        "n_pending": sum(1 for v in meta_indicators.values()
                         if v["status"] == "pending-sourcing"),
        "n_error": sum(1 for v in meta_indicators.values()
                       if v["status"] == "error"),
        "indicators": meta_indicators,
    }
    (PULSE_DIR / "_meta.json").write_text(strict_json(meta) + "\n")

    site = {"meta": {"built_at_utc": now}, "segments": site_segments}
    SITE_JSON.write_text(strict_json(site) + "\n")
    # Final strict parse of exactly what the site will serve.
    json.loads(SITE_JSON.read_text())
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true")
    args = ap.parse_args()
    meta = build(incremental=args.incremental)
    print(f"live: {meta['n_live']}/{meta['n_indicators']}  "
          f"pending: {meta['n_pending']}  errors: {meta['n_error']}")
    for k, v in meta["indicators"].items():
        if v["status"] in FETCHABLE_STATUSES:
            print(f"  {k}: {v['n_observations']} obs, last {v['last_observation']}")
        else:
            print(f"  {k}: {v['status']}")
