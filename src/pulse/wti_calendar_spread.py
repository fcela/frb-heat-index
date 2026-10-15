"""WTI calendar spread, prompt minus 12-months-ahead (term-structure slope).

Measures: front-month WTI futures price (Yahoo Finance CL=F, continuous
front-month NYMEX contract) minus the price of the NYMEX WTI contract
expiring ~12 calendar months later, in USD per barrel. Positive =
backwardation (tight physical market / low inventories); negative =
contango.

Source (public, no auth): Yahoo Finance v8 chart API, individual NYMEX
contract symbols of the form CL<code><yy>.NYM (e.g. CLV27.NYM = Oct-2027),
  https://query1.finance.yahoo.com/v8/finance/chart/CL=F
  https://query1.finance.yahoo.com/v8/finance/chart/CLV27.NYM
Month codes: F G H J K M N Q U V X Z.

Trail of alternatives tried: the CME quotes site bot-walls curl (HTTP 403);
Nasdaq Data Link's CHRIS continuous-futures API returns HTTP 403 without an
API key (free key requires signup, and the task scope excludes new accounts);
EIA's dnav NYMEX futures page (https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm)
is reachable and publishes contracts 1-4 daily, but only out to ~4 months, so
it cannot supply the 12M leg. Yahoo's per-contract symbols do supply it.

Coverage note: expired contracts are delisted from Yahoo's API, so the 12M
leg only exists for dates whose 12M-ahead contract is still listed. In
practice that limits history to roughly the trailing 12 months (contracts
list ~2 years before expiry). Rows lacking the 12M leg are dropped.

Frequency: daily (trading days). Unit: USD/bbl.
"""

import datetime
import json
import subprocess

import polars as pl

_YAHOO_TMPL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec


def _curl(url: str, timeout: int = 60) -> bytes:
    r = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", _UA, url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"fetch failed for {url}: curl rc={r.returncode}")
    return r.stdout


def _fetch_closes(sym: str, timeout: int = 60) -> pl.DataFrame:
    raw = _curl(_YAHOO_TMPL.format(sym=sym), timeout)
    try:
        res = json.loads(raw.decode())["chart"]["result"][0]
    except Exception as e:
        raise RuntimeError(f"Yahoo {sym} parse failed: {e}")
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    df = pl.DataFrame(
        {
            "date": [
                datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()
                for t in ts
            ],
            "close": closes,
        }
    ).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("close").cast(pl.Float64, strict=False),
    ).drop_nulls("close").sort("date")
    if df.height == 0:
        raise RuntimeError(f"Yahoo {sym}: no close observations")
    return df


def _contract_symbol(year: int, month: int) -> str:
    code = _MONTH_CODES[month - 1]
    return f"CL{code}{year % 100:02d}.NYM"


def _add_months(d: datetime.date, n: int) -> datetime.date:
    m = d.month - 1 + n
    return datetime.date(d.year + m // 12, m % 12 + 1, 1)


def fetch_wti_calendar_spread(timeout: int = 60) -> pl.DataFrame:
    """Fetch WTI prompt-minus-12M calendar spread, daily USD/bbl.

    Returns pl.DataFrame with columns date (pl.Date) and value (pl.Float64).
    Raises RuntimeError on fetch failure or empty output.
    """
    prompt = _fetch_closes("CL=F", timeout).rename({"close": "prompt"})

    # Distinct 12M-ahead contracts needed across all prompt dates.
    needed: dict[tuple[int, int], str] = {}
    for d in prompt["date"]:
        t = _add_months(d, 12)
        needed[(t.year, t.month)] = _contract_symbol(t.year, t.month)

    legs = []
    for (yr, mo), sym in sorted(needed.items()):
        try:
            leg = _fetch_closes(sym, timeout).rename({"close": "leg12m"})
        except RuntimeError:
            continue  # expired/delisted contract: those dates simply drop out
        leg = leg.with_columns(
            pl.lit(datetime.date(yr, mo, 1)).alias("leg_month")
        )
        legs.append(leg)
    if not legs:
        raise RuntimeError("WTI spread: no 12M-ahead contract legs retrievable")

    prompt = prompt.with_columns(
        pl.col("date").map_elements(
            lambda d: _add_months(d, 12).replace(day=1), return_dtype=pl.Date
        ).alias("leg_month")
    )
    curve = pl.concat(legs)
    df = (
        prompt.join(curve, on=["leg_month", "date"], how="inner")
        .sort("date")
        .with_columns((pl.col("prompt") - pl.col("leg12m")).alias("value"))
        .select("date", "value")
    )
    df = df.filter(pl.col("value").is_finite())
    if df.height < 20:
        raise RuntimeError(
            f"WTI spread: only {df.height} overlapping observations (< 20)"
        )
    return df
