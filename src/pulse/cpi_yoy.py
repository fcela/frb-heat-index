"""CPI inflation as a 12-month percent change — headline and core.

FRED carries the CPI as an index level (CPIAUCSL headline, CPILFESL less
food and energy; BLS via FRED). The pulse charts the 12-month percent
change — the measure policy and markets price (the prediction-markets
layer prices CPI year over year) — computed from those levels.

The change is computed by joining each month to the same calendar month
one year earlier, never by row position. That distinction is load-bearing:
October 2025 was never collected (the 2025 federal shutdown interrupted
BLS collection), so no 12-month change exists for October 2026 and that
point is absent, not estimated — BLS likewise published no 12-month rate
for October 2026. A positional shift(12) would silently pair October 2026
with September 2025 and print a wrong number.

Source: FRED CPIAUCSL / CPILFESL (BLS CPI-U, seasonally adjusted levels).
Units: percent (12-month change), monthly. History from 1948 (headline)
and 1958 (core), bounded by the first available year-ago base month.
"""

from src.pulse import fred

import polars as pl


def compute_yoy(df: pl.DataFrame) -> pl.DataFrame:
    """12-month percent change of a monthly level frame (date/value).

    Joins on calendar month one year earlier; months whose base month is
    absent from the input produce no row.
    """
    base = df.select(
        pl.col("date").dt.offset_by("1y").alias("date"),
        pl.col("value").alias("base"),
    )
    out = (
        df.join(base, on="date", how="inner")
        .with_columns(((pl.col("value") / pl.col("base") - 1.0) * 100.0).alias("value"))
        .select(["date", "value"])
        .sort("date")
    )
    if out.height == 0:
        raise RuntimeError("CPI YoY computation produced no rows")
    if out.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in CPI YoY")
    return out


def fetch_cpi_yoy(fred_id: str, transport=None) -> pl.DataFrame:
    """Fetch a CPI level series from FRED and return its 12-month change."""
    try:
        levels = fred.fetch_fred_csv(fred_id, transport=transport)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"CPI fetch failed for {fred_id}: {e}")
    return compute_yoy(levels)


if __name__ == "__main__":
    for fid in ("CPIAUCSL", "CPILFESL"):
        df = fetch_cpi_yoy(fid)
        print(fid, df.height, df["date"].min(), df["date"].max(),
              round(df["value"].last(), 3))
