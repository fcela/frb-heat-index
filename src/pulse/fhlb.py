"""Federal Home Loan Bank advances outstanding (system-wide).

Source: FHLB Office of Finance Combined Financial Reports (CFR), filed with
the FHFA: https://cdn.fhlb-of.com/files/{YYYY}/{YYYY}Q{N}CFR.pdf
(There is no EDGAR alternative for the combined figure; each FHLBank files
separately, so the CFR is the only public system aggregate.)

Frequency: quarterly. Unit: USD billions.

Extraction: the MD&A states "Advances totaled $X billion at {date}" in
quarterly CFRs and "Advance balances totaled $X billion at {date}" in the
annual (Q4) CFR — one regex covers both phrasings.

Notes:
- Pre-2020 CFRs live under different URL paths on fhlb-of.com; this fetcher
  does not chase them (documented limitation).
- Unpublished quarters (future or not-yet-filed) are skipped by walking back
  until `quarters_back` successful quarters are collected.
"""

import datetime
import io
import re
import subprocess

import polars as pl

URL_TMPL = "https://cdn.fhlb-of.com/files/{yyyy}/{yyyy}Q{q}CFR.pdf"
UA = "Mozilla/5.0 (research; financial-vulnerability-monitor)"

# Covers "Advances totaled $810.7 billion at June 30, 2026" (quarterly CFR)
# and "Advance balances totaled $676.7 billion at December 31, 2025" (annual).
ADV_RE = re.compile(
    r"Advance[s]?(?: balances)? totaled \$([\d.,]+) billion at "
    r"([A-Z][a-z]+ \d{1,2},?\s+\d{4})"
)
_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}


def _get_pdf(year: int, q: int, timeout: int = 120) -> bytes | None:
    url = URL_TMPL.format(yyyy=year, q=q)
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or len(r.stdout) < 50000:
        return None
    if not r.stdout[:5].startswith(b"%PDF"):
        return None
    return r.stdout


def _parse(pdf_bytes: bytes) -> tuple[str, float]:
    try:
        import pypdf
    except ImportError:
        raise RuntimeError("fhlb: pypdf is required (pip install pypdf)")
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = re.sub(
        r"[ \t]+", " ",
        "\n".join((p.extract_text() or "") for p in reader.pages),
    )
    m = ADV_RE.search(text)
    if not m:
        raise RuntimeError("fhlb: advances figure not found in CFR text")
    amount = float(m.group(1).replace(",", ""))
    dm = re.match(r"([A-Z][a-z]+) (\d{1,2}),?\s+(\d{4})", m.group(2))
    if not dm:
        raise RuntimeError(f"fhlb: unparseable as-of date {m.group(2)!r}")
    date = f"{int(dm.group(3)):04d}-{_MONTHS[dm.group(1)]:02d}-{int(dm.group(2)):02d}"
    if not (0 < amount < 10000) or amount != amount:
        raise RuntimeError(f"fhlb: implausible advances figure {amount}")
    return date, amount


def fetch_advances(quarters_back: int = 8, timeout: int = 120) -> pl.DataFrame:
    """Fetch system-wide FHLB advances for the last `quarters_back` quarters.

    Returns date (as-of quarter-end) / value (USD billions), oldest first.
    Unpublished quarters are skipped.
    """
    today = datetime.date.today()
    q = (today.month - 1) // 3 + 1
    year = today.year
    recs: list[tuple[str, float]] = []
    attempts = 0
    # Allow extra attempts so unpublished quarters are skipped over.
    while len(recs) < quarters_back and attempts < quarters_back + 6:
        pdf = _get_pdf(year, q, timeout=timeout)
        attempts += 1
        if pdf is not None:
            try:
                recs.append(_parse(pdf))
            except RuntimeError:
                pass  # unparseable -> treat as skipped quarter
        q -= 1
        if q == 0:
            q, year = 4, year - 1
    if len(recs) < quarters_back:
        raise RuntimeError(
            f"fhlb: only {len(recs)} quarters parsed, needed {quarters_back}"
        )
    df = pl.DataFrame({"date": [d for d, _ in recs], "value": [v for _, v in recs]})
    df = df.with_columns(
        pl.col("date").str.to_date(), pl.col("value").cast(pl.Float64)
    ).sort("date")
    return df.select("date", "value")
