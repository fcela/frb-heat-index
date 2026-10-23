#!/usr/bin/env python3
"""Apply the rebuilt Y-9C panel to data/panel_quarterly.csv (2023Q4 hole fix).

Root cause of the hole: the 2023Q4 NIC NPW bulk file contains one memo field
that starts with a literal double quote ("Service Relationship Allocation and
Transfer pricing" -- unterminated; caret-delimited NPW files carry no quoting
convention). Reading such files with CSV quote processing ON swallows every
caret and newline after that quote, corrupting the row structure; the parser
error was caught and the quarter silently skipped -- one ingestion hole that
emptied 7 Y-9C-derived series at 2023Q4 (the Weighted Average Maturity Gap
follows because loans/deposits, its regression input, was also missing).
The parse fix (quote_char=None on all NPW reads; nic_text_to_csv in the
fetchers) lives in build_y9c_fixed.py / src/systemic/fetch_y9c.py /
.github/scripts/update.py. The 2023Q4 bulk file used for the rebuild was
verified byte-identical (md5) to a fresh download from
https://www.ffiec.gov/npw/FinancialReport/ReturnBHCFZipFiles?zipfilename=BHCF20231231.ZIP

This script re-applies the rebuilt research/structural-fixes/y9c_panel_full.csv
(146 quarters, 2023Q4 present and consistent with 2023Q3/2024Q1) to
data/panel_quarterly.csv, exactly like the original structural-fixes merge
(COLMAP in assemble_panel.py):
  - the 6 Y-9C series take the y9c panel's value for every quarter;
  - the Weighted Average Maturity Gap is regenerated wherever its documented
    OLS construction (src/build_maturity_gap.py: gap = B0 + B_Y10*y10 +
    B_VIX*vix + B_LOANDEP*loan_dep, with y10 implied from the panel's E/P
    series, vix = panel VIX, loan_dep = panel loans/deposits -- all observed
    at 2023Q4) yields a value for a null cell.

Guards (fail loudly BEFORE writing anything):
  - every y9c value that would replace an existing non-null panel value must
    equal it exactly (no silent revision of history);
  - the only cells allowed to change are 2023Q4 cells of the 7 affected
    series; any other difference aborts.

Requires: polars. Run: python3 research/structural-fixes/apply_y9c_2023q4.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "src"))
from build_maturity_gap import B0, B_LOANDEP, B_VIX, B_Y10  # noqa: E402

PANEL = REPO / "data" / "panel_quarterly.csv"
Y9C = HERE / "y9c_panel_full.csv"

# y9c column -> panel column (same map as assemble_panel.py COLMAP)
COLMAP = {
    "tangible": "Bank Leverage (Total common equity to total assets)",
    "t1c_cet1": "Bank Leverage (Tier 1 common equity ratio)",
    "total_cap": "Bank Leverage (Total Capital Ratio)",
    "loans_dep": "Maturity Mismatch (Loans as a percent of deposits)",
    "top5": "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets",
    "st_money": "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)",
}
MG_COL = "Weighted Average Maturity Gap at Commerical Banks"
EP_COL = "Equity Valuations (Earnings/Price ratio )"
EPM10_COL = "Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)"
VIX_COL = "Indicators of Volitility (VIX)"


def main() -> int:
    panel = pl.read_csv(PANEL)
    y9c = pl.read_csv(Y9C)
    y9c_map = {c: dict(zip(y9c["quarter"].to_list(),
                           y9c[c].to_list())) for c in COLMAP}
    quarters = panel["quarter"].to_list()

    changed: list[tuple[str, str, float | None, float | None]] = []
    new_cols = {}
    for y9c_col, col in COLMAP.items():
        vals = panel[col].to_list()
        for i, q in enumerate(quarters):
            v = y9c_map[y9c_col].get(q)
            if v is None or v != v:  # absent or NaN in y9c panel
                continue
            old = vals[i]
            if old is None:
                changed.append((q, col, None, float(v)))
                vals[i] = float(v)
            elif float(old) != float(v):
                print(f"ABORT: {col} {q}: panel {old} != y9c {v} "
                      "(history would be silently revised)", file=sys.stderr)
                return 1
        new_cols[col] = vals

    # maturity gap: regenerate null cells whose OLS inputs are all observed
    mg = panel[MG_COL].to_list()
    ep = panel[EP_COL].to_list()
    epm = panel[EPM10_COL].to_list()
    vix = panel[VIX_COL].to_list()
    ld = new_cols[COLMAP["loans_dep"]]
    for i, q in enumerate(quarters):
        if mg[i] is not None or ep[i] is None or epm[i] is None \
                or vix[i] is None or ld[i] is None:
            continue
        y10 = float(ep[i]) - float(epm[i])
        v = B0 + B_Y10 * y10 + B_VIX * float(vix[i]) + B_LOANDEP * float(ld[i])
        changed.append((q, MG_COL, None, v))
        mg[i] = v
    new_cols[MG_COL] = mg

    # guard: only 2023Q4 cells of the 7 affected series may change
    allowed = {c for c in COLMAP.values()} | {MG_COL}
    bad = [(q, c) for q, c, *_ in changed
           if q != "2023Q4" or c not in allowed]
    if bad:
        print(f"ABORT: unexpected changes outside 2023Q4: {bad}",
              file=sys.stderr)
        return 1

    out = panel
    for col, vals in new_cols.items():
        out = out.with_columns(pl.Series(col, vals, dtype=pl.Float64))
    out.write_csv(PANEL)

    print(f"panel_quarterly.csv: {len(changed)} cell(s) filled at 2023Q4:")
    for q, c, _o, v in sorted(changed):
        print(f"  {q}  {c}: {v:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
