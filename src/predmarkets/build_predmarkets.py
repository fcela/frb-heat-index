"""Build the prediction-markets dataset (Kalshi event-market probabilities).

For each indicator series (see indicators.py):
  1. discover open events dynamically (no hardcoded meetings or prints),
  2. fetch daily candlesticks for every live market (batched, curl),
  3. read close prices of $1-notional binary contracts as probabilities,
  4. append to the committed per-indicator CSV (dedupe on date+ticker),
  5. emit the site bundle (front event chart, or the annual ladder for
     the recession series) and per-indicator provenance in _meta.json.

Outputs:
  data/predmarkets/<key>.csv     date,value,event,ticker,strike,volume
  data/predmarkets/_meta.json    per-indicator status + market metadata
  docs/data/predmarkets.json     site bundle (strict JSON, NaN rejected)

Honesty rules: listed-print gaps are marked in notes, never interpolated;
days without trades carry the exchange's previous close (volume = 0 marks
them); settled events stay in the CSV archive but drop out of discovery.

Run: python3 -m src.predmarkets.build_predmarkets [--incremental]
"""

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.predmarkets import kalshi
from src.predmarkets.indicators import (
    FOMC_STRIKE_ORDER,
    INDICATORS,
    MONTH_CODES,
    RELEASE_GAP_DAYS,
)

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data" / "predmarkets"
SITE_JSON = REPO / "docs" / "data" / "predmarkets.json"

CATEGORICAL_COLORS = ["#1d4ed8", "#b45309", "#059669", "#7c3aed", "#be185d", "#0369a1"]
SEQUENTIAL_ANCHORS = [(0x1D, 0x4E, 0xD8), (0xB4, 0x53, 0x09), (0xB2, 0x18, 0x2B)]
CSV_COLUMNS = ["date", "value", "event", "ticker", "strike", "volume"]


# ------------------------------------------------------------------ helpers

def strict_json(obj) -> str:
    """Serialize with NaN rejected loudly (fail-closed, the outage lesson)."""
    return json.dumps(obj, allow_nan=False)


def sanitize_value(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def sequential_colors(n: int) -> list[str]:
    """Ordered ramp through the site palette family (blue -> amber -> dark red)."""
    if n <= 0:
        return []
    if n == 1:
        return ["#1d4ed8"]
    out = []
    span = len(SEQUENTIAL_ANCHORS) - 1
    for i in range(n):
        t = i / (n - 1) * span
        k = min(int(t), span - 1)
        f = t - k
        a, b = SEQUENTIAL_ANCHORS[k], SEQUENTIAL_ANCHORS[k + 1]
        rgb = [round(a[c] + (b[c] - a[c]) * f) for c in range(3)]
        out.append("#%02x%02x%02x" % tuple(rgb))
    return out


def strike_colors(palette: str, n: int) -> list[str]:
    if palette == "sequential":
        return sequential_colors(n)
    return [CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)] for i in range(n)]


# ------------------------------------------------------------ event helpers

def open_events(events: list[dict]) -> list[dict]:
    """Events with nested (live) markets, sorted by close time."""
    live = [e for e in events if e.get("markets")]
    live.sort(key=lambda e: event_close_time(e))
    return live


def event_close_time(event: dict) -> dt.datetime:
    times = [kalshi.parse_iso_z(m["close_time"]) for m in event["markets"]
             if m.get("close_time")]
    return min(times) if times else dt.datetime.max.replace(tzinfo=dt.timezone.utc)


def event_year(event: dict) -> int | None:
    tail = event["event_ticker"].rsplit("-", 1)[-1]
    if len(tail) == 2 and tail.isdigit():
        return 2000 + int(tail)
    if len(tail) == 4 and tail.isdigit():
        return int(tail)
    return None


def event_year_month(event: dict) -> tuple[int, int] | None:
    tail = event["event_ticker"].rsplit("-", 1)[-1]
    if len(tail) == 5 and tail[:2].isdigit() and tail[2:] in MONTH_CODES:
        return 2000 + int(tail[:2]), MONTH_CODES[tail[2:]]
    return None


def event_horizon(event: dict, reg: dict) -> dt.date:
    """Chart marker date: release/meeting day, or calendar year-end for ladders."""
    if reg.get("event_date_mode") == "year_end":
        year = event_year(event)
        if year:
            return dt.date(year, 12, 31)
    return event_close_time(event).date()


def chart_events(reg: dict, evs: list[dict], now: dt.datetime) -> tuple[list[dict], bool]:
    """Events to chart. front = nearest open event; ladder = every open event.

    Returns (events, front_closed). If nothing closes in the future, the
    most recent event is kept and flagged so the gap is noted honestly.
    """
    future = [e for e in evs if event_close_time(e) > now]
    if reg["event_scope"] == "ladder":
        return evs, False
    if future:
        return [future[0]], False
    if evs:
        return [evs[-1]], True
    return [], False


def front_gap_note(reg: dict, front: dict | None, front_closed: bool,
                   now: dt.datetime) -> str | None:
    """Honest note when a nearer scheduled release appears unlisted."""
    today = now.date()
    if front is None:
        return f"No open markets were listed for this series as of {today}."
    if front_closed:
        close = event_close_time(front).date()
        return (f"The most recent listed event closed {close}; no further market "
                f"was listed as of {today}.")
    kind = reg["release_kind"]
    if kind not in RELEASE_GAP_DAYS:
        return None
    days_out = (event_close_time(front).date() - today).days
    if days_out <= RELEASE_GAP_DAYS[kind]:
        return None
    close = event_close_time(front).date()
    if kind == "monthly":
        ym = event_year_month(front)
        if ym:
            prev = dt.date(ym[0], ym[1], 1) - dt.timedelta(days=1)
            missing = f"the {prev.strftime('%B %Y')} print"
        else:
            missing = "the preceding monthly print"
    elif kind == "quarterly":
        missing = "the preceding quarterly release"
    else:
        missing = "the nearer scheduled meeting"
    return (f"No market was listed for {missing} as of {today} — the nearest "
            f"listed market closes {close}. Gaps are marked, not interpolated.")


# ----------------------------------------------------------- strike handling

def _subtitle_core(subtitle: str) -> str:
    s = subtitle.strip()
    if s.lower().startswith("above "):
        s = s[6:]
    return s.rstrip("%").strip()


def strike_label(market: dict, event: dict, reg: dict) -> str:
    order = reg["strike_order"]
    sub = (market.get("subtitle") or "").strip()
    if order == "numeric" and market.get("strike_type") == "greater":
        core = _subtitle_core(sub) or str(market.get("floor_strike") or "?")
        return f"≥{core}%"
    if order == "year":
        year = event_year(event)
        return str(year) if year else (sub or market["ticker"])
    if sub:
        return sub
    return market["ticker"].rsplit("-", 1)[-1]


def strike_rank(market: dict, event: dict, reg: dict) -> tuple:
    order = reg["strike_order"]
    label = strike_label(market, event, reg)
    if order == "fomc":
        return (FOMC_STRIKE_ORDER.get(label, 50), label)
    if order == "numeric":
        try:
            return (float(_subtitle_core(label)), label)
        except ValueError:
            return (float("inf"), label)
    if order == "year":
        try:
            return (int(label), label)
        except ValueError:
            return (float("inf"), label)
    return (0, label)


# ------------------------------------------------------------ frame handling

def candles_to_frame(candles_by_ticker: dict, markets_by_ticker: dict) -> pl.DataFrame:
    rows = []
    for ticker, candles in candles_by_ticker.items():
        meta = markets_by_ticker.get(ticker)
        if meta is None:
            continue
        event = meta["_event"]
        raw_strike = (meta.get("subtitle") or "").strip()
        for c in candles:
            value = kalshi.candle_close(c)
            if value is None:
                continue
            rows.append({
                "date": kalshi.candle_trading_date(c["end_period_ts"]),
                "value": value,
                "event": event["event_ticker"],
                "ticker": ticker,
                "strike": raw_strike,
                "volume": kalshi.candle_volume(c),
            })
    if not rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in CSV_COLUMNS})
    df = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Utf8))
    return df.select(CSV_COLUMNS)


def load_existing(data_dir: Path, key: str) -> pl.DataFrame | None:
    p = data_dir / f"{key}.csv"
    if not p.exists():
        return None
    df = pl.read_csv(p, schema_overrides={"date": pl.Utf8})
    if df.height == 0:
        return None
    return df


def merge_incremental(old: pl.DataFrame | None, new: pl.DataFrame) -> pl.DataFrame:
    """Dedupe on (date, ticker), keep the freshly fetched row last."""
    if old is None or old.height == 0:
        return new
    if new.height == 0:
        return old
    combined = pl.concat([old, new]).unique(subset=["date", "ticker"], keep="last")
    return combined.sort(["date", "ticker"])


def validate_frame(df: pl.DataFrame, key: str) -> None:
    if df.height == 0:
        raise RuntimeError(f"{key}: no candle rows produced")
    bad = df.filter(~pl.col("value").is_finite())
    if bad.height:
        raise RuntimeError(f"{key}: {bad.height} non-finite values")
    out = df.filter((pl.col("value") < 0) | (pl.col("value") > 1))
    if out.height:
        raise RuntimeError(f"{key}: {out.height} probabilities outside [0,1]")


# ---------------------------------------------------------------- build

def _fetch_starts(evs: list[dict], existing: pl.DataFrame | None,
                   incremental: bool) -> dict[str, int]:
    """Per-ticker fetch start timestamps, floored to the day to keep batches fat.

    Incremental: re-fetch from the ticker's last committed date minus a two-day
    overlap. New tickers (and full backfills): from their creation day. Overlap
    is harmless — merge dedupes on (date, ticker) keeping the fresh row.
    """
    starts: dict[str, int] = {}
    last_dates: dict[str, dt.date] = {}
    if existing is not None and existing.height:
        for t, d in existing.group_by("ticker").agg(
                pl.col("date").max().alias("d")).iter_rows():
            last_dates[t] = dt.date.fromisoformat(str(d))
    for ev in evs:
        for m in ev["markets"]:
            ticker = m["ticker"]
            if incremental and ticker in last_dates:
                day = last_dates[ticker] - dt.timedelta(days=2)
            else:
                created = kalshi.parse_iso_z(m["created_time"]) - dt.timedelta(hours=12)
                day = created.date()
            start = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
            starts[ticker] = int(start.timestamp())
    return starts


def _fetch_all_candles(starts: dict[str, int], now: dt.datetime, transport):
    by_start: dict[int, list[str]] = {}
    for ticker, ts in starts.items():
        by_start.setdefault(ts, []).append(ticker)
    candles: dict[str, list[dict]] = {}
    for ts in sorted(by_start):
        got = kalshi.fetch_candles(by_start[ts], ts, int(now.timestamp()), transport)
        candles.update(got)
    return candles


def _site_entry(key: str, reg: dict, evs: list[dict], chart_evs: list[dict],
                df: pl.DataFrame) -> tuple[dict, dict]:
    """Assemble the site-bundle entry and the _meta entry for one indicator."""
    chart_markets = []
    for ev in chart_evs:
        for m in ev["markets"]:
            chart_markets.append((m, ev))
    chart_markets.sort(key=lambda me: strike_rank(me[0], me[1], reg))

    strikes = [strike_label(m, ev, reg) for m, ev in chart_markets]
    last_value = {label: None for label in strikes}
    colors = strike_colors(reg["palette"], len(strikes))
    strike_colors_map = dict(zip(strikes, colors))

    chart_tickers = {m["ticker"] for m, _ in chart_markets}
    series = []
    if df.height and chart_markets:
        view = df.filter(pl.col("ticker").is_in(sorted(chart_tickers)))
        ticker_label = {m["ticker"]: strike_label(m, ev, reg)
                        for m, ev in chart_markets}
        for r in view.sort(["date", "ticker"]).iter_rows(named=True):
            label = ticker_label[r["ticker"]]
            value = sanitize_value(r["value"])
            series.append({"date": r["date"], "strike": label, "value": value})
            last_value[label] = value

    if reg["event_scope"] == "ladder":
        horizons = [{
            "label": f"{strike_label(ev['markets'][0], ev, reg)} year-end",
            "date": str(event_horizon(ev, reg)),
            "event_ticker": ev["event_ticker"],
        } for ev in chart_evs]
    else:
        horizons = [{
            "label": chart_evs[0].get("title", ""),
            "date": str(event_horizon(chart_evs[0], reg)),
            "event_ticker": chart_evs[0]["event_ticker"],
        }] if chart_evs else []

    entry = {
        "key": key,
        "title": reg["title"],
        "why": reg["why"],
        "source": reg["source"],
        "frequency": reg["frequency"],
        "unit": reg["unit"],
        "status": "live",
        "note": reg["note"],
        "event_title": chart_evs[0].get("title", "") if chart_evs else "",
        "event_ticker": chart_evs[0]["event_ticker"] if chart_evs else None,
        "event_scope": reg["event_scope"],
        "horizons": horizons,
        "strikes": strikes,
        "strike_colors": strike_colors_map,
        "last_observation": str(df["date"].max()) if df.height else None,
        "first_observation": str(df["date"].min()) if df.height else None,
        "n_observations": df.height,
        "last_value": last_value,
        "series": series,
    }
    meta_markets = [{
        "ticker": m["ticker"],
        "strike": strike_label(m, ev, reg),
        "status": m.get("status"),
        "volume": float(m.get("volume_fp") or 0),
        "open_interest": float(m.get("open_interest_fp") or 0),
        "close_time": m.get("close_time"),
        "created_time": m.get("created_time"),
    } for m, ev in chart_markets]
    meta_entry = {k: v for k, v in entry.items() if k != "series"}
    meta_entry["series_ticker"] = reg["series_ticker"]
    meta_entry["open_events"] = [{
        "event_ticker": ev["event_ticker"],
        "title": ev.get("title", ""),
        "close_time": str(event_close_time(ev)),
        "n_markets": len(ev["markets"]),
        "charted": ev in chart_evs,
    } for ev in evs]
    meta_entry["markets"] = meta_markets
    return entry, meta_entry


def build(incremental: bool = False, transport=None, data_dir: Path = DATA_DIR,
          site_json: Path = SITE_JSON) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    site_json.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    site_indicators, meta_indicators = [], {}

    for key, reg in INDICATORS.items():
        try:
            events = kalshi.fetch_events(reg["series_ticker"], transport)
            evs = open_events(events)
            chart_evs, front_closed = chart_events(reg, evs, now)
            existing = load_existing(data_dir, key)
            starts = _fetch_starts(evs, existing, incremental)
            candles = _fetch_all_candles(starts, now, transport)

            markets_by_ticker = {}
            for ev in evs:
                for m in ev["markets"]:
                    m["_event"] = ev
                    markets_by_ticker[m["ticker"]] = m
            new_df = candles_to_frame(candles, markets_by_ticker)
            df = merge_incremental(existing, new_df)
            if df.height or evs:
                # Rows are only demanded when open markets exist; a series
                # with nothing open and nothing archived is a status, not a
                # pipeline failure.
                validate_frame(df, key)
                df.write_csv(data_dir / f"{key}.csv")

            entry, meta_entry = _site_entry(key, reg, evs, chart_evs, df)
            if not chart_evs:
                entry["status"] = meta_entry["status"] = "no-open-market"
                note = (reg["note"] + " " +
                        front_gap_note(reg, None, False, now)).strip()
                entry["note"] = meta_entry["note"] = note
            else:
                gap = front_gap_note(reg, chart_evs[0], front_closed, now)
                if gap:
                    entry["note"] = meta_entry["note"] = reg["note"] + " " + gap
            strict_json(entry["series"])
            site_indicators.append(entry)
            meta_indicators[key] = meta_entry
        except Exception as e:
            err = str(e)[:200]
            entry = {
                "key": key, "title": reg["title"], "why": reg["why"],
                "source": reg["source"], "frequency": reg["frequency"],
                "unit": reg["unit"], "status": "error", "note": reg["note"],
                "event_title": "", "event_ticker": None,
                "event_scope": reg["event_scope"], "horizons": [],
                "strikes": [], "strike_colors": {},
                "last_observation": None, "first_observation": None,
                "n_observations": 0, "last_value": {}, "series": [],
            }
            meta_entry = dict(entry)
            meta_entry.pop("series")
            meta_entry["error"] = err
            site_indicators.append(entry)
            meta_indicators[key] = meta_entry

    built = now.isoformat(timespec="seconds")
    meta = {
        "built_at_utc": built,
        "incremental": incremental,
        "api_base": kalshi.API_BASE,
        "n_indicators": len(meta_indicators),
        "n_live": sum(1 for v in meta_indicators.values() if v["status"] == "live"),
        "n_no_open_market": sum(1 for v in meta_indicators.values()
                                if v["status"] == "no-open-market"),
        "n_error": sum(1 for v in meta_indicators.values() if v["status"] == "error"),
        "indicators": meta_indicators,
    }
    (data_dir / "_meta.json").write_text(strict_json(meta) + "\n")

    site = {"meta": {"built_at_utc": built}, "indicators": site_indicators}
    site_json.write_text(strict_json(site) + "\n")
    json.loads(site_json.read_text())  # strict re-parse of what the site serves
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true")
    args = ap.parse_args()
    m = build(incremental=args.incremental)
    print(f"live: {m['n_live']}/{m['n_indicators']}  "
          f"no-open-market: {m['n_no_open_market']}  errors: {m['n_error']}")
    for k, v in m["indicators"].items():
        if v["status"] == "error":
            print(f"  {k}: ERROR — {v['error']}")
        else:
            print(f"  {k}: {v['status']}, {v['n_observations']} obs, "
                  f"last {v['last_observation']}")
