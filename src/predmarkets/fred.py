"""FRED fetch + parse for the realized-outcomes dataset (no API key).

Endpoint: https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES> —
a plain CSV, no key, no auth (brief-verified). Transport is the same
curl-subprocess contract as the Kalshi client (injected for tests, so the
suite never touches the network). Raw responses are preserved verbatim
under data/predmarkets/fred/ before any derivation touches them.

Series used, and what each measures (verified against the exchange's
contract rules on 2026-10-19):

  CPIAUCSL        CPI-U index, NSA, monthly (BLS). YoY % change computed
                  here in one decimal, matching the one-decimal YoY rate
                  the CPI contracts settle on. The index is not revised,
                  so latest == release print up to index-rounding (the
                  BLS computes YoY from unrounded indexes; differences at
                  the last decimal are possible and are documented).
  A191RL1Q225SBEA Real GDP growth, quarterly, SAAR, one decimal (BEA).
                  Same QUANTITY as the GDP contracts, but FRED serves
                  the LATEST vintage, while the contracts settle on the
                  ADVANCE estimate — the vintage difference is explicit
                  wherever these values are plotted.
  UNRATE          U-3 unemployment rate, SA, monthly, one decimal (BLS
                  Employment Situation) — the exact contract quantity.
  DFEDTARU        Target-range UPPER bound, daily (Fed). The KXFED
                  contracts settle on exactly this quantity, and it
                  provides the pre-meeting base rate for the FOMC
                  decision fan (per-meeting, never a single hardcode).
"""

import datetime as dt
from pathlib import Path

import polars as pl

from src.predmarkets import kalshi

FRED_CSV_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# series_id -> (what it is, unit, vintage note shown next to realized marks)
FRED_SERIES = {
    "CPIAUCSL": {
        "label": "CPI-U index (NSA, monthly)",
        "unit": "index",
        "vintage": "index not revised; YoY computed from the published "
                   "one-decimal index",
    },
    "A191RL1Q225SBEA": {
        "label": "Real GDP growth (quarterly, SAAR)",
        "unit": "percent",
        "vintage": "latest BEA vintage — the contracts settle on the "
                   "advance estimate, which can later be revised",
    },
    "UNRATE": {
        "label": "U-3 unemployment rate (SA, monthly)",
        "unit": "percent",
        "vintage": "release print; later re-estimation can move it "
                   "slightly",
    },
    "DFEDTARU": {
        "label": "Fed funds target-range upper bound (daily)",
        "unit": "percent",
        "vintage": "effective on the day the Fed implements a decision",
    },
}


def fred_url(series_id: str) -> str:
    return f"{FRED_CSV_BASE}?id={series_id}"


def fetch_fred_series(series_id: str, raw_dir: Path, transport=None
                      ) -> pl.DataFrame:
    """Fetch one series, preserve the raw CSV, return (date, value) rows.

    The raw response is written verbatim to raw_dir/<series_id>.csv before
    parsing, honoring the raw-data-preserved rule. Missing observations
    ('.') become null value cells but keep their date row.
    """
    transport = transport or kalshi.curl_get
    body = transport(fred_url(series_id))
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{series_id}.csv").write_text(body)
    return parse_fred_csv(body, series_id)


def parse_fred_csv(body: str, series_id: str) -> pl.DataFrame:
    """fredgraph.csv body -> DataFrame(date: Utf8, value: Float64).

    FRED emits 'observation_date,<SERIES>' with ISO dates and '.' for
    missing values. Malformed bodies fail loudly (fail-closed).
    """
    df = pl.read_csv(body.encode() if isinstance(body, str) else body)
    cols = df.columns
    if "observation_date" not in cols or series_id not in cols:
        raise RuntimeError(
            f"FRED {series_id}: unexpected columns {cols} — expected "
            f"observation_date,{series_id}")
    out = (df.select([
              pl.col("observation_date").cast(pl.Utf8).alias("date"),
              pl.col(series_id).cast(pl.Float64, strict=False)
                .alias("value")])
           .sort("date"))
    return out


# ---------------------------------------------------------- derived series

def cpi_yoy_from_index(cpi: pl.DataFrame) -> pl.DataFrame:
    """Monthly CPI YoY % (one decimal) from the NSA index level series.

    (I_t / I_{t-12} - 1) * 100, rounded to one decimal to match the
    one-decimal YoY rate the exchange's CPI rules settle on.
    """
    if cpi.height == 0:
        return pl.DataFrame(schema={"date": pl.Utf8, "value": pl.Float64})
    d = cpi.sort("date").with_columns([
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d").alias("_d")])
    d = d.with_columns(pl.col("_d").shift(12).alias("_prev_d"),
                       pl.col("value").shift(12).alias("_prev_v"))
    d = d.filter(pl.col("_prev_d") == pl.col("_d").dt.offset_by("-12mo"))
    d = d.with_columns(
        ((pl.col("value") / pl.col("_prev_v") - 1.0) * 100.0)
        .round(1).alias("yoy"))
    return d.select([
        pl.col("_d").dt.to_string("%Y-%m").alias("date"),
        pl.col("yoy").alias("value"),
    ])


def quarter_of_release(release: dt.date) -> str | None:
    """GDP release day -> the quarter whose advance estimate it prices.

    Advance estimates land ~4 weeks after quarter-end (verified release
    days in KXGDP tickers: Oct 30 2026, Jan 28 2027, Apr 29 2027, Jul 29
    2027 — 28–31 days after quarter end). The priced quarter is the most
    recent quarter-end 25–45 days before the release; None if none fits.
    """
    for back in range(25, 46):
        cand = release - dt.timedelta(days=back)
        if (cand.month, cand.day) in ((3, 31), (6, 30), (9, 30), (12, 31)):
            return f"{cand.year}Q{(cand.month - 1) // 3 + 1}"
    return None


def fed_funds_around(ffr: pl.DataFrame, day: dt.date,
                     window: int = 7) -> tuple[float | None, float | None]:
    """(base before `day`, realized post-`day` level) from DFEDTARU.

    base  = last observation strictly before `day` (the prevailing
            target-range upper bound the meeting acts on);
    post  = first observation in [day, day+window] whose value differs
            from base (the implemented decision), else base (a hold).
            None when no observation at-or-after `day` exists yet —
            the meeting has not happened.

    A missing feed yields (None, None); callers mark the row honestly
    rather than guessing.
    """
    if ffr is None or ffr.height == 0:
        return None, None
    d = ffr.with_columns(
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d").alias("_d")
    ).sort("_d")
    before = d.filter(pl.col("_d") < day)
    base = before["value"].tail(1).to_list()
    if not base or base[0] is None:
        return None, None
    base = float(base[0])
    after = d.filter((pl.col("_d") >= day)
                     & (pl.col("_d") <= day + dt.timedelta(days=window)))
    if after.height == 0:
        return base, None
    for v in after["value"].to_list():
        if v is not None and v != base:
            return base, float(v)
    return base, base


def technical_recession_quarters(gdp: pl.DataFrame) -> pl.DataFrame:
    """Per-quarter growth with the exchange's recession rule applied.

    The KXRECSSNBER contracts resolve Yes on two consecutive quarters of
    negative real GDP growth (per BEA) within the covered window — NOT on
    NBER committee calls (verified against the listed contract rules
    2026-10-19). Output rows: date 'YYYYQn', value = growth, rec_flag =
    true when this quarter and its immediately preceding quarter are both
    negative (adjacency enforced by date, so a data gap never fakes a
    pair).
    """
    schema = pl.DataFrame(schema={"date": pl.Utf8, "value": pl.Float64,
                                  "rec_flag": pl.Boolean})
    if gdp.height == 0:
        return schema
    d = gdp.sort("date").with_columns(
        pl.col("date").str.strptime(pl.Date, "%Y-%m-%d").alias("_d"))
    d = d.with_columns(pl.col("_d").shift(1).alias("_prev_d"),
                       pl.col("value").shift(1).alias("_prev_v"))
    d = d.filter(pl.col("_prev_d").is_not_null()
                 & (pl.col("_prev_d") == pl.col("_d").dt.offset_by("-3mo")))
    d = d.with_columns(((pl.col("value") < 0) & (pl.col("_prev_v") < 0))
                       .alias("rec_flag"))
    return d.select([
        pl.col("_d").dt.to_string("%YQ%q").alias("date"),
        pl.col("value"),
        pl.col("rec_flag"),
    ])
