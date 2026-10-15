"""FRED fetcher for market-pulse indicators.

Uses curl (not Python urllib — urllib stalls on FRED). Returns Polars frames.
"""

import io
import subprocess

import polars as pl


def fetch_fred_csv(fred_id: str, timeout: int = 60) -> pl.DataFrame:
    """Fetch a FRED series as CSV. Raises on HTTP error or empty data."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}"
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", "curl", url],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"FRED fetch failed for {fred_id}: curl rc={r.returncode}")
    df = pl.read_csv(io.StringIO(r.stdout))
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
