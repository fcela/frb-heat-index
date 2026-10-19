"""CBOE VVIX — the market's price of volatility-regime instability.

VVIX is the 30-day expected volatility *of the VIX*, implied from VIX
option prices: how unstable the volatility outlook itself is. It is the
vol-of-vol, not a level of anything — interpretation is threshold-based:

  - VVIX > 120-140 flags a volatility-regime break (Feb 2018 "Volmageddon",
    Mar 2020: VVIX turned up before VIX peaked);
  - low VIX *and* low VVIX = complacency that historically preceded
    disorderly volatility unwinds;
  - VVIX mean-reverts hard — compare against thresholds and its own
    history, never against other indices' scales.

Not on FRED. CBOE publishes the full daily history as a free CSV
(redirecting CDN URL — the fetch follows redirects).

Source: CBOE VVIX_History.csv, daily, from 2007-03.
Unit: index points (annualized vol-of-vol). Frequency: daily.
"""

import io
import subprocess

import polars as pl

VVIX_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv"
)


def curl_transport(url: str, timeout: int = 60) -> str:
    """GET via curl, following the CDN's redirects (urllib stalls here)."""
    r = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), "-A", "curl", url],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def fetch_vvix(transport=None, timeout: int = 60) -> pl.DataFrame:
    """Fetch the VVIX daily history. Returns date/value, ascending.

    Raises on HTTP failure, empty data, or any unparseable row — rows are
    never silently dropped.
    """
    transport = transport or curl_transport
    text = transport(VVIX_URL, timeout=timeout)
    df = pl.read_csv(io.StringIO(text))
    if {"DATE", "VVIX"} - set(df.columns):
        raise RuntimeError(f"vvix: unexpected columns {df.columns}")
    if df.height == 0:
        raise RuntimeError("vvix: no rows")
    try:
        df = (
            df.rename({"DATE": "date", "VVIX": "value"})
            .with_columns(
                pl.col("date").str.to_date("%m/%d/%Y"),
                pl.col("value").cast(pl.Float64, strict=True),
            )
            .select("date", "value")
            .sort("date")
        )
    except Exception as e:
        raise RuntimeError(f"vvix: unparseable row ({e})")
    bad = df.filter(~pl.col("value").is_finite())
    if bad.height:
        raise RuntimeError(f"vvix: {bad.height} non-finite values")
    return df


if __name__ == "__main__":
    d = fetch_vvix()
    print(d.height, d["date"].min(), d["date"].max(), round(d["value"].last(), 2))
