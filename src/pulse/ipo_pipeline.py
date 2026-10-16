"""Weekly S-1 (IPO registration) filing counts from SEC EDGAR full indexes.

Counts initial Form S-1 registration statements per week from EDGAR's
quarterly master indexes
(https://www.sec.gov/Archives/edgar/full-index/<YYYY>/QTR<Q>/master.gz),
which list every filing accepted by EDGAR. Only the exact form type "S-1"
is counted: amendments (S-1/A), Rule 462(b) takedowns (S-1MEF) and
post-effective amendments are excluded, so the series tracks new
registration statements entering the pipeline rather than follow-on
paperwork for deals already filed.

Methodology choices (documented):
- "S-1" here is the SEC form type, not strictly IPOs: the count includes
  SPAC IPOs, follow-on offerings and selling-shareholder registrations
  filed on Form S-1. It is the standard "S-1 filing count" pipeline metric.
- Filings are counted by EDGAR accession number, not registrant row: one
  S-1 can name dozens of co-registrant subsidiaries (e.g. a restructuring
  S-1 naming ~100 entities under one accession), and counting accessions
  counts registration statements filed.
- Filing date is EDGAR's acceptance date (Date Filed in the index).
- Weeks run Monday-Sunday; the observation date is the Monday (week start).
- The current quarter's master index is cumulative and refreshed daily by
  the SEC, so the trailing week is near-complete; the earliest weeks of a
  fresh quarter rely on the same file and need no daily-index fallback.
- History starts 2015 (master.gz is served back to 2015; earlier quarters
  fall back to the uncompressed master.idx).

Source: SEC EDGAR quarterly master indexes (public, no key). Requires an
identifying User-Agent per SEC fair-access rules; requests are paced.

Frequency: weekly. Unit: filings per week (count).
"""

import datetime as dt
import gzip
import io
import subprocess
import time

import polars as pl

UA = "frb-pulse research (market-pulse-monitor; contact: research@example.com)"
_MASTER_TMPL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/master.gz"
_MASTER_IDX_TMPL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/master.idx"
_START_YEAR = 2015


def _quarter_list(year_start: int, quarters_back: int | None) -> list[tuple[int, int]]:
    """Quarters to fetch: trailing `quarters_back` (incl. current quarter),
    or every quarter from `year_start` when quarters_back is None."""
    today = dt.date.today()
    cur = (today.year, (today.month - 1) // 3 + 1)
    if quarters_back is not None:
        out, y, q = [], cur[0], cur[1]
        for _ in range(quarters_back):
            out.append((y, q))
            q -= 1
            if q == 0:
                q, y = 4, y - 1
        return sorted(out)
    out = []
    for y in range(year_start, cur[0] + 1):
        for q in range(1, 5):
            if (y, q) <= cur:
                out.append((y, q))
    return out


def _curl(url: str, timeout: int) -> bytes:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"EDGAR fetch failed: {url} (curl rc={r.returncode})")
    return r.stdout


def _s1_filings(year: int, q: int, timeout: int) -> list[tuple[str, str]]:
    """(filing date YYYYMMDD, accession) of exact-form-type S-1 rows.

    Accession numbers (from the index file name) de-duplicate multi-registrant
    filings: one S-1 can list dozens of co-registrant subsidiaries (e.g. the
    Valaris restructuring S-1 of 2021-06-11 named ~100 entities under a single
    accession). Counting accessions counts registration statements filed.
    """
    try:
        raw = _curl(_MASTER_TMPL.format(year=year, q=q), timeout)
        text = gzip.decompress(raw).decode("utf-8", errors="replace")
    except Exception:
        # Older quarters may only have the uncompressed index.
        raw = _curl(_MASTER_IDX_TMPL.format(year=year, q=q), timeout)
        text = raw.decode("utf-8", errors="replace")
    if "CIK|Company Name|Form Type|Date Filed|File" not in text:
        raise RuntimeError(f"EDGAR {year}Q{q}: not a master index")
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        # Pipe-delimited: CIK|Company Name|Form Type|Date Filed|Filename.
        # Company names can contain '|', so index from the right. Date Filed
        # is YYYY-MM-DD in quarterly masters (YYYYMMDD in old daily files).
        parts = line.rsplit("|", 4)
        if len(parts) != 5:
            continue
        form, filed = parts[-3].strip(), parts[-2].strip().replace("-", "")
        if form == "S-1" and len(filed) == 8 and filed.isdigit():
            # Filename: edgar/data/<cik>/<accession>-<seq>.txt (or .idx)
            fname = parts[-1].strip().rsplit("/", 1)[-1]
            accession = fname.rsplit(".", 1)[0]
            out.append((filed, accession))
    return out


def fetch_ipo_pipeline(
    quarters_back: int | None = 4,
    year_start: int = _START_YEAR,
    timeout: int = 120,
    pace: float = 0.4,
) -> pl.DataFrame:
    """Weekly initial S-1 filing counts.

    Default fetches the trailing 4 quarters (incl. the current partial
    quarter) — the cheap incremental refresh; pass quarters_back=None for
    the full history back to `year_start`. Returns date (week-start Monday,
    pl.Date) / value (filings that week, float), sorted ascending.
    """
    quarters = _quarter_list(year_start, quarters_back)
    filings: list[tuple[str, str]] = []
    failed: list[str] = []
    for i, (y, q) in enumerate(quarters):
        try:
            filings.extend(_s1_filings(y, q, timeout))
        except RuntimeError:
            failed.append(f"{y}Q{q}")
        if i < len(quarters) - 1:
            time.sleep(pace)
    if not filings:
        raise RuntimeError(f"EDGAR S-1 fetch failed for all quarters: {failed}")
    df = (
        pl.DataFrame({"filed": [f[0] for f in filings], "acc": [f[1] for f in filings]})
        .with_columns(pl.col("filed").str.to_date("%Y%m%d").alias("date"))
        .with_columns(
            # Monday of the filing week.
            (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week")
        )
        .group_by("week")
        .agg(pl.col("acc").n_unique().alias("value"))
        .rename({"week": "date"})
        .with_columns(pl.col("value").cast(pl.Float64))
        .sort("date")
    )
    # The trailing (current) week is preliminary — the incremental merge
    # keeps the last value per date, so next week's run revises it upward.
    vals = df["value"].to_list()
    if any(v != v or v == float("inf") or v == float("-inf") for v in vals):
        raise RuntimeError("EDGAR S-1: non-finite weekly count")
    return df


if __name__ == "__main__":
    out = fetch_ipo_pipeline(quarters_back=None)
    print(out.head(3))
    print(out.tail(3))
    print("n =", out.height)
