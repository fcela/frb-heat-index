"""Regression tests for the revenue panel builder.

Run: python3 tests/test_revenue_panel.py (no pytest needed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import polars as pl

from src.revenue.taxonomy import TAXONOMY_LINES, map_quality
from src.revenue.reorg_registry import REORGS, regime_for, is_break, NO_REORG_FIRMS
from src.revenue.build_revenue import build_panel


def test_taxonomy_lines():
    assert len(TAXONOMY_LINES) == 11  # 9 core + ecm_dcm_combined + placement_fees
    assert "advisory" in TAXONOMY_LINES
    assert "placement_fees" in TAXONOMY_LINES
    assert "ecm_dcm_combined" in TAXONOMY_LINES


def test_map_quality_goldman():
    line, q, _ = map_quality("Goldman Sachs", "Asset & Wealth Management")
    assert line == "awm" and q == "D"
    line, q, _ = map_quality("PJT Partners", "Placement Fees")
    assert line == "placement_fees" and q == "D"


def test_map_quality_unmappable():
    line, q, note = map_quality("Wells Fargo", "Consumer Banking and Lending")
    assert line is None and q == "U"
    assert note  # reason recorded, never silent


def test_reorg_registry_coverage():
    firms = {r["firm"] for r in REORGS}
    # Every firm with a reorg is registered; the no-reorg firms are explicit.
    assert "Goldman Sachs" in firms
    assert "Citigroup" in firms
    for f in NO_REORG_FIRMS:
        assert f not in firms


def test_regime_breaks():
    # GS reorg Q4 2022 -> FY2022 reported under the new structure
    assert is_break("Goldman Sachs", 2021, 2022)
    assert not is_break("Goldman Sachs", 2022, 2023)
    assert not is_break("Goldman Sachs", 2023, 2024)
    # Citi Q4 2023 reorg -> FY2023 under the new 5-segment structure
    assert is_break("Citigroup", 2022, 2023)
    assert not is_break("Citigroup", 2023, 2024)
    # MS rename-only is not a break
    assert not is_break("Morgan Stanley", 2012, 2014)
    # Lazard: no breaks at all
    assert regime_for("Lazard", 2010) == regime_for("Lazard", 2025) == "r0"


def test_build_panel_dedupe_and_regime():
    seg = pl.DataFrame([
        {"firm": "Goldman Sachs", "cik": "0000886982", "segment_native": "Asset & Wealth Management",
         "year": 2023, "net_revenue_m": 13000.0, "accession": "0000886982-24-000001", "r_file": "R33.htm",
         "source": "xbrl:R33"},
        # Same cell restated in a later filing -> later wins
        {"firm": "Goldman Sachs", "cik": "0000886982", "segment_native": "Asset & Wealth Management",
         "year": 2023, "net_revenue_m": 13100.0, "accession": "0000886982-25-000001", "r_file": "R33.htm",
         "source": "xbrl:R33"},
        # Unmappable segment -> recorded, not in panel
        {"firm": "Wells Fargo", "cik": "0000072971", "segment_native": "Consumer Banking and Lending",
         "year": 2023, "net_revenue_m": 37000.0, "accession": "0000072971-24-000001", "r_file": "R33.htm",
         "source": "xbrl:R33"},
    ])
    sub = pl.DataFrame({
        "firm": [], "cik": [], "taxonomy_line": [], "year": [],
        "net_revenue_m": [], "source": [], "accession": [],
    })
    panel, unmapped = build_panel(seg, sub)
    # Restated value wins
    row = panel.filter(
        (pl.col("firm") == "Goldman Sachs") & (pl.col("year") == 2023)
    )
    assert len(row) == 1
    assert row["net_revenue_m"][0] == 13100.0
    assert row["taxonomy_line"][0] == "awm"
    # Unmappable recorded separately
    assert len(unmapped) == 1
    assert unmapped[0]["segment_native"] == "Consumer Banking and Lending"


def test_build_panel_never_splices_breaks():
    # Two years on either side of a break keep distinct regimes.
    seg = pl.DataFrame([
        {"firm": "Citigroup", "cik": "0000831001", "segment_native": "Wealth",
         "year": y, "net_revenue_m": 8000.0 + y, "accession": "x", "r_file": "R33.htm",
         "source": "xbrl:R33"}
        for y in (2022, 2023)
    ])
    sub = pl.DataFrame({
        "firm": [], "cik": [], "taxonomy_line": [], "year": [],
        "net_revenue_m": [], "source": [], "accession": [],
    })
    panel, _ = build_panel(seg, sub)
    regimes = panel.sort("year")["regime"].to_list()
    assert regimes[0] != regimes[1]  # Q4-2023 reorg break preserved


def test_build_panel_direct_beats_approximate():
    # Citi "Banking" division maps to advisory with quality A; the MD&A
    # advisory fee split is quality D. The direct disclosure must win even
    # when the approximate mapping comes from a later filing.
    seg = pl.DataFrame([
        {"firm": "Citigroup", "cik": "0000831001", "segment_native": "Banking",
         "year": 2023, "net_revenue_m": 39000.0,
         "accession": "0000831001-26-000011", "r_file": "R1.htm",
         "source": "XBRL R-file"},
    ])
    sub = pl.DataFrame([
        {"firm": "C", "cik": "0000831001", "taxonomy_line": "advisory",
         "year": 2023, "net_revenue_m": 1017.0,
         "source": "mda:Banking business metrics",
         "accession": "0000831001-24-000033"},
    ])
    panel, _ = build_panel(seg, sub)
    row = panel.filter((pl.col("firm") == "Citigroup")
                       & (pl.col("taxonomy_line") == "advisory"))
    assert len(row) == 1
    assert row["net_revenue_m"][0] == 1017.0
    assert row["map_quality"][0] == "D"
    assert row["layer"][0] == "mda"


def test_reconcile_splits_pass_and_flag():
    from src.revenue.build_revenue import reconcile_splits
    seg = pl.DataFrame([
        {"firm": "Morgan Stanley", "cik": "0000895421",
         "segment_native": "Institutional Securities", "year": 2025,
         "net_revenue_m": 33080.0, "accession": "a1", "r_file": "R1.htm",
         "source": "XBRL R-file"},
        {"firm": "Morgan Stanley", "cik": "0000895421",
         "segment_native": "Institutional Securities", "year": 2024,
         "net_revenue_m": 10000.0, "accession": "a1", "r_file": "R1.htm",
         "source": "XBRL R-file"},
    ])
    sub = pl.DataFrame([
        # 2025: splits sum 2888+1965+2766+15631+8716 = 31966 <= 33080*1.05+25
        {"firm": "MS", "cik": "0000895421", "taxonomy_line": line,
         "year": 2025, "net_revenue_m": v,
         "source": "mda:t", "accession": "a2"}
        for line, v in [("advisory", 2888.0), ("ecm", 1965.0),
                        ("dcm", 2766.0), ("equities", 15631.0),
                        ("ficc", 8716.0)]
    ] + [
        # 2024: splits sum far above the parent -> flag, not fudge
        {"firm": "MS", "cik": "0000895421", "taxonomy_line": "advisory",
         "year": 2024, "net_revenue_m": 99999.0,
         "source": "mda:t", "accession": "a2"},
    ])
    rep = reconcile_splits(sub, seg)
    by = {(c["firm"], c["year"]): c["status"] for c in rep["checks"]}
    assert by[("Morgan Stanley", 2025)] == "pass", rep["checks"]
    assert by[("Morgan Stanley", 2024)] == "flag", rep["checks"]
    assert rep["n_pass"] == 1 and rep["n_flag"] == 1
    assert rep["match_rate"] == 0.5


def main():
    test_taxonomy_lines()
    test_map_quality_goldman()
    test_map_quality_unmappable()
    test_reorg_registry_coverage()
    test_regime_breaks()
    test_build_panel_dedupe_and_regime()
    test_build_panel_never_splices_breaks()
    test_build_panel_direct_beats_approximate()
    test_reconcile_splits_pass_and_flag()
    print("test_revenue_panel: all 9 tests passed")


if __name__ == "__main__":
    main()
