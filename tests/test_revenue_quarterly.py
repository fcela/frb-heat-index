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


def main():
    test_period_parsing()
    test_quarter_regimes()
    test_ytd_derivation()
    test_derivation_validation_flags()
    test_reconcile_quarterly()
    test_period_column_date_alignment()
    test_period_column_point_in_time_skipped()
    test_segment_rename_canonicalization()
    test_hl_fiscal_q3_qtd_q4()
    test_hl_no_annual_derived_q4()
    test_mda_annual_derived_q4()
    print("ALL REVENUE-QUARTERLY TESTS PASS")


if __name__ == "__main__":
    main()
