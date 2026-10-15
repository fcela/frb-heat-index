"""Treasury cash-futures basis proxy (dealer balance-sheet stress gauge).

Measures: 10-year on-the-run Treasury cash yield minus the yield implied by
the front-month CME 10-year T-note futures price, in basis points. A wider
(more positive) basis means cash Treasuries cheapen relative to futures, the
classic footprint of dealer balance-sheet constraints (see e.g. the
Treasury-market stress literature on the cash-futures basis).

Sources (both public, no auth):
  - Cash leg: FRED series DGS10 (10-Year Treasury Constant Maturity Rate),
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10 (daily, percent).
  - Futures leg: Yahoo Finance v8 chart API, symbol ZN=F (CBOT 10-year T-note
    futures, continuous front-month contract),
    https://query1.finance.yahoo.com/v8/finance/chart/ZN=F (daily closes).
    The CME quotes page itself (cmegroup.com) returns HTTP 403 to curl, so the
    futures price is sourced from Yahoo's public quote feed instead.

Method: the futures-implied yield is the bond-equivalent yield of the
contract's hypothetical 6%-coupon 10-year note at the quoted futures price
(per $100 par): P = 3*a_angle_20(y/2) + 100/(1+y/2)^20, solved by bisection.
This abstracts from the cheapest-to-deliver option, delivery timing and
financing (repo) effects, so the series is a *proxy* for the true
delivery-adjusted basis, not a tradable basis print.

Frequency: daily (trading days). Unit: basis points.
"""

import datetime
import io
import json
import subprocess

import polars as pl

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/ZN=F?range=2y&interval=1d"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _curl(url: str, timeout: int = 60) -> bytes:
    ua = "curl" if "fred.stlouisfed.org" in url else _UA  # FRED stalls on browser UAs
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", ua, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def _implied_yield_10y(price: float) -> float:
    """Bond-equivalent yield (percent) of a 6% 10y semiannual note at `price`."""
    lo, hi = 0.0001, 0.20  # semiannual yield bounds
    for _ in range(200):
        mid = (lo + hi) / 2
        v = 1.0 / (1.0 + mid)
        pv = 3.0 * (1.0 - v**20) / mid + 100.0 * v**20
        if pv > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) * 100.0  # semiannual -> annual percent


def fetch_treasury_cash_futures_basis(timeout: int = 60) -> pl.DataFrame:
    """Fetch the 10y Treasury cash-futures basis proxy.

    Returns pl.DataFrame with columns date (pl.Date) and value (pl.Float64,
    basis points). Raises RuntimeError on fetch failure or empty output.
    """
    raw = _curl(FRED_URL, timeout)
    dgs = pl.read_csv(io.StringIO(raw.decode()))
    dgs = dgs.rename({dgs.columns[0]: "date", dgs.columns[1]: "cash_yield"}).with_columns(
        pl.col("date").str.to_date(),
        pl.col("cash_yield").cast(pl.Float64, strict=False),
    ).drop_nulls("cash_yield")

    raw = _curl(YAHOO_URL, timeout)
    try:
        res = json.loads(raw.decode())["chart"]["result"][0]
    except Exception as e:
        raise RuntimeError(f"Yahoo ZN=F parse failed: {e}")
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    fut = pl.DataFrame(
        {
            "date": [datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date() for t in ts],
            "fut_price": closes,
        }
    ).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("fut_price").cast(pl.Float64, strict=False),
    ).drop_nulls("fut_price")

    df = dgs.join(fut, on="date", how="inner").sort("date")
    if df.height == 0:
        raise RuntimeError("treasury basis: no overlapping DGS10 / ZN=F dates")
    df = df.with_columns(
        pl.col("fut_price").map_elements(_implied_yield_10y, return_dtype=pl.Float64).alias("implied_yield")
    ).with_columns(
        ((pl.col("cash_yield") - pl.col("implied_yield")) * 100.0).alias("value")
    ).select("date", "value")
    df = df.filter(pl.col("value").is_finite())
    if df.height == 0:
        raise RuntimeError("treasury basis: no finite observations")
    return df
