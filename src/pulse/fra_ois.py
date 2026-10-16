"""Forward funding spread: 1m-forward-2m AA financial CP minus 1m-forward-2m T-bill.

Concept: the "3Mx3M FRA-OIS spread" -- the market's forward expectation of
private funding rates versus the risk-free rate. No free FRA or forward-OIS
feed exists, so both legs are implied from public FRED curves:

  fwd(r_short, r_long) = ((1 + r_long*d_long/360) / (1 + r_short*d_short/360) - 1)
                         * 360 / (d_long - d_short)      (annualized %)

  * CP leg: DCPF1M (30-day) and DCPF3M (90-day) AA financial CP -> the 60-day
    rate starting in 30 days. Financial CP is the bank/dealer funding leg,
    and the forward embeds the AA credit/liquidity premia that drive FRA-OIS.
  * T-bill leg: DGS1MO and DGS3MO -> the same-tenor forward risk-free rate.

Deviations from a traded 3Mx3M FRA-OIS, stated honestly: tenor is 1m2m not
3m3m (FRED has no 6M CP or 4M bill to build 3m3m); T-bill stands in for OIS
(no public forward OIS); CP forward stands in for FRA (no public FRA feed).
The economics match: forward private funding rate minus forward risk-free
rate, i.e. forward funding-stress expectations.

A Treasury-implied 3m-forward-3m minus SOFR-average alternative was built
and REJECTED: it printed -3bp at the March 2020 peak (wrong sign in stress)
because a Treasury forward contains no bank-credit component and collapses
when the Fed cuts. The CP-forward construction spikes in every historical
stress episode: +444bp on 2008-10-15 (GFC), +283bp on 2020-03-25 (COVID),
+18bp on 2023-03-17 (SVB, correctly modest).

Limitations: tenor mismatch vs 3m3m; T-bill convenience premia leak into the
bill leg; simple money-market annualization on both legs (a few bp of
day-count noise); currently about -15bp (forward bills above forward CP,
mirroring the spot inversion). Consider renaming the key to ``fwd_cp_bill``.

Source: FRED (Board H.15 CP rates; Treasury constant-maturity via DGS).
Units: percentage points, daily business days. History from 2001-07-31
(DGS1MO start).
"""

from src.pulse import fred

import polars as pl


def _forward_1m2m(df: pl.DataFrame, short_col: str, long_col: str) -> pl.DataFrame:
    """Implied 60-day rate starting in 30 days from 30d/90d spot rates (pct)."""
    return df.with_columns(
        (
            (
                (1 + pl.col(long_col) / 100 * 90 / 360)
                / (1 + pl.col(short_col) / 100 * 30 / 360)
                - 1
            )
            * (360 / 60)
            * 100
        ).alias("fwd")
    ).select(["date", "fwd"])


def fetch_fra_ois() -> pl.DataFrame:
    """Fetch the 1m-forward-2m CP minus T-bill spread. date/value (pp), ascending."""
    try:
        cp1m = fred.fetch_fred_csv("DCPF1M")
        cp3m = fred.fetch_fred_csv("DCPF3M")
        t1m = fred.fetch_fred_csv("DGS1MO")
        t3m = fred.fetch_fred_csv("DGS3MO")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"FRA/OIS inputs fetch failed: {e}")
    fwd_cp = _forward_1m2m(
        cp1m.rename({"value": "cp1m"}).join(cp3m.rename({"value": "cp3m"}), on="date", how="inner"),
        "cp1m",
        "cp3m",
    ).rename({"fwd": "fwd_cp"})
    fwd_tr = _forward_1m2m(
        t1m.rename({"value": "t1m"}).join(t3m.rename({"value": "t3m"}), on="date", how="inner"),
        "t1m",
        "t3m",
    ).rename({"fwd": "fwd_tr"})
    df = (
        fwd_cp.join(fwd_tr, on="date", how="inner")
        .with_columns((pl.col("fwd_cp") - pl.col("fwd_tr")).alias("value"))
        .select(["date", "value"])
        .sort("date")
    )
    if df.height == 0:
        raise RuntimeError("FRA/OIS forward-leg join produced no rows")
    if df.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in forward funding spread")
    return df


if __name__ == "__main__":
    df = fetch_fra_ois()
    print(df.height, df["date"].min(), df["date"].max(), round(df["value"].last(), 3))
