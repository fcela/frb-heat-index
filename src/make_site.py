#!/usr/bin/env python3
"""Build the website's data files: data/*.csv -> docs/data/*.json.

Outputs (under --out, default <repo>/docs/data):
  aggregate.json   [{"q": "1990Q1", "v": 0.34}, ...]
  components.json  {"Equity markets": [{"q": ..., "v": ...}, ...], ...}
  meta.json        data/_meta.json + site-contract fields

Missing or non-finite values are emitted as null. JSON is serialized with
allow_nan=False so a missed NaN/Inf fails the build loudly instead of
shipping invalid JSON to the site (2026-10-14 outage: bare NaN literals
killed every chart).

meta.json contract (what docs/assets/app.js renderMeta() reads):
  components, last_updated_quarter, start_quarter, n_quarters, stale_sources
plus the build fields already in data/_meta.json (kept verbatim).

Usage:
    python src/make_site.py [--data data] [--out docs/data]

Requires: polars.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent.parent


def json_value(v):
    """Map one scalar to a JSON-safe value: None/NaN/Inf -> None."""
    if v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return float(v)


def sanitize_json(obj):
    """Recursively replace NaN/Inf floats with None (strict-JSON safe)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_json(x) for k, x in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(x) for x in obj]
    return obj


def dumps_strict(obj, **kwargs) -> str:
    """Serialize to JSON, failing loudly on any NaN/Inf that slipped through."""
    return json.dumps(sanitize_json(obj), allow_nan=False, **kwargs)


def series_json(df: pl.DataFrame, value_col: str) -> list[dict]:
    out = []
    for q, v in df.select(["quarter", value_col]).iter_rows():
        out.append({"q": q, "v": json_value(v)})
    return out


def build(data: Path, out: Path) -> dict:
    """Build all site JSON files; return the meta dict that was written."""
    out.mkdir(parents=True, exist_ok=True)

    agg = pl.read_csv(data / "aggregate.csv")
    (out / "aggregate.json").write_text(
        dumps_strict(series_json(agg, "Aggregate index"), indent=1))

    comp = pl.read_csv(data / "components.csv")
    comp_cols = [c for c in comp.columns if c != "quarter"]
    comp_json = {c: series_json(comp, c) for c in comp_cols}
    (out / "components.json").write_text(dumps_strict(comp_json, indent=1))

    quarters = comp["quarter"].to_list()
    meta = json.loads((data / "_meta.json").read_text())
    # Site contract (docs/assets/app.js renderMeta): data-derived fields are
    # authoritative from the CSVs actually shipped; staleness flags stay with
    # the pipeline that authored them in data/_meta.json.
    meta["components"] = comp_cols
    meta["last_updated_quarter"] = quarters[-1] if quarters else None
    meta["start_quarter"] = quarters[0] if quarters else None
    meta["n_quarters"] = len(quarters)
    meta.setdefault("stale_sources", [])
    (out / "meta.json").write_text(dumps_strict(meta, indent=1))

    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--out", default=str(HERE / "docs" / "data"))
    args = ap.parse_args()

    meta = build(Path(args.data), Path(args.out))
    out = Path(args.out)
    print(f"wrote {out / 'aggregate.json'}")
    print(f"wrote {out / 'components.json'} ({len(meta['components'])} components)")
    print(f"wrote {out / 'meta.json'} "
          f"({meta['start_quarter']}..{meta['last_updated_quarter']}, "
          f"{meta['n_quarters']} quarters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
