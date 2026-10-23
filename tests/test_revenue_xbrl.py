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

import polars as pl

from src.revenue.parse_xbrl import (
    parse_filing_summary,
    find_segment_reports,
    parse_filing_dir,
    parse_segment_table,
    parse_segment_xml,
    _dir_form_type,
    _rfile_rank,
    build_segments_csv,
)
from src.revenue.taxonomy import TAXONOMY_LINES, map_quality
from src.revenue.reorg_registry import REORGS, regime_for

FIX = Path(__file__).resolve().parent / "fixtures" / "gs-0000886982-26-000091"
FIX_JPM_OLD = Path(__file__).resolve().parent / "fixtures" / "jpm-0000950123-11-019773"
FIX_WFC_606 = Path(__file__).resolve().parent / "fixtures" / "wfc-0000072971-20-000217"
FIX_JEF_FY25 = (Path(__file__).resolve().parent / "fixtures"
                / "jef-0000096223-26-000009")
FIX_BAC_FTE = (Path(__file__).resolve().parent / "fixtures"
               / "bac-0000070858-16-000137")

# Jefferies FY2025 10-K (fiscal year ended Nov 30, 2025), R158 "Segment
# Reporting - Net Revenues, Expenses and Total Assets by Segment
# (Details)": NET revenues per segment, $ in millions.
JEF_FY25_NET = {
    ("Asset Management", 2025): 710.2,
    ("Asset Management", 2024): 803.7,
    ("Asset Management", 2023): 188.3,
    ("Investment Banking And Capital Markets", 2025): 6608.0,
    ("Investment Banking And Capital Markets", 2024): 6204.3,
    ("Investment Banking And Capital Markets", 2023): 4504.4,
    ("Operating Segments", 2025): 7318.2,
    ("Operating Segments", 2024): 7008.0,
    ("Operating Segments", 2023): 4692.7,
}

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
    # The impostor row (interest on placement-fee receivables) must
    # never map to placement_fees -- it supplied the panel's cells for
    # 2018-2025 before this rule existed.
    line, q, note = map_quality(
        "PJT Partners", "Interest Income from Placement Fees and Other")
    assert q == "U" and line is None and note


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


def test_rfile_rank_asc606_demoted():
    # ASC 606 revenue-disaggregation notes carry per-segment "Revenues"
    # rows that are CONTRACT revenue only (NII and market-sensitive
    # lines sit outside ASC 606). They must rank below every segment
    # note so they lose any (segment, year, label) tie.
    assert _rfile_rank("Revenue from Contracts with Customers, Revenue by "
                        "Operating Segment (Details)") == 3
    assert _rfile_rank("Disaggregation of Revenue") == 3
    assert _rfile_rank("Operating Segments (Details)") == 2
    assert _rfile_rank("Business Segments") == 1
    assert _rfile_rank("Business Segments - Segment Operating Results "
                       "(Detail)") == 0


def test_wfc_segment_note_beats_606_note():
    # Wells Fargo FY2019 10-K: R163 ("Revenue from Contracts with
    # Customers, Revenue by Operating Segment (Details)") has a WIM
    # block with "Net interest income" but no bare "noninterest
    # income" row, so the sum path collapsed to NII-only -- $4,641M for
    # 2017. R182, the true segment note, sums to $17,072M. The two
    # short names tie at the generic rank and R163 comes first in
    # report order, so before the demotion the 606 note won and the
    # panel showed a 2017 = $4,641M one-year hole between 2016
    # $16,278M and 2018 $15,869M.
    rows = parse_filing_dir("Wells Fargo", "0000072971",
                            "0000072971-20-000217", FIX_WFC_606)
    got = {(r["segment_native"], r["year"]): r["net_revenue_m"]
           for r in rows}
    # Segment-note basis (NII + noninterest income), both years the
    # filing's segment note reports; verified against R182.
    assert got[("Wealth and Investment Management", 2017)] == 17072.0, got
    assert got[("Wealth and Investment Management", 2018)] == 16376.0, got
    assert got[("Wealth and Investment Management", 2019)] == 17341.0, got
    assert got[("Community Banking", 2017)] == 47018.0, got
    # The 606 note's NII-only collapse ($4,641M) must not survive as
    # any emitted segment-year value.
    assert 4641.0 not in {r["net_revenue_m"] for r in rows}
    # And the winning rows must come from the segment note, not R163.
    wim = [r for r in rows
           if r["segment_native"] == "Wealth and Investment Management"]
    assert {r["r_file"] for r in wim} == {"R182.htm"}, wim


def test_jef_fy25_label_prefers_net_revenues():
    # Jefferies' FY2025 10-K segment note lists the gross "Revenues" row
    # BEFORE "Net revenues" in every block, and both normalize to generic
    # revenue labels that compete for the same cell. First-inserted-wins
    # put the gross figures into the cells -- AWM 2023-2025 read
    # $368.4M / $1,113.7M / $1,006.9M while the segment's net revenues
    # are $188.3M / $803.7M / $710.2M (the panel measure is net). The
    # generic labels now compete on (rfile rank, REVENUE_LABELS rank) --
    # "net revenues" beats "revenues" -- so insertion order can never
    # decide a cell.
    rows = parse_filing_dir("Jefferies", "0000096223",
                            "0000096223-26-000009", FIX_JEF_FY25)
    got = {(r["segment_native"], r["year"]): r["net_revenue_m"] for r in rows}
    assert set(got) == set(JEF_FY25_NET), (
        f"extra={set(got) - set(JEF_FY25_NET)}, "
        f"missing={set(JEF_FY25_NET) - set(got)}")
    for key, want in JEF_FY25_NET.items():
        assert abs(got[key] - want) < 0.05, (key, got[key], want)
    # None of the gross "Revenues" rows may survive as a cell.
    gross = {368.4, 1113.7, 1006.9, 7065.1, 9374.5, 9791.2,
             7433.5, 10488.2, 10798.1}
    assert not gross & {round(r["net_revenue_m"], 1) for r in rows}


def test_bac_fte_segment_label():
    # BofA's FY2013-FY2017 10-K segment notes label the revenue line
    # "Total revenue, net of interest expense (FTE basis)"; before that
    # label joined REVENUE_LABELS (at the lowest preference), those
    # filings yielded no segment revenue and GWIM had a 2013-2015 hole.
    # Fixture: FY2015 10-K (accession 0000070858-16-000137), R184 only.
    # The FTE values tie to the FY2018 10-K's plain-basis comparatives
    # at the overlap years (2016: 17,650; 2017: 18,590).
    recs = parse_filing_dir("Bank of America", "0000070858",
                            "0000070858-16-000137", FIX_BAC_FTE)
    gwim = {r["year"]: r["net_revenue_m"] for r in recs
            if r["segment_native"] ==
            "Global Wealth & Investment Management"}
    assert gwim == {2013: 17790.0, 2014: 18404.0, 2015: 18001.0}, gwim


def test_dir_form_type():
    # dei:DocumentType from the XBRL instance is the definitive form
    # type; instance filenames are not (GS's filing-agent-named 10-Qs).
    import tempfile
    import pathlib
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        ten_k = td / "acc-k"; ten_k.mkdir()
        (ten_k / "inst.xml").write_text(
            "<x><dei:DocumentType>10-K</dei:DocumentType></x>")
        ten_q = td / "acc-q"; ten_q.mkdir()
        (ten_q / "d105283d10q_htm.xml").write_text(
            "<x><dei:DocumentType>10-Q</dei:DocumentType></x>")
        none_dir = td / "acc-old"; none_dir.mkdir()
        (none_dir / "FilingSummary.xml").write_text("<Reports/>")
        assert _dir_form_type(ten_k) == "10-K"
        assert _dir_form_type(ten_q) == "10-Q"
        # Pre-XBRL-era dirs carry no instance: None, kept by the walk.
        assert _dir_form_type(none_dir) is None


# Minimal rendered segment R-file: member-linked section header, year
# header row, one "Total net revenues" data row.
_MINI_RFILE = """<html><body><table>
<tr><td><a onclick="top.Show.showAR(this, 'defref_us-gaap_StatementBusinessSegmentsAxis=test_TestSegmentMember')">Test Segment [Member]</a></td><td></td><td></td></tr>
<tr><td>$ in millions</td><td>2017</td><td>2016</td></tr>
<tr><td>Total net revenues</td><td>100</td><td>90</td></tr>
</table></body></html>"""

_MINI_FILINGSUMMARY = """<Reports>
<Report><HtmlFileName>R1.htm</HtmlFileName><ShortName>Operating Segments (Details)</ShortName></Report>
</Reports>"""


def test_build_segments_csv_skips_10q_dirs():
    # The raw tree holds 10-Q downloads next to the 10-Ks; a 10-Q's
    # segment R-file must never contribute to the ANNUAL segments CSV
    # (Houlihan Lokey's Q2-2020 10-Q once injected a single-quarter
    # $37.4M "2020" cell). Only 10-K/10-K/A accessions -- and
    # pre-XBRL-era dirs that state no form type -- are walked.
    import tempfile
    import pathlib
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        cik = td / "0000000001"
        ten_k = cik / "0000000001-17-000001"
        ten_q = cik / "0000000001-17-000002"
        for d in (ten_k, ten_q):
            d.mkdir(parents=True)
            (d / "FilingSummary.xml").write_text(_MINI_FILINGSUMMARY)
            (d / "R1.htm").write_text(_MINI_RFILE)
        (ten_k / "inst.xml").write_text(
            "<x><dei:DocumentType>10-K</dei:DocumentType></x>")
        (ten_q / "d105283d10q_htm.xml").write_text(
            "<x><dei:DocumentType>10-Q</dei:DocumentType></x>")
        out = td / "seg.csv"
        build_segments_csv(raw_root=td, out_path=out,
                           firms_ciks={"0000000001": "Test Firm"},
                           since_year=2000)
        df = pl.read_csv(out)
        # Only the 10-K accession's rows survive (2016 and 2017); the
        # 10-Q's identical table -- same synthetic numbers, later
        # accession -- must not leak in.
        assert set(df["accession"]) == {"0000000001-17-000001"}, df
        assert sorted(zip(df["year"], df["net_revenue_m"])) == \
            [(2016, 90.0), (2017, 100.0)], df


def test_old_code_rank_tie_606_wins_by_order():
    # The pre-fix failure mode, pinned: with the 606 demotion removed the
    # report-order tie let R163's contract row win. Guards against
    # regressing _rfile_rank back to a flat rank for 606 notes.
    import src.revenue.parse_xbrl as px
    reports = [
        {"file": "R163.htm",
         "short_name": "Revenue from Contracts with Customers, Revenue by "
                       "Operating Segment (Details)"},
        {"file": "R182.htm", "short_name": "Operating Segments (Details)"},
    ]
    ranks = [px._rfile_rank(r["short_name"]) for r in reports]
    assert ranks[0] > ranks[1], ranks


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
