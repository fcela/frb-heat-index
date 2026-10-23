"""Regression tests for the 2023Q4 Y-9C ingestion fix.

Root cause: the 2023Q4 NIC NPW bulk file contains one memo field that starts
with a literal double quote ("Service Relationship Allocation and Transfer
pricing" -- unterminated). Caret-delimited NPW files carry no quoting
convention, so any CSV quote processing swallows carets/newlines after that
quote, corrupts the row structure, and the quarter was silently skipped --
emptying 7 Y-9C-derived panel series at 2023Q4. The fix reads NPW/NIC files
with quote processing OFF everywhere (build_y9c_fixed.py, and
src/systemic/fetch_y9c.py nic_text_to_csv shared with
.github/scripts/update.py).

Checks:
- a fixture NPW file with the same pathologies parses to all rows with quote
  processing OFF, and can never yield all rows with it ON (old behavior)
- nic_text_to_csv converts it to a standard comma CSV preserving every row
- research/structural-fixes/y9c_panel_full.csv covers all 146 quarters
  (2023Q4 present, values equal to data/panel_quarterly.csv)
- the 2023Q4 maturity-gap cell reproduces its documented OLS construction
- 2023Q4 values are consistent with 2023Q3/2024Q1 (both adjacent changes
  within 3x the series' 90th-percentile adjacent change)
- no Y-9C-derived series has interior nulls (nulls, where present, form a
  leading run only)

Runnable with plain python3 (no pytest needed):
    python3 tests/test_y9c_2023q4.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
from build_maturity_gap import B0, B_LOANDEP, B_VIX, B_Y10  # noqa: E402
from systemic.fetch_y9c import nic_text_to_csv  # noqa: E402

FIXTURE = HERE / "tests" / "fixtures" / "y9c" / "bhcf_npw_fixture.txt"
Y9C_PANEL = HERE / "research" / "structural-fixes" / "y9c_panel_full.csv"
PANEL = HERE / "data" / "panel_quarterly.csv"
COMPLETE = HERE / "data" / "panel_complete.csv"

Y9C_SERIES = {
    "tangible": "Bank Leverage (Total common equity to total assets)",
    "t1c_cet1": "Bank Leverage (Tier 1 common equity ratio)",
    "total_cap": "Bank Leverage (Total Capital Ratio)",
    "loans_dep": "Maturity Mismatch (Loans as a percent of deposits)",
    "top5": "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets",
    "st_money": "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)",
}
MG_COL = "Weighted Average Maturity Gap at Commerical Banks"

ALL_QUARTERS = [f"{y}Q{q}" for y in range(1990, 2027)
                for q in range(1, 5) if not (y == 2026 and q > 2)]


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return False
    return True


def valid_rows(fn: str, quote_char) -> int:
    d = pl.read_csv(fn, columns=["RSSD9001", "RSSD9999", "BHCK2170"],
                    separator="^", encoding="utf8-lossy",
                    truncate_ragged_lines=True, quote_char=quote_char)
    ok = d.filter(pl.col("RSSD9999").cast(pl.String).str.contains(r"^\d{8}$"))
    return ok.height


def main() -> int:
    ok = True

    # --- parse fix: quote OFF gets every row; quote ON never can ----------
    ok &= check(valid_rows(str(FIXTURE), None) == 3,
                "quote_char=None must parse all 3 fixture filers")
    try:
        n_on = valid_rows(str(FIXTURE), '"')
    except Exception:
        n_on = -1  # parser error -- the real 2023Q4 file did exactly this
    ok &= check(n_on != 3,
                f"quote processing ON must not yield all filers (got {n_on})")

    csv_text = nic_text_to_csv(FIXTURE.read_text())
    d = pl.read_csv(csv_text.encode(), columns=["RSSD9001", "BHCK2170"])
    ok &= check(d.height == 3 and sorted(d["RSSD9001"].to_list())
                == [1039502, 1075648, 1120754],
                f"nic_text_to_csv round-trip lost rows: {d.height}")

    # --- rebuilt Y-9C panel covers every quarter -------------------------
    y9c = pl.read_csv(Y9C_PANEL)
    ok &= check(sorted(y9c["quarter"].to_list()) == ALL_QUARTERS,
                "y9c_panel_full.csv must cover 1990Q1..2026Q2 with no gap")
    r24 = y9c.filter(pl.col("quarter") == "2023Q4")
    ok &= check(r24.height == 1 and r24["n_filers"][0] > 0,
                "y9c panel 2023Q4 missing or has no filers")

    # --- panel agrees with the rebuilt y9c panel at 2023Q4 ----------------
    panel = pl.read_csv(PANEL)
    prow = panel.filter(pl.col("quarter") == "2023Q4").row(0, named=True)
    for y9c_col, col in Y9C_SERIES.items():
        v = float(r24[y9c_col][0])
        ok &= check(prow[col] is not None and abs(prow[col] - v) < 1e-9,
                    f"{col}: panel 2023Q4 {prow[col]} != y9c {v}")

    # --- maturity gap 2023Q4 reproduces its documented OLS ----------------
    row = panel.filter(pl.col("quarter") == "2023Q4").row(0, named=True)
    y10 = (row["Equity Valuations (Earnings/Price ratio )"]
           - row["Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)"])
    gap = B0 + B_Y10 * y10 + B_VIX * row["Indicators of Volitility (VIX)"] \
        + B_LOANDEP * row[Y9C_SERIES["loans_dep"]]
    ok &= check(abs(gap - row[MG_COL]) < 0.01,
                f"maturity gap 2023Q4: OLS {gap:.4f} != panel {row[MG_COL]}")

    # --- 2023Q4 consistent with 2023Q3/2024Q1 -----------------------------
    idx = {q: i for i, q in enumerate(panel["quarter"].to_list())}
    i = idx["2023Q4"]
    for col in list(Y9C_SERIES.values()) + [MG_COL]:
        v = panel[col].to_numpy().astype(float)
        d_ = np.abs(np.diff(v[~np.isnan(v)]))
        p90 = float(np.percentile(d_, 90))
        ok &= check(abs(v[i] - v[i - 1]) <= 3 * p90 and
                    abs(v[i + 1] - v[i]) <= 3 * p90,
                    f"{col}: 2023Q4 jump vs neighbors exceeds 3x p90 "
                    f"({v[i-1]} -> {v[i]} -> {v[i+1]})")

    # --- no interior nulls in any Y-9C-derived series ----------------------
    comp = pl.read_csv(COMPLETE)
    cidx = {q: i for i, q in enumerate(comp["quarter"].to_list())}
    for col in list(Y9C_SERIES.values()) + [MG_COL]:
        v = comp[col].to_list()
        nn = [j for j, x in enumerate(v) if x is not None]
        holes = [j for j in range(nn[0], nn[-1] + 1) if v[j] is None]
        ok &= check(not holes,
                    f"{col}: interior holes at {[comp['quarter'][j] for j in holes]}")

    print("test_y9c_2023q4: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
