"""Net insider selling in large bank stocks (insider-sentiment stress gauge).

Measures: monthly net open-market insider selling, in USD millions, aggregated
across 8 large US banks: JPM, BAC, C, WFC, GS, MS, USB, PNC. For each reported
transaction, selling = |value| for trade types "S - Sale" and "S - Sale+OE"
(sale after option exercise, still an open-market disposition); buying =
value for "P - Purchase". Net selling = sales - purchases, grouped by trade
month (first of month).

Source (public, no auth): OpenInsider ticker pages, which republish SEC
Form 4 filings,
  http://openinsider.com/JPM  (and /BAC, /C, /WFC, /GS, /MS, /USB, /PNC)
Fetched with a browser User-Agent via curl; the page HTML carries the full
insider-transaction table (filing date, trade date, insider, trade type,
price, quantity, value).

Coverage note: each ticker page lists only recent transactions (roughly the
last 70-100 filings, reaching back to ~Oct 2024), so the earliest months in
the series are less complete than recent ones; the trailing ~12 months are
the usable window. Amendments/duplicates in Form 4 filings are not
de-duplicated, so treat the level as approximate and the month-to-month
changes as the signal.

Frequency: monthly. Unit: USD millions.
"""

import datetime
import re
import subprocess

import polars as pl

TICKERS = ["JPM", "BAC", "C", "WFC", "GS", "MS", "USB", "PNC"]
_OI_TMPL = "http://openinsider.com/{t}"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_SALE_TYPES = {"S - Sale", "S - Sale+OE"}
_BUY_TYPES = {"P - Purchase"}


def _clean(cell: str) -> str:
    return (
        re.sub(r"<[^>]+>", "", cell)
        .replace("&nbsp;", " ")
        .replace("\xa0", " ")
        .strip()
    )


def _parse_value(s: str) -> float:
    return float(s.replace("$", "").replace(",", "").replace("+", "").strip())


def _fetch_ticker(ticker: str, timeout: int = 60) -> list[tuple[str, str, float]]:
    """Return (trade_date, trade_type, signed_value_usd) for one ticker."""
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, _OI_TMPL.format(t=ticker)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"OpenInsider fetch failed for {ticker}: curl rc={r.returncode}")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.stdout, re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 12:
            continue
        trade_date, ttype = cells[2], cells[6]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
            continue
        if ttype not in _SALE_TYPES and ttype not in _BUY_TYPES:
            continue
        try:
            val = _parse_value(cells[11])
        except ValueError:
            continue
        out.append((trade_date, ttype, val))
    return out


def fetch_bank_insider_selling(timeout: int = 60) -> pl.DataFrame:
    """Fetch monthly net insider selling across large bank stocks.

    Returns pl.DataFrame with columns date (pl.Date, first of month) and
    value (pl.Float64, USD millions, sales - purchases). Raises RuntimeError
    on fetch failure or empty output.
    """
    rows: list[tuple[datetime.date, float]] = []
    for t in TICKERS:
        for trade_date, ttype, val in _fetch_ticker(t, timeout):
            d = datetime.date.fromisoformat(trade_date)
            month = datetime.date(d.year, d.month, 1)
            # OpenInsider signs sales negative / purchases positive in the value
            # column, so net selling (sales - purchases) is simply -val.
            rows.append((month, -val))
    if not rows:
        raise RuntimeError("insider selling: no transactions parsed")
    df = (
        pl.DataFrame({"date": [r[0] for r in rows], "net": [r[1] for r in rows]})
        .with_columns(pl.col("date").cast(pl.Date), pl.col("net").cast(pl.Float64))
        .group_by("date")
        .agg((pl.col("net").sum() / 1e6).alias("value"))
        .sort("date")
    )
    df = df.filter(pl.col("value").is_finite())
    if df.height == 0:
        raise RuntimeError("insider selling: no finite monthly observations")
    return df
