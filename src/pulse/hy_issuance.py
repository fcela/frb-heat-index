"""Quarterly US high-yield corporate bond issuance (SIFMA Research).

SIFMA Research publishes a free quarterly "US Fixed Income Markets -
Issuance & Trading" report. The monthly issuance Excel is behind a lead-gen
form, so this fetcher instead reads each quarter's report PDF: the
"Corporates: Review of Quarterly Statistics" section gives total corporate
bond issuance for the quarter and the issuance-by-type split
(IG / HY / Private Placement / Convertible). HY issuance = total x HY share.

Methodology choices (documented):
- Long-term securities only (maturity >= 13 months), per SIFMA's definition.
- HY share is the quarter's issuance-type split, not trading; the first
  HY percentage after the corporate issuance headline is used (the trading
  split appears later in the section and is ignored).
- Report URLs: the naming stabilized in 3Q23 to
  SIFMA-Research-Quarterly-Fixed-Income-IT-{N}Q{YY}.pdf under
  /{YYYY}/{01,04,07,10}/; earlier quarters and the 3Q24 re-issue use
  explicit URLs (discovered via the web archive's CDX index). Each PDF is
  checked to contain the expected quarter label before its numbers are used.
- Quarterly frequency: SIFMA does not publish a monthly HY series, so this
  indicator is quarterly (first of the pulse's IB block at that cadence).

Source: SIFMA Research quarterly reports (public PDFs, no key).
https://www.sifma.org/research/statistics/research-quarterly-fixed-income-issuance-and-trading

Frequency: quarterly. Unit: USD billions. Date: quarter-end.
"""

import datetime as dt
import io
import re
import subprocess
import time

import polars as pl

import pypdf

_BASE = "https://www.sifma.org/wp-content/uploads"

# Explicit URLs for quarters whose naming predates the stable convention
# (plus the 3Q24 re-issue). Discovered via web.archive.org CDX; verified live.
_EXPLICIT_URLS = {
    (2020, 2): f"{_BASE}/2020/07/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2020-07-16-SIFMA.pdf",
    (2020, 3): f"{_BASE}/2020/07/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2020-10-13-SIFMA.pdf",
    (2020, 4): f"{_BASE}/2021/08/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2021-01-15-SIFMA.pdf",
    (2021, 1): f"{_BASE}/2021/01/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2021-04-13-SIFMA.pdf",
    (2021, 2): f"{_BASE}/2021/07/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2021-07-19-SIFMA.pdf",
    (2021, 3): f"{_BASE}/2021/07/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2021-10-15-SIFMA.pdf",
    (2021, 4): f"{_BASE}/2021/12/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2022-01-26-SIFMA.pdf",
    (2022, 1): f"{_BASE}/2022/01/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2022-04-26-SIFMA.pdf",
    (2022, 2): f"{_BASE}/2022/07/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2022-07-18-SIFMA-1.pdf",
    (2022, 3): f"{_BASE}/2022/10/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2022-10-19-SIFMA.pdf",
    (2022, 4): f"{_BASE}/2022/10/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2023-02-22-SIFMA.pdf",
    (2023, 1): f"{_BASE}/2023/04/US-Research-Quarterly-Fixed-Income-Issuance-Trading-2023-04-27-SIFMA.pdf",
    (2023, 2): f"{_BASE}/2023/04/SIFMA-Research-Quarterly-Fixed-Income-Issuance-and-Trading-2Q23.pdf",
    (2023, 3): f"{_BASE}/2023/08/SIFMA-Research-Quarterly-Fixed-Income-Issuance-and-Trading-3Q23.pdf",
    (2024, 3): f"{_BASE}/2024/07/Research-Quarterly-Fixed-Income_IT-3Q24-v2.pdf",
}

_TMPL = _BASE + "/{y}/{mm}/SIFMA-Research-Quarterly-Fixed-Income-IT-{q}Q{yy}.pdf"
_QTR_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_UA = "Mozilla/5.0"


def _url_for(year: int, q: int) -> str:
    if (year, q) in _EXPLICIT_URLS:
        return _EXPLICIT_URLS[(year, q)]
    mm = {1: "01", 2: "04", 3: "07", 4: "10"}[q]
    return _TMPL.format(y=year, mm=mm, q=q, yy=str(year)[2:])


def _extract_quarter(pdf_bytes: bytes, year: int, q: int) -> tuple[float, float]:
    """Return (total corporate issuance $B, HY share %) for the quarter."""
    text = ""
    for page in pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages:
        text += (page.extract_text() or "") + "\n"
    label = f"{q}Q{str(year)[2:]}"
    if label not in text:
        raise RuntimeError(f"SIFMA {label}: quarter label not found in PDF")
    headers = [
        "Corporates: Review of Quarterly Statistics",  # 2023+ format
        "Chart Book: Corporate Bonds (Corporates)",  # 2020-2022 format
    ]
    for hdr in headers:
        for m in re.finditer(re.escape(hdr), text):
            seg = text[m.start(): m.start() + 2200]
            mi = re.search(r"Issuance:[^$\n]{0,40}\$([0-9.,]+)B", seg)
            if not mi:
                continue
            # HY issuance-type split chart directly follows the headline;
            # bound the search so a later section's numbers can't leak in.
            mh = re.search(r"HY\s+([0-9]+(?:\.[0-9]+)?)%", seg[mi.end(): mi.end() + 800])
            if not mh:
                continue
            total = float(mi.group(1).replace(",", ""))
            share = float(mh.group(1))
            if not (50.0 <= total <= 3000.0 and 0.5 <= share <= 60.0):
                raise RuntimeError(
                    f"SIFMA {label}: implausible extraction total={total} HY%={share}"
                )
            return total, share
    raise RuntimeError(f"SIFMA {label}: corporate issuance block not parsed")


def _fetch_pdf(year: int, q: int, timeout: int) -> bytes:
    url = _url_for(year, q)
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or len(r.stdout) < 50000:
        raise RuntimeError(f"SIFMA {year}Q{q}: download failed ({url})")
    if not r.stdout.startswith(b"%PDF"):
        raise RuntimeError(f"SIFMA {year}Q{q}: not a PDF ({url})")
    return r.stdout


def _trailing_quarters(n: int) -> list[tuple[int, int]]:
    """Last `n` calendar quarters ending before the current quarter."""
    today = dt.date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            q, y = 4, y - 1
        out.append((y, q))
    return sorted(out)


def fetch_hy_issuance(
    quarters_back: int = 8,
    timeout: int = 90,
    pace: float = 0.5,
) -> pl.DataFrame:
    """Quarterly US HY corporate bond issuance, USD billions.

    Fetches the trailing `quarters_back` published SIFMA quarterly reports
    (unpublished/future quarters are skipped). Returns date (quarter-end,
    pl.Date) / value (USD billions), sorted ascending.
    """
    rows: list[tuple[dt.date, float]] = []
    for i, (y, q) in enumerate(_trailing_quarters(quarters_back + 2)):
        try:
            pdf = _fetch_pdf(y, q, timeout)
            total, share = _extract_quarter(pdf, y, q)
        except RuntimeError:
            continue  # unpublished quarter or renamed file; skip
        if i:
            time.sleep(pace)
        m, d = _QTR_END[q]
        rows.append((dt.date(y, m, d), round(total * share / 100.0, 2)))
    if not rows:
        raise RuntimeError("SIFMA HY issuance: no quarters extracted")
    df = (
        pl.DataFrame({"date": [r[0] for r in rows], "value": [r[1] for r in rows]})
        .with_columns(pl.col("date").cast(pl.Date), pl.col("value").cast(pl.Float64))
        .sort("date")
    )
    vals = df["value"].to_list()
    if any(v != v or v in (float("inf"), float("-inf")) for v in vals):
        raise RuntimeError("SIFMA HY issuance: non-finite value")
    return df


def fetch_hy_issuance_history(
    year_start: int = 2020, timeout: int = 90, pace: float = 0.5
) -> pl.DataFrame:
    """Full history from `year_start` (one-shot backfill; raises on failure)."""
    today = dt.date.today()
    quarters = [
        (y, q)
        for y in range(year_start, today.year + 1)
        for q in (1, 2, 3, 4)
        if not (y == 2020 and q == 1)  # series starts 2Q20
        and dt.date(y, *_QTR_END[q]) < today
    ]
    rows = []
    skipped: list[str] = []
    for i, (y, q) in enumerate(quarters):
        qend = dt.date(y, *_QTR_END[q])
        is_recent = i >= len(quarters) - 2  # SIFMA publishes ~6 weeks after quarter-end
        try:
            pdf = _fetch_pdf(y, q, timeout)
            total, share = _extract_quarter(pdf, y, q)
        except RuntimeError as e:
            if is_recent:
                skipped.append(f"{y}Q{q}")
                continue
            raise
        m, d = _QTR_END[q]
        rows.append((dt.date(y, m, d), round(total * share / 100.0, 2)))
        if i < len(quarters) - 1:
            time.sleep(pace)
    if skipped:
        print(f"note: unpublished quarters skipped: {skipped}")
    return (
        pl.DataFrame({"date": [r[0] for r in rows], "value": [r[1] for r in rows]})
        .with_columns(pl.col("date").cast(pl.Date), pl.col("value").cast(pl.Float64))
        .sort("date")
    )
