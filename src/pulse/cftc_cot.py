"""CFTC Traders-in-Financial-Futures speculative positioning z-scores.

Source: CFTC TFF (financial futures) annual text files
    https://www.cftc.gov/files/dea/history/fut_fin_txt_YYYY.zip
(weekly, Tuesdays; published Fridays). Speculators = "Lev Money" (leveraged
funds) category; net = long - short per contract. Each week is z-scored
against its own trailing 104-week window, excluding the current week.
Contracts: UST 2Y/5Y/10Y NOTE (CBOT), SOFR-3M (CME).
Long format: date / contract / value.
"""

import io
import subprocess
import zipfile

import polars as pl

CONTRACTS = {
    "UST 2Y NOTE - CHICAGO BOARD OF TRADE": "UST2Y",
    "UST 5Y NOTE - CHICAGO BOARD OF TRADE": "UST5Y",
    "UST 10Y NOTE - CHICAGO BOARD OF TRADE": "UST10Y",
    "SOFR-3M - CHICAGO MERCANTILE EXCHANGE": "SOFR3M",
}

WINDOW = 104


def _download_year(year: int) -> pl.DataFrame:
    url = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
    r = subprocess.run(
        ["curl", "-s", "--max-time", "300", "-L", url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"CFTC download failed for {year}: curl rc={r.returncode}")
    try:
        with zipfile.ZipFile(io.BytesIO(r.stdout)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not names:
                raise RuntimeError(f"no .txt inside CFTC zip for {year}")
            raw = zf.read(names[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"CFTC zip for {year} is not a zip: {e}")
    df = pl.read_csv(io.StringIO(raw), infer_schema_length=0)
    # Normalize column names (strip quotes).
    df = df.rename({c: c.strip('"').strip() for c in df.columns})
    date_col = next(c for c in df.columns if "YYYY-MM-DD" in c)
    name_col = next(c for c in df.columns if "Market_and_Exchange" in c)
    long_col = next(c for c in df.columns if "Lev_Money_Positions_Long_All" in c)
    short_col = next(c for c in df.columns if "Lev_Money_Positions_Short_All" in c)
    df = (
        df.select([name_col, date_col, long_col, short_col])
        .rename(
            {
                name_col: "mkt",
                date_col: "date",
                long_col: "long",
                short_col: "short",
            }
        )
        .with_columns(
            pl.col("mkt").str.strip_chars('"').str.strip_chars(),
            pl.col("date").str.strip_chars('"').str.to_date(),
            pl.col("long").cast(pl.Utf8).str.strip_chars("'").str.strip_chars(),
            pl.col("short").cast(pl.Utf8).str.strip_chars("'").str.strip_chars(),
        )
        .with_columns(
            pl.col("long").cast(pl.Float64, strict=False),
            pl.col("short").cast(pl.Float64, strict=False),
        )
        .drop_nulls(["long", "short"])
        .with_columns((pl.col("long") - pl.col("short")).alias("net"))
        .filter(pl.col("mkt").is_in(list(CONTRACTS.keys())))
        .with_columns(pl.col("mkt").replace(CONTRACTS).alias("contract"))
        .select(["date", "contract", "net"])
    )
    return df


def fetch_spec_net_zscores(years: tuple[int, ...] = (2024, 2025, 2026)) -> pl.DataFrame:
    frames = [_download_year(y) for y in years]
    df = (
        pl.concat(frames)
        .sort(["contract", "date"])
        .group_by(["contract", "date"], maintain_order=True)
        .last()
    )
    if df.height == 0:
        raise RuntimeError("CFTC: no observations for target contracts")
    out = []
    for contract in df["contract"].unique().to_list():
        s = df.filter(pl.col("contract") == contract).sort("date")
        dates = s["date"].to_list()
        vals = s["net"].to_list()
        for i in range(WINDOW, len(vals)):
            window = vals[i - WINDOW : i]
            mu = sum(window) / WINDOW
            var = sum((x - mu) ** 2 for x in window) / (WINDOW - 1)
            z = 0.0 if var <= 0 else (vals[i] - mu) / (var**0.5)
            out.append((dates[i], contract, z))
    res = pl.DataFrame(
        {"date": [o[0] for o in out], "contract": [o[1] for o in out], "value": [o[2] for o in out]}
    ).with_columns(pl.col("value").cast(pl.Float64))
    if res.height == 0:
        raise RuntimeError("CFTC: insufficient history for z-scores")
    if res.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in CFTC z-scores")
    return res.sort(["date", "contract"])


if __name__ == "__main__":
    df = fetch_spec_net_zscores()
    print(df.height, df["date"].min(), df["date"].max())
    print(df.group_by("contract").len())
