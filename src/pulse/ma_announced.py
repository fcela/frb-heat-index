"""Announced global M&A deal value from PitchBook's free Global M&A Reports.

PitchBook publishes free quarterly and annual Global M&A Reports with
extractable announced deal values (global, $B, including estimates for
late-reporting and nondisclosed deals). No public API exists; the values
below were extracted from the PDFs' annual chart data labels and validated.

Sources (verified 2026-10-15):
- 2025 Annual: https://cdn.roxhillmedia.com/production/email/attachment/1790001_1800000/48a130c47f887126489d806280c38cd14667b41e.pdf
  (annual 2015-2025, as of Dec 31 2025; latest vintage - used on restatement)
- Q3 2024: https://files.pitchbook.com/website/files/pdf/Q3_2024_Global_MA_Report.pdf
  (annual 2014-2023, as of Q3 2024; provides 2014 only)

Extraction: pypdf text-object positions on the annual chart page; values are
the "$X,XXX.X" data labels above each annual bar. Cross-checked overlapping
years across vintages (latest vintage wins):
  2015: Q3'24 $3,721.8B vs Annual'25 $3,933.9B -> 3933.9
  2021: Q3'24 $4,718.5B vs Annual'25 $4,857.0B -> 4857.0
All values in $2-6T sane range.

Limitations:
- Annual frequency only. PitchBook's quarterly charts render values as
  vector graphics without text labels - quarterly values are not extractable
  from the free PDFs. (Dealogic's free PDFs were also probed: only 2 usable
  observations, no backfill.)
- Values include PitchBook estimates for late-reporting/nondisclosed deals;
  vintages restate history.
- Refresh: download the latest Annual Global M&A Report PDF, extract the
  annual chart labels, append. The quarterly URL pattern
  files.pitchbook.com/website/files/pdf/Q{N}_{YYYY}_Global_MA_Report.pdf
  is bot-walled for most quarters.

Unit: $B (announced global M&A deal value). Frequency: annual.
"""

import polars as pl

# (year, value_$B, vintage_source)
ANNUAL_VALUES = [
    (2014, 3187.1, "Q3_2024"),
    (2015, 3933.9, "Annual_2025"),
    (2016, 3411.7, "Annual_2025"),
    (2017, 3227.7, "Annual_2025"),
    (2018, 3755.4, "Annual_2025"),
    (2019, 3525.8, "Annual_2025"),
    (2020, 3093.6, "Annual_2025"),
    (2021, 4857.0, "Annual_2025"),
    (2022, 3696.9, "Annual_2025"),
    (2023, 3246.9, "Annual_2025"),
    (2024, 3605.4, "Annual_2025"),
    (2025, 4938.5, "Annual_2025"),
]

MIN_SANE_BN = 2000.0
MAX_SANE_BN = 6000.0


def fetch_ma_announced() -> pl.DataFrame:
    """Return annual announced global M&A deal values.

    Returns pl.DataFrame with columns date (pl.Date, Dec 31 of year) and
    value (pl.Float64, $B), sorted ascending. Raises RuntimeError if any
    value is outside the sane range.
    """
    for year, val, _vintage in ANNUAL_VALUES:
        if not (MIN_SANE_BN <= val <= MAX_SANE_BN):
            raise RuntimeError(
                f"ma_announced: {year} value {val} outside sane range "
                f"[{MIN_SANE_BN}, {MAX_SANE_BN}]"
            )
    df = pl.DataFrame(
        {
            "date": [f"{y}-12-31" for y, _v, _s in ANNUAL_VALUES],
            "value": [v for _y, v, _s in ANNUAL_VALUES],
        }
    ).with_columns(pl.col("date").str.to_date()).sort("date")
    return df.select("date", "value")
