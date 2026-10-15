"""Primary dealer Treasury fails-to-deliver (settlement stress).

Source: NY Fed Markets Data API, primary dealer statistics, per-series CSVs:
    https://markets.newyorkfed.org/api/pd/get/<CODE>.csv
Series catalog: https://markets.newyorkfed.org/api/pd/list/timeseries.csv

Sum of 11 Treasury fails-to-deliver series (SBN2024 schema, codes ending in
-TD; -TR = fails-to-receive, PDFTD-* = financing/repo fails, a different
concept — neither used):
  nominal coupons: PDSI2F, PDSI3F, PDSI5F, PDSI7F, PDSI10F, PDSI20F, PDSI30F
  STRIPS:          PDST5F, PDST10F, PDST30F
  FRN:             PDFRN2F
Unit: USD millions, weekly (Wednesdays). Values of "*" are suppressed below
the confidentiality threshold and treated as 0.
"""

import io
import subprocess

import polars as pl

CODES = [
    "PDSI2F-TD",
    "PDSI3F-TD",
    "PDSI5F-TD",
    "PDSI7F-TD",
    "PDSI10F-TD",
    "PDSI20F-TD",
    "PDSI30F-TD",
    "PDST5F-TD",
    "PDST10F-TD",
    "PDST30F-TD",
    "PDFRN2F-TD",
]


def _fetch_series(code: str, timeout: int = 60) -> pl.DataFrame:
    url = f"https://markets.newyorkfed.org/api/pd/get/{code}.csv"
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), url],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"NY Fed fetch failed for {code}: curl rc={r.returncode}")
    df = pl.read_csv(io.StringIO(r.stdout))
    if df.height == 0:
        raise RuntimeError(f"NY Fed returned no rows for {code}")
    df = df.rename(
        {df.columns[0]: "date", df.columns[1]: "series", df.columns[2]: "raw"}
    )
    df = df.with_columns(
        pl.col("date").str.to_date(),
        pl.when(pl.col("raw").cast(pl.Utf8) == "*")
        .then(0.0)
        .otherwise(pl.col("raw").cast(pl.Float64, strict=False))
        .alias("value"),
    ).select(["date", "value"])
    return df


def fetch_fails_to_deliver() -> pl.DataFrame:
    series = [_fetch_series(c) for c in CODES]
    df = series[0]
    for s in series[1:]:
        df = df.join(s, on="date", how="full", suffix="_r").with_columns(
            pl.col("value").fill_null(0) + pl.col("value_r").fill_null(0)
        ).select(["date", "value"]).rename({"value": "value"})
    df = df.sort("date")
    if df.height == 0:
        raise RuntimeError("fails-to-deliver: no rows after summing")
    if df.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in fails-to-deliver")
    return df


if __name__ == "__main__":
    df = fetch_fails_to_deliver()
    print(df.height, df["date"].min(), df["date"].max(), df["value"].last())
    print(df.filter(pl.col("date") == pl.date(2020, 3, 11)))
