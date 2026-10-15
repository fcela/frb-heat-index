"""FINRA margin-statistics fetcher (monthly debit balances).

Public Excel workbook published under FINRA Rule 4521(d).
"""

import io
import subprocess

import polars as pl

XLSX_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"


def fetch_margin_debt(timeout: int = 90) -> pl.DataFrame:
    """Fetch FINRA margin debit balances. Returns date/value in USD millions."""
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", "Mozilla/5.0", XLSX_URL],
        capture_output=True,
    )
    if r.returncode != 0 or len(r.stdout) < 1000:
        raise RuntimeError(f"FINRA fetch failed: curl rc={r.returncode}")
    df = pl.read_excel(io.BytesIO(r.stdout))
    # Columns: Year-Month | Debit Balances ... | Free Credit ... | ...
    date_col = df.columns[0]
    val_col = [c for c in df.columns if "Debit Balances" in c][0]
    out = df.select(
        pl.col(date_col).alias("ym"),
        pl.col(val_col).cast(pl.Float64, strict=False).alias("value"),
    ).drop_nulls("value")
    # Year-Month like "2026-08" -> date (first of month)
    out = out.with_columns(
        pl.col("ym").str.to_date("%Y-%m").alias("date")
    ).select("date", "value").sort("date")
    if out.height == 0:
        raise RuntimeError("FINRA: no usable rows")
    return out
