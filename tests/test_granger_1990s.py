"""Regression tests for the Granger 1990Q1-1992Q4 extension (slice 4).

The series' committed 1993Q1 start was a builder-side quarter filter
(build_granger.py hardcoded q >= "1993Q1"), not a data boundary: the
pipeline's own Yahoo fetcher (period1=0) returns monthly bars from 1985-01
for the core firm panel, so full 36-month rolling windows exist from 1987Q4
and every panel quarter 1990Q1..1992Q4 satisfies the documented methodology
(36-month window, >=30 monthly obs per firm, >=10 firms). The extension was
applied by research/structural-fixes/apply_granger_1990_1992.py with guards:
committed history reproduced exactly; only the 12 leading cells filled.

Checks:
- data/systemic/granger_quarterly.csv covers exactly 1990Q1..2026Q2 with no
  missing quarters and no null values
- 1993Q1..2026Q2 matches the pre-extension fixture exactly (all four
  columns: value, n_firms, n_sig, n_possible) -- history can never be
  silently revised by a future rebuild
- the 12 extension quarters have values in (0,1), n_firms >= 15, and
  n_possible == n_firms*(n_firms-1) (the documented edge share denominator)
- extension values are consistent with the first observed years: each is
  positive and within [0.5x, 3x] of the mean of the first 12 committed
  quarters (crisis-era elevation expected, not a different series)
- data/panel_complete.csv carries the systemic series 1:1 for all 146
  quarters, so the panel can never drift from the published build

Runnable with plain python3 (no pytest needed):
    python3 tests/test_granger_1990s.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent.parent
SYSTEMIC = HERE / "data" / "systemic" / "granger_quarterly.csv"
FIXTURE = HERE / "tests" / "fixtures" / "systemic" / "granger_pre_extension.csv"
COMPLETE = HERE / "data" / "panel_complete.csv"
COL = "Granger-causality connections among large financial institutions"

ALL_QUARTERS = [f"{y}Q{q}" for y in range(1990, 2027)
                for q in range(1, 5) if not (y == 2026 and q > 2)]
NEW_QUARTERS = [f"{y}Q{q}" for y in (1990, 1991, 1992) for q in range(1, 5)]


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return False
    return True


def main() -> int:
    ok = True
    new = pl.read_csv(SYSTEMIC).sort("quarter")
    old = pl.read_csv(FIXTURE).sort("quarter")

    ok &= check(new["quarter"].to_list() == ALL_QUARTERS,
                "systemic granger quarters must be exactly 1990Q1..2026Q2")
    ok &= check(new["value"].null_count() == 0, "granger series has null values")

    # history preservation: 1993Q1..2026Q2 identical to the pre-extension
    # fixture on every column
    j = old.join(new, on="quarter", how="inner", suffix="_new")
    ok &= check(j.height == old.height == 134,
                f"fixture join covers all 134 pre-extension quarters "
                f"(got {j.height}/{old.height})")
    for c in ["value", "n_firms", "n_sig", "n_possible"]:
        bad = j.filter(pl.col(c) != pl.col(c + "_new"))
        ok &= check(bad.height == 0,
                    f"granger history revised in {c} at "
                    f"{bad['quarter'].head(3).to_list()}")

    # extension quarters: documented-methodology sanity
    ext = new.filter(pl.col("quarter").is_in(NEW_QUARTERS))
    ok &= check(((ext["value"] > 0) & (ext["value"] < 1)).all(),
                "extension values must be edge shares in (0,1)")
    ok &= check((ext["n_firms"] >= 15).all(),
                f"extension n_firms >= 15 (got min {ext['n_firms'].min()})")
    ok &= check((ext["n_possible"]
                 == ext["n_firms"] * (ext["n_firms"] - 1)).all(),
                "n_possible must equal n_firms*(n_firms-1)")
    first12_q = [f"{y}Q{q}" for y in range(1993, 1996) for q in range(1, 5)]
    first12 = new.filter(pl.col("quarter").is_in(first12_q))["value"]
    lo, hi = 0.5 * first12.mean(), 3.0 * first12.mean()
    bad = ext.filter(~pl.col("value").is_between(lo, hi, closed="both"))
    ok &= check(bad.height == 0,
                f"extension values outside [{lo:.3f}, {hi:.3f}] "
                f"(consistent with first 12 committed quarters) at "
                f"{bad['quarter'].to_list()}")

    # panel carries the published series 1:1
    panel = pl.read_csv(COMPLETE).select(["quarter", COL]).sort("quarter")
    pm = dict(zip(new["quarter"].to_list(), new["value"].to_list()))
    j2 = panel.join(new.select(["quarter", "value"]), on="quarter", how="inner")
    ok &= check(j2.height == 146 and panel[COL].null_count() == 0,
                "panel_complete Granger column must be complete (146, no nulls)")
    bad = j2.filter(pl.col(COL) != pl.col("value"))
    ok &= check(bad.height == 0,
                f"panel_complete != systemic granger at "
                f"{bad['quarter'].head(3).to_list()}")

    print("test_granger_1990s: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
