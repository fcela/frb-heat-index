"""Yahoo Finance v8 chart API fetcher for the Pulse of the Market section.

Prices come from the Yahoo Finance v8 chart API (public, no key).
Closes are the unadjusted quote closes, the repository's standing
convention; dividends are not reinvested, and the choice is
documented on the section's methodology page.

Only completed sessions are returned: any bar dated today in
America/New_York is dropped, because before the close it is a partial
bar (the builder normally runs at 06:30 UTC, before the US open, but a
manual mid-session run must not archive an intraday print as a close).
"""

import datetime
import json
import subprocess
from zoneinfo import ZoneInfo

import polars as pl

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    "?range=15y&interval=1d"
)
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_ET = ZoneInfo("America/New_York")


def _curl(url: str, timeout: int = 60) -> bytes:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def fetch_daily_closes(sym: str, timeout: int = 60, transport=None) -> pl.DataFrame:
    """Daily unadjusted closes for one ticker, completed sessions only.

    Returns pl.DataFrame with columns date (pl.Date) and value
    (pl.Float64), sorted ascending. `transport(url, timeout=...) -> bytes`
    is injectable so tests run against fixtures with zero live calls.
    Raises RuntimeError on fetch/parse failure or empty output.
    """
    raw = (transport or _curl)(YAHOO_URL.format(sym=sym), timeout)
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        res = json.loads(raw)["chart"]["result"][0]
    except Exception as e:
        raise RuntimeError(f"Yahoo {sym} parse failed: {e}")
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    today_et = datetime.datetime.now(_ET).date()
    rows = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()
        if d >= today_et:
            continue  # today's bar is incomplete before the close
        rows.append((d, float(c)))
    if not rows:
        raise RuntimeError(f"Yahoo {sym}: no completed sessions returned")
    df = pl.DataFrame(
        {"date": [r[0] for r in rows], "value": [r[1] for r in rows]},
        schema={"date": pl.Date, "value": pl.Float64},
    ).unique("date", keep="last").sort("date")
    df = df.filter(pl.col("value").is_finite())
    if df.height == 0:
        raise RuntimeError(f"Yahoo {sym}: no finite closes")
    return df
