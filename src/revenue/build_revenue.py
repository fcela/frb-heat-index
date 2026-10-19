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

    Precedence: direct (D) disclosures beat approximate (A) mappings of the
    same line -- MD&A sub-splits win for the lines they cover (they are the
    finer granularity). Segment totals fill the lines the splits don't
    cover. Within a quality tier the later filing's restated value wins.
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

    # Dedupe: same firm/line/year from overlapping filings and the two
    # layers. Direct (D) disclosures beat approximate (A) mappings of the
    # same line -- e.g. MD&A advisory fees beat the whole "Banking"
    # division mapped to advisory with quality A. Within a quality tier
    # the later filing's restated value wins (`accession` sorts
    # chronologically).
    quality_rank = {"D": 0, "A": 1, "U": 2}
    df = df.with_columns(
        pl.col("map_quality").replace_strict(
            quality_rank, default=3).alias("_qrank"))
    # Winner sorts first: best quality tier, then later accession
    # (restated value wins). maintain_order keeps polars' non-stable
    # sort from reshuffling rows that tie on every key -- several
    # segments can map to one line at the same quality from the same
    # filing (e.g. GS's three Investing-and-Lending sub-segments), and
    # an unstable order made the surviving cell vary run to run.
    # Ties resolve to the first row in frame order (segment_native
    # ascending within a firm-year).
    df = df.sort(["firm", "taxonomy_line", "year", "_qrank", "accession"],
                 descending=[False, False, False, False, True],
                 maintain_order=True)
    df = df.unique(subset=["firm", "taxonomy_line", "year"], keep="first")
    df = df.drop("_qrank")

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


# MD&A splits -> parent XBRL division(s), for the reconciliation check.
# Each group lists candidate parent segment names (the first name present
# for that firm+year wins; filing vintages restate labels, e.g. JPM's
# "Corporate & Investment Bank" vs "Commercial & Investment Bank") and the
# taxonomy lines whose values must fit inside the parent total. Pre-2021
# Citi has no XBRL Banking/Markets split, so both groups fall back to the
# wider "Institutional Clients Group" parent (a looser but valid bound).
RECONCILE_GROUPS = {
    "JPMorgan": [
        (["Commercial & Investment Bank", "Corporate & Investment Bank",
          "Investment Bank"],
         ["advisory", "ecm", "dcm", "ficc", "equities"]),
    ],
    "Morgan Stanley": [
        (["Institutional Securities"],
         ["advisory", "ecm", "dcm", "ficc", "equities"]),
    ],
    "Bank of America": [
        (["Global Banking"], ["advisory", "ecm_dcm_combined"]),
        (["Global Markets"], ["ficc", "equities"]),
    ],
    "Citigroup": [
        (["Banking", "Institutional Clients Group"],
         ["advisory", "ecm", "dcm"]),
        (["Markets", "Institutional Clients Group"],
         ["ficc", "equities"]),
    ],
}

RECONCILE_TOL_PCT = 0.05
RECONCILE_TOL_ABS = 25.0  # USD millions


def reconcile_splits(sub, seg):
    """Check MD&A splits against parent XBRL division totals.

    For each (firm, year, group): the splits' sum must not exceed the
    parent division's XBRL total by more than 5% or $25M, whichever is
    larger. Mismatches are flagged in the report, never fudged.

    `sub` is the subsplits frame (firm codes), `seg` the XBRL segments
    frame (firm display names). Returns a report dict for _meta.json.
    """
    checks = []
    sub = sub.with_columns(
        pl.col("firm").map_elements(canonical_firm,
                                    return_dtype=pl.String).alias("_cfirm"))
    for firm, groups in RECONCILE_GROUPS.items():
        fsub = sub.filter(pl.col("_cfirm") == firm)
        fseg = seg.filter(pl.col("firm") == firm)
        years = sorted(set(fsub["year"].to_list()))
        for year in years:
            for parents, lines in groups:
                srows = fsub.filter((pl.col("year") == year)
                                    & pl.col("taxonomy_line").is_in(lines))
                if len(srows) == 0:
                    continue
                splits_sum = srows["net_revenue_m"].sum()
                parent = next(
                    (p for p in parents
                     if len(fseg.filter(
                         (pl.col("year") == year)
                         & (pl.col("segment_native") == p))) > 0),
                    None)
                if parent is None:
                    checks.append({
                        "firm": firm, "year": int(year),
                        "group": "|".join(lines), "parent": None,
                        "status": "no_parent",
                    })
                    continue
                prow = fseg.filter(
                    (pl.col("year") == year)
                    & (pl.col("segment_native") == parent)).sort("accession")
                parent_total = prow["net_revenue_m"][-1]
                allowed = parent_total * (1 + RECONCILE_TOL_PCT) \
                    + RECONCILE_TOL_ABS
                status = "pass" if splits_sum <= allowed else "flag"
                checks.append({
                    "firm": firm, "year": int(year),
                    "group": "|".join(lines), "parent": parent,
                    "parent_total_m": round(float(parent_total), 3),
                    "splits_sum_m": round(float(splits_sum), 3),
                    "status": status,
                })
    n_pass = sum(1 for c in checks if c["status"] == "pass")
    n_flag = sum(1 for c in checks if c["status"] == "flag")
    n_noparent = sum(1 for c in checks if c["status"] == "no_parent")
    denom = n_pass + n_flag
    return {
        "tolerance": "5% or $25M, whichever is larger",
        "n_checks": len(checks),
        "n_pass": n_pass,
        "n_flag": n_flag,
        "n_no_parent": n_noparent,
        "match_rate": round(n_pass / denom, 4) if denom else None,
        "checks": checks,
    }


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
        "reconciliation": reconcile_splits(sub, seg),
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
