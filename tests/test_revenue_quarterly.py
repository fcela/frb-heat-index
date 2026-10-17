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
    panel, unmapped, dlog = build_quarterly(seg_q, seg_a)
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
    panel, _, dlog = build_quarterly(seg_q, seg_a)
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


def main():
    test_period_parsing()
    test_quarter_regimes()
    test_ytd_derivation()
    test_derivation_validation_flags()
    test_reconcile_quarterly()
    print("ALL REVENUE-QUARTERLY TESTS PASS")


if __name__ == "__main__":
    main()
