"""Accuracy records for the fixed-horizon series (issue #67).

accuracy.py scores each EVENT's final pre-settlement distribution
against its realized outcome. The fixed-horizon series make a
different claim — "the market's implied reading 3, 6, and 12 months
ahead, as of vintage V" — so each READING is scored against the
outcome it was forecasting, keyed to the vintage that produced it:

  data/predmarkets/fixed_horizon_accuracy.csv — one row per reading
  (family, vintage_date, horizon_months): the reading's implied value,
  its source event, and the reference period it forecasts (the meeting
  date for fed funds; the reference month or quarter for the print
  families).

Merge semantics mirror accuracy.py: the file is persistent state, not
a rebuild. Prediction fields (predicted value, source event, reference
period, target date) are frozen at first write; the outcome fields
(actual, abs_error) fill at the first non-null realized value observed
for the source event and never change afterwards; `last_checked`
refreshes on every merge; rows are retained even if a later archive
state no longer yields the reading. realized.py is the only source of
actuals — prints frozen at first observation there (the advance
estimate for GDP), so a reading is scored only against a settled,
realized value from a primary source, never an estimate of one.

The archive begins 2026-11-02 and every current reading targets a
meeting, month, or quarter still in the future, so the first records
are all pending: `actual` and `abs_error` stay empty until the
forecast period's outcome is realized. The machinery is complete now;
the scores populate as events settle.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from src.predmarkets.realized import realized_lookup

CSV_NAME = "fixed_horizon_accuracy.csv"

SCHEMA_OVERRIDES = {
    "family": pl.Utf8,
    "vintage_date": pl.Utf8,
    "horizon_months": pl.Int64,
    "target_date": pl.Utf8,
    "source_event": pl.Utf8,
    "reference_period": pl.Utf8,
    "predicted": pl.Float64,
    "actual": pl.Float64,
    "abs_error": pl.Float64,
    "first_seen": pl.Utf8,
    "last_checked": pl.Utf8,
    "note": pl.Utf8,
}

COLUMNS = list(SCHEMA_OVERRIDES)

NOTES = {
    "fed_funds": "implied mean upper bound vs realized post-meeting "
                 "upper bound (FRED DFEDTARU)",
    "cpi_yoy": "implied mean YoY CPI-U vs realized print (FRED CPIAUCSL)",
    "gdp_quarterly": "implied mean vs realized advance estimate "
                     "(FRED A191RL1Q225SBEA)",
}


def load_records(data_dir: Path) -> pl.DataFrame:
    """Existing fixed-horizon accuracy records (empty frame if none)."""
    path = Path(data_dir) / CSV_NAME
    if not path.is_file():
        return pl.DataFrame(schema=SCHEMA_OVERRIDES)
    return pl.read_csv(path, schema_overrides=SCHEMA_OVERRIDES,
                       null_values=[""])


def _today() -> str:
    return dt.date.today().isoformat()


def reading_rows(family: str, frame: pl.DataFrame | None) -> list[dict]:
    """Normalize one family's fixed-horizon frame to reading dicts.

    The fed funds frame carries implied_rate / source_meeting_date;
    the print frames carry implied_value / reference_period. Both map
    to (predicted, reference_period) here.
    """
    if frame is None or frame.height == 0:
        return []
    value_col = "implied_rate" if family == "fed_funds" else "implied_value"
    period_col = ("source_meeting_date" if family == "fed_funds"
                  else "reference_period")
    out = []
    for r in frame.iter_rows(named=True):
        out.append({
            "family": family,
            "vintage_date": r["vintage_date"],
            "horizon_months": int(r["horizon_months"]),
            "target_date": r["target_date"],
            "source_event": r["source_event"],
            "reference_period": r[period_col],
            "predicted": float(r[value_col]),
        })
    return out


def merge_records(existing: pl.DataFrame, frames: dict,
                  realized_df: pl.DataFrame,
                  today: str | None = None) -> pl.DataFrame:
    """Merge the current readings into the persistent records.

    `frames` maps family -> fixed-horizon frame (any family may be
    absent or empty). New readings are appended with prediction fields
    frozen at first write; pending rows pick up the source event's
    realized value at its first non-null observation (and only when
    the realized row's period matches the reading's reference period);
    every retained row's last_checked refreshes.
    """
    today = today or _today()
    records = {(r["family"], r["vintage_date"], int(r["horizon_months"])): r
               for r in existing.iter_rows(named=True)}

    for family, frame in frames.items():
        for rd in reading_rows(family, frame):
            key = (rd["family"], rd["vintage_date"], rd["horizon_months"])
            rec = records.get(key)
            if rec is None:
                records[key] = {
                    **rd,
                    "actual": None,
                    "abs_error": None,
                    "first_seen": today,
                    "last_checked": today,
                    "note": NOTES.get(family, ""),
                }
            else:
                rec["last_checked"] = today

    for rec in records.values():
        if rec["actual"] is not None:
            continue  # outcome frozen at first observation
        rz = realized_lookup(realized_df, rec["family"],
                             rec["source_event"])
        if rz is None or rz.get("value") is None:
            continue
        if rz.get("period") != rec["reference_period"]:
            continue  # the realized row must be the forecasted period
        actual = float(rz["value"])
        rec["actual"] = actual
        rec["abs_error"] = abs(rec["predicted"] - actual)

    rows = sorted(records.values(),
                  key=lambda r: (r["family"], r["horizon_months"],
                                 r["vintage_date"]))
    if not rows:
        return pl.DataFrame(schema=SCHEMA_OVERRIDES)
    return pl.DataFrame(rows, schema=SCHEMA_OVERRIDES).select(COLUMNS)


def update_records(data_dir: Path, frames: dict,
                   realized_df: pl.DataFrame,
                   today: str | None = None) -> pl.DataFrame:
    """Load, merge, and persist the records; returns the merged frame."""
    data_dir = Path(data_dir)
    merged = merge_records(load_records(data_dir), frames, realized_df,
                           today=today)
    merged.write_csv(data_dir / CSV_NAME)
    return merged


def summarize(rows: list[dict]) -> dict:
    """Per-(family, horizon) counts and mean absolute error.

    mae is None while no reading at that horizon has scored — the same
    pending-not-zero convention as accuracy.py's scorecard.
    """
    out: dict = {}
    for r in rows:
        fam = out.setdefault(r["family"], {"horizons": {}})
        h = fam["horizons"].setdefault(
            int(r["horizon_months"]),
            {"n_readings": 0, "n_scored": 0, "errors": []})
        h["n_readings"] += 1
        if r.get("abs_error") is not None:
            h["n_scored"] += 1
            h["errors"].append(float(r["abs_error"]))
    for fam in out.values():
        horizons = []
        for months in sorted(fam["horizons"]):
            h = fam["horizons"][months]
            horizons.append({
                "months": months,
                "n_readings": h["n_readings"],
                "n_scored": h["n_scored"],
                "mae": (sum(h["errors"]) / len(h["errors"])
                        if h["errors"] else None),
            })
        fam["horizons"] = horizons
    return out
