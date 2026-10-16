"""CAPE z-score vs trailing 10-year mean (monthly).

The canonical source, Robert Shiller's ie_data.xls at econ.yale.edu, is
stale: the file's Last-Modified is 2023-10-17, i.e. frozen for three years as
of 2026-10, and the xlsx variant is unreachable. This module therefore uses
multpl.com's monthly Shiller-PE table — a public, current republication of
the Shiller CAPE series (S&P price divided by the 10-year average of real
earnings) — and computes the indicator from it.

Construction
------------
    z[t] = (CAPE[t] - mean(CAPE[t-119 .. t])) / stdev(CAPE[t-119 .. t])

trailing 120-month window, inclusive of the current month, sample standard
deviation. The z-score is chosen over a plain ratio so the unit is
scale-free across valuation regimes.

Source: Shiller CAPE via https://www.multpl.com/shiller-pe/table/by-month
(monthly; history back to 1871; current through the latest month).
Unit: z-score. Frequency: monthly (month-start dates). Series starts 120
months after the CAPE history begins.
"""

import datetime
import re
import subprocess

import polars as pl

MULTPL_URL = "https://www.multpl.com/shiller-pe/table/by-month"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
WINDOW_MONTHS = 120

_ROW_RE = re.compile(
    r"<tr class=\"(?:odd|even)\">\s*<td>([A-Za-z]{3} \d{1,2}, \d{4})</td>\s*"
    r"<td>\s*&#x2002;\s*\n?([0-9.]+)\s*</td>",
)


def _fetch_table(timeout: int = 60) -> list[tuple[str, str]]:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, MULTPL_URL],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"multpl fetch failed: curl rc={r.returncode}")
    rows = _ROW_RE.findall(r.stdout)
    if not rows:
        raise RuntimeError("multpl: no table rows parsed (page format changed?)")
    return rows


def fetch_cape_zscore(timeout: int = 60) -> pl.DataFrame:
    """Fetch CAPE and z-score it against its trailing 10-year mean.

    Returns pl.DataFrame with columns date (pl.Date, month-start) and value
    (pl.Float64, z-score), sorted ascending. Raises RuntimeError on fetch
    failure or empty output.
    """
    rows = _fetch_table(timeout)
    recs = []
    for datestr, val in rows:
        dt = datetime.datetime.strptime(datestr, "%b %d, %Y").date()
        recs.append((dt.replace(day=1), float(val)))
    df = (
        pl.DataFrame({"date": [r[0] for r in recs], "cape": [r[1] for r in recs]})
        .with_columns(pl.col("date").cast(pl.Date), pl.col("cape").cast(pl.Float64))
        .unique("date", keep="last")
        .sort("date")
    )
    df = df.with_columns(
        (
            (pl.col("cape") - pl.col("cape").rolling_mean(WINDOW_MONTHS))
            / pl.col("cape").rolling_std(WINDOW_MONTHS)
        ).alias("value")
    ).drop_nulls("value").select("date", "value")
    df = df.filter(pl.col("value").is_finite())
    if df.height == 0:
        raise RuntimeError("cape_zscore: no finite observations")
    return df
