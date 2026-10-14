#!/usr/bin/env python3
"""Build the website's data files: data/*.csv -> docs/data/*.json.

Outputs (under --out, default <repo>/docs/data):
  aggregate.json   [{"q": "1990Q1", "v": 0.34}, ...]
  components.json  {"Equity markets": [{"q": ..., "v": ...}, ...], ...}
  meta.json        copy of data/_meta.json

Missing values are emitted as null (JSON has no NaN).

Usage:
    python src/make_site.py [--data data] [--out docs/data]

Requires: polars.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent.parent


def series_json(df: pl.DataFrame, value_col: str) -> list[dict]:
    out = []
    for q, v in df.select(["quarter", value_col]).iter_rows():
        out.append({"q": q, "v": None if v is None else float(v)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--out", default=str(HERE / "docs" / "data"))
    args = ap.parse_args()

    data = Path(args.data)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    agg = pl.read_csv(data / "aggregate.csv")
    (out / "aggregate.json").write_text(
        json.dumps(series_json(agg, "Aggregate index"), indent=1))

    comp = pl.read_csv(data / "components.csv")
    comp_json = {c: series_json(comp, c) for c in comp.columns if c != "quarter"}
    (out / "components.json").write_text(json.dumps(comp_json, indent=1))

    meta = json.loads((data / "_meta.json").read_text())
    (out / "meta.json").write_text(json.dumps(meta, indent=1))

    print(f"wrote {out / 'aggregate.json'} "
          f"({len(series_json(agg, 'Aggregate index'))} quarters)")
    print(f"wrote {out / 'components.json'} ({len(comp_json)} components)")
    print(f"wrote {out / 'meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
