"""SOFR minus IORB spread (ON RRP corridor proxy).

Source: FRED — SOFR (30-day avg overnight financing rate) and IORB (interest on
reserve balances), both in percent, daily business days.
Spread = SOFR - IORB in percentage points. Negative = SOFR below the
administered rate, i.e. reserves abundant / cash seeking alternatives.
IORB starts 2021-07-29, which limits the backfill.
"""

from .fred import fetch_fred_csv

import polars as pl


def fetch_sofr_iorb_spread() -> pl.DataFrame:
    try:
        sofr = fetch_fred_csv("SOFR")
        iorb = fetch_fred_csv("IORB")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"SOFR/IORB fetch failed: {e}")
    df = (
        sofr.rename({"value": "sofr"})
        .join(iorb.rename({"value": "iorb"}), on="date", how="inner")
        .with_columns((pl.col("sofr") - pl.col("iorb")).alias("value"))
        .select(["date", "value"])
    )
    if df.height == 0:
        raise RuntimeError("SOFR/IORB join produced no rows")
    if df.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in SOFR-IORB spread")
    return df


if __name__ == "__main__":
    df = fetch_sofr_iorb_spread()
    print(df.height, df["date"].min(), df["date"].max(), df["value"].last())
