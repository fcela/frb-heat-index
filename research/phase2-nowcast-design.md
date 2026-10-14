# Phase 2: Current-Quarter Nowcast Design

**Date:** 2026-10-14
**Goal:** Produce a 2026Q3 (partial, ended 14 days ago) heat-index nowcast without repeating the 7.6-sigma ragged-edge artifact (see `spike-audit-2026-10-14.md` — required reading).

## The mixed-frequency reality (verified 2026-10-14)

| Frequency | Series | 2026Q3 status |
|---|---|---|
| Daily | VIXCLS, DGS10 | Complete through 2026-10-13 |
| Monthly | BAA, rent CPI (2026-09); TOTALSL, REVOLSL, PSAVERT, DSPI (2026-08); Case-Shiller (2026-07) | Partial — 1–2 months of Q3 |
| Quarterly (SLOOS) | DRTSCILM | **2026Q3 published** (2026-07-01) |
| Quarterly (Z.1, NIPA, Y-9C) | Everything else | Not yet — Z.1 2026Q3 due ~mid-Dec 2026; Y-9C ~45 days post-quarter |

## Design principles

1. **Never publish a partial quarter as a full quarter.** The spike audit's lesson: a partial-quarter value treated as complete produced a 7.6-sigma artifact. The nowcast is a *separate product* from the quarterly index, clearly labeled.
2. **Fast vs slow decomposition.** Market-price series (VIX, spreads, valuations) move within the quarter and have high-frequency correlates — nowcast them. Balance-sheet stocks (leverage ratios, debt/GDP) move slowly — carry forward with drift, don't pretend to observe them.
3. **Every nowcast value carries its method.** No silent imputation. The site shows *how* each component was nowcast.

## Per-category nowcast procedure

### Category A — Directly observable (publish as preliminary)
Series with complete or near-complete Q3 data: VIX (daily), DGS10 (daily), DRTSCILM (SLOOS Q3 published), BAA (monthly through September ≈ Q3 average).
- **Procedure:** quarterly aggregation of available high-frequency data (mean of daily/monthly observations in quarter). Mark `method: observed_preliminary`.
- **Guard:** require ≥2/3 of quarter's trading days for daily series; ≥2 of 3 months for monthly. Below threshold → Category C.

### Category B — Bridge equations from high-frequency correlates
Series with strong intra-quarter correlates but no direct Q3 observation:
- **Credit spreads / issuance appetite:** bridge from daily IG/HY OAS (ICE BofA via FRED: BAMLH0A0HYM2, BAMLC0A0CMEY). Estimated relationship on historical data, applied to Q3-to-date.
- **House prices:** bridge from monthly Case-Shiller (2-month lag) + high-frequency housing proxies.
- **Procedure:** OLS bridge on 1990–2025Q4 history (in-sample, documented); nowcast = fitted value for Q3. Report the bridge R² alongside.
- **Guard:** if |nowcast − last observed| > 3 trailing standard deviations, cap at 3σ and flag `method: bridge_capped`.

### Category C — Slow-moving stocks (carry forward with drift)
Leverage ratios, debt/GDP, savings ratios, Y-9C/Z.1/NIPA constructs: these are balance-sheet stocks that evolve slowly.
- **Procedure:** last-observation-carried-forward + trailing-4-quarter drift: `x̂_Q3 = x_Q2 + mean(x_Q2 − x_Q1, ..., x_Q2−3 − x_Q2−4)/4`. Mark `method: carried_with_drift`, show the drift explicitly.
- **Rationale:** honest about what we don't observe. A leverage ratio doesn't jump in a quarter without a crisis — and if there's a crisis, Category A/B will show it.

### Category D — Unavailable (exclude, don't impute)
Shiller E/P (frozen 2023Q1), discontinued series without successors, maturity gap.
- **Procedure:** component computed over available series only; the validity manifest (Phase 3) records the exclusion. Never impute a value.

## Aggregation

- Components computed from whatever series are available under A/B/C; each component's nowcast notes its composition (e.g. "3 of 4 series: 1 observed, 1 bridged, 1 carried").
- The aggregate nowcast is the mean of available components, **with a coverage badge**: "Nowcast based on N/14 components; M series carried."
- The 10y-MA and standardization use the *frozen* historical parameters (no restandardization on partial data — the spike audit showed restandardization shifts history).

## Uncertainty communication (site spec)

1. **Preliminary badge** on every nowcast value: "Preliminary — will revise when Q3 finalizes (~Dec 2026)."
2. **Revision history:** when the quarter finalizes, the site shows nowcast vs finalized side by side for the last 4 quarters, building a track record.
3. **Method tags:** every component shows its worst method (observed > bridged > carried), color-coded.
4. **No nowcast for the heat-map colors:** the nowcast is published as a separate "current conditions" panel, not spliced into the historical heat map. Splicing would imply comparability the methodology can't support.

## What the nowcast is NOT

- Not a forecast of Q4 or beyond.
- Not a substitute for the quarterly pipeline (which remains the authoritative series).
- Not comparable point-for-point with the historical index (different standardization basis — documented, not hidden).
