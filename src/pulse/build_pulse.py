"""Build the market-pulse dataset.

Fetches every live indicator, validates, and writes:
  data/pulse/<segment>/<indicator>.csv   (date,value)
  data/pulse/_meta.json                  (per-indicator sourcing metadata)
  docs/data/pulse.json                   (site bundle)

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
from src.pulse.segments import SEGMENTS, all_indicators

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
    df.select("date", "value").write_csv(p)
    return p


def load_existing(seg: str, key: str) -> pl.DataFrame | None:
    p = PULSE_DIR / seg / f"{key}.csv"
    if not p.exists():
        return None
    return pl.read_csv(p).with_columns(pl.col("date").str.to_date())


def merge_incremental(old: pl.DataFrame | None, new: pl.DataFrame) -> pl.DataFrame:
    if old is None or old.height == 0:
        return new
    combined = pl.concat([old, new]).unique("date", keep="last").sort("date")
    return combined


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
            if ind["status"] != "live":
                entry.update({"last_observation": None, "n_observations": 0})
                meta_indicators[f"{seg_key}/{ind_key}"] = entry
                site_inds.append({**entry, "series": []})
                continue
            try:
                if "fred_id" in ind:
                    df = fred.fetch_fred_csv(ind["fred_id"])
                elif ind_key == "mbs_spread":
                    a = fred.fetch_fred_csv("OBMMIC30YF").rename({"value": "mort"})
                    b = fred.fetch_fred_csv("DGS30").rename({"value": "tsy"})
                    df = a.join(b, on="date", how="inner").with_columns(
                        (pl.col("mort") - pl.col("tsy")).alias("value")
                    ).select("date", "value")
                elif ind_key == "margin_debt":
                    df = finra.fetch_margin_debt()
                else:
                    raise RuntimeError(f"no fetcher for {ind_key}")

                if incremental:
                    df = merge_incremental(load_existing(seg_key, ind_key), df)
                write_indicator_csv(seg_key, ind_key, df)

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
        "n_live": sum(1 for v in meta_indicators.values() if v["status"] == "live"),
        "n_pending": sum(1 for v in meta_indicators.values() if v["status"] == "pending-sourcing"),
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
          f"pending: {meta['n_pending']}")
    for k, v in meta["indicators"].items():
        if v["status"] == "live":
            print(f"  {k}: {v['n_observations']} obs, last {v['last_observation']}")
        else:
            print(f"  {k}: {v['status']}")
