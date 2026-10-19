"""Build the prediction-markets dataset (Kalshi event-market probabilities).

For each indicator series (see indicators.py):
  1. discover open events dynamically (no hardcoded meetings or prints),
  2. fetch daily candlesticks for every live market (batched, curl),
  3. read close prices of $1-notional binary contracts as probabilities,
  4. append to the committed per-indicator CSV (dedupe on date+ticker),
  5. emit the site bundle (front event chart, or the annual ladder for
     the recession series) and per-indicator provenance in _meta.json.

Realized outcomes (work item 3): every run also refreshes the keyless
FRED feeds (fred.py), rebuilds data/predmarkets/realized.csv (prints
frozen at first observation, statuses updating — see realized.py), and
attaches realized markers, per-indicator track records, and the FOMC
decision->rate fan chart to the site bundle.

Outputs:
  data/predmarkets/<key>.csv     date,value,event,ticker,strike,volume
  data/predmarkets/realized.csv  indicator,period,event,kind,value,...
  data/predmarkets/fred/*.csv    raw FRED responses, preserved verbatim
  data/predmarkets/_meta.json    per-indicator status + market metadata
  docs/data/predmarkets.json     site bundle (strict JSON, NaN rejected)

Honesty rules: listed-print gaps are marked in notes, never interpolated;
days without trades carry the exchange's previous close (volume = 0 marks
them); settled events stay in the CSV archive but drop out of discovery;
realized vintages are stated, never silently spliced.

Run: python3 -m src.predmarkets.build_predmarkets [--incremental]
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.predmarkets import distribution as distmod
from src.predmarkets import fred
from src.predmarkets import kalshi
from src.predmarkets import realized as realized_mod
from src.predmarkets.indicators import (
    FOMC_BUCKET_DELTAS,
    FOMC_FAN_LEVEL_NOTE,
    FOMC_STRIKE_ORDER,
    INDICATORS,
    KALSHI_MARKET_URL,
    MONTH_CODES,
    PAPER_DOI,
    RELEASE_GAP_DAYS,
    SPEC_VERIFIED,
    kalshi_event_url,
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
    """The event's last trading session: the LATEST close among its nested
    markets. A settled early-closer inside a live event (e.g. a confirmed
    IPO company) must not make the open event look expired — the event
    trades until its last market closes."""
    times = [kalshi.parse_iso_z(m["close_time"]) for m in event["markets"]
             if m.get("close_time")]
    return max(times) if times else dt.datetime.max.replace(tzinfo=dt.timezone.utc)


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


# --------------------------------------------------- measures / references

def _fmt_day(d: dt.date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _month_name(y: int, m: int) -> str:
    return f"{dt.date(y, m, 1):%B} {y}"


def _front_reference(key: str, ev: dict | None,
                     close_times: dict[str, dt.datetime],
                     now: dt.datetime) -> tuple[str | None, dt.date | None]:
    """(reference-period text, resolution date) for an indicator's front
    event, derived from the event ticker and the exchange's close time —
    never a hardcoded calendar."""
    if ev is None:
        return None, None
    ticker = ev["event_ticker"]
    close = close_times.get(ticker)
    if key in ("fomc_decision", "fed_funds"):
        if close is None:
            return None, None
        return _fmt_day(close.date()), close.date()
    m = re.match(r"KXCPIYOY-(\d{2})([A-Z]{3})$", ticker)
    if m and key == "cpi_yoy":
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            y = 2000 + int(m.group(1))
            return (_month_name(y, mon),
                    close.date() if close else None)
    m = re.match(r"KXU3-(\d{2})([A-Z]{3})$", ticker)
    if m and key == "unemployment":
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            y = 2000 + int(m.group(1))
            return (_month_name(y, mon),
                    close.date() if close else None)
    m = re.match(r"KXGDP-(\d{2})([A-Z]{3})(\d{2})$", ticker)
    if m and key == "gdp_quarterly":
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            release = dt.date(2000 + int(m.group(1)), mon, int(m.group(3)))
            quarter = fred.quarter_of_release(release)
            if quarter:
                return quarter, release
    m = re.match(r"KXIPO-(\d{2}|\d{4})$", ticker)
    if m and key == "ipo_pipeline":
        y = int(m.group(1)) if len(m.group(1)) == 4 else 2000 + int(m.group(1))
        return (f"calendar year {y}", dt.date(y, 12, 31))
    return None, None


def _measures_text(m: dict) -> str:
    """One-line card text from the measures fields."""
    if not m.get("quantity"):
        return ""
    parts = [m["quantity"]]
    if m.get("reference"):
        parts.append(f"Reference: {m['reference']}.")
    if m.get("source_release"):
        parts.append(f"Source: {m['source_release']}.")
    if m.get("resolution"):
        parts.append(f"Resolution: {m['resolution']}.")
    return " ".join(parts)


def _measures_block(key: str, reg: dict, ev: dict | None,
                    close_times: dict[str, dt.datetime],
                    now: dt.datetime) -> dict:
    """Card-ready definition: exact quantity, reference period, source
    release, resolution date — static text verified against the exchange's
    listed contract rules (SPEC_VERIFIED), with the reference/resolution
    filled dynamically from the front event."""
    spec = reg.get("measures") or {}
    # Ladder indicators (recession) receive the whole charted-event list;
    # their reference is built from the per-year rule windows below.
    ref, res_date = ((None, None) if key == "recession"
                     else _front_reference(key, ev, close_times, now))
    if key == "recession":
        # Ladder: reference text covers every charted year's rule window.
        bits = []
        for e in (ev if isinstance(ev, list) else [ev] if ev else []):
            w = realized_mod.RECESSION_WINDOWS.get(e["event_ticker"])
            if w:
                bits.append(f"{realized_mod._recession_year(e['event_ticker'])}: "
                            f"{w[0]}\u2013{w[1]}")
        ref = ("two consecutive negative real-GDP quarters within "
               + ("; ".join(bits) if bits
                  else "the windows stated in each listed market's rules"))
        res_date = None
    if ref is None:
        ref = f"no open market listed as of {now.date().isoformat()}"
    res_txt = spec.get("resolution", "")
    if res_date is not None:
        res_txt = (f"{res_txt} — expected {res_date.isoformat()}"
                   if res_txt else f"expected {res_date.isoformat()}")
    text = (f"{spec.get('quantity', '')} Reference: {ref}. "
            f"Source: {spec.get('source_release', '')}. "
            f"Resolution: {res_txt}." if spec.get("quantity") else "")
    return {
        "quantity": spec.get("quantity"),
        "reference": ref,
        "source_release": spec.get("source_release"),
        "resolution": res_txt,
        "resolution_date": res_date.isoformat() if res_date else None,
        "verified": SPEC_VERIFIED,
        "text": text,
    }


# ------------------------------------------------------------ strike handling

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
        # ">" not ">=": the listed rules settle on strict inequalities
        # ("increases by more than X%", "above X%"), so an exact X.0
        # print resolves the strike to No.
        return f">{core}%"
    if order == "year":
        year = event_year(event)
        return str(year) if year else (sub or market["ticker"])
    if order == "company":
        # KXIPO-style: subtitle empty; company code is the ticker suffix.
        return sub or market["ticker"].rsplit("-", 1)[-1]
    if order == "tail":
        return sub or market["ticker"]
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
    if order == "tail":
        return (TAIL_STRIKE_ORDER.get(label, 50), label)
    return (0, label)


# Fixed display order for the derived tail-risk series.
TAIL_STRIKE_ORDER = {
    "CPI > 3%": 0,
    "CPI > 4%": 1,
    "GDP < 1.5%": 2,
    "GDP < 0%": 3,
}

# Realized FOMC decisions, meeting date -> decision bucket (Kalshi wording).
# Public record (FOMC statements); verified 2026-10-18 against the Fed's
# 2026 meeting chronology. "Hike 0bps" is Kalshi's label for no change.
REALIZED_FOMC = {
    "2025-09-17": "Cut 25bps",
    "2025-10-29": "Cut 25bps",
    "2025-12-10": "Cut 25bps",
    "2026-01-28": "Hike 0bps",
    "2026-03-18": "Hike 0bps",
    "2026-04-29": "Hike 0bps",
    "2026-06-17": "Hike 0bps",
    "2026-07-29": "Hike 0bps",
    "2026-09-16": "Hike 25bps",
}


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
    # Legacy CSVs may carry numeric strikes (inferred Float64); the frame
    # contract is String strikes.
    if df.schema.get("strike") != pl.Utf8:
        df = df.with_columns(pl.col("strike").cast(pl.Utf8))
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


# ------------------------------------------- implied-distribution machinery

# Fixed outcome grids (cell edges) for the density heatmaps, per indicator.
DIST_GRIDS = {
    "fed_funds": [round(0.0 + 0.25 * i, 2) for i in range(25)],      # 0–6%
    "cpi_yoy": [round(2.0 + 0.1 * i, 2) for i in range(36)],        # 2–5.5%
    "gdp_quarterly": [round(0.0 + 0.25 * i, 2) for i in range(19)],  # 0–4.5%
    "unemployment": [round(3.5 + 0.1 * i, 2) for i in range(21)],   # 3.5–5.5%
}
DIST_QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.95]

# Tail-risk definitions: (label, source_key, predicate on bins).
# Predicates see bins as {low, high, mass}; None = open end.
TAIL_DEFS = [
    ("CPI > 3%", "cpi_yoy",
     lambda b: b["low"] is not None and b["low"] >= 3.0),
    ("CPI > 4%", "cpi_yoy",
     lambda b: b["low"] is not None and b["low"] >= 4.0),
    ("GDP < 1.5%", "gdp_quarterly",
     lambda b: b["high"] is not None and b["high"] <= 1.5),
    ("GDP < 0%", "gdp_quarterly",
     lambda b: b["high"] is not None and b["high"] <= 0.0),
]


def _ladder_on(df: pl.DataFrame, date: str, event: str) -> list[tuple[float, float]]:
    """(strike level, probability) pairs for one date × event."""
    out = []
    for r in df.filter(
            (pl.col("date") == date) & (pl.col("event") == event)
            ).iter_rows(named=True):
        level = distmod.parse_level(r["strike"])
        if level is not None:
            out.append((level, r["value"]))
    return out


def _front_event_on(df: pl.DataFrame, date: str,
                    close_times: dict[str, dt.datetime]) -> str | None:
    """Front event on a date: nearest close among events with data that day
    that had not yet closed."""
    cands = df.filter(pl.col("date") == date)["event"].unique().to_list()
    best, best_close = None, None
    for e in cands:
        ct = close_times.get(e)
        if ct is None:
            ct = _infer_horizon(e)
            if ct is None:
                continue
            ct = dt.datetime.combine(ct, dt.time.min, tzinfo=dt.timezone.utc)
        if ct.date() > dt.date.fromisoformat(date):
            if best_close is None or ct < best_close:
                best, best_close = e, ct
    return best


def _infer_horizon(event_ticker: str) -> dt.date | None:
    """Horizon date inferred from the event ticker, for ordering events.

    Proxies are month-accurate (good for front-event ordering); exact
    meeting/release days come from the API's close_time where available.
    """
    # FOMC decision / fed funds level: KXFED(DECISION)-YYMON. Meetings fall in
    # the latter half of the month; month-end is the ordering-safe proxy
    # (the API's close_time is used where available).
    m = re.match(r"KXFED(?:DECISION)?-(\d{2})([A-Z]{3})$", event_ticker)
    if m:
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            y = 2000 + int(m.group(1))
            last = (dt.date(y + (mon == 12), mon % 12 + 1, 1)
                    - dt.timedelta(days=1)).day
            return dt.date(y, mon, last)
        return None
    # CPI / unemployment: KX(CPIYOY|U3)-YYMON -> release ~mid following month
    m = re.match(r"KX(?:CPIYOY|U3)-(\d{2})([A-Z]{3})$", event_ticker)
    if m:
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            y, mo = 2000 + int(m.group(1)), mon + 1
            if mo > 12:
                y, mo = y + 1, 1
            return dt.date(y, mo, 15)
        return None
    # GDP: KXGDP-YYMONDD -> release date is in the ticker
    m = re.match(r"KXGDP-(\d{2})([A-Z]{3})(\d{2})$", event_ticker)
    if m:
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            return dt.date(2000 + int(m.group(1)), mon, int(m.group(3)))
        return None
    # Annual ladders: KXRECSSNBER-YY(YY), KXIPO-YY(YY) -> year end
    m = re.match(r"KX(?:RECSSNBER|IPO)-(\d{2}|\d{4})$", event_ticker)
    if m:
        y = m.group(1)
        return dt.date(2000 + int(y) if len(y) == 2 else int(y), 12, 31)
    return None


def build_distribution_views(key: str, df: pl.DataFrame,
                             close_times: dict[str, dt.datetime]
                             ) -> tuple[pl.DataFrame, list[dict], dict]:
    """Per-date implied distributions for a threshold indicator.

    Returns (dist_csv_frame, fan_series, heatmap). The fan series holds
    quantile rows for the front event each day; the heatmap holds the fixed
    grid plus per-date cell masses. Moments come from the same bins.
    """
    grid = DIST_GRIDS[key]
    dist_rows, fan, heat_cells = [], [], []
    dates = sorted(df["date"].unique().to_list())
    for date in dates:
        ev = _front_event_on(df, date, close_times)
        if ev is None:
            continue
        ladder = _ladder_on(df, date, ev)
        if len(ladder) < 2:
            continue
        levels = [lv for lv, _ in ladder]
        probs = [p for _, p in ladder]
        bins = distmod.recover_threshold_bins(levels, probs)
        qs = distmod.distribution_quantiles(bins, DIST_QUANTILES)
        if any(v is None for v in qs.values()):
            continue
        mo = distmod.distribution_moments(bins)
        dist_rows.append({
            "date": date, "event": ev,
            "q05": qs[0.05], "q25": qs[0.25], "q50": qs[0.50],
            "q75": qs[0.75], "q95": qs[0.95],
            "mean": mo["mean"], "variance": mo["variance"],
        })
        fan.append({
            "date": date, "event": ev,
            "q05": qs[0.05], "q25": qs[0.25], "q50": qs[0.50],
            "q75": qs[0.75], "q95": qs[0.95],
        })
        heat_cells.append({
            "date": date, "event": ev,
            "masses": distmod.allocate_to_grid(bins, grid),
        })
    dist_df = (pl.DataFrame(dist_rows) if dist_rows
               else pl.DataFrame(schema={
                   "date": pl.Utf8, "event": pl.Utf8, "q05": pl.Float64,
                   "q25": pl.Float64, "q50": pl.Float64, "q75": pl.Float64,
                   "q95": pl.Float64, "mean": pl.Float64,
                   "variance": pl.Float64}))
    heatmap = {"grid": grid, "cells": heat_cells}
    return dist_df, fan, heatmap


def build_tail_risks(frames: dict[str, pl.DataFrame],
                     close_times: dict[str, dict[str, dt.datetime]]
                     ) -> pl.DataFrame:
    """Daily tail probabilities summed from the CPI/GDP implied distributions
    (paper Figure 13). One row per date × tail definition."""
    rows = []
    for label, src_key, pred in TAIL_DEFS:
        df = frames.get(src_key)
        if df is None or df.height == 0:
            continue
        for date in sorted(df["date"].unique().to_list()):
            ev = _front_event_on(df, date, close_times.get(src_key, {}))
            if ev is None:
                continue
            ladder = _ladder_on(df, date, ev)
            if len(ladder) < 2:
                continue
            bins = distmod.recover_threshold_bins(
                [lv for lv, _ in ladder], [p for _, p in ladder])
            mass = sum(b["mass"] for b in bins if pred(b))
            rows.append({
                "date": date, "value": mass, "event": ev,
                "ticker": f"TAIL-{label}", "strike": label, "volume": 0.0,
            })
    if not rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in CSV_COLUMNS})
    df = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Utf8))
    return df.select(CSV_COLUMNS).sort(["date", "strike"])


# FOMC meeting dates by event ticker (decision day; public FOMC calendar).
FOMC_EVENT_DATES = {
    "KXFEDDECISION-26SEP": "2026-09-16",
    "KXFEDDECISION-26OCT": "2026-10-28",
    "KXFEDDECISION-26DEC": "2026-12-09",
}

# The KXFED level events price the same meetings as the decision events;
# resolved level events lose their API close times just like the decision
# events do, so the same public-record calendar fills their archive gaps.
FOMC_LEVEL_EVENT_DATES = {
    f"KXFED-{k.rsplit('-', 1)[-1]}": v for k, v in FOMC_EVENT_DATES.items()
}


def archive_close_times(key: str,
                        live: dict[str, dt.datetime]) -> dict[str, dt.datetime]:
    """Live close times plus the public-record meeting calendar for FOMC
    series. Resolved events stop being listed with nested markets, so their
    close times vanish from the API; the public FOMC calendar (already the
    track record's source) fills those gaps. Live API times always win."""
    if key not in ("fomc_decision", "fed_funds"):
        return live
    out = dict(live)
    table = (FOMC_EVENT_DATES if key == "fomc_decision"
             else FOMC_LEVEL_EVENT_DATES)
    for ev_t, day in table.items():
        out.setdefault(ev_t,
                       dt.datetime.fromisoformat(day).replace(
                           tzinfo=dt.timezone.utc))
    return out


def build_fomc_track_record(df: pl.DataFrame) -> list[dict]:
    """Day-before mode bucket vs realized decision, per resolved meeting.

    For each event whose meeting date has a realized decision on record, take
    the mode bucket (highest-probability) on the last trading day with data
    before the meeting and grade it against the realized bucket. The archive
    starts at one meeting (Sep 2026); it extends automatically as meetings
    settle and their markets enter the archive.
    """
    out = []
    for event, meeting in FOMC_EVENT_DATES.items():
        realized = REALIZED_FOMC.get(meeting)
        if realized is None:
            continue
        ev_df = df.filter(pl.col("event") == event)
        if ev_df.height == 0:
            continue
        days = [d for d in ev_df["date"].unique().to_list() if d < meeting]
        if not days:
            continue
        last_day = max(days)
        day_df = ev_df.filter(pl.col("date") == last_day)
        if day_df.height == 0:
            continue
        best = day_df.sort("value", descending=True).row(0, named=True)
        mode_bucket = best["strike"].strip()
        out.append({
            "meeting": meeting,
            "event": event,
            "as_of": last_day,
            "mode_bucket": mode_bucket,
            "mode_probability": round(float(best["value"]), 4),
            "realized_bucket": realized,
            "hit": mode_bucket == realized,
        })
    return sorted(out, key=lambda r: r["meeting"])


def _day_before_rows(df: pl.DataFrame, event: str,
                     horizon: dt.date) -> pl.DataFrame:
    """Rows on the last trading day with data strictly before the
    resolution morning (markets close 8:29 AM ET on release day, so the
    release-day candle is the prior session's; data always stops at or
    before the horizon, which keeps this honest even when the exact
    close time is only proxied)."""
    days = [d for d in df.filter(pl.col("event") == event)["date"]
            .unique().to_list() if d < horizon.isoformat()]
    if not days:
        return df.clear()
    return df.filter((pl.col("event") == event)
                     & (pl.col("date") == max(days)))


def build_distribution_track_record(key: str, df: pl.DataFrame,
                                    close_times: dict[str, dt.datetime],
                                    realized_df: pl.DataFrame,
                                    now: dt.datetime) -> list[dict]:
    """For every RESOLVED event in the archive: the market's last-day
    implied distribution (median + 50%/90% interval) vs the realized
    value from the realized dataset (FRED prints frozen at first
    observation). Extends the FOMC track-record pattern to the
    distributional indicators (CPI, GDP, unemployment, fed funds)."""
    out = []
    if df is None or df.height == 0:
        return out
    for event in sorted(df["event"].unique().to_list()):
        ct = close_times.get(event)
        horizon = ct.date() if ct is not None else _infer_horizon(event)
        if horizon is None or dt.datetime.combine(
                horizon, dt.time.min, tzinfo=dt.timezone.utc) > now:
            continue  # not resolved yet
        day = _day_before_rows(df, event, horizon)
        if day.height < 2:
            continue
        ladder = [(lv, p) for lv, p in
                  ((distmod.parse_level(r["strike"]), r["value"])
                   for r in day.iter_rows(named=True))
                  if lv is not None]
        if len(ladder) < 2:
            continue
        bins = distmod.recover_threshold_bins([l for l, _ in ladder],
                                              [p for _, p in ladder])
        qs = distmod.distribution_quantiles(bins, DIST_QUANTILES)
        if any(v is None for v in qs.values()):
            continue
        rz = realized_mod.realized_lookup(realized_df, key, event)
        realized_value = rz["value"] if rz else None
        out.append({
            "event": event,
            "period": rz["period"] if rz else None,
            "as_of": max(day["date"].to_list()),
            "q05": qs[0.05], "q25": qs[0.25], "q50": qs[0.50],
            "q75": qs[0.75], "q95": qs[0.95],
            "realized": realized_value,
            "hit_50": (realized_value is not None
                       and qs[0.25] <= realized_value <= qs[0.75]),
            "hit_90": (realized_value is not None
                       and qs[0.05] <= realized_value <= qs[0.95]),
            "note": (rz["note"] if rz else
                     "Realized print not yet available — marked, "
                     "not guessed."),
        })
    return out


# FOMC fan chart: translate the decision buckets into implied post-
# meeting target-range upper bounds. Levels are upper bounds (the
# quantity the KXFED family settles on), the pre-meeting base is read
# per meeting from the daily target-range series, and ">25bps" buckets
# map to the smallest move in their family (see FOMC_FAN_LEVEL_NOTE).
def _bucket_probs_on(df: pl.DataFrame, event: str, date: str) -> list[tuple[str, float]]:
    rows = df.filter((pl.col("event") == event) & (pl.col("date") == date))
    return [(r["strike"].strip(), r["value"]) for r in rows.iter_rows(named=True)]


def _discrete_quantiles(points: list[tuple[float, float]]) -> dict | None:
    """Quantiles of a finite distribution over exact levels.

    Strict convention: the q-quantile is the smallest level x with
    P(X <= x) > q. A >= comparison would hide a priced tail that lands
    exactly at the band edge (e.g. 5% on Hike 25bps would leave q95 at
    the base and the band would silently drop the hike), so the fan
    bands always show every outcome carrying at least the tail mass.
    """
    pts = sorted((lv, p) for lv, p in points if p and p > 0)
    if not pts:
        return None
    total = sum(p for _, p in pts)
    if total <= 0:
        return None
    pts = [(lv, p / total) for lv, p in pts]
    out = {}
    cum = 0.0
    cum_at = []  # (level, cumulative mass strictly through that level)
    for lv, p in pts:
        cum += p
        cum_at.append((lv, cum))
    for q in DIST_QUANTILES:
        level = cum_at[-1][0]
        for lv, c in cum_at:
            if c > q:
                level = lv
                break
        out[q] = level
    return out


def build_fomc_fan(df: pl.DataFrame, front_event: str | None,
                   close_times: dict[str, dt.datetime],
                   ffr: pl.DataFrame, now: dt.datetime) -> dict | None:
    """Market-implied post-meeting rate: daily median + 50%/90% bands
    for the FRONT meeting, plus realized post-meeting marks for every
    resolved meeting in the archive. Returns None (and the card notes
    why) when the base rate cannot be established."""
    if front_event is None:
        return None
    ct = close_times.get(front_event)
    meeting = ct.date() if ct is not None else None
    if meeting is None:
        return {"note": "Meeting date unavailable — fan withheld.", "rows": [],
                "realized": [], "base_rate": None}
    base, _ = fred.fed_funds_around(ffr, meeting)
    if base is None:
        return {"note": ("Pre-meeting target-range base unavailable from "
                         "the daily series — fan withheld rather than "
                         "guessed."), "rows": [], "realized": [],
                "base_rate": None}
    rows = []
    for date in sorted(df.filter(pl.col("event") == front_event)["date"]
                       .unique().to_list()):
        probs = _bucket_probs_on(df, front_event, date)
        points = []
        for bucket, p in probs:
            delta = FOMC_BUCKET_DELTAS.get(bucket)
            if delta is not None:
                points.append((round(base + delta, 2), float(p)))
        qs = _discrete_quantiles(points)
        if qs is None or any(v is None for v in qs.values()):
            continue
        rows.append({"date": date, "event": front_event,
                     "q05": qs[0.05], "q25": qs[0.25], "q50": qs[0.50],
                     "q75": qs[0.75], "q95": qs[0.95]})
    realized = []
    for event in sorted(df["event"].unique().to_list()):
        rct = close_times.get(event)
        rmeeting = rct.date() if rct is not None else None
        if rmeeting is None or dt.datetime.combine(
                rmeeting, dt.time.min, tzinfo=dt.timezone.utc) > now:
            continue
        rbase, post = fred.fed_funds_around(ffr, rmeeting)
        if post is None:
            continue
        day = _day_before_rows(df, event, rmeeting)
        entry = {"meeting": rmeeting.isoformat(), "event": event,
                 "post_meeting_upper": post, "base_rate": rbase}
        if day.height and rbase is not None:
            probs = _bucket_probs_on(df, event, max(day["date"].to_list()))
            points = [(round(rbase + FOMC_BUCKET_DELTAS[b], 2), float(p))
                      for b, p in probs if b in FOMC_BUCKET_DELTAS]
            qs = _discrete_quantiles(points)
            if qs and all(v is not None for v in qs.values()):
                entry.update({"as_of": max(day["date"].to_list()),
                              "q05": qs[0.05], "q25": qs[0.25],
                              "q50": qs[0.50], "q75": qs[0.75],
                              "q95": qs[0.95],
                              "hit_50": qs[0.25] <= post <= qs[0.75],
                              "hit_90": qs[0.05] <= post <= qs[0.95]})
        realized.append(entry)
    return {
        "meeting": meeting.isoformat(),
        "base_rate": base,
        "level_note": FOMC_FAN_LEVEL_NOTE,
        "rows": rows,
        "realized": realized,
        "note": ("Post-meeting target-range upper bound implied by the "
                 f"decision buckets, with the pre-meeting base {base:.2f}% "
                 "recorded per meeting."),
    }


def _company_of(ticker: str) -> str:
    return ticker.rsplit("-", 1)[-1]


def build_ipo_expected(df: pl.DataFrame,
                       close_times: dict[str, dt.datetime],
                       settled_yes_tickers: set[str] | None = None
                       ) -> pl.DataFrame:
    """Expected IPO count per day: the sum of company Yes prices for the
    front (current-year) event PLUS 1.0 for every already-confirmed
    company (its market settled Yes and stopped trading, so it is no
    longer in the day's price sum — counting it at 1.0 keeps the
    construction 'sum of Yes prices across the named companies' intact
    across settlements; without this the series would step down on
    every confirmation, misreading a confirmation as decay)."""
    settled_yes_tickers = settled_yes_tickers or set()
    last_dates = {}
    if settled_yes_tickers and df.height:
        for t, d in df.filter(pl.col("ticker").is_in(sorted(settled_yes_tickers))
                              ).group_by("ticker").agg(
                pl.col("date").max().alias("d")).iter_rows():
            last_dates[t] = str(d)
    rows = []
    for date in sorted(df["date"].unique().to_list()):
        ev = _front_event_on(df, date, close_times)
        if ev is None:
            continue
        day = df.filter((pl.col("date") == date) & (pl.col("event") == ev))
        if day.height == 0:
            continue
        exp_n = float(day["value"].sum())
        confirmed_by_then = sum(1 for t, ld in last_dates.items()
                                if ld < date)
        exp_n += confirmed_by_then
        top = day.sort("value", descending=True).head(5)
        rows.append({
            "date": date, "value": exp_n, "event": ev,
            "ticker": "IPO-EXPECTED", "strike": "expected IPOs",
            "volume": 0.0,
            "top5": "; ".join(
                f"{_company_of(r['ticker'])} {r['value']:.0%}"
                for r in top.iter_rows(named=True)),
        })
    if not rows:
        return pl.DataFrame(schema={c: pl.Utf8 for c in CSV_COLUMNS})
    base = (pl.DataFrame([{k: r[k] for k in CSV_COLUMNS} for r in rows])
            .with_columns(pl.col("date").cast(pl.Utf8)))
    return base.select(CSV_COLUMNS).sort("date")


def _derived_entry(key: str, reg: dict, df: pl.DataFrame,
                   now: dt.datetime) -> tuple[dict, dict]:
    """Site + meta entries for a derived indicator from its CSV frame."""
    strikes = sorted(df["strike"].unique().to_list(),
                     key=lambda s: TAIL_STRIKE_ORDER.get(s, 99))
    colors = strike_colors(reg["palette"], len(strikes))
    strike_colors_map = dict(zip(strikes, colors))
    last_value = {s: None for s in strikes}
    series = []
    for r in df.sort(["date", "strike"]).iter_rows(named=True):
        v = sanitize_value(r["value"])
        series.append({"date": r["date"], "strike": r["strike"], "value": v})
        last_value[r["strike"]] = v
    entry = {
        "key": key,
        "title": reg["title"],
        "why": reg["why"],
        "source": reg["source"],
        "frequency": reg["frequency"],
        "unit": reg["unit"],
        "status": "live" if df.height else "no-open-market",
        "note": reg["note"],
        "paper": reg.get("paper"),
        "paper_doi": PAPER_DOI,
        "event_title": "",
        "event_ticker": None,
        "event_scope": reg["event_scope"],
        "horizons": [],
        "measures": _measures_block(key, reg, None, {}, now),
        "kalshi_url": None,
        "kalshi_urls": {},
        "market_data_through": str(df["date"].max()) if df.height else None,
        "strikes": strikes,
        "strike_colors": strike_colors_map,
        "last_observation": str(df["date"].max()) if df.height else None,
        "first_observation": str(df["date"].min()) if df.height else None,
        "n_observations": df.height,
        "last_value": last_value,
        "series": series,
    }
    meta_entry = {k: v for k, v in entry.items() if k != "series"}
    meta_entry["derived_from"] = reg.get("derived_from")
    return entry, meta_entry


def _build_derived(site_indicators: list, meta_indicators: dict,
                   frames: dict[str, pl.DataFrame],
                   close_times: dict[str, dict[str, dt.datetime]],
                   data_dir: Path, now: dt.datetime,
                   realized_df: pl.DataFrame | None = None,
                   fred_frames: dict[str, pl.DataFrame] | None = None,
                   payloads: dict[str, list[dict]] | None = None,
                   realized_err: str | None = None) -> None:
    """Derived indicators, distribution views, and realized-outcome views.
    Mutates the site/meta lists in place. Market inputs are committed CSV
    frames; realized inputs are the FRED-derived dataset; no network."""
    realized_df = realized_df if realized_df is not None else pl.DataFrame()
    fred_frames = fred_frames or {}
    payloads = payloads or {}
    ffr = fred_frames.get("DFEDTARU")
    dist_note = ("Implied distributions are recovered from each day's strike "
                 "ladder following Diercks/Katz/Wright (2026), FEDS 2026-010, "
                 "Section 3: bin probabilities by strike differencing with "
                 "mode-outward monotonicity. These are risk-neutral (Q-measure) "
                 "probabilities, not physical ones; thin tail contracts can be stale.")
    realized_note = ("Realized outcomes are pipeline-built from keyless FRED "
                     "feeds (data/predmarkets/realized.csv). Prints are frozen "
                     "at first observation — GDP at the advance-estimate "
                     "vintage the contract settles on, CPI/unemployment at "
                     "the release print — so later revisions are never "
                     "spliced silently; see the realized-dataset notes per row.")

    def entry_of(key: str) -> dict | None:
        for e in site_indicators:
            if e["key"] == key:
                return e
        return None

    # ---- implied-distribution views + realized track records ----
    for key in DIST_GRIDS:
        df = frames.get(key)
        if df is None or df.height == 0:
            continue
        dist_df, fan, heatmap = build_distribution_views(
            key, df, close_times.get(key, {}))
        if dist_df.height:
            dist_df.write_csv(data_dir / f"dist_{key}.csv")
        for entry in site_indicators:
            if entry["key"] == key:
                entry["distribution"] = {
                    "fan": fan,
                    "heatmap": heatmap,
                    "note": dist_note,
                }
                # Realized marker on the front fan once the front event
                # has a print (usually only in the front_closed case).
                rz = realized_mod.realized_lookup(realized_df, key,
                                                  entry.get("event_ticker")) \
                    if entry.get("event_ticker") else None
                if rz and rz["value"] is not None:
                    entry["distribution"]["realized"] = {
                        "event": entry["event_ticker"],
                        "date": (entry["measures"]["resolution_date"]
                                 or entry["horizons"][0]["date"]
                                 if entry.get("horizons") else None),
                        "value": rz["value"],
                        "note": rz["note"],
                    }
                # Companion track record: day-before median and 50%/90%
                # interval vs the realized print, for every resolved
                # event in the archive.
                tr = build_distribution_track_record(
                    key, df, close_times.get(key, {}), realized_df, now)
                entry["realized_track"] = tr
                h50 = sum(1 for r in tr if r["hit_50"])
                h90 = sum(1 for r in tr if r["hit_90"])
                n_known = sum(1 for r in tr if r["realized"] is not None)
                # Empty archive must not read as "0 of 0 hits" - it is
                # "nothing resolved yet", and the record extends itself.
                if not tr:
                    entry["realized_track_note"] = (
                        "No resolved events in the archive yet - the record "
                        "extends automatically as each event settles and "
                        "its print arrives. " + realized_note)
                else:
                    entry["realized_track_note"] = (
                        f"Last-day implied distribution vs the realized print "
                        f"for each resolved event in the archive: realized "
                        f"inside the 50% interval {h50}/{n_known}, inside the "
                        f"90% interval {h90}/{n_known}. " + realized_note)
                # Daily moments double as an uncertainty gauge (paper Sec. 7).
                entry["moments"] = [
                    {"date": r["date"], "mean": r["mean"],
                     "variance": r["variance"]}
                    for r in dist_df.iter_rows(named=True)
                    if r["mean"] is not None
                ]
                break
        meta_indicators[key]["distribution_days"] = dist_df.height
        e = entry_of(key)
        if e:
            meta_indicators[key]["realized_track"] = e.get("realized_track", [])

    # ---- tail risks (paper Figure 13) ----
    tail_reg = INDICATORS["tail_risks"]
    tail_df = build_tail_risks(frames, close_times)
    if tail_df.height:
        tail_df.write_csv(data_dir / "tail_risks.csv")
    entry, meta_entry = _derived_entry("tail_risks", tail_reg, tail_df, now)
    # Tail measures reference the CPI/GDP front events their ladders price.
    cpi_e, gdp_e = entry_of("cpi_yoy"), entry_of("gdp_quarterly")
    refs = []
    urls = {}
    for src in (cpi_e, gdp_e):
        if src and src.get("measures"):
            ref = src["measures"]["reference"] or ""
            if ref and not ref.startswith("no open market"):
                refs.append(ref)
            if src.get("event_ticker"):
                urls[src["event_ticker"]] = kalshi_event_url(
                    src["event_ticker"])
    m = entry["measures"]
    if refs:
        m["reference"] = ("CPI tails price " + refs[0]
                          + (f"; GDP tails price {refs[1]}"
                             if len(refs) > 1 else ""))
    m["resolution"] = ("inherits the underlying CPI and GDP events' "
                       "resolution days")
    m["text"] = _measures_text(m)
    entry["kalshi_url"] = (kalshi_event_url(cpi_e["event_ticker"])
                           if cpi_e and cpi_e.get("event_ticker") else None)
    entry["kalshi_urls"] = urls
    strict_json(entry["series"])
    site_indicators.append(entry)
    meta_indicators["tail_risks"] = meta_entry

    # ---- FOMC: bucket track record (kept) + decision->rate fan (new) ----
    fomc_df = frames.get("fomc_decision")
    if fomc_df is not None and fomc_df.height:
        record = build_fomc_track_record(fomc_df)
        fomc_entry = entry_of("fomc_decision")
        if fomc_entry is not None:
            fomc_entry["track_record"] = record
            hits = sum(1 for r in record if r["hit"])
            fomc_entry["track_record_note"] = (
                f"Day-before mode matched the realized decision in "
                f"{hits}/{len(record)} past meetings in the archive. "
                f"Realized decisions are public record (FOMC statements).")
            fan = build_fomc_fan(fomc_df, fomc_entry.get("event_ticker"),
                                 close_times.get("fomc_decision", {}),
                                 ffr, now)
            if fan:
                fomc_entry["fomc_fan"] = fan
                r_hits = fan.get("realized") or []
                n_in50 = sum(1 for r in r_hits if r.get("hit_50"))
                fomc_entry["fomc_fan"]["track_note"] = (
                    f"Post-meeting rate fell inside the day-before 50% "
                    f"interval in {n_in50}/{len(r_hits)} resolved meetings "
                    f"in the archive." if r_hits else
                    "No resolved meetings with realized rates yet.")
            meta_indicators["fomc_decision"]["track_record"] = record
            meta_indicators["fomc_decision"]["fomc_fan_days"] = \
                len((fan or {}).get("rows", []))

    # ---- IPO pipeline: expected count replaces the company lines ----
    ipo_df = frames.get("ipo_pipeline")
    ipo_entry = entry_of("ipo_pipeline")
    ipo_events = payloads.get("ipo_pipeline") or []
    ipo_event = None
    if ipo_entry and ipo_entry.get("event_ticker"):
        ipo_event = next((e for e in ipo_events
                          if (e.get("event_ticker") == ipo_entry["event_ticker"]
                              and e.get("markets"))), None)
    if ipo_event is None:
        ipo_event = next((e for e in ipo_events if e.get("markets")), None)
    settled_yes = set()
    names = []
    if ipo_event:
        for mk in ipo_event["markets"]:
            code = _company_of(mk["ticker"])
            nm = realized_mod.company_name_of(mk) or code
            names.append({"code": code, "name": nm,
                         "confirmed": mk.get("result") == "yes"})
            if mk.get("result") == "yes":
                settled_yes.add(mk["ticker"])
        names.sort(key=lambda n: n["code"])
    if ipo_df is not None and ipo_df.height:
        exp_df = build_ipo_expected(ipo_df, close_times.get("ipo_pipeline", {}),
                                   settled_yes)
        if exp_df.height:
            exp_df.write_csv(data_dir / "ipo_expected.csv")
        for entry in site_indicators:
            if entry["key"] == "ipo_pipeline":
                series = [{"date": r["date"], "strike": "expected IPOs",
                           "value": sanitize_value(r["value"])}
                          for r in exp_df.iter_rows(named=True)]
                entry["series"] = series
                entry["strikes"] = ["expected IPOs"]
                entry["strike_colors"] = {"expected IPOs": "#1d4ed8"}
                last = exp_df.row(-1, named=True)
                entry["last_value"] = {"expected IPOs": last["value"]}
                entry["n_observations"] = exp_df.height
                entry["last_observation"] = str(exp_df["date"].max())
                entry["first_observation"] = str(exp_df["date"].min())
                # Top-5 front-runners as card context.
                top5 = {}
                for r in ipo_df.filter(
                        pl.col("date") == ipo_df["date"].max()
                        ).sort("value", descending=True).head(5
                        ).iter_rows(named=True):
                    top5[_company_of(r["ticker"])] = round(float(r["value"]), 3)
                entry["top5"] = top5
                # Precision block: the named companies + realized count.
                entry["ipo_names"] = names
                entry["n_named"] = len(names)
                rz = realized_mod.realized_lookup(realized_df, "ipo_pipeline",
                                                  entry.get("event_ticker")) \
                    if entry.get("event_ticker") else None
                confirmed_names = [n["name"] for n in names if n["confirmed"]]
                entry["realized_ipo"] = {
                    "confirmed_count": len(confirmed_names),
                    "confirmed": sorted(confirmed_names),
                    "note": (rz["note"] if rz else
                             "Realized count unavailable this run."),
                }
                m = entry["measures"]
                m["reference"] = (
                    f"calendar {realized_mod._ipo_year(entry['event_ticker'])} "
                    f"— {len(names)} named companies listed as of "
                    f"{entry['market_data_through']}; expected number of "
                    f"them to confirm an IPO in calendar "
                    f"{realized_mod._ipo_year(entry['event_ticker'])}")
                m["text"] = _measures_text(m)
                strict_json(entry["series"])
                meta_indicators["ipo_pipeline"]["strikes"] = ["expected IPOs"]
                meta_indicators["ipo_pipeline"]["top5"] = top5
                meta_indicators["ipo_pipeline"]["n_named"] = len(names)
                meta_indicators["ipo_pipeline"]["ipo_names"] = names
                break
        meta_indicators["ipo_pipeline"]["n_expected_series"] = exp_df.height

    # ---- recession realized overlay: quarterly growth + rule status ----
    rec_q = fred.technical_recession_quarters(
        fred_frames.get("A191RL1Q225SBEA"))
    rec_entry = entry_of("recession")
    if rec_entry is not None and rec_q.height:
        quarters = [r for r in rec_q.sort("date").tail(12).iter_rows(named=True)]
        statuses = []
        for ev_t in (rec_entry.get("kalshi_urls") or {}):
            rz = realized_mod.realized_lookup(realized_df, "recession", ev_t)
            if rz:
                statuses.append({"event": ev_t, "value": rz["value"],
                                 "note": rz["note"]})
        rec_entry["recession_realized"] = {
            "quarters": quarters,
            "statuses": statuses,
            "note": ("Realized overlay applies the exchange's own rule "
                     "(two consecutive negative BEA quarters within the "
                     "covered window — not NBER committee calls). GDP "
                     "growth is the latest published estimate; flagged "
                     "quarters are shaded where they fall in range."),
        }
        meta_indicators["recession"]["recession_realized"] = \
            rec_entry["recession_realized"]

    if realized_err:
        for e in site_indicators:
            if e.get("status") == "live":
                e["note"] = (e.get("note", "") + " Realized-outcome refresh "
                             f"failed this run: {realized_err}").strip()


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
                df: pl.DataFrame, close_times: dict[str, dt.datetime],
                now: dt.datetime) -> tuple[dict, dict]:
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
        # Ladder horizons mark each market's last trading day (the annual
        # recession markets close in late January of the following year,
        # per their listed rules — not at the calendar year they cover).
        horizons = [{
            "label": strike_label(ev['markets'][0], ev, reg),
            "date": str(event_horizon(ev, reg)),
            "event_ticker": ev["event_ticker"],
        } for ev in chart_evs]
    else:
        horizons = [{
            "label": chart_evs[0].get("title", ""),
            "date": str(event_horizon(chart_evs[0], reg)),
            "event_ticker": chart_evs[0]["event_ticker"],
        }] if chart_evs else []

    measures = _measures_block(
        key, reg,
        chart_evs if reg["event_scope"] == "ladder"
        else (chart_evs[0] if chart_evs else None),
        close_times, now)

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
        "measures": measures,
        "kalshi_url": (kalshi_event_url(chart_evs[0]["event_ticker"])
                       if chart_evs else None),
        "kalshi_urls": {ev["event_ticker"]: kalshi_event_url(ev["event_ticker"])
                        for ev in chart_evs},
        "market_data_through": str(df["date"].max()) if df.height else None,
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


def _refresh_realized(frames: dict[str, pl.DataFrame],
                      close_times: dict[str, dict[str, dt.datetime]],
                      payloads: dict[str, list[dict]], data_dir: Path,
                      now: dt.datetime, transport) -> tuple[pl.DataFrame, dict, str | None]:
    """Fetch the keyless FRED feeds and rebuild realized.csv.

    Raw FRED responses are preserved verbatim under data/predmarkets/fred/
    before derivation. Any failure degrades to (empty, {}, err): the bundle
    still builds, cards carry the error note, and last good realized data
    stays committed — never a silent splice or a missing bundle.
    """
    try:
        raw_dir = data_dir / "fred"
        fred_frames = {sid: fred.fetch_fred_series(sid, raw_dir, transport)
                       for sid in fred.FRED_SERIES}
        cpi_yoy = fred.cpi_yoy_from_index(fred_frames["CPIAUCSL"])
        gdp_q = fred_frames["A191RL1Q225SBEA"]
        unrate = fred_frames["UNRATE"]
        ffr = fred_frames["DFEDTARU"]
        rec_q = fred.technical_recession_quarters(gdp_q)
        ipo_events = payloads.get("ipo_pipeline") or []
        ipo_event = next((e for e in ipo_events if e.get("markets")), None)
        rows: list[dict] = []
        rows += realized_mod.cpi_realized_rows(frames.get("cpi_yoy"), cpi_yoy)
        rows += realized_mod.u3_realized_rows(frames.get("unemployment"), unrate)
        rows += realized_mod.gdp_realized_rows(frames.get("gdp_quarterly"), gdp_q)
        rows += realized_mod.fomc_realized_rows(
            frames.get("fomc_decision"),
            close_times.get("fomc_decision", {}), ffr, "fomc_decision")
        rows += realized_mod.fomc_realized_rows(
            frames.get("fed_funds"),
            close_times.get("fed_funds", {}), ffr, "fed_funds")
        rows += realized_mod.recession_realized_rows(
            frames.get("recession"), rec_q)
        rows += realized_mod.ipo_realized_rows(ipo_event)
        realized_df = realized_mod.merge_realized(
            realized_mod.load_realized(data_dir / "realized.csv"), rows, now)
        if realized_df.height:
            realized_df.write_csv(data_dir / "realized.csv")
        return realized_df, fred_frames, None
    except Exception as e:
        return pl.DataFrame(), {}, str(e)[:200]


def build(incremental: bool = False, transport=None, data_dir: Path = DATA_DIR,
          site_json: Path = SITE_JSON) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    site_json.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    site_indicators, meta_indicators = [], {}
    frames: dict[str, pl.DataFrame] = {}
    close_times: dict[str, dict[str, dt.datetime]] = {}
    payloads: dict[str, list[dict]] = {}

    for key, reg in INDICATORS.items():
        if reg.get("series_ticker") is None:
            # Derived indicator (e.g. tail_risks): built after the fetch loop
            # from the committed CSVs of its sources.
            continue
        try:
            events = kalshi.fetch_events(reg["series_ticker"], transport)
            payloads[key] = events
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
            frames[key] = df
            close_times[key] = archive_close_times(
                key, {ev["event_ticker"]: event_close_time(ev)
                      for ev in evs})

            entry, meta_entry = _site_entry(key, reg, evs, chart_evs, df,
                                            close_times[key], now)
            entry["paper"] = reg.get("paper")
            entry["paper_doi"] = PAPER_DOI
            meta_entry["paper"] = reg.get("paper")
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
                "paper": reg.get("paper"), "paper_doi": PAPER_DOI,
                "event_title": "", "event_ticker": None,
                "event_scope": reg["event_scope"], "horizons": [],
                "measures": _measures_block(key, reg, None, {}, now),
                "kalshi_url": None, "kalshi_urls": {},
                "market_data_through": None,
                "strikes": [], "strike_colors": {},
                "last_observation": None, "first_observation": None,
                "n_observations": 0, "last_value": {}, "series": [],
            }
            meta_entry = dict(entry)
            meta_entry.pop("series")
            meta_entry["error"] = err
            site_indicators.append(entry)
            meta_indicators[key] = meta_entry

    # ---- realized outcomes: keyless FRED feeds -> realized.csv ----
    realized_df, fred_frames, realized_err = _refresh_realized(
        frames, close_times, payloads, data_dir, now, transport)

    # ---- derived indicators & distribution views (no fetching) ----
    _build_derived(site_indicators, meta_indicators, frames, close_times,
                   data_dir, now, realized_df=realized_df,
                   fred_frames=fred_frames, payloads=payloads,
                   realized_err=realized_err)

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
