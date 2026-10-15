"""Overnight Reverse Repo (ON RRP) take-up — cash draining into the facility.

Source: FRED series RRPONTTLD (daily, business days), units USD BILLIONS
(e.g. 2553.716 on 2022-12-30 = the $2.55tn peak). A collapse toward zero
means cash is no longer seeking the Fed's balance sheet, i.e. reserves are
no longer abundant.
"""

import polars as pl


def fetch_on_rrp() -> pl.DataFrame:
    from .fred import fetch_fred_csv

    try:
        return fetch_fred_csv("RRPONTTLD")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"ON RRP fetch failed: {e}")


if __name__ == "__main__":
    df = fetch_on_rrp()
    print(df.height, df["date"].min(), df["date"].max(), df["value"].last())
