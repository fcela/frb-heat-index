"""US IPO + follow-on (secondary) issuance volume, quarterly (SIFMA).

SIFMA Research's free xls download for the US Equity statistics is gated
behind a HubSpot form (no stable direct file URL), so this fetcher parses the
quarterly "Research Quarterly: Equities & Related" PDFs instead. Each report's
Table 1 ("Primary Markets (Issuance, $B)") prints Total Equity, Secondaries,
Preferreds, IPOs and SPACs for [this quarter, prior quarter, same quarter
prior year]; the indicator is

    value[Q] = IPOs[Q] + Secondaries[Q]        (USD billions)

i.e. gross primary equity supply excluding preferreds and SPACs, matching
SIFMA's "Total Equity (ex-SPACs)" = Secondaries + Preferreds + IPOs split.

Sources
-------
- Current reports: https://www.sifma.org/research/statistics/research-quarterly-equity-and-related
  (lists the five most recent quarterlies; URLs follow the stable pattern
  .../wp-content/uploads/YYYY/MM/SIFMA-Research-Quarterly-Equity-and-Related-NQYY.pdf)
- Older reports (1Q25, 4Q24, 2Q24): same URL pattern, discovered by probing
  (the index page only lists the five most recent).

Revision rule: a quarter that appears in several reports takes the NEWEST
report's figure (SIFMA revises; e.g. 1Q25 total equity was 56.6 in the 1Q25
report and 59.1 in the 2Q25 report).

Coverage: 2Q23-2Q26 (3Q23 falls in no fetched report's three-quarter window).
Requires the `pypdf` package (pure Python) for PDF text extraction.

Unit: USD billions. Frequency: quarterly (quarter-start dates).
"""

import datetime
import io
import re
import subprocess

import polars as pl

INDEX_URL = "https://www.sifma.org/research/statistics/research-quarterly-equity-and-related"
PDF_URL_TMPL = "https://www.sifma.org/wp-content/uploads/{ym}/SIFMA-Research-Quarterly-Equity-and-Related-{q}.pdf"
# (upload YYYY/MM, report label) for reports the index page no longer lists.
ARCHIVE_REPORTS = [
    ("2025/01", "1Q25"),
    ("2024/10", "4Q24"),
    ("2024/04", "2Q24"),
]
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_Q_RE = re.compile(r"([1-4])Q(\d{2})")
_LINK_RE = re.compile(r"/wp-content/uploads/(\d{4}/\d{2})/SIFMA-Research-Quarterly-Equity-and-Related-([1-4]Q\d{2})\.pdf")
_ROW_RE = re.compile(r"^(Secondaries|IPOs)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", re.M)


def _curl(url: str, timeout: int = 90, binary: bool = False):
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
        text=not binary,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def _quarter_start(label: str) -> datetime.date:
    m = _Q_RE.fullmatch(label)
    q, yy = int(m.group(1)), int(m.group(2))
    year = 2000 + yy
    return datetime.date(year, 3 * q - 2, 1)


def _add_months(d: datetime.date, n: int) -> datetime.date:
    m = d.month - 1 + n
    return datetime.date(d.year + m // 12, m % 12 + 1, 1)


def _parse_report(pdf_bytes: bytes, label: str) -> dict[datetime.date, float]:
    """Return {quarter-start date: IPO+Secondaries $B} from one report."""
    try:
        import pypdf
    except ImportError:
        raise RuntimeError("equity_issuance: pypdf is required for PDF parsing")
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    i = text.find("Primary Markets (Issuance")
    if i < 0:
        raise RuntimeError(f"equity_issuance: Table 1 not found in {label} report")
    section = text[i : i + 2000]
    vals: dict[str, list[float]] = {}
    for name, a, b, c in _ROW_RE.findall(section):
        vals[name] = [float(x.replace(",", "")) for x in (a, b, c)]
    if "Secondaries" not in vals or "IPOs" not in vals:
        raise RuntimeError(f"equity_issuance: issuance rows not parsed in {label} report")
    this_q = _quarter_start(label)
    quarters = [this_q, _add_months(this_q, -3), _add_months(this_q, -12)]
    return {
        q: vals["IPOs"][k] + vals["Secondaries"][k]
        for k, q in enumerate(quarters)
    }


def fetch_equity_issuance(timeout: int = 90) -> pl.DataFrame:
    """Fetch quarterly US IPO + follow-on issuance volume.

    Returns pl.DataFrame with columns date (pl.Date, quarter-start) and value
    (pl.Float64, USD billions), sorted ascending. Raises RuntimeError on fetch
    failure or empty output.
    """
    index_html = _curl(INDEX_URL, timeout)
    links = _LINK_RE.findall(index_html)
    reports = [(ym, q) for ym, q in links] + ARCHIVE_REPORTS
    # Newest report first so its (revised) figures win per quarter.
    reports = sorted(set(reports), key=lambda r: _quarter_start(r[1]), reverse=True)
    if not reports:
        raise RuntimeError("equity_issuance: no SIFMA quarterly reports discovered")

    combined: dict[datetime.date, float] = {}
    for ym, label in reports:
        url = PDF_URL_TMPL.format(ym=ym, q=label)
        try:
            pdf = _curl(url, timeout, binary=True)
        except RuntimeError:
            continue  # a listed report that no longer downloads; skip
        if isinstance(pdf, str) or len(pdf) < 50000:
            continue
        for qdate, val in _parse_report(pdf, label).items():
            combined.setdefault(qdate, val)

    df = (
        pl.DataFrame({"date": list(combined.keys()), "value": list(combined.values())})
        .with_columns(pl.col("date").cast(pl.Date), pl.col("value").cast(pl.Float64))
        .filter(pl.col("value").is_finite())
        .sort("date")
    )
    if df.height == 0:
        raise RuntimeError("equity_issuance: no finite observations")
    return df
