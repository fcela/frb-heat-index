"""Regression tests locking in revenue values that look like errors but were
verified against the primary SEC filings (2026-10-24 audit).

Run: python3 tests/test_revenue_verified.py (no pytest needed).

Each test cites the filing that confirms the value, so a future re-extraction
that silently changes one of these cells fails here instead of shipping.
All values in $M. No value in this file was changed by the audit: every
flagged cell below checked out against its filing, so the bundle is
untouched and these tests record why the suspicious-looking values stay.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import polars as pl

REPO = Path(__file__).resolve().parents[1]


def _annual(firm, line):
    panel = pl.read_csv(REPO / "data" / "revenue" / "panel_annual.csv")
    s = (panel.filter((pl.col("firm") == firm)
                      & (pl.col("taxonomy_line") == line))
         .sort("year"))
    return dict(zip(s["year"].to_list(), s["net_revenue_m"].to_list()))


def _quarterly(firm, line):
    panel = pl.read_csv(REPO / "data" / "revenue" / "panel_quarterly.csv")
    s = (panel.filter((pl.col("firm") == firm)
                      & (pl.col("taxonomy_line") == line))
         .sort(["year", "quarter"]))
    return {(y, q): v for y, q, v in
            zip(s["year"].to_list(), s["quarter"].to_list(),
                s["net_revenue_m"].to_list())}


def test_jefferies_awm_2023_verified():
    # Flagged: $188.3M between $1,243.5M (2022) and $803.7M (2024), no regime
    # break. Confirmed in TWO vintages of Jefferies' own segment note:
    #   FY2023 10-K, accession 0000096223-24-000006, R167.htm:
    #     Asset Management net revenues 2023/2022/2021 =
    #     $188,300 / $1,243,500 / $1,084,800 (thousands).
    #   FY2025 10-K, accession 0000096223-26-000009, R158.htm:
    #     Asset Management net revenues 2025/2024/2023 =
    #     $710,200 / $803,700 / $188,300 (thousands).
    # FY2023 was a loss year for the segment (pre-tax loss $162.7M; equity-
    # method investee losses of $205.2M in the same note), which is why the
    # collapse is real, not an extraction error.
    got = _annual("Jefferies", "awm")
    assert got[2023] == 188.3, got
    assert got[2022] == 1243.5 and got[2024] == 803.7, got
    print("test_jefferies_awm_2023_verified: OK")


def test_jpm_awm_investments_2012_verified():
    # Flagged: -$1,091M between $4,203M (2011) and $1,254M (2013), with a
    # regime break r0 -> r1 in 2012. Confirmed in JPMorgan's FY2013 10-K,
    # accession 0000019617-14-000289, R216.htm, "Business Segments":
    #   Corporate/Private Equity total net revenue 2013/2012/2011 =
    #   $1,254M / $(1,091)M / $4,203M.
    # The segment's pre-tax loss was $5,613M in 2012 (noninterest revenue
    # collapsed to $190M from $3,621M); the negative is Corporate's loss
    # year, not a mapping error. The taxonomy maps Corporate/Private
    # Equity to awm_investments ("private equity investments", quality A).
    got = _annual("JPMorgan", "awm_investments")
    assert got[2012] == -1091.0, got
    assert got[2011] == 4203.0 and got[2013] == 1254.0, got
    print("test_jpm_awm_investments_2012_verified: OK")


def test_gs_awm_investments_quarters_verified():
    # Flagged: three quarterly collapses in Investing and Lending net
    # revenues, all D-quality 10-Q extractions. Each confirmed in the
    # 10-Q segment note of the following year's 10-Q (comparative column):
    #   2011Q3 -$2,479M: Q3 2012 10-Q, accession 0001193125-12-458820,
    #     R108.htm (3M Sep 30, 2011 column; 9M 2011 = $1,270M, and
    #     2705 + 1044 - 2479 = 1270, so the quarter is arithmetically
    #     consistent with the filed 9-month total; pre-tax loss $2,565M).
    #   2012Q2 $203M: Q2 2013 10-Q, accession 0001193125-13-324664,
    #     R107.htm (3M Jun 30, 2012 column; 6M 2012 = $2,114M = 1911 + 203).
    #   2016Q1 $87M: Q1 2017 10-Q, accession 0001193125-17-156659,
    #     R167.htm (3M Mar 31, 2016 column; pre-tax loss $356M).
    got = _quarterly("Goldman Sachs", "awm_investments")
    assert got[(2011, 3)] == -2479.0, got
    assert got[(2012, 2)] == 203.0, got
    assert got[(2016, 1)] == 87.0, got
    print("test_gs_awm_investments_quarters_verified: OK")


def test_jpm_advisory_2008_verified():
    # Flagged: $2,008M, D-quality MD&A extraction, value exactly equal to
    # the calendar year. Confirmed in JPMorgan's 2010 10-K, accession
    # 0000950123-11-019773, IB segment "Investment banking fees" table
    # (year ended Dec 31; columns 2010/2009/2008):
    #   Advisory $1,469M / $1,867M / $2,008M.
    # The value-year coincidence is real: the filing's own table prints
    # $2,008M for 2008. The bundle's neighbors match the same table.
    got = _annual("JPMorgan", "advisory")
    assert got[2008] == 2008.0, got
    assert got[2009] == 1867.0 and got[2010] == 1469.0, got
    print("test_jpm_advisory_2008_verified: OK")


def main():
    test_jefferies_awm_2023_verified()
    test_jpm_awm_investments_2012_verified()
    test_gs_awm_investments_quarters_verified()
    test_jpm_advisory_2008_verified()
    print("test_revenue_verified: all 4 tests passed")


if __name__ == "__main__":
    main()
