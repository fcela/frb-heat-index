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
