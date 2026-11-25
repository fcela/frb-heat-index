"""Regression tests for the MD&A sub-split scraper (src/revenue/parse_mda.py).

Plain-python tests (no pytest): run with `python3 tests/test_revenue_mda.py`.

Fixtures are real 10-K main documents (unedited filing HTML, gzip-compressed):
  tests/fixtures_revenue_mda/gs_10k_fy2023.htm.gz  (Goldman Sachs FY2023 10-K,
      accession 0000886982-24-000006)
  tests/fixtures_revenue_mda/c_10k_fy2023.htm.gz   (Citigroup FY2023 10-K,
      accession 0000831001-24-000033)
  tests/fixtures_revenue_mda/jpm_10k_fy2025.htm.gz (JPMorgan FY2025 10-K,
      accession 0001628280-26-008131)
  tests/fixtures_revenue_mda/ms_10k_fy2025.htm.gz  (Morgan Stanley FY2025 10-K,
      accession 0000895421-26-000086)
  tests/fixtures_revenue_mda/bac_10k_fy2025.htm.gz (Bank of America FY2025
      10-K, accession 0000070858-26-000157)
  tests/fixtures_revenue_mda/jef_leucadia_10k_fy2017.htm.gz (Leucadia National
      FY2017 10-K, accession 0000096223-18-000009 -- Jefferies is a segment)
  tests/fixtures_revenue_mda/jef_10k_fy2019.htm.gz (Jefferies Financial Group
      FY2019 10-K, accession 0000096223-20-000008)
  tests/fixtures_revenue_mda/gs_10k_fy2019.htm.gz (Goldman Sachs FY2019 10-K,
      accession 0001193125-20-043853 -- first filing under the realigned
      four-segment structure, restated 2017-2019 comparatives)
Expected values below were read off the filings' MD&A tables.
"""

import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.revenue.parse_mda import (  # noqa: E402
    FIRM_SPECS,
    TableNotFoundError,
    RowSpec,
    dedupe_records,
    detect_years,
    normalize_label,
    parse_filing,
    parse_money,
)

FIX = Path(__file__).resolve().parent / "fixtures_revenue_mda"

# (taxonomy_line, year) -> USD millions, from the fixture filings' MD&A tables.
EXPECTED_GS = {
    ("advisory", 2023): 3299.0, ("advisory", 2022): 4704.0,
    ("advisory", 2021): 5654.0,
    ("ecm", 2023): 1153.0, ("ecm", 2022): 848.0, ("ecm", 2021): 4985.0,
    ("dcm", 2023): 1764.0, ("dcm", 2022): 1808.0, ("dcm", 2021): 3497.0,
    ("ficc", 2023): 12060.0, ("ficc", 2022): 14676.0, ("ficc", 2021): 10611.0,
    ("equities", 2023): 11549.0, ("equities", 2022): 10988.0,
    ("equities", 2021): 11722.0,
}
EXPECTED_C = {
    ("advisory", 2023): 1017.0, ("advisory", 2022): 1332.0,
    ("advisory", 2021): 1785.0,
    ("ecm", 2023): 500.0, ("ecm", 2022): 621.0, ("ecm", 2021): 2152.0,
    ("dcm", 2023): 1196.0, ("dcm", 2022): 1100.0, ("dcm", 2021): 2081.0,
    ("ficc", 2023): 14820.0, ("ficc", 2022): 15710.0, ("ficc", 2021): 14345.0,
    ("equities", 2023): 4037.0, ("equities", 2022): 4451.0,
    ("equities", 2021): 5054.0,
}

# JPMorgan FY2025 10-K: CIB "Investment banking fees" (advisory / equity /
# debt underwriting) and "Selected income statement data" revenue-by-business
# (Fixed Income Markets / Equity Markets).
EXPECTED_JPM = {
    ("advisory", 2025): 3497.0, ("advisory", 2024): 3290.0,
    ("advisory", 2023): 2814.0,
    ("ecm", 2025): 1732.0, ("ecm", 2024): 1692.0, ("ecm", 2023): 1151.0,
    ("dcm", 2025): 4506.0, ("dcm", 2024): 4134.0, ("dcm", 2023): 2666.0,
    ("ficc", 2025): 22532.0, ("ficc", 2024): 20066.0,
    ("ficc", 2023): 19180.0,
    ("equities", 2025): 13250.0, ("equities", 2024): 9941.0,
    ("equities", 2023): 8784.0,
}

# Morgan Stanley FY2025 10-K: Institutional Securities income statement --
# Advisory once; "Equity"/"Fixed Income" twice (first = underwriting,
# last = sales & trading).
EXPECTED_MS = {
    ("advisory", 2025): 2888.0, ("advisory", 2024): 2378.0,
    ("advisory", 2023): 2244.0,
    ("ecm", 2025): 1965.0, ("ecm", 2024): 1599.0, ("ecm", 2023): 889.0,
    ("dcm", 2025): 2766.0, ("dcm", 2024): 2193.0, ("dcm", 2023): 1445.0,
    ("ficc", 2025): 8716.0, ("ficc", 2024): 8418.0, ("ficc", 2023): 7673.0,
    ("equities", 2025): 15631.0, ("equities", 2024): 12230.0,
    ("equities", 2023): 9986.0,
}

# Morgan Stanley FY2016 10-K: S&T by asset class in the GAAP/non-GAAP
# table ("Equity-U.S. GAAP" / "Fixed Income-U.S. GAAP"; lxml decodes the
# em-dash as U+0097, which the \W* separator absorbs). Values verified
# against the filing ($ in millions).
EXPECTED_MS_2016_ST = {
    ("ficc", 2016): 5117.0, ("ficc", 2015): 4758.0, ("ficc", 2014): 4214.0,
    ("equities", 2016): 8037.0, ("equities", 2015): 8288.0,
    ("equities", 2014): 7135.0,
}

# Morgan Stanley FY2018 10-K: the "By Business" S&T summary (rows
# Equity / Fixed income / Other / Total) plus the investment-banking
# fragment (bare "Equity"/"Fixed income" = the UNDERWRITING rows).
# Regression: the pre-fix extractor pulled the fragment's underwriting
# rows into the S&T slots, making equities byte-identical to ECM and
# FICC byte-identical to DCM for 2015-2018.
EXPECTED_MS_2018 = {
    ("ficc", 2018): 5005.0, ("ficc", 2017): 4928.0, ("ficc", 2016): 5117.0,
    ("equities", 2018): 8976.0, ("equities", 2017): 7982.0,
    ("equities", 2016): 8037.0,
    ("advisory", 2018): 2436.0, ("advisory", 2017): 2077.0,
    ("advisory", 2016): 2220.0,
    ("ecm", 2018): 1726.0, ("ecm", 2017): 1484.0, ("ecm", 2016): 887.0,
    ("dcm", 2018): 1926.0, ("dcm", 2017): 1976.0, ("dcm", 2016): 1369.0,
}

# Bank of America FY2025 10-K: "Noninterest Income by Business Segment"
# (Total Corporation block: advisory + combined underwriting) and the
# Global Markets sales-and-trading table (FICC/Equities, 2-year table).
EXPECTED_BAC = {
    ("advisory", 2025): 1890.0, ("advisory", 2024): 1690.0,
    ("advisory", 2023): 1575.0,
    ("ecm_dcm_combined", 2025): 3320.0, ("ecm_dcm_combined", 2024): 3275.0,
    ("ecm_dcm_combined", 2023): 2235.0,
    ("ficc", 2025): 12267.0, ("ficc", 2024): 11371.0,
    ("equities", 2025): 8604.0, ("equities", 2024): 7436.0,
}

# Leucadia National FY2017 10-K (CIK 0000096223 was Leucadia until the
# 2018 Jefferies Financial Group rename): the Jefferies segment MD&A
# "net revenues by source" table is the only advisory disclosure for
# FY2015-FY2017 (the ASC 606 disaggregation note first appears in the
# FY2019 10-K). This spec backfills the pre-2018 annual gap; before it,
# the annual panel showed advisory 2017 = $3,198M -- the whole-firm
# XBRL segment total mapped to advisory -- followed by 2018 = $820M, an
# impossible 74% drop.
EXPECTED_JEF_LEUCADIA = {
    ("advisory", 2017): 770.092, ("advisory", 2016): 654.19,
    ("advisory", 2015): 632.354,
}

# Jefferies Financial Group FY2019 10-K: the "net revenues by source"
# summary (advisory 2017-2019; equities/ficc 2017-2019 via the grid-
# columns spec) plus the ASC 606 disaggregation note (advisory +
# combined underwriting for FY2019 and the 11-month 2018 transition
# period; its Equities/Fixed income rows are contract revenue only and
# are no longer spec'd -- see test_jefferies_grid_columns_fy2023).
EXPECTED_JEF_2019 = {
    ("advisory", 2019): 767.421, ("advisory", 2018): 820.042,
    ("advisory", 2017): 770.092,
    ("ecm_dcm_combined", 2019): 761.308,
    ("ecm_dcm_combined", 2018): 1090.161,
    ("equities", 2019): 773.979, ("equities", 2018): 665.557,
    ("equities", 2017): 674.424,
    ("ficc", 2019): 681.362, ("ficc", 2018): 559.712,
    ("ficc", 2017): 618.388,
}


# Goldman Sachs FY2019 10-K: the realignment's first annual filing --
# Investment Banking and Global Markets operating results on the
# restated four-segment basis, 2017-2019. This is the filing that fills
# the panel's GS 2019 ecm_dcm_combined gap and replaces the as-reported
# 2017-2018 underwriting cells with the restated basis ($3,915M/$3,986M
# vs the XBRL $4,183M/$4,355M); the Global Markets spec window was
# widened to (2019, 2021) for its restated 2017 FICC/Equities cells
# (old-basis XBRL: FICC client execution $5,299M -> realigned $6,218M).
EXPECTED_GS_2019 = {
    ("advisory", 2019): 3197.0, ("advisory", 2018): 3444.0,
    ("advisory", 2017): 3161.0,
    ("ecm", 2019): 1482.0, ("ecm", 2018): 1628.0, ("ecm", 2017): 1235.0,
    ("dcm", 2019): 2119.0, ("dcm", 2018): 2358.0, ("dcm", 2017): 2680.0,
    ("ecm_dcm_combined", 2019): 3601.0,
    ("ecm_dcm_combined", 2018): 3986.0,
    ("ecm_dcm_combined", 2017): 3915.0,
    ("ficc", 2019): 7388.0, ("ficc", 2018): 6985.0, ("ficc", 2017): 6218.0,
    ("equities", 2019): 7391.0, ("equities", 2018): 7453.0,
    ("equities", 2017): 6077.0,
}


def _fixture(name: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / name.replace(".gz", "")
    tmp.write_bytes(gzip.decompress((FIX / name).read_bytes()))
    return tmp


def _by_line_year(records):
    return {(r["taxonomy_line"], r["year"]): r["net_revenue_m"]
            for r in records}


def test_helpers():
    assert normalize_label(
        "Equity underwriting (Equity Capital Markets (ECM))") == \
        "equity underwriting (equity capital markets (ecm))"
    assert normalize_label("Advisory(1)") == "advisory"
    assert parse_money("$ 3,534") == 3534.0
    assert parse_money("1,677") == 1677.0
    assert parse_money("(537)") == -537.0
    assert parse_money("$ (45)") == -45.0
    assert parse_money("—") is None
    assert parse_money("-") is None
    assert parse_money("") is None
    assert parse_money("NM") is None
    # Row matching: prefix + optional parenthetical, exclusions honored.
    assert RowSpec("ecm", [r"equity underwriting"]).matches(
        "equity underwriting (equity capital markets (ecm))")
    assert not RowSpec("equities", [r"equities"]).matches(
        "equities intermediation")
    assert RowSpec("ficc", [r"fixed income"],
                   exclude=[r"underwriting"]).matches("fixed income")
    assert not RowSpec("ficc", [r"fixed income"],
                       exclude=[r"underwriting"]).matches(
        "fixed income underwriting")
    print("test_helpers: OK")


def test_gs_ib_and_markets():
    """Goldman Sachs: IB-fee split + FICC/Equities from the real 10-K."""
    path = _fixture("gs_10k_fy2023.htm.gz")
    recs = parse_filing("GS", "0000886982", "0000886982-24-000006", path)
    by = _by_line_year(recs)
    assert len(recs) == 15, f"expected 15 cells, got {len(recs)}"
    for key, want in EXPECTED_GS.items():
        assert by.get(key) == want, f"GS {key}: got {by.get(key)}, want {want}"
    # Sources are tagged per table.
    assert {r["source"] for r in recs} == {
        "mda:Investment banking fees",
        "mda:Global Banking & Markets net revenues"}, \
        {r["source"] for r in recs}
    # Accession + CIK ride along on every row.
    assert {r["accession"] for r in recs} == {"0000886982-24-000006"}
    assert {r["cik"] for r in recs} == {"0000886982"}
    print(f"test_gs_ib_and_markets: OK ({len(recs)} cells)")


def test_gs_fy2019_realignment():
    """GS FY2019 10-K: restated four-segment comparatives, 2017-2019.

    The realignment (segments Investment Banking / Global Markets /
    Asset Management / Consumer & Wealth Management) was presented in
    the FY2019 10-K with restated 2017-2019 comparatives, so fiscal
    2019 is inside the (2019, 2021) windows of the Global Markets and
    IB-underwriting specs. Fixture mode resolves the fiscal year only
    when the accession's raw filing directory is cached (it feeds the
    dir_period_end fallback); without it the (2022, 2100) GBM spec
    also applies "anyway" and lands on the same Global Markets table,
    emitting duplicate ficc/equities cells. The duplicates carry
    identical values, so every record is asserted against the same
    expected set either way.
    """
    path = _fixture("gs_10k_fy2019.htm.gz")
    recs = parse_filing("GS", "0000886982", "0001193125-20-043853", path)
    assert len(recs) in (18, 24), \
        f"expected 18 cells (+6 GBM dups w/o raw cache), got {len(recs)}"
    seen = set()
    for r in recs:
        key = (r["taxonomy_line"], r["year"])
        want = EXPECTED_GS_2019.get(key)
        assert want is not None, f"GS unexpected cell {key}"
        assert r["net_revenue_m"] == want, \
            f"GS {key}: got {r['net_revenue_m']}, want {want}"
        seen.add(key)
    assert seen == set(EXPECTED_GS_2019), \
        set(EXPECTED_GS_2019) - seen
    sources = {r["source"] for r in recs}
    assert "mda:Investment banking fees" in sources, sources
    assert "mda:Global Markets net revenues" in sources, sources
    assert "mda:Investment Banking underwriting (four-segment era)" \
        in sources, sources
    assert {r["accession"] for r in recs} == {"0001193125-20-043853"}
    print(f"test_gs_fy2019_realignment: OK ({len(recs)} cells)")


def test_citi_banking_and_markets():
    """Citigroup: Banking IB-fee split + Markets FI/Equities, real 10-K."""
    path = _fixture("c_10k_fy2023.htm.gz")
    recs = parse_filing("C", "0000831001", "0000831001-24-000033", path)
    by = _by_line_year(recs)
    assert len(recs) == 15, f"expected 15 cells, got {len(recs)}"
    for key, want in EXPECTED_C.items():
        assert by.get(key) == want, f"C {key}: got {by.get(key)}, want {want}"
    assert {r["source"] for r in recs} == {
        "mda:Banking business metrics - investment banking fees",
        "mda:Markets revenue by component"}, {r["source"] for r in recs}
    print(f"test_citi_banking_and_markets: OK ({len(recs)} cells)")


def test_jpm_cib_splits():
    """JPMorgan: CIB IB-fee split + Fixed Income/Equity Markets, real 10-K."""
    path = _fixture("jpm_10k_fy2025.htm.gz")
    recs = parse_filing("JPM", "0000019617", "0001628280-26-008131", path)
    by = _by_line_year(recs)
    assert len(recs) == 15, f"expected 15 cells, got {len(recs)}"
    for key, want in EXPECTED_JPM.items():
        assert by.get(key) == want, f"JPM {key}: got {by.get(key)}, want {want}"
    assert {r["source"] for r in recs} == {
        "mda:Investment banking fees",
        "mda:CIB revenue by business"}, {r["source"] for r in recs}
    print(f"test_jpm_cib_splits: OK ({len(recs)} cells)")


def test_ms_institutional_securities():
    """Morgan Stanley: IS advisory/underwriting + sales & trading, real 10-K.

    The underwriting "Equity"/"Fixed Income" rows are the FIRST matches;
    the sales & trading rows the LAST -- the occurrence logic is the point.
    """
    path = _fixture("ms_10k_fy2025.htm.gz")
    recs = parse_filing("MS", "0000895421", "0000895421-26-000086", path)
    by = _by_line_year(recs)
    assert len(recs) == 15, f"expected 15 cells, got {len(recs)}"
    for key, want in EXPECTED_MS.items():
        assert by.get(key) == want, f"MS {key}: got {by.get(key)}, want {want}"
    assert {r["source"] for r in recs} == {
        "mda:Institutional Securities income statement",
        "mda:Institutional Securities investment banking"}, \
        {r["source"] for r in recs}
    print(f"test_ms_institutional_securities: OK ({len(recs)} cells)")


def test_ms_fy2016_st_gaap():
    """Morgan Stanley FY2016 10-K: S&T by asset class, GAAP rows.

    The table labels its rows "Equity-U.S. GAAP" (em-dash, which lxml
    decodes as the control char U+0097); the GAAP rows carry the same
    values the FY2018 filing's By-Business table later restates, so the
    layout eras chain without a jump. Pinned: the underwriting fee
    fragment ("Equity underwriting revenues") must NOT satisfy this
    spec's rows.
    """
    path = _fixture("ms_10k_fy2016.htm.gz")
    recs = parse_filing("MS", "0000895421", "0001193125-17-059212", path)
    by = _by_line_year(recs)
    assert len(recs) == 6, f"expected 6 cells, got {len(recs)}"
    for key, want in EXPECTED_MS_2016_ST.items():
        assert by.get(key) == want, f"MS {key}: got {by.get(key)}, want {want}"
    assert {r["source"] for r in recs} == {
        "mda:Institutional Securities sales and trading (GAAP/non-GAAP)"}, \
        {r["source"] for r in recs}
    print(f"test_ms_fy2016_st_gaap: OK ({len(recs)} cells)")


def test_ms_fy2018_st_by_business():
    """Morgan Stanley FY2018 10-K: By-Business S&T table vs the IB
    fragment -- the equities/ECM clone failure mode stays dead.

    The filing's investment-banking summary fragment carries bare
    "Equity"/"Fixed income" rows for the UNDERWRITING split; the
    By-Business table carries the same bare labels for SALES & TRADING.
    The "Other" pin row (present only in the S&T table) is what tells
    the two apart. Pre-fix, ficc 2015-2018 was byte-identical to DCM
    and equities byte-identical to ECM.
    """
    path = _fixture("ms_10k_fy2018.htm.gz")
    recs = parse_filing("MS", "0000895421", "0001193125-19-051943", path)
    by = _by_line_year(recs)
    assert len(recs) == 15, f"expected 15 cells, got {len(recs)}"
    for key, want in EXPECTED_MS_2018.items():
        assert by.get(key) == want, f"MS {key}: got {by.get(key)}, want {want}"
    # The clone failure mode, pinned directly.
    assert by[("ficc", 2018)] != by[("dcm", 2018)]
    assert by[("equities", 2018)] != by[("ecm", 2018)]
    assert {r["source"] for r in recs} == {
        "mda:Institutional Securities sales and trading (By Business)",
        "mda:Institutional Securities investment banking (fragment era)"}, \
        {r["source"] for r in recs}
    print(f"test_ms_fy2018_st_by_business: OK ({len(recs)} cells)")


def test_bac_segments():
    """Bank of America: segment-note IB splits + GM sales & trading."""
    path = _fixture("bac_10k_fy2025.htm.gz")
    recs = parse_filing("BAC", "0000070858", "0000070858-26-000157", path)
    by = _by_line_year(recs)
    assert len(recs) == 10, f"expected 10 cells, got {len(recs)}"
    for key, want in EXPECTED_BAC.items():
        assert by.get(key) == want, \
            f"BAC {key}: got {by.get(key)}, want {want}"
    assert {r["source"] for r in recs} == {
        "mda:Noninterest income by business segment",
        "mda:Global Markets sales and trading revenue"}, \
        {r["source"] for r in recs}
    print(f"test_bac_segments: OK ({len(recs)} cells)")


def test_bac_fy2016_sales_and_trading():
    """BAC FY2016 10-K: S&T rows parse; the filing must not abort.

    The "Noninterest income by business segment" table was introduced
    in the FY2018 10-K; while its spec window covered filing years
    2016+, the FY2016/FY2017 10-Ks raised TableNotFoundError and the
    whole filings were skipped -- the 2016 FICC/equities hole. The
    FY2017 10-K's comparative column confirms both 2016 values.
    """
    path = _fixture("bac_10k_fy2016.htm.gz")
    recs = parse_filing("BAC", "0000070858", "0000070858-17-000013", path)
    by = _by_line_year(recs)
    assert by == {("ficc", 2016): 9373.0, ("ficc", 2015): 7869.0,
                  ("equities", 2016): 4017.0, ("equities", 2015): 4335.0}, by
    assert {r["source"] for r in recs} == {
        "mda:Global Markets sales and trading revenue"}
    print(f"test_bac_fy2016_sales_and_trading: OK ({len(recs)} cells)")


def test_missing_table_raises():
    """A filing without the expected table fails loudly, not silently."""
    html = ("<html><body><h1>Item 7. MD&A</h1>"
            "<table><tr><td>Something else</td><td>2024</td></tr>"
            "<tr><td>Other revenue</td><td>100</td></tr></table>"
            "</body></html>")
    with tempfile.NamedTemporaryFile("w", suffix=".htm",
                                     delete=False) as f:
        f.write(html)
        tmp = Path(f.name)
    try:
        parse_filing("GS", "0000886982", "0000886982-00-000000", tmp)
    except TableNotFoundError as e:
        msg = str(e)
        assert "Investment banking fees" in msg, msg
        assert "not found" in msg, msg
        print(f"test_missing_table_raises: OK ({msg[:90]}...)")
    else:
        raise AssertionError("expected TableNotFoundError, got silent pass")
    finally:
        tmp.unlink()


def test_dedupe_prefers_later_filing():
    recs = [
        {"firm": "GS", "cik": "0000886982", "taxonomy_line": "advisory",
         "year": 2023, "net_revenue_m": 3299.0, "source": "mda:t",
         "accession": "0000886982-24-000006"},
        {"firm": "GS", "cik": "0000886982", "taxonomy_line": "advisory",
         "year": 2023, "net_revenue_m": 3295.0, "source": "mda:t",
         "accession": "0000886982-25-000005"},
    ]
    out = dedupe_records(recs)
    assert len(out) == 1
    assert out[0]["net_revenue_m"] == 3295.0
    assert out[0]["accession"] == "0000886982-25-000005"
    print("test_dedupe_prefers_later_filing: OK")


def test_dedupe_cross_prefix_chronology():
    # Accession strings do NOT sort chronologically across filing-agent
    # prefixes: GS's self-filed "0000886982-23-*" string-sorts BEFORE
    # its agent-filed "0001193125-22-*". A raw-string comparison kept
    # the original-vintage value over the later restatement; the
    # (filing year, sequence) key must not.
    from src.revenue.taxonomy import accession_order_key
    assert accession_order_key("0000886982-23-000005") \
        > accession_order_key("0001193125-22-134459")
    assert accession_order_key("0000019617-25-000123") \
        > accession_order_key("0001193125-18-036702")
    assert accession_order_key("") < accession_order_key(
        "0000019617-10-000001")
    recs = [
        {"firm": "GS", "cik": "0000886982", "taxonomy_line": "ficc",
         "year": 2022, "net_revenue_m": 4723.0, "source": "mda:t",
         "accession": "0001193125-22-134459"},   # original vintage, 2022
        {"firm": "GS", "cik": "0000886982", "taxonomy_line": "ficc",
         "year": 2022, "net_revenue_m": 4730.0, "source": "mda:t",
         "accession": "0000886982-23-000005"},  # restated, filed 2023
    ]
    out = dedupe_records(recs)
    assert len(out) == 1
    assert out[0]["net_revenue_m"] == 4730.0, out
    assert out[0]["accession"] == "0000886982-23-000005", out
    print("test_dedupe_cross_prefix_chronology: OK")


def test_jefferies_leucadia_backfill():
    """Leucadia-era 10-K: advisory from the revenues-by-source table.

    The ASC 606 note does not exist in FY2017-vintage filings, so the
    note spec is fiscal-year-gated (FY2019+) and this filing must parse
    via the revenues-by-source spec alone -- no raise, no note rows.
    """
    path = _fixture("jef_leucadia_10k_fy2017.htm.gz")
    recs = parse_filing("JEF", "0000096223", "0000096223-18-000009", path)
    by = _by_line_year(recs)
    assert by == EXPECTED_JEF_LEUCADIA, by
    assert {r["source"] for r in recs} == {
        "mda:Jefferies net revenues by source - advisory"}, \
        {r["source"] for r in recs}
    print(f"test_jefferies_leucadia_backfill: OK ({len(recs)} cells)")


def test_jefferies_jfg2019_both_specs():
    """JFG FY2019 10-K: revenues-by-source + ASC 606 note together.

    The summary tables supply advisory and equities/ficc 2017-2019
    (grid-columns spec); the note adds advisory and combined
    underwriting for FY2019/FY2018 (its contract-revenue Equities/Fixed
    income rows are no longer spec'd). The raw parse keeps every
    source's records (dedupe happens in build_csv).
    """
    path = _fixture("jef_10k_fy2019.htm.gz")
    recs = parse_filing("JEF", "0000096223", "0000096223-20-000008", path)
    by = _by_line_year(recs)
    for key, want in EXPECTED_JEF_2019.items():
        assert by.get(key) == want, f"JEF {key}: got {by.get(key)}, want {want}"
    # Advisory appears twice for 2018/2019 (summary + note), once for 2017.
    adv = [r for r in recs if r["taxonomy_line"] == "advisory"]
    assert len(adv) == 5, [(r["year"], r["source"]) for r in adv]
    assert {r["source"] for r in recs} == {
        "mda:Jefferies net revenues by source - advisory",
        "mda:Investment Banking and Capital Markets revenues by source",
        "mda:Investment Banking and Capital Markets net revenues summary"}, \
        {r["source"] for r in recs}
    print(f"test_jefferies_jfg2019_both_specs: OK ({len(recs)} cells)")


def test_jefferies_grid_columns_fy2023():
    """JEF FY2023 10-K: header-grid extraction of the "% of Net
    Revenues" summary; the positional-slot failure mode stays dead.

    The FY2022+ summaries interleave Amount and % columns, and the
    Equities/Fixed income rows carry NO "%" marker on their percent
    cells (inline-XBRL tagging inconsistency). The old positional
    slotter leaked percents into the value stream -- equities 2022
    parsed as 17.7 (a percent) and the whole year shifted, landing
    e.g. 2020 -> $1,301M (the 2021 column) and 2022 -> $0.024M. The
    grid-columns mode pins each year to its header column range, so
    the first parseable money cell in the range is the Amount. Values
    verified against the filing's table (in thousands).
    """
    path = _fixture("jef_10k_fy2023.htm.gz")
    recs = parse_filing("JEF", "0000096223", "0000096223-24-000006", path)
    by = _by_line_year(recs)
    assert by[("equities", 2023)] == 1123.477, by
    assert by[("equities", 2022)] == 1054.064, by
    assert by[("equities", 2021)] == 1294.392, by
    assert by[("ficc", 2023)] == 1092.736, by
    assert by[("ficc", 2022)] == 800.492, by
    assert by[("ficc", 2021)] == 984.54, by
    # The fee lines still come from the ASC 606 note.
    assert by[("advisory", 2023)] == 1198.915, by
    assert by[("ecm_dcm_combined", 2023)] == 970.451, by
    print(f"test_jefferies_grid_columns_fy2023: OK ({len(recs)} cells)")


def test_specs_cover_expected_firms():
    # JPM added 2026-10-17: CIB IB-fee and revenue-by-business splits are
    # MD&A-sourced (the XBRL layer carries division totals only).
    assert set(FIRM_SPECS) == {"GS", "MS", "C", "BAC", "JPM", "JEF", "EVR",
                               "LAZ", "MC", "PJT", "HL"}, set(FIRM_SPECS)
    print("test_specs_cover_expected_firms: OK")


def main():
    test_helpers()
    test_specs_cover_expected_firms()
    test_dedupe_prefers_later_filing()
    test_dedupe_cross_prefix_chronology()
    test_missing_table_raises()
    test_gs_ib_and_markets()
    test_gs_fy2019_realignment()
    test_citi_banking_and_markets()
    test_jpm_cib_splits()
    test_ms_institutional_securities()
    test_ms_fy2016_st_gaap()
    test_ms_fy2018_st_by_business()
    test_bac_segments()
    test_bac_fy2016_sales_and_trading()
    test_jefferies_leucadia_backfill()
    test_jefferies_jfg2019_both_specs()
    test_jefferies_grid_columns_fy2023()
    print("ALL REVENUE-MDA TESTS PASS")


if __name__ == "__main__":
    main()
