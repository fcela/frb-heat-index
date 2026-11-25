"""Regression tests for quarterly MD&A sub-split extraction (parse_mda_q).

Uses saved 10-Q main documents in data/revenue/raw/ (git-ignored fixtures;
tests skip if the fixture is absent).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.revenue.parse_mda_q import (  # noqa: E402
    detect_qtd_periods,
    parse_filing_q,
)
from src.revenue.parse_mda import RAW_ROOT  # noqa: E402


def _fixture(cik, accession, pattern):
    d = RAW_ROOT / cik / accession
    if not d.is_dir():
        pytest.skip(f"fixture {accession} not on disk")
    for p in sorted(d.iterdir()):
        if (p.suffix.lower() in (".htm", ".html")
                and p.stat().st_size > 50_000
                and not p.name.startswith("R")
                and pattern in p.name):
            return p
    pytest.skip(f"main doc for {accession} not on disk")


def test_detect_qtd_periods_jpm():
    """JPM 2024 Q2 10-Q: QTD group is (2024, Q2), (2023, Q2)."""
    import warnings
    warnings.filterwarnings("ignore")
    from bs4 import BeautifulSoup
    p = _fixture("0000019617", "0000019617-24-000453", "jpm-20240630")
    soup = BeautifulSoup(p.read_bytes(), "lxml")
    table = None
    for t in soup.find_all("table"):
        txt = t.get_text(" ", strip=True).lower()
        if ("fixed income markets" in txt
                and "three months ended" in txt):
            table = t
            break
    assert table is not None
    assert detect_qtd_periods(table) == [(2024, 2), (2023, 2)]


def test_jpm_quarterly_ficc_equities():
    """JPM 2024Q2 10-Q MD&A: FICC/Equities extracted as QTD values."""
    p = _fixture("0000019617", "0000019617-24-000453", "jpm-20240630")
    recs = parse_filing_q("JPM", "0000019617", "0000019617-24-000453", p)
    by_line = {}
    for r in recs:
        by_line.setdefault(
            (r["taxonomy_line"], r["year"], r["quarter"]),
            r["net_revenue_m"])
    # Values verified against the filing's "Revenue by business" table.
    assert by_line[("ficc", 2024, 2)] == pytest.approx(4822.0)
    assert by_line[("equities", 2024, 2)] == pytest.approx(2971.0)
    assert by_line[("ficc", 2023, 2)] == pytest.approx(4608.0)
    # IB fee splits from the same filing.
    assert by_line[("advisory", 2024, 2)] == pytest.approx(785.0)
    assert by_line[("dcm", 2024, 2)] == pytest.approx(1076.0)


def test_ms_quarterly_splits():
    """MS 2026Q2 10-Q MD&A: IS income-statement splits extracted."""
    p = _fixture("0000895421", "0000895421-26-000212", "ms-20260630")
    recs = parse_filing_q("MS", "0000895421", "0000895421-26-000212", p)
    by_line = {}
    for r in recs:
        by_line.setdefault(
            (r["taxonomy_line"], r["year"], r["quarter"]),
            r["net_revenue_m"])
    assert by_line[("ficc", 2026, 2)] == pytest.approx(2455.0)
    assert by_line[("equities", 2026, 2)] == pytest.approx(6300.0)
    assert by_line[("advisory", 2026, 2)] == pytest.approx(798.0)


def test_gs_quarterly_gb_markets_and_ib_fees():
    """GS 2026Q2 10-Q MD&A (GBM regime): FICC/Equities + IB fee rows.

    Values verified against the filing's GBM results table
    (gs-20260630.htm): current Q2 2026 and comparative Q2 2025 columns.
    """
    p = _fixture("0000886982", "0000886982-26-000297", "gs-20260630")
    recs = parse_filing_q("GS", "0000886982", "0000886982-26-000297", p)
    by_line = {}
    for r in recs:
        by_line.setdefault(
            (r["taxonomy_line"], r["year"], r["quarter"]),
            r["net_revenue_m"])
    assert by_line[("advisory", 2026, 2)] == pytest.approx(1378.0)
    assert by_line[("ecm", 2026, 2)] == pytest.approx(985.0)
    assert by_line[("dcm", 2026, 2)] == pytest.approx(1032.0)
    assert by_line[("ficc", 2026, 2)] == pytest.approx(4592.0)
    assert by_line[("equities", 2026, 2)] == pytest.approx(7416.0)
    # Comparative column: prior-year quarter from the same table.
    assert by_line[("advisory", 2025, 2)] == pytest.approx(1174.0)
    assert by_line[("ficc", 2025, 2)] == pytest.approx(3487.0)
    assert by_line[("equities", 2025, 2)] == pytest.approx(4301.0)


def test_gs_quarterly_restated_comparative_wins():
    """GS Q1 2023 10-Q: the comparative Q1 2022 column carries the
    GBM-restated FICC ($4,730M), not the originally-filed $4,723M.

    This is the restated-vintage preference that keeps a year's Q1-Q3
    on the same presentation basis as the annual sub-split the Q4
    derivation subtracts from (FY2022 10-K restated FICC $14,676M).
    The stale-intermediate regression this pins: Q1 2022 filed as
    $4,723M under the pre-reorg four-segment regime.
    """
    p = _fixture("0000886982", "0000886982-23-000005", "gs-20230331")
    recs = parse_filing_q("GS", "0000886982", "0000886982-23-000005", p)
    by_line = {}
    for r in recs:
        by_line.setdefault(
            (r["taxonomy_line"], r["year"], r["quarter"]),
            r["net_revenue_m"])
    assert by_line[("ficc", 2022, 1)] == pytest.approx(4730.0)
    assert by_line[("equities", 2022, 1)] == pytest.approx(3239.0)
    assert by_line[("advisory", 2022, 1)] == pytest.approx(1127.0)
    # Current quarter from the same filing.
    assert by_line[("ficc", 2023, 1)] == pytest.approx(3931.0)


def test_gs_enumerate_10q_form_type_based():
    """enumerate_10q uses dei:DocumentType, not instance filenames.

    GS's 2019Q2-2022Q2 10-Qs use filing-agent instance names
    (d105283d10q_htm.xml -- no period date); filename heuristics
    dropped them, which is why GS quarterly stopped at 2019Q1. The
    fix must include those 10-Qs, keep post-2022 self-filed 10-Qs,
    and exclude every 10-K.
    """
    from src.revenue.parse_mda import dir_form_type
    from src.revenue.parse_mda_q import enumerate_10q
    accs = enumerate_10q("0000886982")
    # Filing-agent-named 10-Q previously missed by the heuristic.
    assert "0001193125-21-148444" in accs, accs
    # Self-filed 10-Qs.
    assert "0000886982-23-000005" in accs, accs
    assert "0000886982-26-000297" in accs, accs
    # No 10-K may be classified as a 10-Q.
    for acc in accs:
        form = dir_form_type(RAW_ROOT / "0000886982" / acc)
        assert form is not None and form.upper().startswith("10-Q"), \
            (acc, form)


def test_quarterly_dedupe_prefers_later_filing():
    """Overlapping 10-Qs: the later filing's restated value wins."""
    from src.revenue.parse_mda_q import dedupe_records
    recs = [
        {"firm": "JPM", "cik": "x", "taxonomy_line": "ficc",
         "year": 2024, "quarter": 2, "net_revenue_m": 4600.0,
         "source": "mda_q:t", "accession": "0000019617-24-000453"},
        {"firm": "JPM", "cik": "x", "taxonomy_line": "ficc",
         "year": 2024, "quarter": 2, "net_revenue_m": 4822.0,
         "source": "mda_q:t", "accession": "0000019617-24-000999"},
    ]
    out = dedupe_records(recs)
    assert len(out) == 1
    assert out[0]["net_revenue_m"] == pytest.approx(4822.0)


def test_quarterly_dedupe_cross_prefix_chronology():
    """Later filing wins even when the accession string sorts earlier.

    GS files 2019-2022 via an agent (0001193125-*) and 2022Q3+ under
    its own CIK (0000886982-*); the self-filed prefix string-sorts
    first, so a raw-string comparison kept the originally-filed Q1
    2022 FICC ($4,723M) over the Q1 2023 10-Q's restated comparative
    ($4,730M).
    """
    from src.revenue.parse_mda_q import dedupe_records
    recs = [
        {"firm": "GS", "cik": "0000886982", "taxonomy_line": "ficc",
         "year": 2022, "quarter": 1, "net_revenue_m": 4723.0,
         "source": "mda_q:t", "accession": "0001193125-22-134459"},
        {"firm": "GS", "cik": "0000886982", "taxonomy_line": "ficc",
         "year": 2022, "quarter": 1, "net_revenue_m": 4730.0,
         "source": "mda_q:t", "accession": "0000886982-23-000005"},
    ]
    out = dedupe_records(recs)
    assert len(out) == 1
    assert out[0]["net_revenue_m"] == pytest.approx(4730.0)
    assert out[0]["accession"] == "0000886982-23-000005"
