"""Realized-outcome dataset for the prediction-markets section.

One committed CSV — data/predmarkets/realized.csv — built entirely by
the pipeline (never hand-edited), refreshed on every daily run:

    indicator, period, event, kind, value, source,
    first_seen, last_checked, note

This module is also the shared home of the public-record FOMC tables
(realized decisions, meeting calendar) and the event-horizon helpers
(infer_event_horizon, day_before_rows) used by the build's track records
and by the accuracy layer (accuracy.py) — no circular imports.

Row kinds:
  print   a completed official statistic (CPI YoY month, GDP quarter,
          unemployment month, post-FOMC target-range upper bound). Prints
          are FROZEN at first observation: GDP is frozen at the advance-
          estimate vintage the contracts settle on, CPI/unemployment at
          the release print. Later revisions are never spliced silently —
          the freeze is stated in the note.
  status  a running determination (recession-window flag, confirmed-IPO
          count) that updates while its market trades.

Realized values come from FRED's keyless fredgraph.csv endpoint (see
fred.py) and, for IPOs, from the exchange's own settlement of the
constituent company markets. Missing inputs produce explicit notes —
never an interpolation.
"""

import datetime as dt
import re

import polars as pl

from src.predmarkets import fred
from src.predmarkets.indicators import MONTH_CODES

REALIZED_COLUMNS = ["indicator", "period", "event", "kind", "value",
                    "source", "first_seen", "last_checked", "note"]

CPI_SOURCE = "FRED CPIAUCSL (BLS CPI-U index, NSA) — YoY computed, 1dp"
GDP_SOURCE = "FRED A191RL1Q225SBEA (BEA real GDP growth, SAAR, 1dp)"
U3_SOURCE = "FRED UNRATE (BLS U-3 unemployment rate, SA)"
FFR_SOURCE = "FRED DFEDTARU (Fed funds target-range upper bound)"

# Recession windows per listed contract rules, verified 2026-10-19:
#   KXRECSSNBER-26: "two consecutive quarters of negative GDP growth in
#                    2025 or 2026" -> quarters 2025Q1..2026Q4
#   KXRECSSNBER-27: "... in Q4 2026 through Q4 2027" -> 2026Q4..2027Q4
# A year market not in this table gets no realized row and an honest
# note on the card rather than a guessed window.
RECESSION_WINDOWS = {
    "KXRECSSNBER-26": ("2025Q1", "2026Q4"),
    "KXRECSSNBER-27": ("2026Q4", "2027Q4"),
}


def _ym_of_month_event(event_ticker: str) -> tuple[int, int] | None:
    """KXCPIYOY-26NOV / KXU3-26NOV -> (2026, 11)."""
    m = re.match(r"KX(?:CPIYOY|U3)-(\d{2})([A-Z]{3})$", event_ticker)
    if m:
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            return 2000 + int(m.group(1)), mon
    return None


def meeting_date_of(event_ticker: str,
                    close_time: dt.datetime | None) -> dt.date | None:
    """Meeting day for KXFED/KXFEDDECISION events.

    Primary: the exchange's close_time (the decision-day session close,
    e.g. 17:59 UTC on the meeting day). Fallback: the month-end proxy
    (ordering-safe only) — callers treat it as approximate and say so.
    """
    if close_time is not None:
        return close_time.date()
    m = re.match(r"KXFED(?:DECISION)?-(\d{2})([A-Z]{3})$", event_ticker)
    if m:
        mon = MONTH_CODES.get(m.group(2))
        if mon:
            y = 2000 + int(m.group(1))
            last = (dt.date(y + (mon == 12), mon % 12 + 1, 1)
                    - dt.timedelta(days=1)).day
            return dt.date(y, mon, last)
    return None


# Realized FOMC decisions, meeting date -> decision bucket (Kalshi wording).
# Public record (FOMC statements); verified 2026-10-18 against the Fed's
# 2026 meeting chronology. "Hike 0bps" is Kalshi's label for no change.
# Extended as meetings resolve — it is the actuals table behind both the
# FOMC track record and the accuracy layer.
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

# FOMC meeting dates by event ticker (decision day; public FOMC calendar).
# Resolved events stop being listed with nested markets, so their close
# times vanish from the API; this public-record calendar fills those gaps.
FOMC_EVENT_DATES = {
    "KXFEDDECISION-26SEP": "2026-09-16",
    "KXFEDDECISION-26OCT": "2026-10-28",
    "KXFEDDECISION-26DEC": "2026-12-09",
}


def infer_event_horizon(event_ticker: str) -> dt.date | None:
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


def day_before_rows(df: pl.DataFrame, event: str,
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


def _period_sort_key(period: str) -> str:
    return period


def cpi_realized_rows(cpi_frame: pl.DataFrame | None,
                      yoy: pl.DataFrame) -> list[dict]:
    """Realized CPI YoY prints for every CPI event in the archive."""
    rows = []
    if cpi_frame is None or cpi_frame.height == 0:
        return rows
    yoy_map = {r["date"]: r["value"] for r in yoy.iter_rows(named=True)}
    for event in sorted(cpi_frame["event"].unique().to_list()):
        ym = _ym_of_month_event(event)
        if ym is None:
            continue
        period = f"{ym[0]:04d}-{ym[1]:02d}"
        v = yoy_map.get(period)
        rows.append({
            "indicator": "cpi_yoy", "period": period, "event": event,
            "kind": "print",
            "value": None if v is None else round(float(v), 1),
            "source": CPI_SOURCE,
            "note": ("YoY change in the NSA CPI-U index, one decimal — "
                     "the quantity the contract settles on. Frozen at "
                     "first observation (the index is not revised; the "
                     "BLS computes YoY from unrounded indexes, so the "
                     "last decimal can differ from the release table)."),
        })
    return rows


def u3_realized_rows(u3_frame: pl.DataFrame | None,
                     unrate: pl.DataFrame) -> list[dict]:
    rows = []
    if u3_frame is None or u3_frame.height == 0:
        return rows
    u_map = {r["date"][:7]: r["value"] for r in unrate.iter_rows(named=True)}
    for event in sorted(u3_frame["event"].unique().to_list()):
        ym = _ym_of_month_event(event)
        if ym is None:
            continue
        period = f"{ym[0]:04d}-{ym[1]:02d}"
        v = u_map.get(period)
        rows.append({
            "indicator": "unemployment", "period": period, "event": event,
            "kind": "print",
            "value": None if v is None else round(float(v), 1),
            "source": U3_SOURCE,
            "note": ("Seasonally adjusted U-3 from the Employment "
                     "Situation — the exact contract quantity. Frozen "
                     "at first observation; later re-estimation can move "
                     "the published rate slightly."),
        })
    return rows


def gdp_realized_rows(gdp_frame: pl.DataFrame | None,
                      gdp_q: pl.DataFrame) -> list[dict]:
    rows = []
    if gdp_frame is None or gdp_frame.height == 0:
        return rows
    q_map = {r["date"]: r["value"] for r in gdp_q.iter_rows(named=True)}
    for event in sorted(gdp_frame["event"].unique().to_list()):
        m = re.match(r"KXGDP-(\d{2})([A-Z]{3})(\d{2})$", event)
        if not m:
            continue
        mon = MONTH_CODES.get(m.group(2))
        if mon is None:
            continue
        release = dt.date(2000 + int(m.group(1)), mon, int(m.group(3)))
        period = fred.quarter_of_release(release)
        if period is None:
            continue
        v = q_map.get(period)
        rows.append({
            "indicator": "gdp_quarterly", "period": period, "event": event,
            "kind": "print",
            "value": None if v is None else round(float(v), 1),
            "source": GDP_SOURCE,
            "note": ("Real GDP growth, quarterly SAAR, one decimal. The "
                     "contract settles on the ADVANCE estimate; this row "
                     "is frozen at first observation (≈ the advance "
                     "vintage when the release lands) and later BEA "
                     "revisions are NOT spliced in — the vintage is "
                     "stated wherever the value is plotted."),
        })
    return rows


def fomc_realized_rows(frame: pl.DataFrame | None,
                       close_times: dict[str, dt.datetime],
                       ffr: pl.DataFrame, indicator: str) -> list[dict]:
    """Per-meeting realized target-range upper bound (pre + post)."""
    rows = []
    if frame is None or frame.height == 0:
        return rows
    for event in sorted(frame["event"].unique().to_list()):
        meeting = meeting_date_of(event, close_times.get(event))
        if meeting is None:
            continue
        base, post = fred.fed_funds_around(ffr, meeting)
        if base is None:
            rows.append({
                "indicator": indicator, "period": meeting.isoformat(),
                "event": event, "kind": "print", "value": None,
                "source": FFR_SOURCE,
                "note": ("No target-range observation available around "
                         f"the {meeting.isoformat()} meeting — marked "
                         "missing, not guessed."),
            })
            continue
        if post is None:
            rows.append({
                "indicator": indicator, "period": meeting.isoformat(),
                "event": event, "kind": "print", "value": None,
                "source": FFR_SOURCE,
                "note": (f"Meeting not yet held as of this run; prevailing "
                         f"pre-meeting base {base:.2f}% (target-range "
                         f"upper bound). The post-meeting value fills in "
                         f"at first observation once the meeting passes."),
            })
            continue
        rows.append({
            "indicator": indicator, "period": meeting.isoformat(),
            "event": event, "kind": "print", "value": post,
            "source": FFR_SOURCE,
            "note": (f"Post-meeting target-range upper bound "
                     f"({post:.2f}%), prevailing pre-meeting base "
                     f"{base:.2f}% — per-meeting bases from the daily "
                     f"target-range series, never a single hardcode. "
                     f"Frozen at first observation."),
        })
    return rows


def recession_realized_rows(rec_frame: pl.DataFrame | None,
                            rec_q: pl.DataFrame) -> list[dict]:
    """Running technical-recession status per open recession market.

    Value 1.0 = the exchange's rule has occurred within the covered
    window (two consecutive negative BEA quarters, per the listed
    contract rules — NOT an NBER committee call); 0.0 = not yet, per
    the currently published estimates.
    """
    rows = []
    if rec_frame is None or rec_frame.height == 0:
        return rows
    flagged = [r for r in rec_q.iter_rows(named=True) if r["rec_flag"]]
    flag_map = {r["date"]: r for r in flagged}
    for event in sorted(rec_frame["event"].unique().to_list()):
        window = RECESSION_WINDOWS.get(event)
        if window is None:
            rows.append({
                "indicator": "recession", "period": _recession_year(event),
                "event": event, "kind": "status", "value": None,
                "source": GDP_SOURCE,
                "note": ("Contract window not yet documented for this "
                         "event — realized status withheld rather than "
                         "guessed."),
            })
            continue
        lo, hi = window
        in_win = [r for r in flagged if lo <= r["date"] <= hi]
        note = (f"Exchange rule: two consecutive negative real-GDP "
                f"quarters (BEA) within {lo}–{hi}. "
                + (f"Occurred: {', '.join(r['date'] for r in in_win)}."
                   if in_win
                   else "Not occurred in the published estimates so far."))
        rows.append({
            "indicator": "recession", "period": _recession_year(event),
            "event": event, "kind": "status",
            "value": 1.0 if in_win else 0.0,
            "source": GDP_SOURCE,
            "note": note,
        })
    return rows


def _recession_year(event: str) -> str:
    m = re.match(r"KXRECSSNBER-(\d{2}|\d{4})$", event)
    if not m:
        return event
    y = m.group(1)
    return str(int(y) if len(y) == 4 else 2000 + int(y))


def ipo_realized_rows(ipo_event: dict | None) -> list[dict]:
    """Confirmed-2026-IPO count from the exchange's own settlement.

    The KXIPO event's constituent company markets settle as companies
    confirm (S-1 effective, priced, or ticker assigned); a market with
    result 'yes' IS the exchange's record that the company confirmed.
    """
    if not ipo_event or not ipo_event.get("markets"):
        return []
    confirmed, named, missing_name = [], 0, []
    for m in ipo_event["markets"]:
        named += 1
        if m.get("result") == "yes":
            name = company_name_of(m)
            confirmed.append(name if name else
                             m["ticker"].rsplit("-", 1)[-1])
    year = _ipo_year(ipo_event.get("event_ticker", ""))
    note = (f"{named} named companies. Confirmed so far: "
            + (", ".join(sorted(confirmed)) if confirmed else "none")
            + ". Count updates as the exchange settles company markets; "
              "the event itself trades through Dec 31, 2026 11:59 PM ET.")
    return [{
        "indicator": "ipo_pipeline", "period": year, "event":
            ipo_event.get("event_ticker"), "kind": "status",
        "value": len(confirmed), "source":
            "Exchange settlement of the constituent KXIPO company markets",
        "note": note,
    }]


def _ipo_year(event_ticker: str) -> str:
    m = re.match(r"KXIPO-(\d{2}|\d{4})$", event_ticker)
    if not m:
        return event_ticker
    y = m.group(1)
    return str(int(y) if len(y) == 4 else 2000 + int(y))


def company_name_of(market: dict) -> str | None:
    """Company display name from the market's own rule sentence
    ('If <Company> confirms an IPO before ...')."""
    rules = market.get("rules_primary") or ""
    m = re.match(r"If (.+?) confirms an IPO before", rules)
    return m.group(1).strip() if m else None


# -------------------------------------------------------------- merge

def load_realized(path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    df = pl.read_csv(path)
    if df.height == 0:
        return None
    return df


def merge_realized(old: pl.DataFrame | None, new_rows: list[dict],
                   now: dt.datetime) -> pl.DataFrame:
    """Freeze prints at first observed value; statuses keep updating.

    Keyed on (indicator, period, event):
      - print rows keep the first NON-NULL value ever observed (a null
        placeholder never freezes — the next observation wins, so a
        delayed release still lands at its first published print);
      - status rows always take the fresh value (running determinations);
      - rows absent from `new_rows` stay (an event the API stopped
        listing must not lose its print);
      - last_checked always refreshes; first_seen survives.
    """
    today = now.date().isoformat()
    schema = {c: pl.Utf8 for c in REALIZED_COLUMNS}
    schema["value"] = pl.Float64
    if new_rows:
        new_df = (pl.DataFrame(new_rows)
                  .with_columns([
                      pl.col(c).cast(pl.Utf8) for c in
                      ("indicator", "period", "event", "kind", "source",
                       "note")])
                  .with_columns(pl.col("value").cast(pl.Float64)))
    else:
        new_df = pl.DataFrame(schema=schema)
    if new_df.height:
        new_df = new_df.with_columns(
            pl.lit(today).alias("first_seen"),
            pl.lit(today).alias("last_checked"))
    if old is None or old.height == 0:
        return _sorted_realized(new_df)
    o = old.with_columns(pl.col("value").cast(pl.Float64, strict=False))
    if new_df.height == 0:
        return _sorted_realized(
            o.with_columns(pl.lit(today).alias("last_checked")))
    key = ["indicator", "period", "event"]
    j = o.join(new_df.select(key + ["value", "note", "kind"])
               .with_columns(pl.col("value").alias("new_value"),
                             pl.col("note").alias("new_note"),
                             pl.col("kind").alias("new_kind")),
               on=key, how="left")
    j = j.with_columns([
        # print: keep first non-null value; adopt the first observed
        # value when the stored one is null. status: always refresh.
        pl.when(pl.col("new_kind").is_null())
        .then(pl.col("value"))
        .when(pl.col("kind") == "status")
        .then(pl.col("new_value"))
        .when(pl.col("value").is_null() & pl.col("new_value").is_not_null())
        .then(pl.col("new_value"))
        .otherwise(pl.col("value")).alias("value"),
        pl.when(pl.col("new_kind").is_null())
        .then(pl.col("note"))
        .when(pl.col("kind") == "status")
        .then(pl.col("new_note"))
        .otherwise(pl.col("note")).alias("note"),
    ]).drop(["new_value", "new_note", "new_kind"])
    fresh = new_df.join(o.select(key), on=key, how="anti")
    if fresh.height:
        j = pl.concat([j.select(REALIZED_COLUMNS),
                       fresh.select(REALIZED_COLUMNS)])
    j = j.with_columns(pl.lit(today).alias("last_checked"))
    return _sorted_realized(j)


def _sorted_realized(df: pl.DataFrame) -> pl.DataFrame:
    if df.height == 0:
        return df
    return (df.select(REALIZED_COLUMNS)
            .sort(["indicator", "period", "event"]))


def realized_lookup(df: pl.DataFrame, indicator: str,
                    event: str) -> dict | None:
    """The realized row for (indicator, event), if any."""
    if df is None or df.height == 0:
        return None
    hit = df.filter((pl.col("indicator") == indicator)
                    & (pl.col("event") == event))
    if hit.height == 0:
        return None
    r = hit.row(0, named=True)
    return {"period": r["period"], "value": r["value"], "note": r["note"],
            "kind": r["kind"], "source": r["source"]}
