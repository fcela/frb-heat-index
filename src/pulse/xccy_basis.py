"""EUR/USD cross-currency basis (DIY CIP-deviation construction).

No published public series exists for the xccy basis (verified: FRED has no
FX forwards; BIS/ECB papers use Bloomberg). This module builds it per
Du-Tepper-Verdelhan (2018), "Deviations from Covered Interest Rate Parity":
  (1 + r_usd * tau) = (F / S) * (1 + (r_eur + x) * tau)
  => x = ((S / F) * (1 + r_usd * tau) - 1) / tau - r_eur
Negative x = dollar funding premium (synthetic USD via EUR costs more than
direct USD), matching dealer-quote sign convention.

Inputs (all public):
- F: CME Euro FX futures, quarterly contracts via Yahoo Finance
  ("{MON}{YY}.CME", e.g. 6EZ26.CME). Front contract, rolled 14 days before
  expiry. Do NOT use Yahoo's 6E=F continuous series: it is back-adjusted and
  sits 25-40 pips below the front contract, unusable for levels.
- S: FRED DEXUSEU (USD per EUR, daily).
- r_eur: FRED IR3TIB01EZM156N, 3-month Euribor (monthly; forward-filled to
  daily). NOTE: this FRED series is stale (last obs 2026-01-01, ~2.03%,
  vs ~2.63% market in Oct 2026) — a ~60bp drag on recent prints.
- r_usd: FRED DGS3MO, 3-month T-bill (daily). NOTE: TED contamination — the
  T-bill is not banks' marginal USD funding rate (SOFR/term funding can sit
  tens of bp away), so treat the LEVEL with suspicion and watch CHANGES.

Frequency: monthly (median of daily prints). Unit: basis points, annualized.

Caveats:
- Short history: Yahoo delists expired contracts (6EM25/6EM26/6EU26 already
  gone), so the series starts 2025-04 and the front-contract tenor varies
  (6EZ26 bridges Mar-Dec 2026, up to ~9m). Tenor variation adds noise; the
  formula annualizes so it is comparable.
- EUR leg only. JPY leg is future work: no public machine-readable TIBOR.
- Prior v1 print (-5.0bp, Oct 2026) was NOT reproduced with this
  construction as specified: current inputs give roughly +40 to +55bp for
  Oct 2026. Likely causes: the stale FRED Euribor (~60bp too low) and/or
  T-bill-vs-funding wedge ("TED contamination", plausibly ~50bp given the
  4.19% T-bill vs ~3.7% money-market funding). Direction and changes are
  more trustworthy than the level until the rate inputs are fixed.
"""

import calendar
import datetime
import io
import json
import re
import subprocess

import polars as pl

UA = "Mozilla/5.0 (research; financial-vulnerability-monitor)"

# Contract symbol -> (expiry year, expiry month). CME FX futures expire on
# the third Wednesday of the contract month. Only contracts Yahoo still
# lists are included; expired/delisted ones are dropped as discovered.
CONTRACTS = {
    "6EU25.CME": (2025, 9),
    "6EZ25.CME": (2025, 12),
    "6EH26.CME": (2026, 3),
    "6EZ26.CME": (2026, 12),
    "6EH27.CME": (2027, 3),
    "6EM27.CME": (2027, 6),
    "6EU27.CME": (2027, 9),
}
ROLL_DAYS_BEFORE_EXPIRY = 14
START = datetime.date(2025, 4, 1)


def _third_wednesday(year: int, month: int) -> datetime.date:
    weds = [w[2] for w in calendar.monthcalendar(year, month) if w[2] != 0]
    return datetime.date(year, month, weds[2])


def _yahoo_daily(symbol: str, timeout: int = 60) -> pl.DataFrame:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?period1=0&period2=9999999999&interval=1d"
    )
    last_err: Exception | None = None
    for _attempt in range(3):
        r = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-A", UA, url],
            capture_output=True, text=True,
        )
        try:
            if r.returncode != 0 or not r.stdout.strip():
                raise RuntimeError("empty response")
            if "No data found" in r.stdout[:2000]:
                raise RuntimeError("delisted")
            res = json.loads(r.stdout)["chart"]["result"][0]
            ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
            recs = [
                (datetime.date.fromtimestamp(t), c)
                for t, c in zip(ts, closes)
                if c and c == c
            ]
            if not recs:
                raise RuntimeError("empty series")
            return pl.DataFrame(
                {"date": [d for d, _ in recs], "F": [c for _, c in recs]}
            ).sort("date")
        except (KeyError, IndexError, TypeError, RuntimeError) as e:
            last_err = e
            if "delisted" in str(e):
                break
    raise RuntimeError(f"xccy: no data for {symbol}") from last_err


def _fred_daily(fred_id: str, timeout: int = 60) -> pl.DataFrame:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}"
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", "curl", url],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"xccy: FRED fetch failed for {fred_id}")
    df = pl.read_csv(io.StringIO(r.stdout))
    df = df.rename({df.columns[0]: "date", df.columns[1]: "value"})
    df = df.with_columns(
        pl.col("date").str.to_date(),
        pl.col("value").cast(pl.Float64, strict=False),
    ).drop_nulls("value").sort("date")
    if df.height == 0:
        raise RuntimeError(f"xccy: FRED returned no rows for {fred_id}")
    return df


def _front_contract_series() -> pl.DataFrame:
    """Daily front-contract futures price with expiry, from START."""
    frames = []
    for sym, (yy, mm) in CONTRACTS.items():
        try:
            df = _yahoo_daily(sym)
        except RuntimeError:
            continue  # delisted since the map was written
        frames.append(
            df.with_columns(pl.lit(_third_wednesday(yy, mm)).alias("expiry"))
        )
    if not frames:
        raise RuntimeError("xccy: no futures contracts available")
    fut = pl.concat(frames)
    days = fut.filter(pl.col("date") >= START)["date"].unique().sort()
    rows = []
    for d in days:
        cand = fut.filter(
            (pl.col("date") == d)
            & (pl.col("expiry") > d + datetime.timedelta(days=ROLL_DAYS_BEFORE_EXPIRY))
        )
        if cand.height == 0:
            continue
        r = cand.sort("expiry").head(1)
        rows.append({"date": d, "F": r["F"][0], "expiry": r["expiry"][0]})
    if not rows:
        raise RuntimeError("xccy: no front-contract days")
    return pl.DataFrame(rows).sort("date")


def fetch_xccy_basis() -> pl.DataFrame:
    """Fetch the DIY EUR/USD cross-currency basis.

    Returns date (month) / value (basis points, annualized), monthly median
    of daily DTV(2018) CIP-deviation prints.
    """
    front = _front_contract_series()
    spot = _fred_daily("DEXUSEU").rename({"value": "S"})
    euribor = _fred_daily("IR3TIB01EZM156N").rename({"value": "r_eur"})
    tbill = _fred_daily("DGS3MO").rename({"value": "r_usd"})
    df = front.join_asof(spot, on="date", strategy="backward")
    df = df.join_asof(euribor, on="date", strategy="backward")  # monthly -> ffill
    df = df.join_asof(tbill, on="date", strategy="backward")
    df = df.with_columns(
        ((pl.col("expiry") - pl.col("date")).dt.total_days() / 360.0).alias("tau"),
        (pl.col("r_eur") / 100.0).alias("re"),
        (pl.col("r_usd") / 100.0).alias("ru"),
    )
    df = df.filter(
        pl.col("S").is_not_null()
        & pl.col("re").is_not_null()
        & pl.col("ru").is_not_null()
        & (pl.col("tau") > 0.03)
    )
    if df.height == 0:
        raise RuntimeError("xccy: no complete daily observations")
    # DTV (2018): x = ((S/F)(1 + r_usd*tau) - 1)/tau - r_eur
    df = df.with_columns(
        (
            ((pl.col("S") / pl.col("F")) * (1 + pl.col("ru") * pl.col("tau")) - 1)
            / pl.col("tau")
            - pl.col("re")
        ).alias("x")
    )
    df = df.with_columns(
        (pl.col("x") * 10000).alias("bp"),
        pl.col("date").dt.strftime("%Y-%m-01").str.to_date().alias("month"),
    )
    df = df.filter(
        pl.col("bp").is_not_nan() & ~pl.col("bp").is_infinite()
    )
    monthly = (
        df.group_by("month")
        .agg(pl.col("bp").median().alias("value"), pl.col("bp").count().alias("n"))
        .filter(pl.col("n") >= 3)
        .sort("month")
    )
    if monthly.height == 0:
        raise RuntimeError("xccy: no monthly observations")
    return monthly.select(
        pl.col("month").alias("date"), pl.col("value").cast(pl.Float64)
    )
