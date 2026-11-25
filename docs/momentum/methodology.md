# Pulse of the Market — methodology & sources

What this section computes, from what data, under which definitions.
The section exists because the heat map and the Market Pulse are
level displays: they say where things stand. This layer is about
movement — leading indicators, dated inflection points, and rotation.
Nothing on the page is a judgement call: every state is a stated
definition evaluated on the section's own archived series.

Series are fetched by `src/momentum/build_momentum.py` (weekly on
Mondays, incremental; a failed source marks the series `error` in
`data/momentum/_meta.json` and keeps its last archived data — never
fabricated, never silently spliced). All sources are free: Yahoo
Finance's chart API for prices, FRED (fredgraph) for macro and
volatility series, and the Federal Reserve's FEDS Notes file for the
excess bond premium. No paid feed is used anywhere in the build.

The archived series are **daily** even though the refresh is weekly:
each run fetches the full daily history, so every window below is
counted in sessions and every dated inflection is exact. The page
states the build date and the data as-of date (the last completed
U.S. session in the archive; an in-progress session's partial bar is
dropped at fetch time).

## Conventions

- **R_n(X)**: trailing n-observation return of X, in percent.
  **C_n(X)**: trailing n-observation change of X, in X's own units.
  Each is computed on the series' own observation rows; for
  cross-series states, the second series is read as of the primary
  series' dates over its own trailing window.
- **Prices are unadjusted closes** (Yahoo Finance chart API,
  `adjclose` not used) — the repository's standing convention (the
  pulse layer's factor-rotation series is built the same way).
  Unadjusted closes embed distributions: a dividend payer's measured
  return drops by roughly the dividend on its ex-date. The
  consequences are quantified and accepted: sector dividend yields
  run roughly 1–3% a year, which over a 21-session window is about
  0.1–0.3 pp of measured return — an order of magnitude below the
  3.0 pp rotation bar — and the 63-session trend window carries
  about 0.3–0.8 pp. A dividend-heavy panel (utilities, staples) can
  therefore read slightly weaker than its total-return self, and a
  state sitting within ~0.3 pp of a bar should be read with that in
  mind. The alternative (adjusted closes) embeds Yahoo's adjustment
  factors, which are themselves revised; the repository chose the
  auditable series.
- **Thresholds are fixed constants**, stated below, not fitted
  percentiles. A fitted threshold would make states
  frequency-stable and event-meaningless: "conditions tightening"
  should mean the same thing in 2019 and in 2026.
- **States are computed only from the archived series the build
  itself stores** (`data/momentum/`). No live-only value enters a
  state.
- Chart windows: the site bundle ships the trailing 252 observations
  per charted series (~1 year of daily data) and the trailing 64
  sessions, rebased to 100, for grid panels. Computations use the
  full archive; CSVs under `data/momentum/` carry the full history
  (Yahoo price history back to each fund's inception or 15 years,
  whichever the API returns first; FRED series to their starts).

## Fingerprint states

Evaluated at every build over the full archive; the board reports the
current state and the date the current run of that state began.

1. **Vol shock** — max(VIX close, trailing 5 sessions) ≥ 30 AND VIX
   21 sessions ago < 25. A spike out of a calm regime, not a chronic
   high.
2. **Vol inversion** — VIXCLS − VXVCLS > 0 (spot vol above 3-month
   vol; backwardation).
3. **Risk-off / risk-on rotation** — mean R_21 of XLP, XLU, XLV
   minus mean R_21 of XLY, XLI, XLK ≥ +3.0 pp → risk-off;
   ≤ −3.0 pp → risk-on (mirror). Cross-check displayed beside it:
   the trailing 60-session SPHB − SPLV return spread computed from
   this section's own archived closes (the same construction as the
   pulse layer's factor-rotation series) — risk-off rotation should
   coincide with a negative high-beta-minus-low-vol spread.
4. **Broadening / narrowing** — R_21(RSP) − R_21(SPY) ≥ +1.5 pp →
   broadening; ≤ −1.5 pp → narrowing. RSP is the equal-weight
   index — the average stock — so the spread is the breadth proxy.
   It is labelled a proxy: true constituent breadth (advance/decline,
   percent above moving averages) has no free, verifiable source.
5. **Credit-confirmed stress / equity-only divergence** —
   R_21(SPY) ≤ −5% AND C_21(HY OAS) ≥ +0.50 → credit-confirmed
   stress; the same equity leg with C_21(HY OAS) < +0.25 →
   equity-only divergence. The 25–50 bp band between is
   deliberately unclassified; the measured widening is shown either
   way.
6. **Haven bid** — R_21(GLD) > 0 AND R_21(SPY) < 0 AND
   C_21(broad dollar, DTWEXBGS) > 0. Its failure during an equity
   fall is informative and is reported as such.
7. **Rates shock** — |C_21(DGS10)| ≥ 0.40 (40 bp in 21 sessions),
   direction labelled.
8. **Energy shock** — |R_21(DCOILWTICO)| ≥ 15%, direction labelled.
   WTI is a FRED series with a publication lag of about a week; the
   window is 21 of its own observations.
9. **Conditions tightening** — NFCI 13-week change ≥ +0.10 while the
   NFCI level is still below 0: conditions moving the wrong way from
   a loose base, weeks before any stress index prints positive.
   Evaluated on weekly closes.
10. **Snapback** — R_21(SPY) ≤ −5% AND R_5(SPY) ≥ +3%. Labelled the
    weakest signal of the set: most snapbacks fail; the state exists
    so the board can say one is being attempted.

## Inflection signals

Turning points as dated events; the log gives the last date each
fired over the full archive.

1. **Vol-curve flip** — VIXCLS − VXVCLS crosses zero. Up-cross =
   inversion onset; down-cross = normalisation.
2. **Spread peak / deterioration turn** — HY OAS prints a
   63-session high, then tightens ≥ 0.15 from that high within 21
   sessions → credit has turned. Mirror: a 63-session low followed
   by ≥ 0.15 of widening within 21 sessions is the deterioration
   inflection.
3. **Momentum zero-cross** — SPY's trailing 21-session return
   crosses zero in either direction; the board dates the current
   momentum regime.
4. **Sector breadth thrust** — the share of the 15 grid panels in
   positive 21-session momentum moves from ≤ 30% to ≥ 70% (thrust
   up) or the reverse (thrust down), computed entirely from the
   section's own grid.
5. **Curve re-steepening** — C_21(T10Y2Y) ≥ +0.15 after a trailing
   63-session minimum ≤ 0 (edge-triggered: the first session the
   joint condition holds).
6. **Dollar turn** — sign flip of C_21(DTWEXBGS) after at least 42
   sessions of the opposite sign.

## Rotation

Per universe member against SPY: **RS-trend** = R_63(X) − R_63(SPY),
**RS-momentum** = R_21(X) − R_21(SPY), computed from closes on common
trading days. Quadrants: Leading (trend > 0, momentum > 0),
Weakening (trend > 0, momentum < 0), Lagging (both < 0), Improving
(trend < 0, momentum > 0). The quadrant is rendered as a table with
the two measured numbers — a scatter's axes would encode exactly
those numbers, and the table states them. The universe is the 15
grid panels plus RSP, QQQ, SPHB, and SPLV.

Named patterns, each a joint condition on the states and quadrants:

- **Defensive rotation** — risk-off rotation active AND at least two
  of XLP/XLU/XLV in Leading AND XLK in Lagging.
- **Cyclical rotation (risk-on)** — risk-on rotation active AND at
  least two of XLY/XLI/XLK in Leading AND XLP in Lagging (the mirror
  of defensive rotation).
- **Concentration unwind** — broadening active AND (QQQ or XLK in
  Lagging) AND RSP in Leading or Improving.
- **Broad liquidation** — at least 12 of the 15 grid panels with
  negative 21-session momentum AND credit-confirmed stress active.

The defensive-minus-cyclical 21-session momentum spread is archived
as a derived series and charted with its ±3 pp bars' zero line.

## Grid statistics

Per panel: R_5, R_21, R_63 (percent); distance from the 52-week high
(last close over the maximum of the trailing 252 observations, minus
one); 21-session realized volatility (standard deviation of the last
21 daily log returns, annualised by √252). All panels share one
rebased axis on the page (100 = 63 sessions ago).

## Sources and known gaps

- **Prices** — Yahoo Finance chart API (v8), unadjusted daily
  closes: SPY, RSP, QQQ, IWM, the eleven Select Sector SPDRs (XLB,
  XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY, XLC), SOXX, KRE,
  XHB, SPHB, SPLV, HYG, LQD, TLT, GLD, USO. The build drops any bar
  dated on or after the current New York date, so only completed
  sessions enter the archive.
- **Macro / volatility** — FRED via fredgraph: SP500, NASDAQCOM,
  VIXCLS, VXVCLS, GVZCLS, OVXCLS, BAMLH0A0HYM2, BAMLC0A0CM, NFCI and
  its credit/leverage/risk subindexes, ANFCI, STLFSI4, DGS2, DGS10,
  T10Y2Y, T10Y3M, T10YIE, DFII10, DTWEXBGS, DEXUSEU, DCOILWTICO,
  DCOILBRENTEU, ICSA, UMCSENT, CBBTCUSD.
- **Excess bond premium** — Gilchrist–Zakrajšek, from the Federal
  Reserve's FEDS Notes data file (the pulse layer's route); a
  labelled proxy in the pulse meta format (`live-proxy`), monthly.
- **Derived** — VIX − VXV spread and the defensive-minus-cyclical
  momentum spread are computed by the build from the archived
  primaries and archived in turn, so the states that read them read
  stored series.

Not sourced, deliberately:

- **Spot gold** — FRED's LBMA gold series are retired; gold enters
  through GLD closes instead. No substitute spot series is used.
- **True constituent breadth** — no free source; the RSP − SPY
  spread is the labelled proxy, and the breadth thrust is computed
  from this section's own grid.
- **Conference Board LEI / OECD CLI** — the LEI is not published on
  FRED (its components are used individually where they lead); the
  OECD US CLI series on FRED is a dead vintage (ends January 2024)
  and is excluded rather than charted as if current.
- **ISM new orders** — not on FRED; no free machine-readable route
  is used.
- **CBOE SKEW** — no verified free route at build time; excluded.

## Cadence

Weekly, Mondays (`momentum.yml`, 06:30 UTC — half an hour after the
Market Pulse run, so the two data commits never race). An earlier
design proposed a daily refresh; the section's states are weekly-
timescale objects (21-session windows, 13-week condition changes),
and the daily archive underneath keeps every dated inflection exact,
so the refresh is weekly by decision. Freshness expectations on the
page are set accordingly: a series is flagged stale only when its
last observation is older than one and a half of its own cadence
periods plus ten days, which absorbs both the weekly refresh and
ordinary publication lag (WTI and the trade-weighted dollar publish
about a week behind; NFCI is weekly; sentiment and the excess bond
premium are monthly).
