"""Geopolitical Risk Index — threats subindex (GPT), monthly.

Caldara & Iacoviello (AER 2022, "Measuring Geopolitical Risk") measure the
share of newspaper articles covering adverse geopolitical events across 10
major US/UK/Canadian papers, split into *threats* (GPT — statements of
risk before anything happens) vs *acts* (GPA). The threats subindex builds
*before* acts occur — a genuine ex-ante lead: GPR shocks raise the
probability of economic disaster, depress investment and employment, and
lift VIX and credit spreads (Gulf War, 9/11, 2003 Iraq, 2014 and 2022
Russia-Ukraine, 2026 Iran conflict).

This module extracts the monthly GPRT column from the authors' public
export workbook (data_gpr_export.xls, legacy BIFF format — read with the
calamine backend, same path as the ICI fund-flow workbooks). Threats, not
acts, per the dashboard's leading-indicator mandate.

Caveats: monthly with ~1-month publication lag; Western-press perspective
(local-language indices can diverge — Bondarenko et al. 2024); keyword
drill can miss novel risks. A daily file exists but only carries a short
recent window, so the stable series is monthly.

Source: https://www.matteoiacoviello.com/gpr.htm (data downloads).
Unit: index (100 = 1985-2019 average article share). Frequency: monthly.
"""

import io
import subprocess

import polars as pl

GPR_EXPORT_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls"


def curl_transport(url: str, timeout: int = 120) -> bytes:
    r = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), "-A", "curl", url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def clean_gpr_sheet(df: pl.DataFrame) -> pl.DataFrame:
    """Raw sheet (month, GPRT, ~113 other columns) -> date/value frame.

    The workbook embeds a variable dictionary in trailing rows/columns, so
    value columns arrive as strings with nulls above 1985-01 (where the
    recent-threats series starts). Nulls before the series start are the
    documented history boundary, not dropped data.
    """
    for col in ("month", "GPRT"):
        if col not in df.columns:
            raise RuntimeError(f"gpr_threats: column {col!r} missing from export")
    try:
        out = (
            df.select(
                pl.col("month").dt.date().alias("date"),
                pl.col("GPRT").cast(pl.Float64, strict=True).alias("value"),
            )
            .drop_nulls("value")
            .sort("date")
        )
    except Exception as e:
        raise RuntimeError(f"gpr_threats: unparseable export ({e})")
    if out.height == 0:
        raise RuntimeError("gpr_threats: no observations after cleaning")
    if out.filter(pl.col("date").is_duplicated()).height:
        raise RuntimeError("gpr_threats: duplicate months in export")
    bad = out.filter(~pl.col("value").is_finite())
    if bad.height:
        raise RuntimeError(f"gpr_threats: {bad.height} non-finite values")
    return out


def fetch_gpr_threats(transport=None, timeout: int = 120) -> pl.DataFrame:
    """Fetch the monthly GPR threats subindex. Returns date/value, ascending."""
    transport = transport or curl_transport
    blob = transport(GPR_EXPORT_URL, timeout=timeout)
    df = pl.read_excel(io.BytesIO(blob), sheet_id=1)
    return clean_gpr_sheet(df)


if __name__ == "__main__":
    d = fetch_gpr_threats()
    print(d.height, d["date"].min(), d["date"].max(), round(d["value"].last(), 2))
