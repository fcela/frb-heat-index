"""Corporate bond ETF price dislocation proxy (LQD and HYG).

Measures: daily percent deviation of the ETF's market price from its trailing
20-day moving average, in percent: (close - MA20) / MA20 * 100. Two series:
``lqd_discount`` (iShares iBoxx $ Investment Grade Corporate Bond ETF, LQD)
and ``hyg_discount`` (iShares iBoxx $ High Yield Corporate Bond ETF, HYG).

Source (public, no auth): Yahoo Finance v8 chart API daily closes,
  https://query1.finance.yahoo.com/v8/finance/chart/LQD
  https://query1.finance.yahoo.com/v8/finance/chart/HYG

Why a proxy: the textbook dislocation is (market price - NAV) / NAV, but no
public source publishes daily NAV history in machine-readable form. iShares'
fund-data JSON endpoint (the historical *.ajax?tab=all&fileType=json pattern)
now returns HTTP 404, and the iShares site bot-walls curl, so the NAV leg is
unreachable without a paid feed. The price-vs-trailing-mean proxy captures the
same stress episodes that discounts do (e.g. March 2020, when LQD/HYG traded
at steep discounts to NAV and mean-reverted): an ETF trading far below its own
recent average price is the observable footprint of a price/NAV dislocation.
Documented as a proxy, not the accounting discount.

Frequency: daily (trading days). Unit: percent.
"""

import datetime
import json
import subprocess

import polars as pl

_YAHOO_TMPL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_MA_WINDOW = 20


def _fetch_closes(sym: str, timeout: int = 60) -> pl.DataFrame:
    url = _YAHOO_TMPL.format(sym=sym)
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"Yahoo fetch failed for {sym}: curl rc={r.returncode}")
    try:
        res = json.loads(r.stdout.decode())["chart"]["result"][0]
    except Exception as e:
        raise RuntimeError(f"Yahoo {sym} parse failed: {e}")
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    df = pl.DataFrame(
        {
            "date": [
                datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()
                for t in ts
            ],
            "close": closes,
        }
    ).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("close").cast(pl.Float64, strict=False),
    ).drop_nulls("close").sort("date")
    if df.height == 0:
        raise RuntimeError(f"Yahoo {sym}: no close observations")
    return df


def _dislocation(df: pl.DataFrame) -> pl.DataFrame:
    out = df.with_columns(
        pl.col("close").rolling_mean(window_size=_MA_WINDOW).alias("ma20")
    ).drop_nulls("ma20").with_columns(
        ((pl.col("close") - pl.col("ma20")) / pl.col("ma20") * 100.0).alias("value")
    ).select("date", "value")
    out = out.filter(pl.col("value").is_finite())
    if out.height == 0:
        raise RuntimeError("etf dislocation: no finite observations")
    return out


def fetch_lqd_dislocation(timeout: int = 60) -> pl.DataFrame:
    """LQD price dislocation proxy: (close - 20d MA)/20d MA * 100, daily %."""
    return _dislocation(_fetch_closes("LQD", timeout))


def fetch_hyg_dislocation(timeout: int = 60) -> pl.DataFrame:
    """HYG price dislocation proxy: (close - 20d MA)/20d MA * 100, daily %."""
    return _dislocation(_fetch_closes("HYG", timeout))
