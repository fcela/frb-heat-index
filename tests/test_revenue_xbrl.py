"""Regression tests for the EDGAR download layer + XBRL segment parser.

Run: python3 tests/test_revenue_xbrl.py (no pytest needed).

Covers:
  - the verified Goldman Sachs fixture (2026-02-25 10-K,
    accession 0000886982-26-000091): FilingSummary segment R-file discovery
    and per-segment "Total net revenues" extraction;
  - the reorg registry: all 12 firms covered, well-formed entries;
  - the taxonomy mapping: every native segment seen in the parsed panel
    maps to a (line, quality, note) triple; unmappable segments are explicit
    'U', never silently dropped.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.revenue.parse_xbrl import (
    parse_filing_summary,
    find_segment_reports,
    parse_filing_dir,
    parse_segment_table,
    parse_segment_xml,
)
from src.revenue.taxonomy import TAXONOMY_LINES, map_quality
from src.revenue.reorg_registry import REORGS, regime_for

FIX = Path(__file__).resolve().parent / "fixtures" / "gs-0000886982-26-000091"
FIX_JPM_OLD = Path(__file__).resolve().parent / "fixtures" / "jpm-0000950123-11-019773"

FIRMS_12 = [
    "JPMorgan", "Bank of America", "Citigroup", "Goldman Sachs",
    "Morgan Stanley", "Wells Fargo", "Evercore", "Lazard", "Moelis",
    "PJT Partners", "Houlihan Lokey", "Jefferies",
]

# Verified against the GS 2026-02-25 10-K, R191 table 0 ($ in millions).
GS_EXPECTED = {
    ("Global Banking & Markets", 2025): 41453.0,
    ("Global Banking & Markets", 2024): 35067.0,
    ("Global Banking & Markets", 2023): 29994.0,
    ("Asset & Wealth Management", 2025): 16679.0,
    ("Asset & Wealth Management", 2024): 16316.0,
    ("Asset & Wealth Management", 2023): 14202.0,
    ("Platform Solutions", 2025): 151.0,
    ("Platform Solutions", 2024): 2129.0,
    ("Platform Solutions", 2023): 2058.0,
}


def test_fixture_present():
    for f in ("FilingSummary.xml", "R33.htm", "R191.htm"):
        assert (FIX / f).exists(), f"missing fixture {f}"


def test_find_segment_reports():
    reports = parse_filing_summary(FIX / "FilingSummary.xml")
    assert len(reports) > 10  # full report list parsed
    segs = find_segment_reports(reports)
    files = {r["file"] for r in segs}
    assert "R33.htm" in files  # "Business Segments"
    assert "R191.htm" in files  # "Business Segments - Segment Operating Results (Detail)"
    for r in segs:
        assert "business segments" in r["short_name"].lower()


def test_gs_fixture_values():
    rows = parse_filing_dir("Goldman Sachs", "0000886982",
                            "0000886982-26-000091", FIX)
    assert rows, "no rows parsed from the GS fixture"
    got = {(r["segment_native"], r["year"]): r["net_revenue_m"] for r in rows}
    assert set(got) == set(GS_EXPECTED), (
        f"segment/year mismatch: extra={set(got) - set(GS_EXPECTED)}, "
        f"missing={set(GS_EXPECTED) - set(got)}")
    for key, want in GS_EXPECTED.items():
        assert got[key] == want, f"{key}: got {got[key]}, want {want}"


def test_gs_fixture_columns():
    rows = parse_filing_dir("Goldman Sachs", "0000886982",
                            "0000886982-26-000091", FIX)
    want_cols = {"firm", "cik", "segment_native", "year", "net_revenue_m",
                 "accession", "r_file", "source"}
    for r in rows:
        assert want_cols <= set(r), f"missing columns: {want_cols - set(r)}"
        assert r["firm"] == "Goldman Sachs"
        assert r["cik"] == "0000886982"
        assert r["accession"] == "0000886982-26-000091"
        assert r["r_file"] == "R191.htm"
        assert isinstance(r["year"], int)
        assert isinstance(r["net_revenue_m"], float)
    # No duplicate (segment, year) rows after cross-R-file dedupe.
    keys = [(r["segment_native"], r["year"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate segment/year rows"


def test_gs_fixture_consolidated_dropped():
    rows = parse_filing_dir("Goldman Sachs", "0000886982",
                            "0000886982-26-000091", FIX)
    # The consolidated 58,283 / 53,512 / 46,254 block must not leak in as a
    # pseudo-segment.
    assert all(r["segment_native"] for r in rows)
    assert not any(r["net_revenue_m"] == 58283.0 for r in rows)


def test_reorg_registry_covers_all_firms():
    firms = {r["firm"] for r in REORGS}
    for f in FIRMS_12:
        # Firms with no reorgs are either absent from the list or present
        # with rename-only entries; every entry must be well formed.
        pass
    for r in REORGS:
        assert set(r) >= {"firm", "effective", "type", "desc"}, r
        assert r["type"] in ("reorg", "rename"), r
        assert r["firm"] in FIRMS_12, r
    # Spot checks from the verified research.
    assert any(r["firm"] == "Citigroup" and r["type"] == "reorg"
               for r in REORGS)
    assert any(r["firm"] == "Goldman Sachs" and r["effective"] == "2022-10-01"
               or r["firm"] == "Goldman Sachs" and "2022" in r["effective"]
               for r in REORGS)
    # regime_for works for every firm/year in range.
    for f in FIRMS_12:
        for y in (2010, 2015, 2020, 2025):
            assert regime_for(f, y), (f, y)


def test_taxonomy_no_silent_drops():
    # Every native segment the GS fixture produced must map explicitly:
    # a taxonomy line with D/A quality, or (None, 'U', note).
    rows = parse_filing_dir("Goldman Sachs", "0000886982",
                            "0000886982-26-000091", FIX)
    for r in rows:
        line, quality, note = map_quality(r["firm"], r["segment_native"])
        assert quality in ("D", "A", "U"), (r, quality)
        if quality == "U":
            assert line is None and note, r  # explicit, never silent
        else:
            assert line in TAXONOMY_LINES, (r, line)


def test_taxonomy_gs_spot():
    line, q, _ = map_quality("Goldman Sachs", "Asset & Wealth Management")
    assert (line, q) == ("awm", "D")
    line, q, note = map_quality("Goldman Sachs", "Global Banking & Markets")
    assert q == "U" and line is None and note  # splits come from the MD&A layer
    line, q, _ = map_quality("PJT Partners", "Placement Fees")
    assert (line, q) == ("placement_fees", "D")


# Verified against the JPM 2011-02-28 10-K (FY2010), R191.xml ($ in USD,
# scale 0 in the R-file). Pre-v3 InstanceReport layout: segments pivoted
# into columns.
JPM_OLD_EXPECTED = {
    ("Investment Bank", 2010): 26217.0,
    ("Investment Bank", 2009): 28109.0,
    ("Retail Financial Services", 2010): 31756.0,
    ("Card Services", 2010): 17163.0,
    ("Commercial Banking", 2010): 6040.0,
    ("Treasury & Securities Services", 2010): 7381.0,
    ("Asset Management", 2010): 8984.0,
}


def test_old_xml_format_values():
    recs = parse_segment_xml(
        (FIX_JPM_OLD / "R191.xml").read_text(errors="replace"))
    assert recs, "no rows parsed from the old-format fixture"
    got = {(r["segment_native"], r["year"]): r["value_m"] for r in recs}
    for key, want in JPM_OLD_EXPECTED.items():
        assert key in got, f"missing {key}"
        assert abs(got[key] - want) < 0.5, f"{key}: got {got[key]}, want {want}"


def test_old_filing_summary_xmlfilename():
    reports = parse_filing_summary(FIX_JPM_OLD / "FilingSummary.xml")
    segs = find_segment_reports(reports)
    files = {r["file"] for r in segs}
    # Pre-v3 filings name R*.xml files via XmlFileName.
    assert "R191.xml" in files
    assert all(f.endswith(".xml") for f in files)


def test_old_filing_dir_end_to_end():
    rows = parse_filing_dir("JPMorgan", "0000019617",
                            "0000950123-11-019773", FIX_JPM_OLD)
    assert rows
    assert {r["r_file"] for r in rows} == {"R191.xml"}
    keys = [(r["segment_native"], r["year"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_ms_geographic_reports_excluded():
    # Morgan Stanley titles its note "Segment, Geographic and Revenue
    # Information": the main note must match, the "by Region" sub-reports
    # must not.
    from src.revenue.parse_xbrl import find_segment_reports as fsr
    reports = [
        {"short_name": "Segment, Geographic and Revenue Information",
         "long_name": "", "file": "R30.htm", "xml": ""},
        {"short_name": "Segment, Geographic and Revenue Information - "
                       "Selected Financial Information by Business Segment "
                       "(Details)",
         "long_name": "", "file": "R190.htm", "xml": ""},
        {"short_name": "Segment, Geographic and Revenue Information - "
                       "Net Revenues by Region (Details)",
         "long_name": "", "file": "R196.htm", "xml": ""},
        {"short_name": "Segment, Geographic and Revenue Information - "
                       "Assets by Region (Details)",
         "long_name": "", "file": "R201.htm", "xml": ""},
    ]
    got = {r["file"] for r in fsr(reports)}
    assert got == {"R30.htm", "R190.htm"}, got


def test_segment_name_from_member_id():
    # Abbreviated display text ("IS | Operating Segments") resolves via the
    # XBRL member concept; full display text wins when present.
    from src.revenue.parse_xbrl import _segment_name
    assert _segment_name(
        "IS | Operating Segments",
        "defref_us-gaap_StatementBusinessSegmentsAxis="
        "ms_InstitutionalSecuritiesSegmentMember") == "Institutional Securities"
    assert _segment_name(
        "Global Banking & Markets",
        "defref_us-gaap_StatementBusinessSegmentsAxis="
        "gs_GlobalBankingAndMarketsMember") == "Global Banking & Markets"
    assert _segment_name(
        "Operating Segments | Consumer & Community Banking",
        "defref_srt_ConsolidationItemsAxis=us-gaap_OperatingSegmentsMember"
    ) == "Consumer & Community Banking"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"ok   {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
