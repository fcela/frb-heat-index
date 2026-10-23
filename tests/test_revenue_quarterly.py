"""Tests for the quarterly revenue layer (parse_xbrl_q, build_revenue_q).

Run: python3 tests/test_revenue_quarterly.py (no pytest needed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import polars as pl

from src.revenue.parse_xbrl_q import (
    _parse_duration,
    _parse_end_date,
    _period_columns,
    extract_tables,
    parse_filing_dir_q,
)
from src.revenue.build_revenue_q import (
    quarter_of,
    build_quarterly,
    reconcile_quarterly,
)
from src.revenue.reorg_registry import regime_for_quarter, is_break_q


def test_period_parsing():
    assert _parse_duration("3 Months Ended") == 3
    assert _parse_duration("9 months ended") == 9
    assert _parse_duration("Year Ended") is None
    assert _parse_end_date("Sep. 30, 2018") == "2018-09-30"
    assert _parse_end_date("Mar 31, 2026") == "2026-03-31"
    assert _parse_end_date("2026") is None
    assert quarter_of("2018-09-30") == (2018, 3)
    assert quarter_of("2026-03-31") == (2026, 1)
    assert quarter_of("2025-11-30") == (2025, 4)  # Jefferies FYE Nov
    print("test_period_parsing: OK")


def test_quarter_regimes():
    # JPM 2024-04-01 reorg: Q1 2024 old regime, Q2 2024 new.
    assert regime_for_quarter("JPMorgan", 2024, 1) == "r1"
    assert regime_for_quarter("JPMorgan", 2024, 2) == "r2"
    assert is_break_q("JPMorgan", 2024, 1, 2024, 2)
    assert not is_break_q("JPMorgan", 2024, 2, 2024, 3)
    # Citi 2023-10-01 reorg: Q3 2023 old, Q4 2023 new.
    assert is_break_q("Citigroup", 2023, 3, 2023, 4)
    assert not is_break_q("Citigroup", 2023, 4, 2024, 1)
    # No-reorg firm: all quarters one regime.
    assert regime_for_quarter("Lazard", 2010, 1) == \
        regime_for_quarter("Lazard", 2025, 4) == "r0"
    print("test_quarter_regimes: OK")


def _seg_q(rows):
    """Build a segments_quarterly-style frame from tuples."""
    return pl.DataFrame(
        [{"firm": f, "cik": c, "segment_native": s,
          "duration_months": d, "period_end": p, "net_revenue_m": v,
          "accession": a, "r_file": "R1.htm",
          "source": "XBRL R-file (10-Q)"}
         for f, c, s, d, p, v, a in rows],
        schema={"firm": pl.String, "cik": pl.String,
                "segment_native": pl.String, "duration_months": pl.Int64,
                "period_end": pl.String, "net_revenue_m": pl.Float64,
                "accession": pl.String, "r_file": pl.String,
                "source": pl.String})


def _seg_a(rows):
    return pl.DataFrame(
        [{"firm": f, "cik": c, "segment_native": s, "year": y,
          "net_revenue_m": v, "accession": a, "r_file": "R1.htm",
          "source": "XBRL R-file"}
         for f, c, s, y, v, a in rows],
        schema={"firm": pl.String, "cik": pl.String,
                "segment_native": pl.String, "year": pl.Int64,
                "net_revenue_m": pl.Float64, "accession": pl.String,
                "r_file": pl.String, "source": pl.String})


def test_ytd_derivation():
    # Q1 direct, Q2 derived from 6M, Q3 derived from 9M, Q4 from annual.
    seg_q = _seg_q([
        ("Lazard", "0001311370", "Financial Advisory", 3, "2024-03-31",
         400.0, "a1"),
        ("Lazard", "0001311370", "Financial Advisory", 3, "2024-06-30",
         450.0, "a2"),
        ("Lazard", "0001311370", "Financial Advisory", 6, "2024-06-30",
         850.0, "a2"),   # 6M includes Q1; QTD Q2 also present -> prefer QTD
        ("Lazard", "0001311370", "Financial Advisory", 9, "2024-09-30",
         1300.0, "a3"),  # no 3M Q3 -> derive 1300-850 = 450
    ])
    seg_a = _seg_a([
        ("Lazard", "0001311370", "Financial Advisory", 2024, 1800.0, "a4"),
    ])
    panel, unmapped, dlog = build_quarterly(seg_q, seg_a, with_mda=False)
    # Financial Advisory -> advisory (D).
    adv = panel.filter(pl.col("taxonomy_line") == "advisory").sort("quarter")
    assert len(adv) == 4, adv
    vals = dict(zip(adv["quarter"].to_list(), adv["net_revenue_m"].to_list()))
    assert vals[1] == 400.0 and vals[2] == 450.0, vals
    assert vals[3] == 450.0, vals  # 1300 - 850
    assert vals[4] == 500.0, vals  # 1800 - 1300
    der = dict(zip(adv["quarter"].to_list(), adv["derivation"].to_list()))
    assert der[1] == "qtd" and der[2] == "qtd"
    assert der[3] == "ytd_derived_q3" and der[4] == "annual_derived_q4"
    assert len(unmapped) == 0
    print("test_ytd_derivation: OK")


def test_derivation_validation_flags():
    # Derived quarter exceeding the YTD magnitude -> logged, not emitted.
    seg_q = _seg_q([
        ("Lazard", "0001311370", "Financial Advisory", 3, "2024-03-31",
         400.0, "a1"),
        ("Lazard", "0001311370", "Financial Advisory", 9, "2024-09-30",
         100.0, "a3"),   # 9M < Q1 -> derived Q2+Q3 = -300, magnitude fail
        ("Lazard", "0001311370", "Financial Advisory", 6, "2024-06-30",
         850.0, "a2"),
    ])
    seg_a = _seg_a([])
    panel, _, dlog = build_quarterly(seg_q, seg_a, with_mda=False)
    adv = panel.filter(pl.col("taxonomy_line") == "advisory")
    # Q3 derived = 100 - 850 = -750, |−750| > 100*1.1 -> flagged, no Q3 cell
    assert 3 not in adv["quarter"].to_list(), adv
    fails = [e for e in dlog if e.get("status") == "failed_magnitude"]
    assert len(fails) == 1 and fails[0]["quarter"] == 3, dlog
    print("test_derivation_validation_flags: OK")


def test_reconcile_quarterly():
    panel = pl.DataFrame([
        {"firm": "Lazard", "taxonomy_line": "advisory", "year": 2024,
         "quarter": q, "net_revenue_m": v}
        for q, v in [(1, 400.0), (2, 450.0), (3, 450.0), (4, 500.0)]
    ])
    annual = pl.DataFrame([
        {"firm": "Lazard", "taxonomy_line": "advisory", "year": 2024,
         "net_revenue_m": 1800.0},
        {"firm": "Lazard", "taxonomy_line": "advisory", "year": 2023,
         "net_revenue_m": 1700.0},
    ])
    rep = reconcile_quarterly(panel, annual)
    assert rep["n_checks"] == 1 and rep["n_pass"] == 1
    assert rep["match_rate"] == 1.0
    # Flag case: quarters sum far from the annual.
    panel2 = panel.with_columns(
        pl.when(pl.col("quarter") == 4).then(99999.0)
        .otherwise(pl.col("net_revenue_m")).alias("net_revenue_m"))
    rep2 = reconcile_quarterly(panel2, annual)
    assert rep2["n_flag"] == 1 and rep2["match_rate"] == 0.0
    print("test_reconcile_quarterly: OK")


def test_period_column_date_alignment():
    # Date row shorter than duration row (missing label cell): align right.
    # Regression test for JPM 2011-Q2 10-Q where 6M was read as 3M.
    from src.revenue.parse_xbrl_q import _period_columns
    table = [
        [("Business Segments", None), ("3 Months Ended", None),
         ("3 Months Ended", None), ("6 Months Ended", None),
         ("6 Months Ended", None)],
        [("Jun. 30, 2011", None), ("Jun. 30, 2010", None),
         ("Jun. 30, 2011", None), ("Jun. 30, 2010", None)],
        [("Noninterest revenue", None), ("$ 14,943", None),
         ("$ 12,414", None), ("$ 28,259", None), ("$ 26,375", None)],
    ]
    cols = _period_columns(table)
    # (column_index, duration, period_end); label column dropped.
    assert cols[0] == (1, 3, "2011-06-30"), cols
    assert cols[1] == (2, 3, "2010-06-30"), cols
    assert cols[2] == (3, 6, "2011-06-30"), cols
    assert cols[3] == (4, 6, "2010-06-30"), cols
    print("test_period_column_date_alignment: OK")


def test_period_column_point_in_time_skipped():
    # A date cell with no duration (point-in-time balance-sheet column)
    # must not inherit the previous duration via forward-fill.
    # Regression: Citi 2025-Q3 10-Q R65 labeled Dec-31-2024 assets as
    # (9, 2024-12-31), shifting 9M values into wrong periods.
    from src.revenue.parse_xbrl_q import _period_columns
    table = [
        [("REPORTABLE BUSINESS SEGMENTS", None),
         ("REPORTABLE BUSINESS SEGMENTS", None),
         ("3 Months Ended", None), ("3 Months Ended", None),
         ("9 Months Ended", None), ("9 Months Ended", None), ("", None)],
        [("Sep. 30, 2025", None), ("Sep. 30, 2024", None),
         ("Sep. 30, 2025", None), ("Sep. 30, 2024", None),
         ("Dec. 31, 2024", None)],
        [("Total revenues", None), ("$ 2,132", None), ("$ 1,597", None),
         ("$ 6,005", None), ("$ 4,960", None), ("$ 1,182,000", None)],
    ]
    cols = _period_columns(table)
    assert cols == [(2, 3, "2025-09-30"), (3, 3, "2024-09-30"),
                    (4, 9, "2025-09-30"), (5, 9, "2024-09-30")], cols
    print("test_period_column_point_in_time_skipped: OK")


def test_segment_rename_canonicalization():
    from src.revenue.reorg_registry import canonical_segment
    assert canonical_segment(
        "JPMorgan", "Asset Management") == "Asset & Wealth Management"
    assert canonical_segment(
        "JPMorgan", "Asset & Wealth Management") == \
        "Asset & Wealth Management"
    # Other firms unaffected.
    assert canonical_segment("Lazard", "Asset Management") == \
        "Asset Management"
    print("test_segment_rename_canonicalization: OK")


def test_hl_fiscal_q3_qtd_q4():
    # Houlihan Lokey (fiscal year ends Mar 31) files a 10-Q for its fiscal
    # Q3, quarter ended Dec 31 = calendar Q4. The 3M value must be emitted
    # directly as Q4, not dropped. Regression: HL was absent from every Q4,
    # injecting a periodic composition change into quarterly HHI.
    # Values pinned against HL's 2024-Q3 10-Q (0001302215-25-000007).
    seg_q = _seg_q([
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 3,
         "2024-06-30", 328.417, "q1"),
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 3,
         "2024-09-30", 364.028, "q2"),
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 9,
         "2024-09-30", 1114.047, "q2"),
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 3,
         "2024-12-31", 421.602, "q3"),
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 9,
         "2024-12-31", 1114.047, "q3"),
    ])
    seg_a = _seg_a([
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 2024,
         1900.0, "k1"),
    ])
    panel, _, _ = build_quarterly(seg_q, seg_a, with_mda=False)
    adv = panel.filter(pl.col("taxonomy_line") == "advisory").sort("quarter")
    vals = dict(zip(adv["quarter"].to_list(), adv["net_revenue_m"].to_list()))
    der = dict(zip(adv["quarter"].to_list(), adv["derivation"].to_list()))
    assert vals[4] == 421.602, vals
    assert der[4] == "qtd", der
    print("test_hl_fiscal_q3_qtd_q4: OK")


def test_hl_no_annual_derived_q4():
    # HL's "annual" is a fiscal year (Apr-Mar); annual - calendar-9M is
    # meaningless and must not be emitted as Q4.
    seg_q = _seg_q([
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 9,
         "2024-09-30", 1114.047, "q2"),
    ])
    seg_a = _seg_a([
        ("Houlihan Lokey", "0001302215", "Corporate Finance", 2024,
         1900.0, "k1"),
    ])
    panel, _, _ = build_quarterly(seg_q, seg_a, with_mda=False)
    adv = panel.filter(pl.col("taxonomy_line") == "advisory")
    assert 4 not in adv["quarter"].to_list(), adv
    print("test_hl_no_annual_derived_q4: OK")


def test_wfc_q_segment_note_beats_606_note():
    # Wells Fargo's 10-Qs carry both an ASC 606 note (R136: "Revenue from
    # Contracts with Customers, Revenue by Operating Segment (Details)")
    # and the true segment note (R143: "Operating Segments (Details)").
    # The 606 note's WIM block has "Net interest income" but no bare
    # "noninterest income" row, so the sum path collapsed to NII-only
    # (~$1.1B/quarter) and, on report order, beat the segment note's
    # full NII + noninterest income (~$4.2B/quarter) -- the quarterly
    # face of the 2017 = $4,641M annual hole. Pinned against the
    # Q1-2018 10-Q (0000072971-18-000324) and its Q1-2017 comparative.
    fix = Path(__file__).resolve().parent / "fixtures" \
        / "wfc-0000072971-18-000324"
    rows = parse_filing_dir_q("Wells Fargo", "0000072971",
                              "0000072971-18-000324", fix)
    wim = [r for r in rows
           if r["segment_native"] == "Wealth and Investment Management"]
    got = {(r["duration_months"], r["period_end"]): r["net_revenue_m"]
           for r in wim}
    assert got[(3, "2017-03-31")] == 4257.0, got
    assert got[(3, "2018-03-31")] == 4242.0, got
    # ...from the segment note, never the 606 note's NII-only collapse.
    assert {r["r_file"] for r in wim} == {"R143.htm"}, wim
    assert 1141.0 not in got.values() and 1112.0 not in got.values(), got
    print("test_wfc_q_segment_note_beats_606_note: OK")


def test_mda_annual_derived_q4():
    # Division-reporters get Q4 = annual MD&A sub-split less Q1-Q3.
    # Without this they vanish from every Q4 (periodic HHI artifact).
    import tempfile, os
    from src.revenue import build_revenue_q as bq
    orig_data = bq.DATA
    with tempfile.TemporaryDirectory() as td:
        bq.DATA = Path(td)
        pl.DataFrame([
            {"firm": "JPM", "cik": "0000019617", "taxonomy_line": "ficc",
             "year": 2024, "quarter": 1, "net_revenue_m": 1000.0,
             "source": "s", "accession": "a1"},
            {"firm": "JPM", "cik": "0000019617", "taxonomy_line": "ficc",
             "year": 2024, "quarter": 2, "net_revenue_m": 1100.0,
             "source": "s", "accession": "a2"},
            {"firm": "JPM", "cik": "0000019617", "taxonomy_line": "ficc",
             "year": 2024, "quarter": 3, "net_revenue_m": 1200.0,
             "source": "s", "accession": "a3"},
        ]).write_csv(Path(td) / "subsplits_quarterly.csv")
        pl.DataFrame([
            {"firm": "JPM", "cik": "0000019617", "taxonomy_line": "ficc",
             "year": 2024, "net_revenue_m": 4000.0,
             "source": "s", "accession": "a0"},
        ]).write_csv(Path(td) / "subsplits_annual.csv")
        seg_q = _seg_q([])
        seg_a = _seg_a([])
        panel, _, _ = build_quarterly(seg_q, seg_a, with_mda=True)
        bq.DATA = orig_data
    ficc = panel.filter(pl.col("taxonomy_line") == "ficc",
                        pl.col("year") == 2024).sort("quarter")
    vals = dict(zip(ficc["quarter"].to_list(), ficc["net_revenue_m"].to_list()))
    assert vals[4] == 700.0, vals  # 4000 - (1000+1100+1200)
    der = dict(zip(ficc["quarter"].to_list(), ficc["derivation"].to_list()))
    assert der[4] == "mda_annual_derived_q4", der
    print("test_mda_annual_derived_q4: OK")


# Mirrors Citi's 2026-Q2 10-Q (0000831001-26-000045) Banking section:
# the fee split was renamed "Investment banking fees" -> "Investment
# banking revenues" (footnote: adds other fee revenue, principal
# transactions and loan NII; the prior-year comparative moved <1%,
# e.g. advisory fees $408M vs restated $407M), and the QTD period
# headers moved into the preceding summary table while the detail
# table carries only row labels and values.
_CITI_NEW_FORMAT_HTML = """
<html><body>
<table>
  <tr><td>In millions of dollars, except as otherwise noted</td>
      <td colspan="3">Second Quarter</td>
      <td colspan="3">Six Months</td></tr>
  <tr><td></td><td>2026</td><td>2025</td><td>% Change</td>
      <td>2026</td><td>2025</td><td>% Change</td></tr>
  <tr><td>Net interest income (including dividends)</td>
      <td>$</td><td>560</td><td></td><td>$</td><td>530</td><td></td>
      <td>6</td><td>%</td><td>$</td><td>1,147</td><td></td>
      <td>$</td><td>1,021</td><td></td><td>12</td><td>%</td></tr>
</table>
<table>
  <tr><td>Revenue by line of business</td></tr>
  <tr><td>Investment banking revenues (2)</td><td></td><td></td>
      <td></td><td></td><td></td><td></td><td></td><td></td></tr>
  <tr><td>Advisory</td>
      <td>$</td><td>390</td><td></td><td>$</td><td>407</td><td></td>
      <td>(4)</td><td>%</td><td>$</td><td>895</td><td></td>
      <td>$</td><td>833</td><td></td><td>7</td><td>%</td></tr>
  <tr><td>Equity underwriting (ECM)</td>
      <td>426</td><td></td><td>222</td><td></td><td>92</td><td></td>
      <td>683</td><td></td><td>357</td><td></td><td>91</td><td></td></tr>
  <tr><td>Debt underwriting (DCM)</td>
      <td>732</td><td></td><td>444</td><td></td><td>65</td><td></td>
      <td>1,296</td><td></td><td>997</td><td></td><td>30</td><td></td></tr>
</table>
</body></html>
"""


def test_citi_new_format_10q_ib_revenues():
    # Regression: on the renamed-metric 10-Qs the spec'd table was
    # skipped entirely, so the XBRL "Banking" division total (incl.
    # corporate lending) leaked in as the only advisory source
    # (Citi advisory TTM $8,031M). Values pinned against the filing.
    import tempfile
    from bs4 import BeautifulSoup
    from src.revenue.parse_mda import YearColumnError
    from src.revenue.parse_mda_q import (
        detect_qtd_periods, parse_filing_q, preceding_qtd_periods,
    )
    soup = BeautifulSoup(_CITI_NEW_FORMAT_HTML, "lxml")
    summary, detail = soup.find_all("table")
    # Detail table has no QTD header of its own; it must inherit the
    # periods of the preceding summary table.
    assert detect_qtd_periods(summary) == [(2026, 2), (2025, 2)]
    try:
        detect_qtd_periods(detail)
        raise AssertionError("detail table should lack a QTD header")
    except YearColumnError:
        pass
    assert preceding_qtd_periods(detail) == [(2026, 2), (2025, 2)]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c-20260630.htm"
        p.write_text(_CITI_NEW_FORMAT_HTML)
        recs = parse_filing_q("C", "0000831001",
                              "0000831001-26-000045-fixture", p)
    got = {(r["taxonomy_line"], r["year"], r["quarter"]):
           r["net_revenue_m"] for r in recs}
    assert got == {
        ("advisory", 2026, 2): 390.0, ("advisory", 2025, 2): 407.0,
        ("ecm", 2026, 2): 426.0, ("ecm", 2025, 2): 222.0,
        ("dcm", 2026, 2): 732.0, ("dcm", 2025, 2): 444.0,
    }, got
    assert all(r["source"].startswith("mda_q:") for r in recs)
    print("test_citi_new_format_10q_ib_revenues: OK")


def test_citi_banking_division_not_advisory_in_panel():
    # The XBRL "Banking" division total mixes IB fees with corporate
    # lending and Services/Markets revenue share (PR #35 taxonomy), so
    # it must stay unmapped; the MD&A fee split (quality D) is the only
    # advisory source. Before the rebuild the stale bundle mapped the
    # division total to advisory (~$2B per quarter).
    import tempfile
    from src.revenue import build_revenue_q as bq
    seg_q = _seg_q([
        ("C", "0000831001", "Banking", 3, "2025-09-30", 2132.0, "a1"),
        ("C", "0000831001", "Markets", 3, "2025-09-30", 4706.0, "a1"),
    ])
    seg_a = _seg_a([])
    orig_data = bq.DATA
    with tempfile.TemporaryDirectory() as td:
        bq.DATA = Path(td)
        pl.DataFrame([
            {"firm": "C", "cik": "0000831001",
             "taxonomy_line": "advisory", "year": 2025, "quarter": 3,
             "net_revenue_m": 427.0,
             "source": "mda_q:Banking business metrics - investment "
                       "banking fees", "accession": "a1"},
        ]).write_csv(Path(td) / "subsplits_quarterly.csv")
        panel, unmapped, _ = build_quarterly(seg_q, seg_a, with_mda=True)
        bq.DATA = orig_data
    citi_adv = panel.filter((pl.col("firm") == "Citigroup")
                            & (pl.col("taxonomy_line") == "advisory"))
    assert len(citi_adv) == 1, citi_adv
    r = citi_adv.to_dicts()[0]
    assert r["net_revenue_m"] == 427.0, r
    assert r["map_quality"] == "D" and r["layer"] == "mda_q", r
    assert any(u.get("segment_native") == "Banking" for u in unmapped), \
        unmapped
    print("test_citi_banking_division_not_advisory_in_panel: OK")


# ---------------------------------------------------------------------------
# Jefferies quarterly advisory. The XBRL layer reports only the IB&CM
# division total (advisory + underwriting + equities + fixed income), which
# the stale bundle mapped to advisory -- quarterly advisory TTM $12,480M.
# The advisory fee line is scraped from the 10-Q revenues-by-source
# tables instead (quality D). Three real header layouts are pinned below
# with values read off the filings.
# ---------------------------------------------------------------------------

# Jefferies FY2025-Q1 10-Q (0001628280-25-017192): month split into the row
# below the loose "Three Months Ended" header, and percent columns merged
# into the value cells ("25.0 %") with no "$"/empty separator cells.
_JEF_Q1_2025_HTML = """
<html><body>
<p>Jefferies Group LLC results of operations follow.</p>
<table>
  <tr><td></td><td>Three Months Ended</td><td></td></tr>
  <tr><td></td><td>February 28, 2025</td><td>February 29, 2024</td></tr>
  <tr><td>$ in thousands</td><td>Amount</td><td>% of Net Revenues</td>
      <td>Amount</td><td>% of Net Revenues</td><td>% Change</td></tr>
  <tr><td>Advisory ............................</td>
      <td>$ 397,780</td><td>25.0 %</td>
      <td>$ 338,567</td><td>19.5 %</td><td>17.5 %</td></tr>
  <tr><td>Equity underwriting ..........</td>
      <td>128,520</td><td>8.1</td>
      <td>209,303</td><td>12.0</td><td>(38.6)</td></tr>
  <tr><td>Total investment banking .......</td>
      <td>$ 854,181</td><td>53.5 %</td>
      <td>$ 876,441</td><td>50.6 %</td><td>(2.5)</td></tr>
</table>
</body></html>
"""

# Jefferies FY2023-Q2 10-Q (0000096223-23-000026): month-bearing header,
# split percent cells ("24.5" + "%"), a Six Months group in the same header
# row (strict span), and a following nine-months twin table that must NOT
# inherit the QTD periods (YTD-declaration guard).
_JEF_Q2_2023_HTML = """
<html><body>
<p>The following provides a summary of Net Revenues by Source:</p>
<table>
  <tr><td colspan="3"></td><td colspan="21">Three Months Ended May 31,</td>
      <td colspan="3"></td><td colspan="21">Six Months Ended May 31,</td>
      <td colspan="3"></td></tr>
  <tr><td colspan="3"></td><td colspan="9">2023</td><td colspan="3"></td>
      <td colspan="9">2022</td><td colspan="3"></td>
      <td colspan="9">2023</td><td colspan="3"></td>
      <td colspan="9">2022</td><td colspan="3"></td></tr>
  <tr><td>$ in thousands</td><td>Amount</td><td></td>
      <td>% of Net Revenues</td><td></td><td>Amount</td><td></td>
      <td>% of Net Revenues</td><td></td><td>% Change</td><td></td>
      <td>Amount</td><td></td><td>% of Net Revenues</td><td></td>
      <td>Amount</td><td></td><td>% of Net Revenues</td><td></td>
      <td>% Change</td><td></td><td>Amount</td><td></td>
      <td>% of Net Revenues</td><td></td><td>Amount</td><td></td>
      <td>% of Net Revenues</td><td></td></tr>
  <tr><td>Advisory</td><td>$</td><td>254,157</td><td></td><td></td>
      <td>24.5</td><td>%</td><td></td><td>$</td><td>371,404</td><td></td>
      <td></td><td>22.9</td><td>%</td><td></td><td>$</td><td>509,813</td>
      <td></td><td></td><td>31.3</td><td>%</td><td></td><td>$</td>
      <td>688,890</td><td></td><td></td><td>42.1</td><td>%</td></tr>
  <tr><td>Total investment banking</td><td>$</td><td>509,813</td><td></td>
      <td></td><td>49.3</td><td>%</td><td></td><td>$</td><td>688,890</td>
      <td></td><td></td><td>42.1</td><td>%</td><td></td></tr>
</table>
<p>The following provides a summary of Net Revenues by Source for the
nine months then ended:</p>
<table>
  <tr><td colspan="3"></td>
      <td colspan="21">Nine Months Ended May 31,</td>
      <td colspan="3"></td></tr>
  <tr><td colspan="3"></td><td colspan="9">2023</td><td colspan="3"></td>
      <td colspan="9">2022</td><td colspan="3"></td></tr>
  <tr><td>$ in thousands</td><td>Amount</td><td></td>
      <td>% of Net Revenues</td><td></td><td>Amount</td><td></td>
      <td>% of Net Revenues</td><td></td><td>% Change</td><td></td></tr>
  <tr><td>Advisory</td><td>$</td><td>509,813</td><td></td><td></td>
      <td>31.3</td><td>%</td><td></td><td>$</td><td>688,890</td><td></td>
      <td></td><td>(26.0)</td><td>%</td><td></td></tr>
  <tr><td>Total investment banking</td><td>$</td><td>1,013,626</td>
      <td></td><td></td><td>62.3</td><td>%</td><td></td></tr>
</table>
</body></html>
"""

# Jefferies FY2021-Q1 10-Q (0000096223-21-000020): the "For the Three
# Months Ended" header cell's colspan covers only the current period, so
# the comparative period's cells sit OUTSIDE the span; with no other
# duration group declared, the whole period row is scanned.
_JEF_Q1_2021_HTML = """
<html><body>
<p>The following table sets forth net revenues by source (in thousands):</p>
<table>
  <tr><td colspan="3"></td><td colspan="3"></td><td colspan="3"></td>
      <td colspan="9">For the Three Months Ended</td></tr>
  <tr><td colspan="3"></td><td colspan="3"></td><td colspan="3"></td>
      <td colspan="3"></td><td colspan="3">February 28, 2021</td>
      <td colspan="3"></td><td colspan="3">February 29, 2020</td></tr>
  <tr><td>Advisory</td><td></td><td></td><td></td><td></td>
      <td>$</td><td>311,439</td><td></td><td></td><td></td>
      <td>$</td><td>343,158</td><td></td><td></td></tr>
  <tr><td>Equity underwriting</td><td></td><td></td><td></td><td></td>
      <td>494,806</td><td></td><td></td><td></td>
      <td>131,692</td><td></td><td></td><td></td></tr>
  <tr><td>Total investment banking</td><td></td><td></td><td></td><td></td>
      <td>$</td><td>1,003,662</td><td></td><td></td><td></td>
      <td>$</td><td>592,002</td><td></td><td></td></tr>
</table>
</body></html>
"""


def _parse_jef_fixture(html: str, accession: str):
    import tempfile
    from bs4 import BeautifulSoup
    from src.revenue.parse_mda_q import parse_filing_q
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "jef-10q.htm"
        p.write_text(html)
        recs = parse_filing_q("JEF", "0000096223", accession, p)
    return {(r["year"], r["quarter"]): r["net_revenue_m"] for r in recs}


def test_jefferies_qtd_merged_percent_cells():
    # FY2025+ layout: percent columns merged into single cells, no
    # "$"/empty separators -- the whole value row is one slot, so the
    # money cells must be kept and the percent cells dropped, or the
    # comparative period would lose its value. Values pinned against
    # 0001628280-25-017192.
    got = _parse_jef_fixture(_JEF_Q1_2025_HTML,
                            "0001628280-25-017192-fixture")
    assert got == {(2025, 1): 397.78, (2024, 1): 338.567}, got
    print("test_jefferies_qtd_merged_percent_cells: OK")


def test_jefferies_qtd_split_percent_and_ytd_guard():
    # FY2023 layout: split "$"/value/percent cells with a Six Months
    # group in the header row (strict colspan span), followed by a
    # nine-months twin table. Without the YTD-declaration guard the
    # twin table would inherit the QTD periods via the
    # preceding-table fallback and emit 9M values as QTD. Values
    # pinned against 0000096223-23-000026.
    got = _parse_jef_fixture(_JEF_Q2_2023_HTML,
                            "0000096223-23-000026-fixture")
    assert got == {(2023, 2): 254.157, (2022, 2): 371.404}, got
    print("test_jefferies_qtd_split_percent_and_ytd_guard: OK")


def test_jefferies_qtd_broken_colspan_header():
    # FY2021 layout: the QTD header's colspan covers only the current
    # period, excluding the comparative period's cells; no other
    # duration group is declared, so the whole period row is scanned.
    # Values pinned against 0000096223-21-000020.
    got = _parse_jef_fixture(_JEF_Q1_2021_HTML,
                            "0000096223-21-000020-fixture")
    assert got == {(2021, 1): 311.439, (2020, 1): 343.158}, got
    print("test_jefferies_qtd_broken_colspan_header: OK")


def test_jefferies_ibcm_not_advisory_in_panel():
    # The Jefferies XBRL layer's "Investment Banking And Capital
    # Markets" segment total (advisory + underwriting + equities +
    # fixed income, ~$1.4-1.7B/quarter) must stay unmapped; the
    # advisory fee line (quality D, ~$0.3-0.8B/quarter) is the only
    # quarterly advisory source. Before the fix the division total WAS
    # the advisory series (TTM $12,480M).
    import tempfile
    from src.revenue import build_revenue_q as bq
    seg_q = _seg_q([
        ("JEF", "0000096223", "Investment Banking And Capital Markets",
         3, "2026-08-31", 1687.411, "a1"),
        ("JEF", "0000096223", "Asset Management", 3, "2026-08-31",
         64.397, "a1"),
    ])
    seg_a = _seg_a([])
    orig_data = bq.DATA
    with tempfile.TemporaryDirectory() as td:
        bq.DATA = Path(td)
        pl.DataFrame([
            {"firm": "JEF", "cik": "0000096223",
             "taxonomy_line": "advisory", "year": 2026, "quarter": 3,
             "net_revenue_m": 817.823,
             "source": "mda_q:Jefferies net revenues by source - "
                       "advisory", "accession": "a1"},
        ]).write_csv(Path(td) / "subsplits_quarterly.csv")
        panel, unmapped, _ = build_quarterly(seg_q, seg_a, with_mda=True)
        bq.DATA = orig_data
    jef_adv = panel.filter((pl.col("firm") == "Jefferies")
                           & (pl.col("taxonomy_line") == "advisory"))
    assert len(jef_adv) == 1, jef_adv
    r = jef_adv.to_dicts()[0]
    assert r["net_revenue_m"] == 817.823, r
    assert r["map_quality"] == "D" and r["layer"] == "mda_q", r
    assert any("Investment Banking And Capital Markets"
               == u.get("segment_native") for u in unmapped), unmapped
    # Asset Management stays a legitimate (approximate) awm source.
    awm = panel.filter((pl.col("firm") == "Jefferies")
                       & (pl.col("taxonomy_line") == "awm"))
    assert len(awm) == 1 and awm["net_revenue_m"][0] == 64.397, awm
    print("test_jefferies_ibcm_not_advisory_in_panel: OK")


def test_jefferies_2018_transition_no_q4():
    # Jefferies' 2018 "annual" is the 11-month transition period
    # Jan 1 - Nov 30, 2018 (fiscal-year-end change), while its 2018
    # quarters are mixed calendar/fiscal periods: annual - Q1..Q3 is
    # not a quarter. The derivation must be skipped and logged, even
    # if all three quarters somehow exist.
    import tempfile
    from src.revenue import build_revenue_q as bq
    orig_data = bq.DATA
    with tempfile.TemporaryDirectory() as td:
        bq.DATA = Path(td)
        pl.DataFrame([
            {"firm": "JEF", "cik": "0000096223",
             "taxonomy_line": "advisory", "year": 2018, "quarter": q,
             "net_revenue_m": v, "source": "s", "accession": "a1"}
            for q, v in [(1, 100.0), (2, 216.982), (3, 187.591)]
        ]).write_csv(Path(td) / "subsplits_quarterly.csv")
        pl.DataFrame([
            {"firm": "JEF", "cik": "0000096223",
             "taxonomy_line": "advisory", "year": 2018,
             "net_revenue_m": 820.042, "source": "s", "accession": "a0"},
        ]).write_csv(Path(td) / "subsplits_annual.csv")
        seg_q = _seg_q([])
        seg_a = _seg_a([])
        panel, _, dlog = build_quarterly(seg_q, seg_a, with_mda=True)
        bq.DATA = orig_data
    jef_2018 = panel.filter((pl.col("firm") == "Jefferies")
                            & (pl.col("year") == 2018))
    assert 4 not in jef_2018["quarter"].to_list(), jef_2018
    skips = [e for e in dlog
             if e.get("status") == "skipped_transition_year"]
    assert len(skips) == 1 and skips[0]["year"] == 2018, dlog
    print("test_jefferies_2018_transition_no_q4: OK")


def test_jefferies_quarterly_advisory_sane():
    # Acceptance check on the committed quarterly panel: every JEF
    # advisory cell is the quality-D fee line (never the XBRL division
    # total), no quarter exceeds $1B, and the last-4-quarter TTM is
    # far below the pre-fix $12,480M. (Actuals: record FY2026 TTM
    # $2,653M, largest quarter $817.8M.)
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_quarterly.csv")
    jef = (panel.filter((pl.col("firm") == "Jefferies")
                        & (pl.col("taxonomy_line") == "advisory"))
           .sort(["year", "quarter"]))
    assert len(jef) >= 20, len(jef)
    assert (jef["map_quality"] == "D").all(), jef
    assert (jef["layer"] == "mda_q").all(), jef
    assert jef["net_revenue_m"].max() < 1000.0, jef.sort("net_revenue_m")
    ttm = jef["net_revenue_m"].tail(4).sum()
    assert 1000.0 < ttm < 4000.0, ttm
    # The 2018 transition year must have no derived Q4.
    assert 4 not in jef.filter(pl.col("year") == 2018)["quarter"].to_list()
    print(f"test_jefferies_quarterly_advisory_sane: OK (TTM ${ttm:.0f}M)")


def test_goldman_quarterly_current_and_sane():
    # Acceptance check on the committed quarterly panel: GS quarterly
    # advisory/FICC/equities extend through the latest quarter (the
    # pre-fix panel stopped at 2019Q1 -- the filing-agent instance
    # names of GS's 2019-2022 10-Qs defeated the filename-based 10-Q
    # detection), with no interior gaps 2020-2025, and every 2020+
    # cell sourced from the 10-Q MD&A (quality D), never a division
    # total. Annual sums are pinned against the 10-Ks: FY2022 10-K
    # restated comparatives for 2020, FY2025 10-K for 2025.
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_quarterly.csv")
    gs = (panel.filter(pl.col("firm") == "Goldman Sachs")
          .filter(pl.col("taxonomy_line").is_in(
              ["advisory", "ecm", "dcm", "ficc", "equities"])))
    assert gs.height > 100, gs.height
    for line in ("advisory", "ficc", "equities"):
        s = (gs.filter(pl.col("taxonomy_line") == line)
             .sort(["year", "quarter"]))
        recent = s.filter(pl.col("year") >= 2020)
        # No interior gaps: 4 quarters for 2020-2025.
        for y in range(2020, 2026):
            qs = (recent.filter(pl.col("year") == y)
                  ["quarter"].sort().to_list())
            assert qs == [1, 2, 3, 4], (line, y, qs)
        # Current: last observation at or after 2026Q1.
        last = recent.tail(1)
        assert last["year"][0] >= 2026, last
        # MD&A-sourced, never a division total (XBRL layer has no
        # post-2019 GS FICC/equities sub-lines).
        assert (recent["map_quality"] == "D").all(), recent
        assert (recent["layer"] == "mda_q").all(), recent
    sums = {}
    for r in gs.iter_rows(named=True):
        if r["year"] in (2020, 2025):
            sums.setdefault((r["taxonomy_line"], r["year"]), 0.0)
            sums[(r["taxonomy_line"], r["year"])] += r["net_revenue_m"]
    # FY2022 10-K restated 2020 comparatives (GS's 2022 GBM reorg
    # re-presented 2020-2021).
    assert sums[("advisory", 2020)] == 3064.0, sums
    assert sums[("ficc", 2020)] == 11453.0, sums
    assert sums[("equities", 2020)] == 9884.0, sums
    assert sums[("ecm", 2020)] == 3376.0, sums
    assert sums[("dcm", 2020)] == 2660.0, sums
    # FY2025 10-K.
    assert sums[("advisory", 2025)] == 4726.0, sums
    assert sums[("ficc", 2025)] == 14522.0, sums
    assert sums[("equities", 2025)] == 16535.0, sums
    assert sums[("ecm", 2025)] == 1784.0, sums
    assert sums[("dcm", 2025)] == 2829.0, sums
    # Within-year vintage consistency: 2022 quarterly path must be on
    # the GBM-restated basis (Q1 2022 FICC $4,730M as restated in the
    # Q1 2023 10-Q, not the originally-filed $4,723M; Q4 2022 then
    # lands on the 10-K's restated $2,687M rather than absorbing the
    # reclass residual).
    f22 = gs.filter((pl.col("taxonomy_line") == "ficc")
                    & (pl.col("year") == 2022)).sort("quarter")
    assert f22["net_revenue_m"].to_list() == \
        [4730.0, 3642.0, 3617.0, 2687.0], f22
    print("test_goldman_quarterly_current_and_sane: OK")


def main():
    test_period_parsing()
    test_quarter_regimes()
    test_ytd_derivation()
    test_derivation_validation_flags()
    test_reconcile_quarterly()
    test_period_column_date_alignment()
    test_period_column_point_in_time_skipped()
    test_segment_rename_canonicalization()
    test_wfc_q_segment_note_beats_606_note()
    test_hl_fiscal_q3_qtd_q4()
    test_hl_no_annual_derived_q4()
    test_mda_annual_derived_q4()
    test_citi_new_format_10q_ib_revenues()
    test_citi_banking_division_not_advisory_in_panel()
    test_jefferies_qtd_merged_percent_cells()
    test_jefferies_qtd_split_percent_and_ytd_guard()
    test_jefferies_qtd_broken_colspan_header()
    test_jefferies_ibcm_not_advisory_in_panel()
    test_jefferies_2018_transition_no_q4()
    test_jefferies_quarterly_advisory_sane()
    test_goldman_quarterly_current_and_sane()
    print("ALL REVENUE-QUARTERLY TESTS PASS")


if __name__ == "__main__":
    main()
