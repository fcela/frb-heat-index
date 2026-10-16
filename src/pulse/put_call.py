"""CBOE equity put/call ratio (daily).

Cboe's free historical CSV
(https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv)
is frozen at 2019-10-04, but Cboe's daily market-statistics page accepts a
?dt=YYYY-MM-DD parameter and server-renders that day's ratios into the HTML,
so the current era is scraped day-by-day:

  2006-11-01 -> 2019-10-04 : CDN equitypc.csv ("P/C Ratio" column)
  2019-10-05 -> present    : daily page scrape (?dt=), same publication

The definitions match exactly: the Cboe (mkt=cone) equity-option put/call
volume ratio. Per the CSV's own disclaimer, equity volume excludes
exchange-traded products (as of 2012-06-11); the daily page reports EQUITY
and EXCHANGE TRADED PRODUCTS as separate ratios, and this fetcher takes the
EQUITY one.

Cross-checks (page value vs independently published CBOE Equity Put/Call
Ratio history): 2026-10-12 = 0.62, 2026-10-13 = 0.54, 2026-10-14 = 0.61,
2026-10-05 = 0.59, 2026-10-06 = 0.57 — all match.

Non-trading days (weekends/holidays) render a page with no ratio table and
are skipped. The full backfill issues ~2,500 small requests (a few minutes
unthrottled; Cboe's CDN slows sustained bursts, so budget ~15-20 min); pass
scrape_since=last_committed_date (or run the build with --incremental) to
make routine refreshes fetch only new days.

Unit: ratio. Frequency: daily (trading days).
"""

import concurrent.futures
import datetime
import io
import re
import subprocess

import polars as pl

CDN_CSV = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv"
DAILY_URL = "https://www.cboe.com/markets/us/options/market-statistics/daily?mkt=cone&dt={dt}"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
FREEZE_END = datetime.date(2019, 10, 4)  # last date in the CDN CSV
_RATIO_RE = re.compile(r"EQUITY PUT/CALL RATIO</td><td[^>]*>([0-9.]+)")


def _curl(url: str, timeout: int = 45) -> bytes:
    r = subprocess.run(
        ["curl", "-s", "--compressed", "-L", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def _fetch_frozen() -> pl.DataFrame:
    raw = _curl(CDN_CSV)
    lines = raw.decode(errors="replace").splitlines()
    # Skip the 2-line preamble (disclaimer + product/exchange row).
    df = pl.read_csv(io.StringIO("\n".join(lines[2:])))
    date_col = df.columns[0]
    ratio_col = [c for c in df.columns if "P/C" in c][0]
    return (
        df.select(
            pl.col(date_col).str.to_date("%m/%d/%Y").alias("date"),
            pl.col(ratio_col).cast(pl.Float64, strict=False).alias("value"),
        )
        .drop_nulls("value")
        .sort("date")
    )


def _fetch_day(day: datetime.date, timeout: int) -> tuple[datetime.date, float] | None:
    try:
        html = _curl(DAILY_URL.format(dt=day.isoformat()), timeout).decode(errors="replace")
    except RuntimeError:
        return None
    m = _RATIO_RE.search(html)
    if not m:
        return None  # weekend / holiday: no ratio table rendered
    return day, float(m.group(1))


def _fetch_scraped(
    timeout: int, workers: int = 10, since: datetime.date | None = None
) -> pl.DataFrame:
    start = max(FREEZE_END + datetime.timedelta(days=1), since or datetime.date.min)
    end = datetime.date.today()
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]
    recs: list[tuple[datetime.date, float]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_day, d, timeout): d for d in days}
        for fut in concurrent.futures.as_completed(futs):
            rec = fut.result()
            if rec is not None:
                recs.append(rec)
    if not recs:
        raise RuntimeError("put_call: daily-page scrape returned no rows")
    return (
        pl.DataFrame({"date": [r[0] for r in recs], "value": [r[1] for r in recs]})
        .with_columns(pl.col("date").cast(pl.Date), pl.col("value").cast(pl.Float64))
        .sort("date")
    )


def fetch_put_call(
    timeout: int = 45, scrape_since: datetime.date | None = None
) -> pl.DataFrame:
    """Fetch the CBOE equity put/call ratio, frozen history + daily scrape.

    Returns pl.DataFrame with columns date (pl.Date) and value (pl.Float64),
    sorted ascending. Raises RuntimeError on fetch failure or empty output.

    scrape_since: when set, the daily-page scrape starts at that date instead
    of 2019-10-05 (the frozen CSV still covers 2006-11-01 -> 2019-10-04, so
    passing the last committed date makes routine incremental refreshes fast
    while a full backfill re-scrapes ~2,500 pages).
    """
    frozen = _fetch_frozen()
    if frozen.height == 0:
        raise RuntimeError("put_call: frozen CBOE CSV parsed to zero rows")
    scraped = _fetch_scraped(timeout, since=scrape_since)
    df = (
        pl.concat([frozen, scraped])
        .unique("date", keep="last")
        .filter(pl.col("value").is_finite())
        .sort("date")
    )
    if df.height == 0:
        raise RuntimeError("put_call: no finite observations")
    return df
