# Prediction-markets vintage archive

Append-only snapshots of the prediction-markets dataset's market state,
one directory per run date: `vintages/<YYYY-MM-DD>/`. Written by
`src/predmarkets/vintage.py` at the end of every daily build
(`src/predmarkets/build_predmarkets.py`).

The date is the America/New_York calendar date of the run (the
exchange's trading day is ET-based). A snapshot records the market
state the build observed on that date, so "what did markets imply on
date X" is a data query over this directory rather than a
reconstruction from later data.

## Append-only guarantee

A vintage directory is written exactly once. The writer stages the
snapshot in a temporary sibling directory and renames it into place
only if the target date directory does not already exist; if it does
exist, the writer changes nothing and the run logs the skip. A
same-day re-run is therefore a no-op for the archive, and no later
run can rewrite a published vintage.

## Layout

```
vintages/<YYYY-MM-DD>/
  manifest.json     run summary + per-series event index
  <indicator>.json  one file per fetched series (e.g. fed_funds.json)
```

### manifest.json

| field | meaning |
|---|---|
| `schema_version` | archive schema version (currently 1) |
| `vintage_date` | the directory's date |
| `created_at_utc` | when the snapshot was taken (build clock, UTC) |
| `api_base` | the exchange API base the events were fetched from |
| `build` | the run's summary counts, copied from `data/predmarkets/_meta.json` (`built_at_utc`, `incremental`, `n_indicators`, `n_live`, `n_no_open_market`, `n_error`) |
| `series` | per indicator key: `file`, `series_ticker`, `n_events`, and the archived `events` tickers |

### `<indicator>.json`

| field | meaning |
|---|---|
| `schema_version`, `key`, `series_ticker`, `vintage_date`, `snapshot_at_utc` | as in the manifest |
| `events` | every event the day's listing returned with nested markets — the build's open-event set — in close-time order. Recently settled events remain in the exchange's listing for some weeks after settlement; they are recognizable by a `close_time` in the past and a `ladder_date` at their final trading day, and their ladders are their final pre-settlement state |

Each event record:

| field | meaning |
|---|---|
| `event_ticker` | the event's exchange ticker |
| `title` | the event's title as listed |
| `close_time` | the latest close among the event's markets (ISO 8601) |
| `ladder_date` | the trading date of the ladder rows below — the latest date for this event in the committed per-indicator CSV at build time (`null` if the event had no rows yet) |
| `markets` | the full strike ladder on `ladder_date`, ordered by parsed strike level (unparseable labels last), then ticker |
| `distribution` | the implied distribution recovered from the ladder, or `null` (see below) |

Each market row is the committed frame's own record — the snapshot
adds no prices of its own:

| field | meaning |
|---|---|
| `ticker` | market ticker |
| `strike` | strike label as listed (e.g. `>4.25%`, `Hike 25bps`) |
| `level` | the parsed numeric strike (`null` when the label is not numeric) |
| `price` | the day's closing price of the $1-notional binary contract, i.e. a probability |
| `volume` | the day's volume; 0 marks a day without trades, where the close is the exchange's carried previous price |

### Recovered distributions

Recovered with `src/predmarkets/distribution.py`, following
Diercks/Katz/Wright (2026), FEDS 2026-010, Section 3 — the same
machinery the site build uses. These are risk-neutral probabilities,
not physical ones; tail contracts are thin and can be stale.

- Threshold series (`fed_funds`, `cpi_yoy`, `gdp_quarterly`,
  `unemployment`): `distribution.kind` is `threshold_bins`, with
  `bins` (`low` / `high` / `mass`; `null` marks an open end),
  `quantiles` (`q05`–`q95`), `mean`, and `variance`. Present when the
  ladder carries at least two parseable strikes.
- Mutually exclusive events (`fomc_decision`): `distribution.kind` is
  `exclusive_buckets`, with one `buckets` entry per market —
  `label`, raw `price`, and renormalized `mass`. The buckets are
  archived as listed; translating them into rate levels requires the
  pre-meeting target range and is left to consumers.
- Independent binaries (`recession`, `ipo_pipeline`): each market is
  its own binary question, so `distribution` is `null` and the
  ladder itself is the record.
