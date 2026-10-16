"""MOVE-index proxy: 21-day realized volatility of the 10Y Treasury yield.

The ICE BofA MOVE index (one-month implied volatility of Treasury options) is
a licensed product; no free daily series exists. This module builds the
standard public fallback: annualized realized volatility of daily changes in
the 10-year constant-maturity Treasury yield (FRED DGS10), in basis points.

Construction
------------
    value[t] = stdev( DGS10[t-20 .. t] day-over-day changes, in bp ) * sqrt(252)

i.e. the trailing 21-trading-day (~1 month) sample standard deviation of
daily yield changes, annualized to basis points. The 21-day window is chosen
to match MOVE's one-month horizon.

Limitations vs the true MOVE index
----------------------------------
- Realized, not implied: measures what happened, not what the options market
  expects; it omits the volatility risk premium (implied usually exceeds
  realized).
- Single tenor (10Y) vs MOVE's 2Y/5Y/10Y/30Y swaption strip.
- Built from yield changes, not forward-rate volatility; level effects and
  the term structure of vol are ignored.

Source: FRED DGS10 (10-Year Treasury Constant Maturity Rate), daily, percent.
Unit: basis points (annualized). Frequency: daily (trading days).
"""

import polars as pl

from src.pulse import fred

WINDOW_DAYS = 21
TRADING_DAYS_PER_YEAR = 252


def fetch_move_index_proxy(timeout: int = 60) -> pl.DataFrame:
    """Fetch the MOVE realized-vol proxy.

    Returns pl.DataFrame with columns date (pl.Date) and value (pl.Float64,
    annualized basis points), sorted ascending. Raises RuntimeError on fetch
    failure or empty output.
    """
    dgs = fred.fetch_fred_csv("DGS10", timeout=timeout)
    df = dgs.with_columns(
        (pl.col("value").diff() * 100.0).alias("d_bp")  # daily change, bp
    ).with_columns(
        (pl.col("d_bp").rolling_std(WINDOW_DAYS) * (TRADING_DAYS_PER_YEAR**0.5)).alias("value")
    ).drop_nulls("value").select("date", "value").sort("date")
    df = df.filter(pl.col("value").is_finite())
    if df.height == 0:
        raise RuntimeError("move_index: no finite observations")
    return df
