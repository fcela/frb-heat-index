"""Regression tests for the two de-duplicated FRB panel proxies.

Background: the panel had 46 columns but only 44 unique series --
'Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages
by riskier borrowers)' was an exact duplicate of the very-rapid-mortgage
series (both YoY % change of household mortgage debt), and
'Indicators of Volitility (Credit Default Swap premium)' was an exact
duplicate of the BBB spread (both Moody's Baa - DGS10).

Distinct public replacements:
- Piggy-back incidence -> closed-end junior-lien (Z.1 home equity loan)
  share of household mortgage debt (FRED BOGZ1FL893065125Q / HHMSDODNS).
- CDS premium -> expected-default component of the Baa spread:
  (BAA - DGS10) - Fed excess bond premium (Gilchrist-Zakrajsek
  decomposition; the Fed's EBP file is work/raw/ebp_raw.csv).

These tests also pin a latent unit bug fixed alongside: FRED HHMSDODNS is
$ millions, but the mortgage-ratio builders assumed $B. Without the /1000
conversion the mortgage-debt/GDP and mortgage-debt/DPI ratios rebuild
1000x too large.

Runnable with plain python3 (no pytest needed):
    python3 tests/test_distinct_proxies.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
import build_panel as bp  # noqa: E402

RAW = REPO / "work" / "raw"

PIGGY = "Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages by riskier borrowers)"
RAPID = "Mortgage Borrowing by Riskier Borrowers (Incidence of very rapid mortgage borrowing by riskier borrowers)"
CDS = "Indicators of Volitility (Credit Default Swap premium)"
BBB = "Spread of Corporate Bonds (over Treasury Securities) (BBB)"
MD_GDP = "Home Mortage Debt (Ratio of total mortgage debt to GDP)"
MD_DPI = "Home Mortage Debt (Ratio of mortgage debt owed by riskier borrowers to DPI)"


def _frame(out, name):
    return out[name].rename({"value": "v"})


def test_piggy_back_is_distinct_from_rapid_mortgage_growth():
    out = bp.b_mortgage(RAW)
    p = _frame(out, PIGGY)
    r = _frame(out, RAPID)
    j = p.join(r, on="quarter", suffix="_r")
    assert not bool((j["v"] == j["v_r"]).all()), "piggy-back still duplicates rapid mortgage growth"
    corr = j.select(pl.corr("v", "v_r")).item()
    assert corr < 0.9, f"piggy-back suspiciously similar to rapid growth (corr {corr})"


def test_piggy_back_is_junior_lien_share_with_boom_bust_shape():
    out = bp.b_mortgage(RAW)
    p = _frame(out, PIGGY)
    assert p.height >= 140, f"piggy-back too short: {p.height}"
    assert p["v"].null_count() == 0
    assert bool((p["v"] > 0).all()), "junior-lien share must be positive"
    assert bool((p["v"] < 50).all()), "junior-lien share implausibly large"
    boom = p.filter(pl.col("quarter") == "2006Q4")["v"][0]
    bust = p.filter(pl.col("quarter") == "2025Q4")["v"][0]
    assert boom > bust, f"no boom-bust shape: 2006Q4={boom}, 2025Q4={bust}"
    assert 9.0 < boom < 13.0, f"2006Q4 share {boom} outside expected 9-13%"


def test_mortgage_ratios_reproduce_panel_units():
    # Guards the $M->$B conversion: FRED HHMSDODNS is $M; the panel's
    # long-standing convention is $B-based ratios.
    out = bp.b_mortgage(RAW)
    g = _frame(out, MD_GDP).filter(pl.col("quarter") == "1990Q4")["v"][0]
    d = _frame(out, MD_DPI).filter(pl.col("quarter") == "1990Q4")["v"][0]
    assert abs(g - 0.4146) < 0.01, f"mortgage-debt/GDP {g} != panel 0.4146 (unit bug?)"
    assert abs(d - 56.84) < 0.5, f"mortgage-debt/DPI {d} != panel 56.84 (unit bug?)"


def test_cds_premium_is_distinct_from_bbb_spread():
    out = bp.b_baa_spread(RAW)
    c = _frame(out, CDS)
    b = _frame(out, BBB)
    j = c.join(b, on="quarter", suffix="_r")
    assert not bool((j["v"] == j["v_r"]).all()), "CDS premium still duplicates BBB spread"
    corr = j.select(pl.corr("v", "v_r")).item()
    assert corr < 0.9, f"CDS premium suspiciously similar to BBB spread (corr {corr})"


def test_cds_premium_is_positive_and_crisis_shaped():
    # The pipeline applies a log transform to this series: it must be
    # strictly positive. Crisis shape: 2008-2009 elevated vs mid-2000s.
    out = bp.b_baa_spread(RAW)
    c = _frame(out, CDS)
    assert c["v"].null_count() == 0
    assert bool((c["v"] > 0).all()), "CDS proxy not strictly positive -- log transform would break"
    calm = c.filter(pl.col("quarter") == "2006Q4")["v"][0]
    crisis = c.filter(pl.col("quarter") == "2008Q4")["v"][0]
    assert crisis > calm, f"no crisis elevation: 2006Q4={calm}, 2008Q4={crisis}"
    assert crisis > 2.0, f"2008Q4 CDS proxy {crisis} too low vs published CDX.IG ~200bp+"


def main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    need = [REPO / "work" / "raw" / "ebp_raw.csv",
            REPO / "work" / "raw" / "fred_BOGZ1FL893065125Q.csv"]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        print(f"SKIP: raw inputs missing: {missing}")
        return 2
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
