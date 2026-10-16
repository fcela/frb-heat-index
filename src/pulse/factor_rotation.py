"""High-beta minus low-vol 60-day relative strength (daily).

Universe: Invesco S&P 500 High Beta ETF (SPHB) vs Invesco S&P 500 Low
Volatility ETF (SPLV) — the standard liquid high-beta / low-vol factor pair.

Prices come from the Yahoo Finance v8 chart API (public, no key), the same
feed used by the treasury-basis fetcher. stooq.com's daily CSV was evaluated
first but now sits behind a JavaScript proof-of-work challenge and is
unusable from curl.

Construction
------------
    R60(X)[t] = close[t] / close[t-60] - 1      (60-trading-day total return)
    value[t]  = 100 * ( R60(SPHB)[t] - R60(SPLV)[t] )

i.e. the trailing-60-session return spread between high-beta and low-vol, in
percentage points. Positive = high-beta leading (risk-on rotation);
negative = low-vol leading (defensive rotation).

Unit: percentage points. Frequency: daily (trading days).
"""

import datetime
import io
import json
import subprocess

import polars as pl

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=15y&interval=1d"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LOOKBACK = 60


def _curl(url: str, timeout: int = 60) -> bytes:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def _daily_closes(sym: str, timeout: int) -> pl.DataFrame:
    raw = _curl(YAHOO_URL.format(sym=sym), timeout)
    try:
        res = json.loads(raw.decode())["chart"]["result"][0]
    except Exception as e:
        raise RuntimeError(f"Yahoo {sym} parse failed: {e}")
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    return pl.DataFrame(
        {
            "date": [datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date() for t in ts],
            sym: closes,
        }
    ).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col(sym).cast(pl.Float64, strict=False),
    ).drop_nulls(sym).sort("date")


def fetch_factor_rotation(timeout: int = 60) -> pl.DataFrame:
    """Fetch the SPHB-minus-SPLV 60-day return spread.

    Returns pl.DataFrame with columns date (pl.Date) and value (pl.Float64,
    percentage points), sorted ascending. Raises RuntimeError on fetch failure
    or empty output.
    """
    hb = _daily_closes("SPHB", timeout).rename({"SPHB": "hb"})
    lv = _daily_closes("SPLV", timeout).rename({"SPLV": "lv"})
    df = hb.join(lv, on="date", how="inner").sort("date")
    if df.height == 0:
        raise RuntimeError("factor_rotation: no overlapping SPHB/SPLV dates")
    df = df.with_columns(
        ((pl.col("hb") / pl.col("hb").shift(LOOKBACK) - 1.0) * 100.0).alias("r_hb"),
        ((pl.col("lv") / pl.col("lv").shift(LOOKBACK) - 1.0) * 100.0).alias("r_lv"),
    ).with_columns(
        (pl.col("r_hb") - pl.col("r_lv")).alias("value")
    ).drop_nulls("value").select("date", "value")
    df = df.filter(pl.col("value").is_finite())
    if df.height == 0:
        raise RuntimeError("factor_rotation: no finite observations")
    return df
