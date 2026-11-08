"""Fixed-horizon implied policy-rate series from the vintage archive.

Issue #67, layer 2. The per-event views track whichever contract is in
front, so each indicator's history is a patchwork of discrete contracts
that changes meaning at every roll. This module re-indexes the archived
distributions by horizon instead of by contract:

  - For each vintage in data/predmarkets/vintages/, take the fed funds
    series' (KXFED) events — one per FOMC meeting — and reduce each
    meeting's recovered distribution to its implied mean: the implied
    target-range upper bound in effect after that meeting, exactly as
    archived by vintage.py (Diercks/Katz/Wright, FEDS 2026-010,
    Section 3 recovery; the distribution's own `mean` field).
  - The meetings form a step path over meeting dates: a meeting's
    implied mean holds from its meeting date until the next meeting's
    date. The meeting date is the ET calendar date of the event's
    close time (the pipeline's standing convention: event dates come
    from close_time, never a hardcoded calendar). Settled status is
    likewise read off close times — the exchange keeps settled events
    listed for weeks, so the listing is never consulted; a settled
    meeting's archived distribution is its final pre-settlement ladder,
    which is what an as-of path should carry for dates before the
    first open meeting.
  - Sample the path at fixed horizons: the step in force at the
    vintage date plus 3, 6, and 12 calendar months.

Meetings whose archived ladder was too thin to recover a distribution
(fewer than two parseable strikes) carry no step: the previous
meeting's level holds through them, and the data file names the
meeting that set each sampled step so a thin-market step can be
audited. Nothing is interpolated: a horizon whose target date precedes
every meeting in the vintage has no value and no row.

Outputs (written by build_predmarkets.build on every run, after the
day's vintage is appended, so a run's own snapshot is included):

  data/predmarkets/fixed_horizon_fed_funds.csv — one row per
  (vintage date, horizon): vintage_date, horizon_months, target_date,
  implied_rate (percent), source_event, source_meeting_date. The file
  is a pure function of the append-only archive, recomputed in full
  each run; the archive is the record, this is an index over it.

  site["fixed_horizon"] — the same series grouped by horizon for the
  site's fixed-horizon view, with definition, provenance, and the
  shared axis domain for the small-multiple panels.

Print families (issue #67's extension of the same construction): the
CPI (KXCPIYOY) and GDP (KXGDP) families get the same treatment, with
one difference in sampling. A policy rate persists between meetings,
so a step path is the right object for fed funds; a print does not
persist, so these families are sampled by reference period: the
reading at a horizon is the implied mean of the event whose reference
month (CPI) or quarter (GDP) contains the target date. A horizon with
no covering event is omitted — the exchange lists CPI events for only
the next two reference months, so the CPI series reports rows only
once a sampled reference month is listed. Outputs:

  data/predmarkets/fixed_horizon_cpi_yoy.csv,
  data/predmarkets/fixed_horizon_gdp_quarterly.csv — one row per
  (vintage date, horizon): vintage_date, horizon_months, target_date,
  implied_value (percent), source_event, reference_period. Also pure
  functions of the archive, recomputed in full each run.

  site["fixed_horizon_families"] — one section per print family that
  has rows, shaped like site["fixed_horizon"].

The risk-neutral (Q-measure) caveat documented in distribution.py
applies unchanged: these are risk-neutral implied means, not physical
forecasts, and far meetings trade thin — their closes can be stale.
"""

import calendar
import datetime as dt
import json
import math
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from src.predmarkets import kalshi
from src.predmarkets.indicators import INDICATORS, MONTH_CODES, kalshi_event_url

SERIES_KEY = "fed_funds"
HORIZONS_MONTHS = (3, 6, 12)
CSV_NAME = "fixed_horizon_fed_funds.csv"

_ET = ZoneInfo("America/New_York")

FRAME_SCHEMA = {
    "vintage_date": pl.Utf8,
    "horizon_months": pl.Int64,
    "target_date": pl.Utf8,
    "implied_rate": pl.Float64,
    "source_event": pl.Utf8,
    "source_meeting_date": pl.Utf8,
}

DEFINITION = (
    "The implied mean upper bound of the federal funds target range in "
    "effect 3, 6, and 12 months after each vintage date. For every FOMC "
    "meeting in the day's archived snapshot (series KXFED), the "
    "meeting's recovered distribution is reduced to its implied mean; "
    "the meetings form a step path over meeting dates, and each horizon "
    "reads the step in force at the vintage date plus that many calendar "
    "months. Indexed by horizon rather than by contract, so the series "
    "are continuous across contract rolls."
)

NOTE = (
    "Risk-neutral (Q-measure) probabilities, not physical ones; far "
    "meetings trade thin and their closes can be stale. Meetings whose "
    "archived ladder is too thin to recover a distribution carry no "
    "step — the previous meeting's level holds through them — and the "
    "data file (data/predmarkets/fixed_horizon_fed_funds.csv) names the "
    "meeting that set each step, so any step can be audited against the "
    "archive. No interpolation: a horizon is reported only when a "
    "meeting is in force at its target date."
)


def add_months(d: dt.date, months: int) -> dt.date:
    """Calendar-month addition with day clamping (Jan 31 + 1 -> Feb 28)."""
    total = (d.year * 12 + (d.month - 1)) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def meeting_date(close_time: str | None) -> dt.date | None:
    """ET calendar date of an event's close time, or None."""
    if not close_time:
        return None
    parsed = kalshi.parse_iso_z(close_time)
    if parsed is None:
        return None
    return parsed.astimezone(_ET).date()


def meeting_steps(series_snapshot: dict) -> list[tuple[dt.date, float, str]]:
    """Step path of one vintage's fed funds snapshot.

    Returns [(meeting_date, implied_mean, event_ticker)] sorted by
    meeting date. Only events with a recovered threshold-bin
    distribution and a finite mean carry a step. Two events sharing a
    meeting date (not a KXFED pattern) resolve to the later close.
    """
    by_date = {}
    for ev in series_snapshot.get("events") or []:
        dist = ev.get("distribution") or {}
        if dist.get("kind") != "threshold_bins":
            continue
        mean = dist.get("mean")
        if mean is None or not math.isfinite(mean):
            continue
        mdate = meeting_date(ev.get("close_time"))
        if mdate is None:
            continue
        key = (mdate, ev.get("close_time") or "")
        by_date[key] = (mdate, float(mean), ev.get("event_ticker"))
    steps = []
    for (mdate, _close), step in sorted(by_date.items()):
        if steps and steps[-1][0] == mdate:
            steps[-1] = step  # same date, later close supersedes
        else:
            steps.append(step)
    return steps


def step_in_force(steps: list, target: dt.date):
    """The step governing `target`: the last meeting on or before it."""
    chosen = None
    for step in steps:
        if step[0] <= target:
            chosen = step
        else:
            break
    return chosen


def vintage_rows(vintage_date: str, series_snapshot: dict,
                 horizons=HORIZONS_MONTHS) -> list[dict]:
    """Fixed-horizon rows for one vintage (empty if no step is in force)."""
    vdate = dt.date.fromisoformat(vintage_date)
    steps = meeting_steps(series_snapshot)
    rows = []
    for months in horizons:
        target = add_months(vdate, months)
        step = step_in_force(steps, target)
        if step is None:
            continue
        mdate, mean, ticker = step
        rows.append({
            "vintage_date": vintage_date,
            "horizon_months": months,
            "target_date": target.isoformat(),
            "implied_rate": mean,
            "source_event": ticker,
            "source_meeting_date": mdate.isoformat(),
        })
    return rows


def _vintage_dates(vintages_dir: Path) -> list[str]:
    if not vintages_dir.is_dir():
        return []
    out = []
    for child in sorted(vintages_dir.iterdir()):
        if not child.is_dir():
            continue
        try:
            dt.date.fromisoformat(child.name)
        except ValueError:
            continue  # staging siblings and the README are not vintages
        if (child / "manifest.json").is_file():
            out.append(child.name)
    return out


def fixed_horizon_frame(vintages_dir: Path,
                        horizons=HORIZONS_MONTHS) -> pl.DataFrame:
    """All fixed-horizon rows over the archive, horizon-major order."""
    rows = []
    for vdate in _vintage_dates(vintages_dir):
        snap_path = vintages_dir / vdate / f"{SERIES_KEY}.json"
        if not snap_path.is_file():
            continue  # a vintage without this series contributes nothing
        snap = json.loads(snap_path.read_text())
        rows.extend(vintage_rows(vdate, snap, horizons))
    rows.sort(key=lambda r: (r["horizon_months"], r["vintage_date"]))
    if not rows:
        return pl.DataFrame(schema=FRAME_SCHEMA)
    return pl.DataFrame(rows, schema=FRAME_SCHEMA)


def _axis_domain(values: list[float]) -> list[float] | None:
    """Shared panel axis: quarter-point padding around the data range."""
    if not values:
        return None
    lo = max(0.0, math.floor((min(values) - 0.25) * 4) / 4)
    hi = math.ceil((max(values) + 0.25) * 4) / 4
    return [lo, hi]


def build_fixed_horizon(data_dir: Path,
                        horizons=HORIZONS_MONTHS) -> tuple[pl.DataFrame, dict | None]:
    """(frame, site section) for the fixed-horizon view.

    The site section is None when the archive yields no rows at all
    (e.g. a checkout predating the archive); the frame is then empty
    and the caller still writes the CSV header so the file's presence
    is stable.
    """
    data_dir = Path(data_dir)
    frame = fixed_horizon_frame(data_dir / "vintages", horizons)
    if frame.height == 0:
        return frame, None
    reg = INDICATORS[SERIES_KEY]
    by_horizon = []
    for months in horizons:
        pts = [
            {"date": r["vintage_date"], "value": r["implied_rate"],
             "target_date": r["target_date"],
             "source_event": r["source_event"],
             "meeting_date": r["source_meeting_date"]}
            for r in frame.filter(pl.col("horizon_months") == months)
            .iter_rows(named=True)
        ]
        if pts:
            by_horizon.append({
                "months": months,
                "label": f"{months} months ahead",
                "points": pts,
                "latest": pts[-1],
            })
    dates = frame["vintage_date"].unique().sort().to_list()
    # Exchange link: the front open meeting of the latest vintage — the
    # earliest step still in the future at that vintage.
    front = None
    snap_path = data_dir / "vintages" / dates[-1] / f"{SERIES_KEY}.json"
    if snap_path.is_file():
        latest = dt.date.fromisoformat(dates[-1])
        for mdate, _mean, ticker in meeting_steps(
                json.loads(snap_path.read_text())):
            if mdate > latest:
                front = ticker
                break
    section = {
        "title": "Implied policy rate at fixed horizons",
        "series_key": SERIES_KEY,
        "series_ticker": reg["series_ticker"],
        "source": reg["source"],
        "frequency": "Daily — one observation per vintage snapshot",
        "unit": "percent",
        "definition": DEFINITION,
        "note": NOTE,
        "archive_start": dates[0],
        "as_of": dates[-1],
        "kalshi_url": kalshi_event_url(front) if front else None,
        "front_event": front,
        "y_domain": _axis_domain(frame["implied_rate"].to_list()),
        "horizons": by_horizon,
    }
    return frame, section


# ---------------------------------------------------------------------------
# Print families (CPI, GDP): the same fixed-horizon treatment for the
# families whose events forecast a dated print rather than set a level.
#
# A policy rate persists between meetings, so the Fed path is sampled
# with the step in force at the target date. A print does not persist:
# the December CPI expectation says nothing about February's print.
# Print families are therefore sampled by reference period — the
# reading at horizon h is the implied mean of the event whose
# reference month (CPI) or quarter (GDP) contains the target date
# vintage + h. When no archived event covers the target period the
# horizon is omitted, exactly as the Fed series omits horizons with no
# meeting in force: nothing is carried forward and nothing is
# interpolated. Every row names its source event and reference period
# so the reading can be audited against the archive.
# ---------------------------------------------------------------------------

PRINT_FRAME_SCHEMA = {
    "vintage_date": pl.Utf8,
    "horizon_months": pl.Int64,
    "target_date": pl.Utf8,
    "implied_value": pl.Float64,
    "source_event": pl.Utf8,
    "reference_period": pl.Utf8,
}

PRINT_FAMILIES = {
    "cpi_yoy": {
        "csv": "fixed_horizon_cpi_yoy.csv",
        "title": "Implied CPI inflation at fixed horizons",
        "definition": (
            "The implied mean year-over-year CPI-U inflation for the "
            "reference month containing the date 3, 6, and 12 months "
            "after each vintage date. Every monthly CPI event in the "
            "day's archived snapshot (series KXCPIYOY) is reduced to "
            "the mean of its recovered distribution; a horizon reports "
            "the event whose reference month contains the sampling "
            "date. The exchange lists CPI events for only the next two "
            "reference months, so a horizon is reported only once an "
            "event for its reference month has been listed — no row is "
            "carried forward from an earlier month. Indexed by horizon "
            "rather than by contract, so the series are continuous "
            "across contract rolls."
        ),
        "note": (
            "Risk-neutral (Q-measure) expectations, upward-biased "
            "relative to survey forecasts by the inflation risk "
            "premium; far prints trade thin and their closes can be "
            "stale. The data file (data/predmarkets/"
            "fixed_horizon_cpi_yoy.csv) names the source event and "
            "reference month of every reading, so any reading can be "
            "audited against the archive. Accuracy records score each "
            "reading against the realized print for the same event "
            "(data/predmarkets/fixed_horizon_accuracy.csv)."
        ),
    },
    "gdp_quarterly": {
        "csv": "fixed_horizon_gdp_quarterly.csv",
        "title": "Implied GDP growth at fixed horizons",
        "definition": (
            "The implied mean real GDP growth (advance estimate, per "
            "cent at an annual rate) for the reference quarter "
            "containing the date 3, 6, and 12 months after each "
            "vintage date. Every quarterly GDP event in the day's "
            "archived snapshot (series KXGDP) is reduced to the mean "
            "of its recovered distribution; a horizon reports the "
            "event whose reference quarter contains the sampling "
            "date. A horizon is reported only when an event for that "
            "quarter is listed — no row is carried forward from an "
            "earlier quarter. Indexed by horizon rather than by "
            "contract, so the series are continuous across contract "
            "rolls."
        ),
        "note": (
            "Risk-neutral (Q-measure) expectations; far quarters "
            "trade thin and their closes can be stale. The event "
            "ticker names the release date; the reference quarter is "
            "the quarter ending the month before the release. The "
            "data file (data/predmarkets/"
            "fixed_horizon_gdp_quarterly.csv) names the source event "
            "and reference quarter of every reading, so any reading "
            "can be audited against the archive. Accuracy records "
            "score each reading against the realized advance print "
            "for the same event "
            "(data/predmarkets/fixed_horizon_accuracy.csv)."
        ),
    },
}


def _month_span(year: int, month: int) -> tuple[dt.date, dt.date]:
    return (dt.date(year, month, 1),
            dt.date(year, month, calendar.monthrange(year, month)[1]))


def _quarter_span(year: int, quarter: int) -> tuple[dt.date, dt.date]:
    first_month = 3 * quarter - 2
    start, _ = _month_span(year, first_month)
    _, end = _month_span(year, first_month + 2)
    return start, end


def cpi_reference_period(event_ticker: str | None):
    """(label, first day, last day) of a CPI event's reference month.

    The reference month is named in the ticker (KXCPIYOY-26NOV ->
    2026-11), the same convention realized.py uses to map these events
    to prints. Returns None when the ticker does not parse.
    """
    m = re.search(r"KXCPIYOY-(\d{2})([A-Z]{3})$", event_ticker or "")
    if not m:
        return None
    month = MONTH_CODES.get(m.group(2))
    if month is None:
        return None
    year = 2000 + int(m.group(1))
    start, end = _month_span(year, month)
    return (f"{year:04d}-{month:02d}", start, end)


_GDP_TICKER_DATE = re.compile(r"KXGDP-(\d{2})([A-Z]{3})(\d{2})$")


def gdp_reference_period(event: dict):
    """(label, first day, last day) of a GDP event's reference quarter.

    The ticker names the release date (KXGDP-27APR29 -> the release of
    2027-04-29); the reference quarter is the quarter ending the month
    before the release month (an April release reports Q1). When the
    ticker does not parse, the event's ET close date stands in for the
    release date. Returns None when neither is available.
    """
    release = None
    m = _GDP_TICKER_DATE.search(event.get("event_ticker") or "")
    if m:
        month = MONTH_CODES.get(m.group(2))
        if month is not None:
            try:
                release = dt.date(2000 + int(m.group(1)), month,
                                  int(m.group(3)))
            except ValueError:
                release = None
    if release is None:
        release = meeting_date(event.get("close_time"))
    if release is None:
        return None
    end_month, end_year = release.month - 1, release.year
    if end_month == 0:
        end_month, end_year = 12, end_year - 1
    quarter = (end_month - 1) // 3 + 1
    start, end = _quarter_span(end_year, quarter)
    return (f"{end_year}Q{quarter}", start, end)


def reference_period(family: str, event: dict):
    """The reference period of one archived event of a print family."""
    if family == "cpi_yoy":
        return cpi_reference_period(event.get("event_ticker"))
    if family == "gdp_quarterly":
        return gdp_reference_period(event)
    return None


def print_steps(series_snapshot: dict, family: str) -> list[dict]:
    """Reference-period steps of one vintage's print-family snapshot.

    Returns [{period, start, end, mean, event}] sorted by period
    start. Only events with a recovered threshold-bin distribution, a
    finite mean, and a parseable reference period carry a step.
    """
    out = []
    for ev in series_snapshot.get("events") or []:
        dist = ev.get("distribution") or {}
        if dist.get("kind") != "threshold_bins":
            continue
        mean = dist.get("mean")
        if mean is None or not math.isfinite(mean):
            continue
        ref = reference_period(family, ev)
        if ref is None:
            continue
        label, start, end = ref
        out.append({"period": label, "start": start, "end": end,
                    "mean": float(mean), "event": ev.get("event_ticker")})
    out.sort(key=lambda s: s["start"])
    return out


def print_vintage_rows(vintage_date: str, series_snapshot: dict,
                       family: str,
                       horizons=HORIZONS_MONTHS) -> list[dict]:
    """Fixed-horizon rows for one vintage of a print family.

    A horizon reports the step whose reference period contains the
    target date; horizons with no covering event are omitted — never
    carried forward from an earlier period, never interpolated.
    """
    vdate = dt.date.fromisoformat(vintage_date)
    steps = print_steps(series_snapshot, family)
    rows = []
    for months in horizons:
        target = add_months(vdate, months)
        for step in steps:
            if step["start"] <= target <= step["end"]:
                rows.append({
                    "vintage_date": vintage_date,
                    "horizon_months": months,
                    "target_date": target.isoformat(),
                    "implied_value": step["mean"],
                    "source_event": step["event"],
                    "reference_period": step["period"],
                })
                break
    return rows


def print_frame(vintages_dir: Path, family: str,
                horizons=HORIZONS_MONTHS) -> pl.DataFrame:
    """All fixed-horizon rows of one print family over the archive."""
    rows = []
    for vdate in _vintage_dates(vintages_dir):
        snap_path = vintages_dir / vdate / f"{family}.json"
        if not snap_path.is_file():
            continue  # a vintage without this series contributes nothing
        snap = json.loads(snap_path.read_text())
        rows.extend(print_vintage_rows(vdate, snap, family, horizons))
    rows.sort(key=lambda r: (r["horizon_months"], r["vintage_date"]))
    if not rows:
        return pl.DataFrame(schema=PRINT_FRAME_SCHEMA)
    return pl.DataFrame(rows, schema=PRINT_FRAME_SCHEMA)


def _print_section(family: str, frame: pl.DataFrame,
                   data_dir: Path) -> dict | None:
    """Site section for one print family (None while it has no rows)."""
    if frame.height == 0:
        return None
    cfg = PRINT_FAMILIES[family]
    reg = INDICATORS[family]
    by_horizon = []
    for months in HORIZONS_MONTHS:
        pts = [
            {"date": r["vintage_date"], "value": r["implied_value"],
             "target_date": r["target_date"],
             "source_event": r["source_event"],
             "reference_period": r["reference_period"]}
            for r in frame.filter(pl.col("horizon_months") == months)
            .iter_rows(named=True)
        ]
        if pts:
            by_horizon.append({
                "months": months,
                "label": f"{months} months ahead",
                "points": pts,
                "latest": pts[-1],
            })
    dates = frame["vintage_date"].unique().sort().to_list()
    # Exchange link: the front event of the latest vintage — the
    # earliest reference period not yet over at that vintage.
    front = None
    snap_path = data_dir / "vintages" / dates[-1] / f"{family}.json"
    if snap_path.is_file():
        latest = dt.date.fromisoformat(dates[-1])
        steps = print_steps(json.loads(snap_path.read_text()), family)
        for step in steps:
            if step["end"] >= latest:
                front = step["event"]
                break
        if front is None and steps:
            front = steps[-1]["event"]
    return {
        "title": cfg["title"],
        "series_key": family,
        "series_ticker": reg["series_ticker"],
        "source": reg["source"],
        "frequency": "Daily — one observation per vintage snapshot",
        "unit": "percent",
        "kind": "print",
        "definition": cfg["definition"],
        "note": cfg["note"],
        "archive_start": dates[0],
        "as_of": dates[-1],
        "kalshi_url": kalshi_event_url(front) if front else None,
        "front_event": front,
        "y_domain": _axis_domain(frame["implied_value"].to_list()),
        "horizons": by_horizon,
    }


def build_print_families(data_dir: Path) -> dict:
    """{family: (frame, site section | None)} for the print families.

    Same contract as build_fixed_horizon: a family's section is None
    while the archive yields no rows for it (for CPI, until the
    exchange lists an event for a reference month a fixed horizon
    samples); the caller still writes its CSV header so the file's
    presence is stable.
    """
    data_dir = Path(data_dir)
    out = {}
    for family in PRINT_FAMILIES:
        frame = print_frame(data_dir / "vintages", family)
        out[family] = (frame, _print_section(family, frame, data_dir))
    return out
