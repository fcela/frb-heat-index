"""Build the quarterly bank revenue tracker panel.

Reads:
  data/revenue/segments_quarterly.csv  (10-Q XBRL segment periods)

Maps each (duration, period_end) to a calendar quarter, prefers 3-month
(QTD) values, and derives missing quarters from YTD durations:
  Q2 = 6M(Q2 end) - Q1,  Q3 = 9M(Q3 end) - 6M(Q2 end),
  Q4 = Annual(10-K) - 9M(Q3 end).
Derived quarters are validated (same sign as the YTD total, magnitude not
exceeding the YTD by more than 10%); failures are recorded, never fudged.

Applies the taxonomy mapping and the quarterly regime registry, then
writes:
  data/revenue/panel_quarterly.csv   (firm x taxonomy_line x year x quarter)
  docs/data/revenue_quarterly.json   (site bundle)

Scope: XBRL segment layer only. 10-Q MD&A sub-business splits (e.g. JPM
CIB revenue-by-business, MS IS income statement) are NOT extracted --
their quarterly tables exist but the MD&A extractor is 10-K-oriented.
Division-reporters therefore show division totals (often unmappable to a
single taxonomy line) at quarterly frequency. This limitation is reported
in _meta.json, not silently worked around.

Run: python3 -m src.revenue.build_revenue_q
"""

import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.revenue.taxonomy import (  # noqa: E402
    TAXONOMY_LINES, map_quality, canonical_firm)
from src.revenue.reorg_registry import regime_for_quarter  # noqa: E402
from src.revenue.build_revenue import sanitize_records  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "revenue"


def quarter_of(period_end):
    """'2018-09-30' -> (2018, 3). Calendar quarters by end-date month."""
    y, m, _ = (int(x) for x in period_end.split("-"))
    return y, (m - 1) // 3 + 1


def build_quarterly(seg_q, seg_a):
    """Derive quarterly values from 10-Q periods + annuals.

    Returns (panel_df, derivation_log). panel_df has firm, taxonomy_line,
    year, quarter, net_revenue_m, derivation, map_quality, layer, regime,
    source, accession.
    """
    # Index QTD and YTD values: (firm, segment, year, quarter) -> value.
    qtd = {}   # 3-month durations
    ytd6 = {}  # 6-month durations keyed by their end quarter (Q2)
    ytd9 = {}  # 9-month durations keyed by their end quarter (Q3)
    acc_of = {}
    for r in seg_q.iter_rows(named=True):
        firm = canonical_firm(r["firm"])
        key = (firm, r["segment_native"])
        y, q = quarter_of(r["period_end"])
        v = r["net_revenue_m"]
        acc_of[(key, y, q, r["duration_months"])] = r["accession"]
        if r["duration_months"] == 3:
            qtd[(key, y, q)] = v
        elif r["duration_months"] == 6:
            ytd6[(key, y, q)] = v
        elif r["duration_months"] == 9:
            ytd9[(key, y, q)] = v

    # Annuals for Q4 derivation: (firm, segment, year) -> value.
    annual = {}
    for r in seg_a.iter_rows(named=True):
        firm = canonical_firm(r["firm"])
        # segment names must match the quarterly ones; both come from the
        # same XBRL parser family, so native names align.
        annual[(firm, r["segment_native"], r["year"])] = r["net_revenue_m"]

    log = []
    rows = []

    def emit(firm, seg, y, q, v, derivation, accession):
        line, quality, _ = map_quality(firm, seg)
        rows.append({
            "firm": firm, "segment_native": seg, "taxonomy_line": line,
            "map_quality": quality, "layer": "xbrl_q",
            "year": y, "quarter": q, "net_revenue_m": v,
            "derivation": derivation,
            "regime": regime_for_quarter(firm, y, q),
            "source": "XBRL R-file (10-Q)" if derivation == "qtd"
                      else f"XBRL R-file (10-Q, {derivation})",
            "accession": accession,
        })

    def validated(name, firm, seg, y, q, v, ref, accession):
        """Validate a derived quarter against its YTD reference."""
        if ref is None or ref == 0:
            log.append({"firm": firm, "segment": seg, "year": y,
                        "quarter": q, "derivation": name,
                        "status": "no_reference"})
            return
        # Same sign (or tiny), magnitude within 10% of the YTD total.
        if abs(v) > abs(ref) * 1.1:
            log.append({"firm": firm, "segment": seg, "year": y,
                        "quarter": q, "derivation": name,
                        "status": "failed_magnitude",
                        "value_m": v, "ref_m": ref})
            return
        if v != 0 and ref != 0 and (v > 0) != (ref > 0) \
                and abs(v) > abs(ref) * 0.05:
            log.append({"firm": firm, "segment": seg, "year": y,
                        "quarter": q, "derivation": name,
                        "status": "failed_sign",
                        "value_m": v, "ref_m": ref})
            return
        log.append({"firm": firm, "segment": seg, "year": y,
                    "quarter": q, "derivation": name, "status": "ok",
                    "value_m": v, "ref_m": ref})
        emit(firm, seg, y, q, v, name, accession)

    segments = {(f, s) for (f, s), _, _ in qtd} | \
               {(f, s) for (f, s), _, _ in ytd6} | \
               {(f, s) for (f, s), _, _ in ytd9}
    for firm, seg in sorted(segments):
        key = (firm, seg)
        years = sorted({y for (k, y, q) in list(qtd) + list(ytd6)
                        + list(ytd9) if k == key})
        for y in years:
            # Q2 = 6M(Q2 end) - Q1(QTD); Q3 = 9M(Q3 end) - 6M(Q2 end).
            # Prefer QTD where the filing carries it.
            q2 = qtd.get((key, y, 2))
            if q2 is not None:
                emit(firm, seg, y, 2, q2, "qtd",
                     acc_of.get((key, y, 2, 3), ""))
            else:
                tot = ytd6.get((key, y, 2))
                prev = qtd.get((key, y, 1))
                if tot is not None and prev is not None:
                    validated("ytd_derived_q2", firm, seg, y, 2,
                              tot - prev, tot,
                              acc_of.get((key, y, 2, 6), ""))
                else:
                    log.append({"firm": firm, "segment": seg,
                                "year": y, "quarter": 2,
                                "derivation": "ytd_derived_q2",
                                "status": "missing_inputs"})
            q3 = qtd.get((key, y, 3))
            if q3 is not None:
                emit(firm, seg, y, 3, q3, "qtd",
                     acc_of.get((key, y, 3, 3), ""))
            else:
                tot = ytd9.get((key, y, 3))
                prev = ytd6.get((key, y, 2))
                if tot is not None and prev is not None:
                    validated("ytd_derived_q3", firm, seg, y, 3,
                              tot - prev, tot,
                              acc_of.get((key, y, 3, 9), ""))
                else:
                    log.append({"firm": firm, "segment": seg,
                                "year": y, "quarter": 3,
                                "derivation": "ytd_derived_q3",
                                "status": "missing_inputs"})
            # Q1 direct only (no YTD to derive from).
            v = qtd.get((key, y, 1))
            if v is not None:
                emit(firm, seg, y, 1, v, "qtd",
                     acc_of.get((key, y, 1, 3), ""))
            # Q4 = Annual - 9M.
            tot9 = ytd9.get((key, y, 3))
            ann = annual.get((firm, seg, y))
            if tot9 is not None and ann is not None:
                validated("annual_derived_q4", firm, seg, y, 4,
                          ann - tot9, ann,
                          acc_of.get((key, y, 3, 9), ""))
            elif (key, y, 4) not in qtd:
                # (10-Qs never carry Q4; only log when truly missing.)
                pass

    df = pl.DataFrame(rows, schema={
        "firm": pl.String, "segment_native": pl.String,
        "taxonomy_line": pl.String, "map_quality": pl.String,
        "layer": pl.String, "year": pl.Int64, "quarter": pl.Int64,
        "net_revenue_m": pl.Float64, "derivation": pl.String,
        "regime": pl.String, "source": pl.String, "accession": pl.String,
    })
    # Dedupe: keep the best derivation (qtd > ytd_derived > annual_derived).
    rank = {"qtd": 0, "ytd_derived_q2": 1, "ytd_derived_q3": 1,
            "annual_derived_q4": 2}
    if len(df):
        df = df.with_columns(
            pl.col("derivation").replace_strict(rank, default=3)
            .alias("_drank"))
        df = df.sort(["firm", "taxonomy_line", "year", "quarter",
                      "_drank", "accession"])
        df = df.unique(subset=["firm", "taxonomy_line", "year", "quarter"],
                       keep="first").drop("_drank")
        df = df.sort(["firm", "taxonomy_line", "year", "quarter"])
    # Drop unmappable rows from the panel (recorded in meta).
    unmapped = df.filter(pl.col("taxonomy_line").is_null())
    panel = df.filter(pl.col("taxonomy_line").is_not_null())
    return panel, unmapped.to_dicts(), log


def reconcile_quarterly(panel, annual_panel):
    """Sum of 4 quarters vs the annual 10-K cell, per firm x line x year.

    Tolerance: 5% or $25M, whichever is larger. Mismatches flagged, never
    fudged. Only checks cells with all 4 quarters present.
    """
    qsum = (panel.group_by(["firm", "taxonomy_line", "year"])
            .agg([pl.col("net_revenue_m").sum().alias("q_sum"),
                  pl.len().alias("n_q")]))
    qsum = qsum.filter(pl.col("n_q") == 4)
    ann = annual_panel.select(
        ["firm", "taxonomy_line", "year", "net_revenue_m"])
    m = qsum.join(ann, on=["firm", "taxonomy_line", "year"], how="inner")
    checks = []
    for r in m.iter_rows(named=True):
        allowed = abs(r["net_revenue_m"]) * 0.05 + 25.0
        diff = abs(r["q_sum"] - r["net_revenue_m"])
        checks.append({
            "firm": r["firm"], "taxonomy_line": r["taxonomy_line"],
            "year": int(r["year"]),
            "q_sum_m": round(r["q_sum"], 1),
            "annual_m": round(r["net_revenue_m"], 1),
            "diff_m": round(diff, 1),
            "status": "pass" if diff <= allowed else "flag",
        })
    n_pass = sum(1 for c in checks if c["status"] == "pass")
    return {
        "tolerance": "5% or $25M, whichever is larger",
        "n_checks": len(checks),
        "n_pass": n_pass,
        "n_flag": len(checks) - n_pass,
        "match_rate": round(n_pass / len(checks), 4) if checks else None,
        "checks": checks,
    }


def main():
    seg_q = pl.read_csv(DATA / "segments_quarterly.csv")
    seg_a = pl.read_csv(DATA / "segments_annual.csv")
    panel, unmapped, dlog = build_quarterly(seg_q, seg_a)

    annual_panel = pl.read_csv(DATA / "panel_annual.csv")
    recon = reconcile_quarterly(panel, annual_panel)

    DATA.mkdir(parents=True, exist_ok=True)
    panel.write_csv(DATA / "panel_quarterly.csv")

    n_derived = sum(1 for e in dlog if e.get("status") == "ok"
                    and e.get("derivation", "").startswith(("ytd", "annual")))
    meta = {
        "n_firms": panel["firm"].n_unique() if len(panel) else 0,
        "n_lines": panel["taxonomy_line"].n_unique() if len(panel) else 0,
        "year_min": int(panel["year"].min()) if len(panel) else None,
        "year_max": int(panel["year"].max()) if len(panel) else None,
        "n_cells": len(panel),
        "taxonomy_lines": TAXONOMY_LINES,
        "unmapped_segments": unmapped,
        "derivation_log": dlog,
        "n_derived_cells": n_derived,
        "reconciliation_vs_annual": recon,
        "scope": ("XBRL segment layer only; 10-Q MD&A sub-business splits "
                  "not extracted (see module docstring)"),
        "break_policy": ("values never spliced across reorg breaks; "
                         "regime column identifies the reporting regime"),
    }
    (DATA / "_meta_quarterly.json").write_text(json.dumps(meta, indent=2))

    # Site bundle: per line -> per firm -> quarterly series.
    bundle_lines = []
    for line in TAXONOMY_LINES:
        firms = []
        for firm in sorted(panel["firm"].unique()) if len(panel) else []:
            s = panel.filter(
                (pl.col("taxonomy_line") == line) & (pl.col("firm") == firm)
            ).sort(["year", "quarter"])
            if len(s) == 0:
                continue
            recs = sanitize_records(s.select(
                ["year", "quarter", "net_revenue_m", "regime",
                 "map_quality", "derivation", "layer"]
            ).rename({"net_revenue_m": "value"}).to_dicts())
            # x-axis position: year + (quarter-1)/4
            for r in recs:
                r["t"] = r["year"] + (r["quarter"] - 1) / 4.0
            firms.append({"firm": firm, "series": recs})
        if firms:
            bundle_lines.append({"line": line, "firms": firms})
    bundle = {
        "meta": {
            "title": "Bank revenue tracker",
            "unit": "USD millions",
            "frequency": "quarterly",
            "lines": TAXONOMY_LINES,
        },
        "lines": bundle_lines,
    }
    bundle = json.loads(json.dumps(bundle, allow_nan=False))
    out = REPO / "docs" / "data" / "revenue_quarterly.json"
    out.write_text(json.dumps(bundle))
    print(f"quarterly panel: {len(panel)} cells, "
          f"reconciliation match rate: {recon['match_rate']} "
          f"({recon['n_pass']}/{recon['n_checks']})")


if __name__ == "__main__":
    main()
