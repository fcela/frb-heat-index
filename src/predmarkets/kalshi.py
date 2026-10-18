"""Kalshi public API v2 client (keyless) for the prediction-markets pipeline.

Transport is curl (subprocess), not urllib/requests: urllib hangs behind
this environment's egress proxy while curl is the brief-verified path, and
curl behaves identically on CI runners. The public host rate-limits
aggressive clients (429), so the real transport enforces >= 1.2s between
calls. Every entry point accepts an injected `transport` (url -> JSON body
string) so tests run against fixtures with zero network.

Endpoint patterns validated against the live API:
  GET /events?series_ticker=X&with_nested_markets=true&limit=100  (cursor-paged)
  GET /markets/candlesticks?market_tickers=A,B&start_ts=..&end_ts=..&period_interval=1440
     - period_interval is MINUTES; 1440 = daily candles (an `interval` param is
       silently ignored and defaults the request to 1-minute candles).
     - Response nests as {"markets": [{"market_ticker": T, "candlesticks": [...]}]}.
     - Requests are capped at 10,000 candlesticks; oversized chunks are split
       in half automatically.
     - `market_tickers` (plural) is required; `ticker` is rejected.

Candle price fields come in dollars of a $1 notional, so a price IS a
probability. Days without trades emit a candle carrying only
`price.previous_dollars` (the exchange's carried last price); the close
extraction falls back to it and `volume_fp` = 0 marks those rows.
"""

import datetime as dt
import json
import subprocess
import time
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
CALL_INTERVAL_S = 1.2
CURL_TIMEOUT_S = 45
RETRY_BACKOFF_S = (5.0, 15.0, 45.0)
CANDLE_CHUNK = 10  # tickers per candlesticks call; split on the 10k cap

_ET = ZoneInfo("America/New_York")
_last_call_at = 0.0


class TransportError(RuntimeError):
    """curl-level failure (network, timeout, HTTP 4xx/5xx)."""


class KalshiAPIError(RuntimeError):
    """API-level error envelope ({"msg": ...} / {"error": {...}})."""


class CandleLimitError(KalshiAPIError):
    """Request exceeded the 10,000-candlestick cap; caller should split."""


def curl_get(url: str) -> str:
    """GET via curl, enforcing the tested inter-call interval."""
    global _last_call_at
    wait = CALL_INTERVAL_S - (time.monotonic() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()
    p = subprocess.run(
        ["curl", "-sS", "--max-time", str(CURL_TIMEOUT_S),
         "-w", "\n%{http_code}", url],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise TransportError(f"curl exit {p.returncode}: {p.stderr.strip()[:200]}")
    body, _, code = p.stdout.rpartition("\n")
    code = code.strip()
    if code and not code.startswith("2"):
        raise TransportError(f"HTTP {code} from Kalshi: {body.strip()[:200]}")
    return body


def _raise_on_api_error(payload: dict) -> None:
    err = payload.get("error") or {}
    msg = err.get("message") or payload.get("msg") or ""
    if not msg and "details" not in err:
        return
    if "max candlesticks" in (str(err.get("details")) + msg):
        raise CandleLimitError(
            f"candlestick cap exceeded: {err.get('details') or msg}")
    raise KalshiAPIError(f"Kalshi API error: {err.get('details') or msg}")


def api_get(path: str, params: dict, transport=None) -> dict:
    """GET one API page, with retries on rate-limit/server errors."""
    transport = transport or curl_get
    url = f"{API_BASE}{path}?{urlencode(params)}"
    for attempt in range(len(RETRY_BACKOFF_S) + 1):
        try:
            body = transport(url)
        except TransportError as e:
            text = str(e)
            retryable = "HTTP 429" in text or "HTTP 5" in text
            if retryable and attempt < len(RETRY_BACKOFF_S):
                time.sleep(RETRY_BACKOFF_S[attempt])
                continue
            raise
        payload = json.loads(body)
        _raise_on_api_error(payload)
        return payload
    raise TransportError("unreachable")


def fetch_events(series_ticker: str, transport=None) -> list[dict]:
    """All events for a series (every status), with nested markets.

    The default listing includes long-settled events; they simply carry no
    nested markets, so "open" == has markets.
    """
    events, cursor = [], None
    while True:
        params = {
            "series_ticker": series_ticker,
            "with_nested_markets": "true",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        page = api_get("/events", params, transport)
        events.extend(page.get("events") or [])
        cursor = page.get("cursor") or None
        if not cursor:
            return events


def parse_candles(payload: dict) -> dict[str, list[dict]]:
    """{"markets": [...]} -> {market_ticker: [candlestick, ...]}."""
    out = {}
    for m in payload.get("markets") or []:
        ticker = m.get("market_ticker")
        if ticker:
            out[ticker] = m.get("candlesticks") or []
    return out


def fetch_candles(tickers: list[str], start_ts: int, end_ts: int,
                  transport=None) -> dict[str, list[dict]]:
    """Daily candlesticks for a batch of tickers (chunks; splits on cap)."""
    out: dict[str, list[dict]] = {}
    tickers = sorted(set(tickers))
    for i in range(0, len(tickers), CANDLE_CHUNK):
        chunk = tickers[i:i + CANDLE_CHUNK]
        try:
            got = _candles_chunk(chunk, start_ts, end_ts, transport)
        except CandleLimitError:
            if len(chunk) == 1:
                raise
            mid = len(chunk) // 2
            got = {
                **fetch_candles(chunk[:mid], start_ts, end_ts, transport),
                **fetch_candles(chunk[mid:], start_ts, end_ts, transport),
            }
        out.update(got)
    return out


def _candles_chunk(chunk, start_ts, end_ts, transport):
    params = {
        "market_tickers": ",".join(chunk),
        "start_ts": int(start_ts),
        "end_ts": int(end_ts),
        "period_interval": 1440,
    }
    return parse_candles(api_get("/markets/candlesticks", params, transport))


def candle_close(candle: dict) -> float | None:
    """Daily close price as probability; carried previous close on no-trade days."""
    price = candle.get("price") or {}
    for key in ("close_dollars", "close", "previous_dollars"):
        v = price.get(key)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def candle_volume(candle: dict) -> float:
    try:
        return float(candle.get("volume_fp") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def candle_trading_date(end_period_ts) -> dt.date:
    """Trading day a candle covers: ET date of its period end, minus one day.

    Daily candles close at midnight ET (04:00/05:00 UTC), so the covered
    trading day is the day before the end timestamp's ET date.
    """
    return (dt.datetime.fromtimestamp(int(end_period_ts), _ET).date()
            - dt.timedelta(days=1))


def parse_iso_z(s: str) -> dt.datetime:
    """Parse Kalshi ISO timestamps ('2026-10-28T18:59:00Z') as aware UTC."""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def url_param(url: str, name: str) -> str | None:
    qs = parse_qs(urlparse(url).query)
    vals = qs.get(name)
    return vals[0] if vals else None
