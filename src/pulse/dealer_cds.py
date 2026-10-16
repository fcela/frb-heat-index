"""Dealer credit-risk proxy: ICE BofA Single-A US Corporate Index OAS, daily.

Concept: average dealer 5Y senior CDS. No free dealer-CDS feed exists
(Markit/S&P are subscription). The public bond-market substitution:

FRED carries no US banking/financial-sector ICE BofA OAS (verified: FRED's
US ICE BofA cuts are rating/maturity only; probed financial-sector ID
patterns return no series). The closest verified dealer-representative cut
is BAMLC0A3CA -- ICE BofA Single-A US Corporate Index option-adjusted
spread. US GSIB senior unsecured bonds cluster in single-A, so the A bucket
is the closest public bond-spread analog to average dealer 5Y CDS.

Limitations, stated plainly: a bond OAS is not a CDS (it includes liquidity
premia and clientele effects, and CDS-bond bases move); the index covers
all maturities >= 1Y, not the 5Y point; it is not dealer-pure (includes
nonfinancial single-A issuers); FRED's ICE BofA history is truncated to a
rolling 3-year window (series starts 2023-10-16), so there is no crisis
backfill. Levels run well below 2008-style CDS prints -- use for
changes/z-scores, not for absolute comparisons to traded CDS.

Source: FRED (ICE Data Indices, LLC via St. Louis Fed). Units: percentage
points, daily business days. Latest ~0.70pp (calm dealer credit).
"""

from src.pulse import fred

import polars as pl


def fetch_dealer_cds() -> pl.DataFrame:
    """Fetch the single-A corporate OAS dealer-CDS proxy. date/value (pp), ascending."""
    try:
        df = fred.fetch_fred_csv("BAMLC0A3CA")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"dealer CDS proxy fetch failed: {e}")
    if df.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in dealer CDS proxy")
    return df


if __name__ == "__main__":
    df = fetch_dealer_cds()
    print(df.height, df["date"].min(), df["date"].max(), round(df["value"].last(), 3))
