"""3M commercial-paper funding spread (CP minus T-bill), daily.

Concept: the "3M CP-OIS spread" funding-stress gauge. There is no public 3M
OIS series (CME Term SOFR is licensed), so the OIS leg must be proxied.

What was tried and why T-bill won:
  * DCPN3M (90-day AA nonfinancial CP) minus SOFR 90-day average (the
    tenor-matched public OIS proxy) was REJECTED. The 90-day average is
    backward-looking: during the 2022 hiking cycle it lagged spot rates and
    printed a spurious +177bp "spread" on 2022-11-07 -- larger than the
    genuine COVID stress spike (+83bp on 2020-03-26). A stress indicator
    whose largest move is a rate-cycle artifact is misleading. The 30-day
    SOFR average has the same flaw (2022 artifact +133bp).
  * Chosen: DCPN3M minus DGS3MO (3-month T-bill) -- the literature's classic
    money-market funding-stress gauge. Both legs are 3M spot rates, so there
    is no averaging staleness. Validated: +219bp on 2008-09-17 (Lehman week),
    +218bp on 2020-03-26, +43bp on 2023-03-17 (SVB); the 2022 hiking-cycle
    max is only +18bp.

Limitations: the T-bill leg carries flight-to-safety / convenience premia,
so the spread also widens when bills are bid (not only when CP is stressed);
it can print negative (currently about -17bp, bills above CP). Strictly this
is a CP-bill spread, not CP-OIS -- consider renaming the indicator key to
``cp_bill`` when wiring the catalog.

Source: FRED (CPN3M/DGS3MO are Board of Governors H.15 series). Units:
percentage points, daily business days. History from 1997-01-02.
"""

from src.pulse import fred

import polars as pl


def fetch_cp_ois() -> pl.DataFrame:
    """Fetch the 3M CP minus 3M T-bill spread. Returns date/value (pp), ascending."""
    try:
        cp = fred.fetch_fred_csv("DCPN3M")
        tbill = fred.fetch_fred_csv("DGS3MO")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"CP/OIS inputs fetch failed: {e}")
    df = (
        cp.join(tbill, on="date", suffix="_tbill", how="inner")
        .with_columns((pl.col("value") - pl.col("value_tbill")).alias("value"))
        .select(["date", "value"])
        .sort("date")
    )
    if df.height == 0:
        raise RuntimeError("CP/T-bill join produced no rows")
    if df.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in CP-bill spread")
    return df


if __name__ == "__main__":
    df = fetch_cp_ois()
    print(df.height, df["date"].min(), df["date"].max(), round(df["value"].last(), 3))
