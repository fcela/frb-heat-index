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
import re
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.predmarkets import distribution as distmod
from src.predmarkets import kalshi
from src.predmarkets.indicators import (
    FOMC_STRIKE_ORDER,
    INDICATORS,
    MONTH_CODES,
    PAPER_DOI,
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


def _company_of(ticker: str) -> str:
    return ticker.rsplit("-", 1)[-1]


def build_ipo_expected(df: pl.DataFrame,
                       close_times: dict[str, dt.datetime]) -> pl.DataFrame:
    """Expected IPO count per day: sum of company probabilities for the
    front (current-year) event."""
    rows = []
    for date in sorted(df["date"].unique().to_list()):
        ev = _front_event_on(df, date, close_times)
        if ev is None:
            continue
        day = df.filter((pl.col("date") == date) & (pl.col("event") == ev))
        if day.height == 0:
            continue
        exp_n = float(day["value"].sum())
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
                   data_dir: Path, now: dt.datetime) -> None:
    """Derived indicators and distribution views. Mutates the site/meta lists
    in place. All inputs are committed CSV frames; no network."""
    dist_note = ("Implied distributions are recovered from each day's strike "
                 "ladder following Diercks/Katz/Wright (2026), FEDS 2026-010, "
                 "Section 3: bin probabilities by strike differencing with "
                 "mode-outward monotonicity. These are risk-neutral (Q-measure) "
                 "probabilities, not physical ones; thin tail contracts can be stale.")

    # ---- implied-distribution views for the threshold indicators ----
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
                # Daily moments double as an uncertainty gauge (paper Sec. 7).
                entry["moments"] = [
                    {"date": r["date"], "mean": r["mean"],
                     "variance": r["variance"]}
                    for r in dist_df.iter_rows(named=True)
                    if r["mean"] is not None
                ]
                break
        meta_indicators[key]["distribution_days"] = dist_df.height

    # ---- tail risks (paper Figure 13) ----
    tail_reg = INDICATORS["tail_risks"]
    tail_df = build_tail_risks(frames, close_times)
    if tail_df.height:
        tail_df.write_csv(data_dir / "tail_risks.csv")
    entry, meta_entry = _derived_entry("tail_risks", tail_reg, tail_df, now)
    strict_json(entry["series"])
    site_indicators.append(entry)
    meta_indicators["tail_risks"] = meta_entry

    # ---- FOMC track record: day-before mode vs realized decision ----
    fomc_df = frames.get("fomc_decision")
    if fomc_df is not None and fomc_df.height:
        record = build_fomc_track_record(fomc_df)
        for entry in site_indicators:
            if entry["key"] == "fomc_decision":
                entry["track_record"] = record
                hits = sum(1 for r in record if r["hit"])
                entry["track_record_note"] = (
                    f"Day-before mode matched the realized decision in "
                    f"{hits}/{len(record)} past meetings in the archive. "
                    f"Realized decisions are public record (FOMC statements).")
                break
        meta_indicators["fomc_decision"]["track_record"] = record

    # ---- IPO pipeline: expected count replaces the company lines ----
    ipo_df = frames.get("ipo_pipeline")
    if ipo_df is not None and ipo_df.height:
        exp_df = build_ipo_expected(ipo_df, close_times.get("ipo_pipeline", {}))
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
                strict_json(entry["series"])
                meta_indicators["ipo_pipeline"]["strikes"] = ["expected IPOs"]
                meta_indicators["ipo_pipeline"]["top5"] = top5
                break
        meta_indicators["ipo_pipeline"]["n_expected_series"] = exp_df.height


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
    frames: dict[str, pl.DataFrame] = {}
    close_times: dict[str, dict[str, dt.datetime]] = {}

    for key, reg in INDICATORS.items():
        if reg.get("series_ticker") is None:
            # Derived indicator (e.g. tail_risks): built after the fetch loop
            # from the committed CSVs of its sources.
            continue
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
            frames[key] = df
            close_times[key] = {ev["event_ticker"]: event_close_time(ev)
                                for ev in evs}

            entry, meta_entry = _site_entry(key, reg, evs, chart_evs, df)
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
                "strikes": [], "strike_colors": {},
                "last_observation": None, "first_observation": None,
                "n_observations": 0, "last_value": {}, "series": [],
            }
            meta_entry = dict(entry)
            meta_entry.pop("series")
            meta_entry["error"] = err
            site_indicators.append(entry)
            meta_indicators[key] = meta_entry

    # ---- derived indicators & distribution views (no fetching) ----
    _build_derived(site_indicators, meta_indicators, frames, close_times,
                   data_dir, now)

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
