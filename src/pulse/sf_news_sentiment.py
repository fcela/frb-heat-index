"""SF Fed Daily News Sentiment Index — lexical tone of economics news.

Daily index of the sentiment of economics-related news articles across 24
major US newspapers (Factiva), aggregated with geometrically declining
weights (Shapiro, Sudhof & Wilson, J. Econometrics 2022). Readings around
zero are neutral; the useful signal is sustained moves and sharp breaks.

Lead evidence: the index fell sharply in January 2020, roughly two months
before the Michigan/Conference Board survey measures moved (FRBSF
Economic Letter 2020-08) — a real-time nowcast of soft data.

Refresh cadence caveat: the *series* is daily, but FRBSF *publishes* only
once a week, so the download always lags a few days to a week; the
pipeline simply takes whatever the file contains (never fabricates the
unpublished tail).

Source: FRBSF download page CSV (full history from 1980).
Unit: index (centered near zero). Frequency: daily (weekly publication).
"""

import io
import subprocess

import polars as pl

NEWS_SENTIMENT_URL = (
    "https://www.frbsf.org/wp-content/uploads/news-sentiment-chart-1.csv"
)


def curl_transport(url: str, timeout: int = 60) -> str:
    r = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), "-A", "curl", url],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def fetch_news_sentiment(transport=None, timeout: int = 60) -> pl.DataFrame:
    """Fetch the daily news-sentiment history. Returns date/value, ascending.

    The chart-1 CSV carries the full 1980->present history; chart-2 is a
    trailing subset of the same series, so only chart-1 is fetched.
    """
    transport = transport or curl_transport
    text = transport(NEWS_SENTIMENT_URL, timeout=timeout)
    df = pl.read_csv(io.StringIO(text))
    if df.height == 0:
        raise RuntimeError("news_sentiment: no rows")
    date_col, val_col = df.columns[0], df.columns[1]
    try:
        df = (
            df.rename({date_col: "date", val_col: "value"})
            .with_columns(
                pl.col("date").str.to_date("%m/%d/%Y"),
                pl.col("value").cast(pl.Float64, strict=True),
            )
            .select("date", "value")
            .sort("date")
        )
    except Exception as e:
        raise RuntimeError(f"news_sentiment: unparseable row ({e})")
    if df.filter(pl.col("date").is_duplicated()).height:
        raise RuntimeError("news_sentiment: duplicate dates")
    bad = df.filter(~pl.col("value").is_finite())
    if bad.height:
        raise RuntimeError(f"news_sentiment: {bad.height} non-finite values")
    return df


if __name__ == "__main__":
    d = fetch_news_sentiment()
    print(d.height, d["date"].min(), d["date"].max(), round(d["value"].last(), 3))
