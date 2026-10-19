"""FRED fetcher for market-pulse indicators.

Uses curl (not Python urllib — urllib stalls on FRED). Returns Polars frames.
"""

import io
import subprocess

import polars as pl


def curl_transport(url: str, timeout: int = 60) -> str:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", "curl", url],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"curl fetch failed for {url}: rc={r.returncode}")
    return r.stdout


def fetch_fred_csv(fred_id: str, timeout: int = 60, transport=None) -> pl.DataFrame:
    """Fetch a FRED series as CSV. Raises on HTTP error or empty data.

    `transport(url, timeout=...) -> CSV text` is injectable so tests run
    against fixtures with zero live calls.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}"
    text = (transport or curl_transport)(url, timeout=timeout)
    df = pl.read_csv(io.StringIO(text))
    if df.height == 0:
        raise RuntimeError(f"FRED returned no rows for {fred_id}")
    # Column is named DATE/<id>; normalize.
    date_col = df.columns[0]
    val_col = df.columns[1]
    df = df.rename({date_col: "date", val_col: "value"})
    df = df.with_columns(
        pl.col("date").str.to_date(),
        pl.col("value").cast(pl.Float64, strict=False),
    ).drop_nulls("value").sort("date")
    if df.height == 0:
        raise RuntimeError(f"FRED {fred_id}: no numeric observations")
    return df


def fetch_many(fred_ids: list[str]) -> dict[str, pl.DataFrame]:
    out = {}
    for fid in fred_ids:
        out[fid] = fetch_fred_csv(fid)
    return out
