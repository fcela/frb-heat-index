"""Append-only vintage archive of daily prediction-market snapshots.

Each build appends one immutable snapshot of that day's market state
under data/predmarkets/vintages/<YYYY-MM-DD>/, where the date is the
America/New_York calendar date of the run (the exchange's trading day
is ET-based; see kalshi.candle_trading_date). A snapshot holds, for
every event the day's listing returned with nested markets — the
build's open-event set, which includes recently settled events the
exchange still lists — in every fetched series:

  - the event's full strike ladder as recorded in the committed frame
    on the event's latest trading date: market ticker, strike label,
    parsed strike level, closing price (a probability), and volume
    (0 marks a day without trades, where the close is the exchange's
    carried previous price), and
  - the implied distribution recovered from that ladder by
    distribution.py, where the pipeline defines one: threshold-bin
    distributions for the series in build_predmarkets.DIST_GRIDS
    (bins, quantiles, moments), and normalized bucket masses for
    mutually exclusive events (the FOMC decision buckets). Series
    whose markets are independent binaries (recession years, IPO
    candidates) carry no recovered distribution; the ladder itself
    is the record.

plus a manifest.json with the run's build summary and the per-series
event index. The schema is documented in
data/predmarkets/vintages/README.md.

Append-only is enforced structurally: snapshots are written to a
temporary sibling directory and renamed into place only when the
target date directory does not exist. If it already exists, the
writer changes nothing and reports the skip, so a same-day re-run is
a no-op for the archive and a published vintage can never be
rewritten by a later run. "What did markets imply on date X" is
therefore a stable data query over the committed archive.
"""

import datetime as dt
import json
import math
import os
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from src.predmarkets import distribution as distmod
from src.predmarkets import kalshi
from src.predmarkets.indicators import FOMC_STRIKE_ORDER, INDICATORS

SCHEMA_VERSION = 1
DEFAULT_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)

_ET = ZoneInfo("America/New_York")


def vintage_date(now: dt.datetime) -> str:
    """Archive date of a run: the ET calendar date of `now`."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(_ET).date().isoformat()


def _clean(v):
    """Non-finite floats -> None, recursively (archive JSON is strict)."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _strict_json(obj) -> str:
    return json.dumps(_clean(obj), allow_nan=False, indent=1) + "\n"


def _event_close_time(event: dict) -> str | None:
    times = [kalshi.parse_iso_z(m["close_time"]) for m in event.get("markets") or []
             if m.get("close_time")]
    return max(times).isoformat() if times else None


def _ladder_rows(df: pl.DataFrame, event_ticker: str) -> tuple[str | None, list[dict]]:
    """(latest trading date, ladder rows on that date) for one event.

    Rows are the committed frame's own records (date, value, event,
    ticker, strike, volume) — the snapshot adds no prices of its own.
    Ordering is deterministic for diff-friendly files: by parsed level
    (unparseable labels last), then ticker.
    """
    if df is None or df.height == 0:
        return None, []
    rows = df.filter(pl.col("event") == event_ticker)
    if rows.height == 0:
        return None, []
    date = rows["date"].max()
    out = []
    for r in rows.filter(pl.col("date") == date).iter_rows(named=True):
        out.append({
            "ticker": r["ticker"],
            "strike": r["strike"],
            "level": distmod.parse_level(r["strike"]),
            "price": float(r["value"]),
            "volume": float(r["volume"]),
        })
    out.sort(key=lambda m: (m["level"] is None,
                            m["level"] if m["level"] is not None else 0.0,
                            m["ticker"]))
    return date, out


def _distribution(key: str, event: dict, ladder: list[dict],
                  threshold_keys, quantiles) -> dict | None:
    """Recovered distribution for one event's ladder, or None.

    Threshold series (the DIST_GRIDS set) recover bins by differencing
    the ladder, exactly as build_distribution_views does for the
    front event — here for every live event. Mutually exclusive
    events normalize their bucket prices. Anything else (independent
    binaries) has no distribution to recover.
    """
    if key in threshold_keys:
        pairs = [(m["level"], m["price"]) for m in ladder
                 if m["level"] is not None]
        if len(pairs) < 2:
            return None
        bins = distmod.recover_threshold_bins(
            [lv for lv, _ in pairs], [p for _, p in pairs])
        qs = distmod.distribution_quantiles(bins, list(quantiles))
        mo = distmod.distribution_moments(bins)
        return {
            "kind": "threshold_bins",
            "bins": bins,
            "quantiles": {f"q{int(q * 100):02d}": qs[q] for q in quantiles},
            "mean": mo["mean"],
            "variance": mo["variance"],
        }
    if event.get("mutually_exclusive") and len(ladder) >= 2:
        ordered = sorted(
            ladder,
            key=lambda m: (FOMC_STRIKE_ORDER.get(str(m["strike"]).strip(), 99),
                           str(m["strike"])))
        masses = distmod.recover_exclusive_buckets(
            [m["price"] for m in ordered])
        return {
            "kind": "exclusive_buckets",
            "buckets": [{"label": m["strike"], "price": m["price"], "mass": mass}
                        for m, mass in zip(ordered, masses)],
        }
    return None


def event_snapshot(key: str, event: dict, df: pl.DataFrame,
                   threshold_keys=frozenset(),
                   quantiles=DEFAULT_QUANTILES) -> dict:
    """One live event's archive record: identity, ladder, distribution."""
    date, ladder = _ladder_rows(df, event["event_ticker"])
    return {
        "event_ticker": event["event_ticker"],
        "title": event.get("title"),
        "close_time": _event_close_time(event),
        "ladder_date": date,
        "markets": ladder,
        "distribution": _distribution(key, event, ladder,
                                      threshold_keys, quantiles),
    }


def series_snapshot(key: str, events: list[dict], df: pl.DataFrame,
                    date: str, snapshot_at_utc: str,
                    threshold_keys=frozenset(),
                    quantiles=DEFAULT_QUANTILES) -> dict:
    """One series' archive file content: every live event, in the
    order the build discovered them (sorted by close time upstream)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "key": key,
        "series_ticker": INDICATORS[key]["series_ticker"],
        "vintage_date": date,
        "snapshot_at_utc": snapshot_at_utc,
        "events": [event_snapshot(key, ev, df, threshold_keys, quantiles)
                   for ev in events],
    }


def write_vintage(data_dir: Path, frames: dict, live_events: dict,
                  meta: dict, now: dt.datetime,
                  threshold_keys=frozenset(),
                  quantiles=DEFAULT_QUANTILES) -> dict:
    """Append the run's snapshot under data_dir/vintages/<date>/.

    frames / live_events are the build's per-series frames and open
    events, keyed by indicator key; meta is the build's _meta dict
    (its summary counts go into the manifest). Returns
    {"date", "status", "path", "n_events"} where status is "written"
    or "skipped-existing" (the date was already archived; nothing was
    modified).
    """
    date = vintage_date(now)
    base = Path(data_dir) / "vintages"
    target = base / date
    info = {"date": date, "path": str(target)}
    if target.exists():
        info.update(status="skipped-existing", n_events=0)
        return info
    snapshot_at = (now if now.tzinfo else now.replace(tzinfo=dt.timezone.utc))
    snapshot_at_utc = snapshot_at.astimezone(dt.timezone.utc).isoformat(
        timespec="seconds")

    series_files, manifest_series, n_events = {}, {}, 0
    for key in INDICATORS:
        if key not in frames:
            continue  # not fetched this run (derived or errored pre-frame)
        events = live_events.get(key) or []
        snap = series_snapshot(key, events, frames[key], date,
                               snapshot_at_utc, threshold_keys, quantiles)
        series_files[key] = snap
        manifest_series[key] = {
            "file": f"{key}.json",
            "series_ticker": snap["series_ticker"],
            "n_events": len(snap["events"]),
            "events": [e["event_ticker"] for e in snap["events"]],
        }
        n_events += len(snap["events"])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "vintage_date": date,
        "created_at_utc": snapshot_at_utc,
        "api_base": kalshi.API_BASE,
        "build": {k: meta.get(k) for k in (
            "built_at_utc", "incremental", "n_indicators", "n_live",
            "n_no_open_market", "n_error")},
        "series": manifest_series,
    }

    # Write everything to a staging sibling, then rename into place:
    # a vintage directory either exists complete or not at all, and an
    # existing one is never touched.
    base.mkdir(parents=True, exist_ok=True)
    staging = base / f".staging-{date}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    try:
        for key, snap in series_files.items():
            (staging / f"{key}.json").write_text(_strict_json(snap))
        (staging / "manifest.json").write_text(_strict_json(manifest))
        if target.exists():  # lost a race with a concurrent run
            shutil.rmtree(staging)
            info.update(status="skipped-existing", n_events=0)
            return info
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    info.update(status="written", n_events=n_events)
    return info
