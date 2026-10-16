"""Regression tests for the MD&A sub-split scraper (src/revenue/parse_mda.py).

Plain-python tests (no pytest): run with `python3 tests/test_revenue_mda.py`.

Fixtures are real 10-K main documents (unedited filing HTML, gzip-compressed):
  tests/fixtures_revenue_mda/gs_10k_fy2023.htm.gz  (Goldman Sachs FY2023 10-K,
      accession 0000886982-24-000006)
  tests/fixtures_revenue_mda/c_10k_fy2023.htm.gz   (Citigroup FY2023 10-K,
      accession 0000831001-24-000033)
Expected values below were read off the filings' MD&A tables; the 2023/2022
columns were cross-checked against the firms' FY2024 10-Ks (restatements are
genuine filing-to-filing revisions, e.g. Citi DCM 2022: 1,100 -> 1,099).
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


def test_specs_cover_expected_firms():
    # JPM is intentionally absent: its FI/Equities split is XBRL-covered.
    assert set(FIRM_SPECS) == {"GS", "MS", "C", "BAC", "JEF", "EVR", "LAZ",
                               "MC", "PJT", "HL"}, set(FIRM_SPECS)
    print("test_specs_cover_expected_firms: OK")


def main():
    test_helpers()
    test_specs_cover_expected_firms()
    test_dedupe_prefers_later_filing()
    test_missing_table_raises()
    test_gs_ib_and_markets()
    test_citi_banking_and_markets()
    print("ALL REVENUE-MDA TESTS PASS")


if __name__ == "__main__":
    main()
