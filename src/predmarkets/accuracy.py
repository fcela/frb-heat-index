"""Prediction-accuracy track record: the market's forecast vs the actual.

Per resolved event, one row in the committed CSV
data/predmarkets/accuracy.csv (built by the pipeline, never hand-edited):

    indicator, event, event_date, as_of, lead_days,
    predicted, predicted_label, predicted_probability,
    actual, actual_label, abs_error, hit, brier,
    first_seen, last_checked, note

Lead-time convention — the prediction is the market's forecast at a
FIXED, documented lead time (per indicator, see ACCURACY_CONVENTIONS):

  fomc_decision  mode decision bucket (the highest-probability outcome,
                 Kalshi's own wording) on the last session that closed
                 before the meeting day. Metrics: hit/miss vs the realized
                 decision and the multiclass Brier score across the five
                 buckets, sum_i (p_i - y_i)^2 over the renormalized
                 day-before prices.
  fed_funds      MEDIAN (q50) of the recovered risk-neutral distribution
                 (paper Section 3) on the last session before the meeting
                 vs the post-meeting target-range upper bound. Metric:
                 absolute error.
  cpi_yoy        median of the day's recovered distribution vs the BLS
                 CPI-U YoY print. Metric: absolute error.
  gdp_quarterly  median of the day's recovered distribution vs the BEA
                 advance estimate. Metric: absolute error.
  unemployment   median of the day's recovered distribution vs the BLS
                 U-3 print. Metric: absolute error.
  recession      Yes price at the final close vs the exchange-rule
                 outcome (two consecutive negative BEA quarters within
                 the covered window — NOT an NBER committee call).
                 Metrics: absolute error and Brier. Resolves at the
                 market's final settlement (late January of the
                 following year); pending until then.
  ipo_pipeline   expected count (sum of Yes prices, settled
                 confirmations at 1.0) at the last close vs the
                 exchange-confirmed count at settlement. Metric:
                 absolute error. The event trades through Dec 31 and
                 settles in early January; pending until then.

The median is the point forecast for the distributional series because
the paper finds the Kalshi mode and median equally accurate (CPI MAE
0.063 both vs 0.069 for the mean) and the median is the statistic the
fan charts already carry — one consistent point forecast across the
page. FOMC uses the mode bucket because the outcome is categorical.

Session selection is fixed per family and stated in every row's note:

  - Morning-close data releases (CPI, GDP, unemployment: trading stops
    8:29 AM ET, ahead of the 8:30 AM release) — the truncated
    release-morning session IS the last pre-outcome quote, so the
    prediction reads the last session dated on or before the release
    day (the final available quote, immune to whether the exchange
    still lists the settled event).
  - FOMC (statement 2:00 PM ET, trading to 2:05 PM) — the meeting-day
    session closes after the outcome is known, so the prediction reads
    the last session STRICTLY BEFORE the meeting day.
  - Annual events (recession, IPO) — the last session on or before the
    event's final trading day.

When the exchange no longer lists a settled event (no close time), the
resolution day comes from the public-record FOMC calendar for the FOMC
families (never a guess) and otherwise from the archive's final
session — a resolved market stops trading at resolution, so its archive
ends at its final quoted session, which is both the prediction and a
lower-bound record of the resolution day.

An event counts as resolved only once its resolution day has fully
passed (a row is first written the day AFTER resolution), so the
recorded session is always complete; events still trading read as
pending and never enter the record.

Merge semantics (merge_accuracy), keyed on (indicator, event):
  - the daily run APPENDS newly resolved events; it never recomputes
    history — prediction fields (event_date, as_of, lead_days,
    predicted, predicted_label, predicted_probability) freeze at
    first write;
  - outcome fields still null (a resolved event whose print has not
    landed yet) fill at first non-null observation and then freeze;
  - rows absent from a later run are retained (archived events keep
    their records);
  - last_checked always refreshes; first_seen survives.

Actuals come from the realized dataset (data/predmarkets/realized.csv:
FRED prints frozen at first observation) and, for FOMC decisions, the
public-record REALIZED_FOMC table — never interpolated, never guessed.
"""

import datetime as dt

import polars as pl

from src.predmarkets import distribution as distmod
from src.predmarkets import realized as realized_mod

ACCURACY_COLUMNS = [
    "indicator", "event", "period", "event_date", "as_of", "lead_days",
    "predicted", "predicted_label", "predicted_probability",
    "actual", "actual_label", "abs_error", "hit", "brier",
    "first_seen", "last_checked", "note",
]

# Prediction fields freeze at first write (they are the recorded history).
PREDICTION_FIELDS = ("period", "event_date", "as_of", "lead_days",
                     "predicted", "predicted_label", "predicted_probability")
# Outcome fields fill at first non-null observation, then freeze.
OUTCOME_FIELDS = ("actual", "actual_label", "abs_error", "hit", "brier")

ACCURACY_SCHEMA = {
    "indicator": pl.Utf8, "event": pl.Utf8, "period": pl.Utf8,
    "event_date": pl.Utf8, "as_of": pl.Utf8, "lead_days": pl.Int64,
    "predicted": pl.Float64, "predicted_label": pl.Utf8,
    "predicted_probability": pl.Float64,
    "actual": pl.Float64, "actual_label": pl.Utf8,
    "abs_error": pl.Float64, "hit": pl.Int64, "brier": pl.Float64,
    "first_seen": pl.Utf8, "last_checked": pl.Utf8, "note": pl.Utf8,
}

# Pipeline-provenance line shared by every row's note.
SOURCE_NOTE = ("Prediction frozen at first record (append-only; the daily "
               "run never recomputes history). Actual from the realized "
               "dataset (prints frozen at first observation) or the "
               "public-record FOMC decisions table.")

# Documented lead-time convention, stated on each card and in the summary.
ACCURACY_CONVENTIONS = {
    "fomc_decision":
        "Prediction: the mode decision bucket (highest-probability outcome) "
        "at the last close strictly before the meeting day (the statement "
        "lands mid-session at 2:00 PM ET, so the meeting-day close is "
        "post-information). Scored hit/miss vs the realized decision and "
        "by the multiclass Brier score across the five buckets, using the "
        "renormalized day-before prices.",
    "fed_funds":
        "Prediction: the median of the recovered risk-neutral distribution "
        "at the last close strictly before the meeting day. Scored by "
        "absolute error vs the post-meeting target-range upper bound "
        "(FED DFEDTARU).",
    "cpi_yoy":
        "Prediction: the median of the recovered risk-neutral distribution "
        "at the final pre-release close (trading stops 8:29 AM ET on "
        "release day, ahead of the 8:30 AM BLS release — the last "
        "available quote). Scored by absolute error vs the CPI-U YoY "
        "print.",
    "gdp_quarterly":
        "Prediction: the median of the recovered risk-neutral distribution "
        "at the final pre-release close (trading stops 8:29 AM ET on "
        "release day, ahead of the 8:30 AM BEA advance release). Scored "
        "by absolute error vs the advance-estimate growth rate.",
    "unemployment":
        "Prediction: the median of the recovered risk-neutral distribution "
        "at the final pre-release close (trading stops 8:29 AM ET on "
        "release day, ahead of the 8:30 AM BLS Employment Situation "
        "release). Scored by absolute error vs the U-3 print.",
    "recession":
        "Prediction: the Yes price at the market's final close. Scored by "
        "absolute error and Brier vs the exchange-rule outcome — two "
        "consecutive negative BEA quarters within the covered window, "
        "not an NBER committee call — as published at settlement. "
        "Resolves at final settlement (late January of the following "
        "year); pending until then.",
    "ipo_pipeline":
        "Prediction: the expected count (sum of company Yes prices, "
        "already-confirmed companies at 1.0) at the last close before "
        "the event's final trading day. Scored by absolute error vs the "
        "exchange-confirmed count at settlement. The event trades "
        "through Dec 31 and settles in early January; pending until "
        "then.",
}

# Which sessions feed the prediction (see module docstring). FOMC markets
# trade past the statement on meeting day, so only the strictly-prior
# session is pre-information; morning-close releases and annual events
# resolve at/after their final session, which is therefore the last
# available quote.
_RESOLUTION_DAY_SESSION = {
    "cpi_yoy": True, "gdp_quarterly": True, "unemployment": True,
    "recession": True, "ipo_pipeline": True,
    "fomc_decision": False, "fed_funds": False,
}

LEVEL_KEYS = ("fed_funds", "cpi_yoy", "gdp_quarterly", "unemployment")

# Primary summary metric per indicator (the scorecard's headline number).
PRIMARY_METRIC = {
    "fomc_decision": ("hit_rate", "hit rate"),
    "fed_funds": ("mae", "MAE"),
    "cpi_yoy": ("mae", "MAE"),
    "gdp_quarterly": ("mae", "MAE"),
    "unemployment": ("mae", "MAE"),
    "recession": ("brier_mean", "Brier"),
    "ipo_pipeline": ("mae", "MAE"),
}


# -------------------------------------------------------------- helpers

def _fomc_calendars() -> dict[str, dict[str, str]]:
    """Public-record meeting dates by event ticker, for both KXFED
    families (decision + level)."""
    dec = realized_mod.FOMC_EVENT_DATES
    lvl = {f"KXFED-{k.rsplit('-', 1)[-1]}": v for k, v in dec.items()}
    return {"fomc_decision": dec, "fed_funds": lvl}


def _prediction_rows(df: pl.DataFrame, event: str, horizon: dt.date,
                     inclusive: bool) -> pl.DataFrame:
    """The event's rows on the prediction session (see
    _RESOLUTION_DAY_SESSION): the last session dated on/before the
    horizon (inclusive) or strictly before it (exclusive)."""
    dates = df.filter(pl.col("event") == event)["date"].unique().to_list()
    if inclusive:
        use = [d for d in dates if d <= horizon.isoformat()]
    else:
        use = [d for d in dates if d < horizon.isoformat()]
    if not use:
        return df.clear()
    return df.filter((pl.col("event") == event)
                     & (pl.col("date") == max(use)))


def _resolve_event(key: str, event: str, df: pl.DataFrame,
                   close_times: dict[str, dt.datetime],
                   now: dt.datetime) -> tuple[dt.date | None, pl.DataFrame]:
    """(resolution day, prediction-session rows) for one event, or
    (None, empty) when the event is unresolved or its resolution day
    cannot be established honestly.

    Resolution day: the exchange's listed close time when available;
    otherwise the public-record FOMC calendar (FOMC families — never a
    guess); otherwise — for data releases and annual events the exchange
    no longer lists — the archive's final session, which IS the final
    quote (trading stops at resolution, so a resolved market's archive
    ends at its last session). A row is only produced once the
    resolution day has fully passed (the recorded session must be
    complete).
    """
    ct = close_times.get(event)
    if ct is not None:
        horizon = ct.date()
        if horizon >= now.date():
            return None, df.clear()
        return horizon, _prediction_rows(df, event, horizon,
                                          _RESOLUTION_DAY_SESSION[key])
    if key in ("fomc_decision", "fed_funds"):
        day = _fomc_calendars()[key].get(event)
        if day is None:
            return None, df.clear()  # no public-record meeting day -> no row
        horizon = dt.date.fromisoformat(day)
        if horizon >= now.date():
            return None, df.clear()
        return horizon, _prediction_rows(df, event, horizon, inclusive=False)
    dates = sorted(df.filter(pl.col("event") == event)["date"]
                   .unique().to_list())
    if not dates:
        return None, df.clear()
    horizon = dt.date.fromisoformat(dates[-1])
    if horizon >= now.date():
        return None, df.clear()
    return horizon, _prediction_rows(df, event, horizon, inclusive=True)


def _base_row(key: str, event: str, period: str | None,
              horizon: dt.date, as_of: str) -> dict:
    return {
        "indicator": key, "event": event, "period": period,
        "event_date": horizon.isoformat(), "as_of": as_of,
        "lead_days": (horizon - dt.date.fromisoformat(as_of)).days,
        "predicted": None, "predicted_label": None,
        "predicted_probability": None,
        "actual": None, "actual_label": None,
        "abs_error": None, "hit": None, "brier": None,
        "note": SOURCE_NOTE,
    }


# --------------------------------------------------- per-indicator rows

def _fomc_rows(key: str, df: pl.DataFrame,
               close_times: dict[str, dt.datetime],
               now: dt.datetime) -> list[dict]:
    """Mode bucket + Brier vs the realized decision, per resolved meeting."""
    out = []
    if df is None or df.height == 0:
        return out
    for event in sorted(df["event"].unique().to_list()):
        horizon, day = _resolve_event(key, event, df, close_times, now)
        if horizon is None or day.height == 0:
            continue
        prices = [(r["strike"].strip(), float(r["value"]))
                  for r in day.iter_rows(named=True)]
        if not prices:
            continue
        probs = distmod.recover_exclusive_buckets([p for _, p in prices])
        mode_i = max(range(len(probs)), key=lambda i: probs[i])
        row = _base_row(key, event, horizon.isoformat(), horizon,
                       str(day["date"].max()))
        row["predicted_label"] = prices[mode_i][0]
        row["predicted_probability"] = round(probs[mode_i], 6)
        actual_label = realized_mod.REALIZED_FOMC.get(horizon.isoformat())
        if actual_label is not None:
            row["actual_label"] = actual_label
            row["hit"] = int(prices[mode_i][0] == actual_label)
            row["brier"] = round(sum(
                (p - (1.0 if label == actual_label else 0.0)) ** 2
                for (label, p) in zip([l for l, _ in prices], probs)), 6)
            row["note"] = (SOURCE_NOTE + " Realized decision: public "
                           "record (FOMC statement), recorded per meeting "
                           "in the realized-decisions table.")
        else:
            row["note"] = (SOURCE_NOTE + " Realized decision not yet "
                           "recorded — the hit/Brier fill in at first "
                           "observation once the meeting resolves.")
        out.append(row)
    return out


def _level_rows(key: str, df: pl.DataFrame,
                close_times: dict[str, dt.datetime],
                realized_df: pl.DataFrame, now: dt.datetime) -> list[dict]:
    """Median of the day-before recovered distribution vs the realized
    print, per resolved release (fed funds, CPI, GDP, unemployment)."""
    out = []
    if df is None or df.height == 0:
        return out
    for event in sorted(df["event"].unique().to_list()):
        horizon, day = _resolve_event(key, event, df, close_times, now)
        if horizon is None or day.height < 2:
            continue
        ladder = [(lv, p) for lv, p in
                  ((distmod.parse_level(r["strike"]), r["value"])
                   for r in day.iter_rows(named=True))
                  if lv is not None]
        if len(ladder) < 2:
            continue
        bins = distmod.recover_threshold_bins([l for l, _ in ladder],
                                              [p for _, p in ladder])
        predicted = distmod.distribution_quantiles(bins, [0.5])[0.5]
        if predicted is None:
            continue
        rz = realized_mod.realized_lookup(realized_df, key, event)
        row = _base_row(key, event, rz["period"] if rz else None, horizon,
                        str(day["date"].max()))
        row["predicted"] = round(float(predicted), 6)
        actual = rz["value"] if rz else None
        if actual is not None:
            row["actual"] = round(float(actual), 6)
            row["abs_error"] = round(abs(float(predicted)
                                         - float(actual)), 6)
            row["note"] = SOURCE_NOTE + " " + rz["note"]
        else:
            row["note"] = (SOURCE_NOTE + " Realized print not yet "
                           "available — the error fills in at first "
                           "observation.")
        out.append(row)
    return out


def _recession_rows(key: str, df: pl.DataFrame,
                    close_times: dict[str, dt.datetime],
                    realized_df: pl.DataFrame, now: dt.datetime) -> list[dict]:
    """Final-close Yes price vs the exchange-rule outcome, per settled
    annual recession market."""
    out = []
    if df is None or df.height == 0:
        return out
    for event in sorted(df["event"].unique().to_list()):
        horizon, day = _resolve_event(key, event, df, close_times, now)
        if horizon is None or day.height == 0:
            continue
        # One Yes market per annual event; the session's close is the
        # final quoted probability. Multiple rows on the session would
        # mean an unexpected market shape — take none rather than guess.
        if day.height != 1:
            continue
        rz = realized_mod.realized_lookup(realized_df, key, event)
        row = _base_row(key, event, realized_mod._recession_year(event),
                        horizon, str(day["date"].max()))
        predicted = float(day.row(0, named=True)["value"])
        row["predicted"] = round(predicted, 6)
        actual = rz["value"] if rz else None
        if actual is not None:
            row["actual"] = round(float(actual), 6)
            row["actual_label"] = ("technical recession" if actual == 1.0
                                   else "no technical recession")
            row["abs_error"] = round(abs(predicted - float(actual)), 6)
            row["brier"] = round((predicted - float(actual)) ** 2, 6)
            row["note"] = (SOURCE_NOTE + " Outcome: the exchange's rule "
                           "applied to the BEA estimates published at "
                           "settlement. " + rz["note"])
        else:
            row["note"] = (SOURCE_NOTE + " Outcome not yet determined at "
                           "this run — fills in at first observation.")
        out.append(row)
    return out


def _ipo_rows(key: str, exp_df: pl.DataFrame,
              close_times: dict[str, dt.datetime],
              realized_df: pl.DataFrame, now: dt.datetime) -> list[dict]:
    """Final expected count vs the exchange-confirmed count, per settled
    annual IPO event. exp_df is the expected-count series (one row per
    session, ticker IPO-EXPECTED)."""
    out = []
    if exp_df is None or exp_df.height == 0:
        return out
    for event in sorted(exp_df["event"].unique().to_list()):
        horizon, day = _resolve_event(key, event, exp_df, close_times, now)
        if horizon is None or day.height == 0:
            continue
        rz = realized_mod.realized_lookup(realized_df, key, event)
        row = _base_row(key, event, realized_mod._ipo_year(event), horizon,
                        str(day["date"].max()))
        predicted = float(day.row(-1, named=True)["value"])
        row["predicted"] = round(predicted, 6)
        row["predicted_label"] = "expected IPOs"
        actual = rz["value"] if rz else None
        if actual is not None:
            row["actual"] = round(float(actual), 6)
            row["abs_error"] = round(abs(predicted - float(actual)), 6)
            row["note"] = (SOURCE_NOTE + " Actual: the exchange's own "
                           "settlement of the constituent company markets. "
                           + rz["note"])
        else:
            row["note"] = (SOURCE_NOTE + " Confirmed count not yet "
                           "available — fills in at first observation.")
        out.append(row)
    return out


def accuracy_rows_for(key: str, df: pl.DataFrame | None,
                      close_times: dict[str, dt.datetime],
                      realized_df: pl.DataFrame, now: dt.datetime,
                      ipo_exp_df: pl.DataFrame | None = None) -> list[dict]:
    """Per-event accuracy rows for one indicator — resolved events only.

    CONTRACT: close_times must carry an entry for every currently OPEN
    event of the series (the build's archive_close_times provides exactly
    this: live API close times, plus the public-record FOMC calendar for
    archive gaps). An event absent from close_times is treated as
    resolved-and-delisted, so a missing entry for a live event would
    misclassify it — callers must not pass a partial live map.

    Pending (still-trading) events produce no row: they read as pending
    in the bundle and enter the record the day after they resolve."""
    if key == "fomc_decision":
        return _fomc_rows(key, df, close_times, now)
    if key in LEVEL_KEYS:
        return _level_rows(key, df, close_times, realized_df, now)
    if key == "recession":
        return _recession_rows(key, df, close_times, realized_df, now)
    if key == "ipo_pipeline":
        return _ipo_rows(key, ipo_exp_df, close_times, realized_df, now)
    return []


# ---------------------------------------------------------------- merge

def load_accuracy(path) -> pl.DataFrame | None:
    if not path.exists():
        return None
    df = pl.read_csv(path, schema_overrides={
        "lead_days": pl.Int64, "predicted": pl.Float64,
        "predicted_probability": pl.Float64, "actual": pl.Float64,
        "abs_error": pl.Float64, "hit": pl.Int64, "brier": pl.Float64,
    })
    if df.height == 0:
        return None
    return df


def merge_accuracy(old: pl.DataFrame | None, new_rows: list[dict],
                   now: dt.datetime) -> pl.DataFrame:
    """Append new resolved events; fill pending outcomes; freeze history.

    Prediction fields freeze at first write; outcome fields fill at first
    non-null observation; rows absent from a later run are retained.
    Keyed on (indicator, event) — see the module docstring.
    """
    today = now.date().isoformat()
    by_key: dict[tuple, dict] = {}
    if old is not None and old.height:
        for r in old.iter_rows(named=True):
            by_key[(r["indicator"], r["event"])] = dict(r)
    for row in new_rows:
        key = (row["indicator"], row["event"])
        cur = by_key.get(key)
        if cur is None:
            fresh = {c: row.get(c) for c in ACCURACY_COLUMNS
                     if c not in ("first_seen", "last_checked")}
            fresh["first_seen"] = today
            fresh["last_checked"] = today
            by_key[key] = fresh
            continue
        changed = False
        for f in OUTCOME_FIELDS:
            if cur.get(f) is None and row.get(f) is not None:
                cur[f] = row[f]
                changed = True
        if changed and cur.get("note") is not None:
            cur["note"] = row.get("note", cur["note"])
        cur["last_checked"] = today
    if not by_key:
        return pl.DataFrame(schema=ACCURACY_SCHEMA)
    rows = sorted(by_key.values(),
                  key=lambda r: (r["indicator"], r["event_date"], r["event"]))
    for r in rows:
        for c in ACCURACY_COLUMNS:
            r.setdefault(c, None)
    return pl.DataFrame(rows, schema=ACCURACY_SCHEMA)


# --------------------------------------------------------------- summary

def summarize_indicator(rows: list[dict]) -> dict:
    """Summary stats for one indicator's accuracy rows.

    A row is scored once its outcome has landed (a numeric actual, or an
    outcome label for the categorical FOMC rows); recorded-but-unscored
    rows are pending. An empty or all-pending record returns null stats
    (rendered as "pending", never 0/0).
    """
    scored = [r for r in rows if (r.get("actual") is not None
                                  or r.get("actual_label") is not None)]
    hits = [r for r in scored if r.get("hit") is not None]
    errs = [r for r in scored if r.get("abs_error") is not None]
    briers = [r for r in scored if r.get("brier") is not None]
    n_hits = sum(1 for r in hits if r["hit"])
    return {
        "n_events": len(rows),
        "n_scored": len(scored),
        "n_hits": n_hits,
        "hit_rate": (round(n_hits / len(hits), 4) if hits else None),
        "mae": (round(sum(r["abs_error"] for r in errs) / len(errs), 6)
                if errs else None),
        "brier_mean": (round(sum(r["brier"] for r in briers) / len(briers), 6)
                       if briers else None),
    }
