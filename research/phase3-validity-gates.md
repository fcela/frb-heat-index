# Phase 3: Per-Series Validity Guarantees — Pipeline Spec

**Date:** 2026-10-14
**Motivation:** Without per-series validity enforcement, aggregation risks obscuring in the average the fact that some series are missing or incorrect. A bad value published is worse than a missing value (spike-audit lesson).

## 1. Per-series checks (every build)

Each check runs per series, per build, on the *raw* fetched data before transformation.

### 1a. Coverage continuity
- **What:** no unexpected gaps vs the series' expected frequency. A quarterly series must have exactly one observation per quarter in its coverage window; monthly/daily per theirs.
- **How:** compare observation dates against expected calendar; list gaps.
- **Severity:** FAIL if any gap > 1 period is unexplained (explained = documented source break, e.g. DRTSPM discontinuation).

### 1b. Freshness
- **What:** last observation within the source's normal publication lag.
- **How:** per-source lag table (committed, e.g. `data/source_lags.yaml`):
  - FRED daily: 5 business days; FRED monthly: 45 days; FRED quarterly: 100 days
  - Z.1: 100 days post-quarter-end; Y-9C: 60 days; SLOOS: 60 days
  - NIPA: 100 days; BIS: 180 days; manual sources: flagged always
- **Severity:** FLAG if stale (published late, still usable with note); FAIL if staleness exceeds 2× normal lag.

### 1c. Range / sanity
- **What:** no absurd values, no suspicious flatlines, no unannounced level shifts.
- **How:**
  - *Absurd:* |z-score vs trailing 40 quarters| > 6 → FAIL (the spike audit's 7.6σ would have been caught here).
  - *Flatline:* >4 consecutive identical values on a series that normally moves → FLAG (frozen source, e.g. Shiller).
  - *Level shift:* quarter-over-quarter change > 5 trailing σ with no documented source break → FLAG for human review.
- **Severity:** absurd → FAIL; flatline/shift → FLAG.

### 1d. Revision detection
- **What:** source restated history.
- **How:** hash the fetched raw file per series; compare against previous build's hash (stored in `_meta.json` history). If changed, diff and log the magnitude of revisions (max abs change in standardized units).
- **Severity:** log always; FLAG if any historical value moved > 0.5σ.

## 2. Validity manifest (`data/_validity.json`)

Every build writes a manifest, committed with the data:

```json
{
  "build_quarter": "2026Q2",
  "built_at_utc": "...",
  "series": {
    "<series name>": {
      "status": "pass|flag|fail",
      "last_obs": "2026Q2",
      "checks": {"coverage": "pass", "freshness": "flag: 12 days late", "sanity": "pass", "revision": "pass"},
      "method": "observed|bridged|carried|excluded"
    }
  },
  "components": {
    "<component>": {"n_series": 4, "n_valid": 3, "coverage_pct": 75, "publish": true}
  }
}
```

## 3. Aggregation rules (no silent averaging)

- **Component publication threshold:** a component is published only if ≥50% of its series are valid (pass, or flag with documented cause). Below threshold → component shows "insufficient data" — never an average over 1 of 4 series presented as normal.
- **Aggregate threshold:** published only if ≥10 of 14 components are publishable.
- **Parallel quality indicator:** every heat value is published alongside its coverage fraction. The site renders coverage as a border/opacity cue, not just a footnote.
- **Fail-closed on absurd, fail-open on stale:** a series failing sanity (absurd value) EXCLUDES the series and, if it breaches the component threshold, the component. A series flagged stale is still published with the flag visible. Rationale: a wrong number actively misleads; a late number merely ages.

## 4. Site surfacing (spec — not implemented yet)

- Per-component badge: green (all series pass), amber (≥1 flag), red (component unpublished).
- Staleness banner: "Data through 2026Q2; Z.1 2026Q3 expected ~Dec 2026" — generated from `_validity.json`, never hardcoded.
- Click-through: each component expands to its series list with per-series status and last-observation date.
- Nowcast panel (Phase 2) gets its own validity treatment: method tags double as validity states.

## 5. Pipeline enforcement (`update.yml`)

- The updater runs checks 1a–1d after fetching, before building.
- **FAIL (block publish):** any series with sanity FAIL and no documented override; any component below the 50% threshold without a recorded maintainer decision.
- **FLAG (publish with banner):** stale series, flatlines, revisions > 0.5σ.
- **Override mechanism:** `data/validity_overrides.yaml` — a series can be published despite a FAIL only with a dated, reasoned entry (e.g. "2026-10-14: DRTSPM discontinued; successor splice pending"). Overrides expire after 4 quarters unless renewed. No silent exceptions.
- **Run log:** every quarterly run opens/updates an issue with the validity manifest summary attached — the same visibility as the current fetch-status log, extended to validity.
