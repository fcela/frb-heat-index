"""Excess Bond Premium (CDS-bond basis PROXY).

Source: https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv
(Gilchrist-Zakrajsek methodology; updated Favara/Gilchrist/Lopez/Zakrajsek
FEDS Notes series).

Frequency: monthly. Unit: percentage points of the non-default component of
corporate bond spreads.

Why this is a valid public proxy for the CDS-bond basis:
  basis = bond spread - CDS premium (liquidity/funding component of credit).
  Bai & Collin-Dufresne (2019, Journal of Finance) show the basis is precisely
  the non-default (liquidity/funding) component of spreads — exactly what the
  Excess Bond Premium isolates: the GZ spread minus its default-risk component.
  Direction matches dealer-quoted basis (spikes when funding stress widens the
  basis), but levels are NOT comparable to dealer CDS quotes, so compare
  deviations/spikes, not absolute levels.
"""

import io
import subprocess

import polars as pl

EBP_URL = (
    "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"
)
SERIES_NAME = "Excess Bond Premium (CDS-bond basis proxy)"


def fetch_ebp(timeout: int = 60) -> pl.DataFrame:
    """Fetch the EBP series. Returns date/value (percentage points)."""
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", "curl", EBP_URL],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"EBP fetch failed: curl rc={r.returncode}")
    df = pl.read_csv(io.StringIO(r.stdout))
    if "ebp" not in df.columns or "date" not in df.columns:
        raise RuntimeError("EBP: unexpected CSV columns: %s" % df.columns)
    df = df.select(
        pl.col("date").str.to_date().alias("date"),
        pl.col("ebp").cast(pl.Float64, strict=False).alias("value"),
    ).drop_nulls("value")
    # NaN/Inf guard
    df = df.filter(
        pl.col("value").is_not_nan() & ~pl.col("value").is_infinite()
    ).sort("date")
    if df.height == 0:
        raise RuntimeError("EBP: no usable observations")
    return df.select("date", "value")
