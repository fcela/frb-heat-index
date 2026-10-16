"""Build the bank revenue tracker panel.

Reads the two-layer parser outputs:
  data/revenue/segments_annual.csv   (XBRL R-file segment totals)
  data/revenue/subsplits_annual.csv  (MD&A sub-business splits)

Applies the taxonomy mapping and the reorg/break registry, then writes:
  data/revenue/panel_annual.csv      (firm x taxonomy_line x year)
  data/revenue/_meta.json            (per-cell sourcing metadata)
  docs/data/revenue.json             (site bundle)

Break policy: values are never spliced across a reorg break. Each cell
carries the segment regime it was reported under; the site draws a visual
break at regime changes.

Run: python3 -m src.revenue.build_revenue
"""

import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.revenue.taxonomy import TAXONOMY_LINES, map_quality, canonical_firm  # noqa: E402
from src.revenue.reorg_registry import REORGS, regime_for  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "revenue"


def sanitize_records(recs):
    """NaN/Inf -> None; strict round-trip check."""
    out = []
    for r in recs:
        nr = {}
        for k, v in r.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                v = None
            nr[k] = v
        out.append(nr)
    # strict parse check: must survive a JSON round trip
    json.dumps(out, allow_nan=False)
    return out


def load_inputs():
    seg = pl.read_csv(DATA / "segments_annual.csv")
    sub = pl.read_csv(DATA / "subsplits_annual.csv")
    return seg, sub


def build_panel(seg, sub):
    """Combine segment totals and MD&A splits into firm x line x year panel.

    Precedence: MD&A sub-splits win for the lines they cover (they are the
    finer granularity). Segment totals fill the lines the splits don't cover.
    """
    # Tag each row with its taxonomy line and map quality.
    rows = []
    for r in seg.iter_rows(named=True):
        firm = canonical_firm(r["firm"])
        line, quality, _note = map_quality(firm, r["segment_native"])
        rows.append({**r, "firm": firm, "taxonomy_line": line,
                     "map_quality": quality, "layer": "xbrl"})
    for r in sub.iter_rows(named=True):
        firm = canonical_firm(r["firm"])
        rows.append({"firm": firm, "cik": r["cik"], "segment_native": None,
                     "year": r["year"], "net_revenue_m": r["net_revenue_m"],
                     "accession": r["accession"], "r_file": None,
                     "source": r["source"], "taxonomy_line": r["taxonomy_line"],
                     "map_quality": "D", "layer": "mda"})
    df = pl.DataFrame(rows, schema={
        "firm": pl.String, "cik": pl.String, "segment_native": pl.String,
        "year": pl.Int64, "net_revenue_m": pl.Float64, "accession": pl.String,
        "r_file": pl.String, "source": pl.String, "taxonomy_line": pl.String,
        "map_quality": pl.String, "layer": pl.String,
    })

    # Regime tagging: which reporting regime each observation falls in.
    df = df.with_columns(
        pl.struct(["firm", "year"])
        .map_elements(
            lambda s: regime_for(s["firm"], s["year"]), return_dtype=pl.String
        )
        .alias("regime")
    )

    # Dedupe: same firm/line/year from overlapping filings — prefer the later
    # filing's restated value. `accession` sorts chronologically.
    df = df.sort(["firm", "taxonomy_line", "year", "accession"])
    df = df.unique(subset=["firm", "taxonomy_line", "year"], keep="last")

    # Drop unmappable (null line) rows but record them in meta.
    unmapped = df.filter(pl.col("taxonomy_line").is_null())
    panel = df.filter(pl.col("taxonomy_line").is_not_null())
    unmapped_recs = []
    for r in unmapped.iter_rows(named=True):
        unmapped_recs.append({
            "firm": r["firm"],
            "segment_native": r.get("segment_native"),
            "taxonomy_line_attempt": r.get("taxonomy_line"),
            "year": r["year"],
            "layer": r["layer"],
        })
    panel = panel.select(
        ["firm", "taxonomy_line", "year", "net_revenue_m", "map_quality",
         "layer", "regime", "source", "accession"]
    ).sort(["firm", "taxonomy_line", "year"])
    return panel, unmapped_recs


def main():
    seg, sub = load_inputs()
    panel, unmapped = build_panel(seg, sub)

    DATA.mkdir(parents=True, exist_ok=True)
    panel.write_csv(DATA / "panel_annual.csv")

    meta = {
        "n_firms": panel["firm"].n_unique(),
        "n_lines": panel["taxonomy_line"].n_unique(),
        "year_min": int(panel["year"].min()),
        "year_max": int(panel["year"].max()),
        "n_cells": len(panel),
        "taxonomy_lines": TAXONOMY_LINES,
        "unmapped_segments": unmapped,
        "raw_location": "data/revenue/raw/<cik>/<accession>/ (preserved, git-ignored)",
        "break_policy": ("values never spliced across reorg breaks; "
                         "regime column identifies the reporting regime"),
    }
    (DATA / "_meta.json").write_text(json.dumps(meta, indent=2))

    # Site bundle: per line -> per firm -> series; regimes for break marks.
    bundle_lines = []
    for line in TAXONOMY_LINES:
        firms = []
        for firm in sorted(panel["firm"].unique()):
            s = panel.filter(
                (pl.col("taxonomy_line") == line) & (pl.col("firm") == firm)
            ).sort("year")
            if len(s) == 0:
                continue
            recs = sanitize_records(s.select(
                ["year", "net_revenue_m", "regime", "map_quality", "layer"]
            ).rename({"net_revenue_m": "value"}).to_dicts())
            firms.append({"firm": firm, "series": recs})
        if firms:
            bundle_lines.append({"line": line, "firms": firms})
    bundle = {
        "meta": {
            "title": "Bank revenue tracker",
            "unit": "USD millions",
            "frequency": "annual",
            "lines": TAXONOMY_LINES,
        },
        "lines": bundle_lines,
    }
    bundle = json.loads(json.dumps(bundle, allow_nan=False))  # strict
    out = REPO / "docs" / "data" / "revenue.json"
    out.write_text(json.dumps(bundle))

    print(f"panel: {len(panel)} cells, "
          f"{panel['firm'].n_unique()} firms x "
          f"{panel['taxonomy_line'].n_unique()} lines x "
          f"{panel['year'].n_unique()} years "
          f"({panel['year'].min()}-{panel['year'].max()})")
    print(f"unmapped segments: {len(unmapped)}")
    for u in unmapped[:10]:
        print(f"  - {u['firm']}: {u['segment_native']} ({u['year']})")


if __name__ == "__main__":
    main()
