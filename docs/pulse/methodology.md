# Market Pulse — methodology & sources

Per-indicator sourcing, construction details, and the full citations behind
each card's commentary. The pulse is a *raw-series* dashboard: every
indicator is charted in its own units at native frequency — never as heat
quantiles — and proxies are labeled as proxies. Series are fetched by
`src/pulse/build_pulse.py` (weekly, incremental; failures mark the
indicator `error` in `data/pulse/_meta.json` and keep the last good data —
never silently dropped or spliced).

Statuses: `live` (verified public feed), `live-proxy` (public stand-in for
an unlicensed/unavailable ideal measure), `live-experimental` (framework
shipped, series still a proof), `live-manual` (no machine feed —
hand-maintained CSV in `data/pulse/manual/`, see its README),
`pending-sourcing` (no confirmed public series yet).

## Segment: Investment Banking

- **Announced M&A deal value** — PitchBook Global M&A Report PDFs
  (annual chart data labels); `pitchbook.com/news/reports`. Announcements
  precede closes and lock in advisory/financing fees (the fee-pipeline
  argument standard in the deal-flow literature). Drawback: annual
  granularity only; includes PitchBook estimates for nondisclosed deals.
- **IPO pipeline (S-1 counts)** — SEC EDGAR quarterly master indexes;
  `sec.gov/edgar`. Registration precedes pricing by ~1-3 months.
- **High-yield issuance** — SIFMA Research quarterly Fixed Income reports
  (`sifma.org/research`): quarterly corporate issuance × HY share by type;
  monthly workbook is form-gated, so no monthly series is possible.
- **IB fee wallet** — LSEG Deals Intelligence quarterly IB Review; no
  stable archive (UUID-addressed) — pending-sourcing, honestly.

## Segment: FICC

- **MOVE proxy** — 21-day realized vol of FRED DGS10
  (`fred.stlouisfed.org/series/DGS10`), annualized; stands in for the
  licensed ICE BofA MOVE index (no free series).
- **HY / IG OAS** — FRED `BAMLH0A0HYM2`, `BAMLC0A0CM` (ICE BofA indices
  via St. Louis Fed). Spread-lead evidence: wide HY into 2007-08 and 2020
  months before realized defaults.
- **Agency MBS spread** — FRED `OBMMIC30YF` minus `DGS30` (weekly
  Wednesdays).
- **SLOOS net % tightening** — FRED `DRTSCILM`
  (`fred.stlouisfed.org/series/DRTSCILM`), C&I standards for large/mid
  firms, quarterly since 1990Q2. Credit-supply anchor: Lown & Morgan,
  "The Credit Channel and the Transmission of Monetary Policy" (NY Fed
  Economic Policy Review, 2000,
  `newyorkfed.org/medialibrary/media/research/epr/00v06n2/0006lown.pdf`) —
  loan growth slows substantially following reports of tighter standards.
  Fed release & charts: `federalreserve.gov/data/sloos.htm`.
- **NACM CMI** — NACM monthly Credit Managers' Index report PDFs
  (`nacm.org`); live-manual, see `data/pulse/manual/README.md`. The
  credit function is forward-looking (payment stress visible before
  delinquencies print); unfavorable factors <50 is the early flag.

## Segment: Equities

- **Put/call ratio** — CBOE daily market statistics
  (`cboe.com/us/options/market_statistics`); historical CSV stitched to
  the daily page, definitions matched exactly.
- **CAPE z-score** — Shiller CAPE (multpl.com republication; Yale
  `ie_data.xls` frozen 2023-10-17); starting-valuation return prediction:
  Campbell & Shiller (J. Portfolio Management 1988) lineage.
- **Equity issuance** — SIFMA quarterly reports (parsed PDF tables).
- **Factor rotation** — Yahoo Finance SPHB/SPLV; high-beta leadership as
  late-cycle marker.
- **VVIX** — CBOE free daily CSV
  `cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv`;
  dashboard `cboe.com/us/indices/dashboard/vvix`. Threshold interpretation
  (>120-140 = vol-regime break) per practitioner/academic use; episodes:
  Feb 2018, Mar 2020.

## Segment: Asset & Wealth Management

- **Fund flows** — ICI statistical release workbooks via public web
  archive (`ici.org`); monthly actuals (weekly release has no continuous
  public archive).
- **MMF assets** — FRED `MMMFFAQ027S` (ICI weekly data, quarterly public
  cut on FRED).
- **S&P 500** — FRED `SP500` (rolling 10-year window; incremental merge
  accumulates local history).

## Segment: Firm-wide

- **CP–OIS / FRA–OIS proxies** — FRED `DCPN3M`/`DGS3MO` and forward
  constructions; CP spreads as funding-stress leads: the commercial-paper
  literature (e.g. Covitz, Downing & Luckett, "A Floating-Rate Index and
  the CP Panic of 2008", Fed FEDS 2013).
- **Margin debt** — FINRA Margin Statistics (Rule 4521)
  (`finra.org/rules-guidance/key-topics/margin-accounts`), monthly.
- **Dealer CDS proxy** — FRED `BAMLC0A3CA` (ICE BofA single-A); no free
  dealer-CDS feed exists.
- **NFCI credit subindex** — FRED `NFCICREDIT`
  (`fred.stlouisfed.org/series/NFCICREDIT`), Chicago Fed National
  Financial Conditions Index (Brave & Butters, "Monitoring Financial
  Stability," Chicago Fed Letter 305, Dec 2012,
  `chicagofed.org/publications/chicago-fed-letter/2012/december-305`;
  NFCI-vs-FCI-G lead: Chicago Fed Insights 2024,
  `chicagofed.org/publications/chicago-fed-insights/2024/nfci-future-economic-growth`).
  Revision caveat: principal-components construction means the entire
  history is recomputed weekly.

## Segment: Funding Plumbing

- **SOFR–IORB** — FRED `SOFR` minus `IORB` (IORB starts 2021-07-29).
- **ON RRP** — FRED `RRPONTTLD`.
- **Dealer fails** — NY Fed primary-dealer statistics (11 Treasury
  fails-to-deliver series, summed; "*" suppressed → 0).
- **FHLB advances** — FHLB Office of Finance Combined Financial Reports
  (`fhlb-of.com`), MD&A advances figure.
- **Treasury basis proxy** — FRED DGS10 vs Yahoo ZN=F implied yield.
- **EUR/USD xccy basis** — Du, Tepper & Verdelhan covered-interest-parity
  construction ("Deviations from Covered Interest Rate Parity", J. Finance
  2018) from CME Euro FX futures + FRED inputs.

## Segment: Positioning & Microstructure

- **Rate-futures z-scores** — CFTC Traders in Financial Futures
  (`cftc.gov/MarketReports`), leveraged-fund nets, 104-week z.
- **ETF dislocation proxy** — Yahoo LQD/HYG vs 20d mean; the March 2020
  discount/NAV episode is the calibration case.
- **Excess Bond Premium** — Gilchrist & Zakrajšek methodology (FEDS
  Notes; original: "Credit Spreads and Business Cycle Fluctuations", AER
  2012); CDS-bond basis interpretation: Bai & Collin-Dufresne, "The
  CDS-bond basis" (2019).
- **Bank insider net selling** — SEC Form 4 via OpenInsider
  (`openinsider.com`), 8 US GSIBs.

## Segment: Filings & Behavior

- **HTM unrealized losses** — FDIC Quarterly Banking Profile aggregate
  workbook (`fdic.gov/quarterly-banking-profile`).
- **MMF repo concentration** — SEC DERA Form N-MFP monthly data sets;
  top-3 dealer share ≈ 27.13% under the verified attribution.
- **Earnings-call credit language** — Motley Fool transcripts (JPM, BAC,
  WFC); v1 scoring framework, verified Q3-2026 calibration.

## Segment: Cross-Market

- **WTI calendar spread** — Yahoo CL=F prompt minus ~12M contract.

## Segment: Sentiment & Uncertainty

- **EPU daily** — FRED `USEPUINDXD`
  (`fred.stlouisfed.org/series/USEPUINDXD`); Baker, Bloom & Davis,
  "Measuring Economic Policy Uncertainty" (QJE 2016); downloads and
  categorical variants at `policyuncertainty.com`. EPU innovations lead
  investment/employment declines; smooth 7-30 days for daily use.
- **SF Fed Daily News Sentiment** — FRBSF download page
  (`frbsf.org/research-and-insights/data-and-indicators/daily-news-sentiment-index/`);
  Shapiro, Sudhof & Wilson, "Measuring News Sentiment" (J. Econometrics
  2022); lead evidence: FRBSF Economic Letter 2020-08 (the index moved ~2
  months ahead of survey sentiment into COVID). Weekly publication of a
  daily series.
- **GPR threats (GPT)** — Caldara & Iacoviello, "Measuring Geopolitical
  Risk" (AER 2022), export at `matteoiacoviello.com/gpr.htm`. Threats
  subindex leads acts; divergence caveat: Bondarenko et al. (2024) on
  local-language indices.
- **CFO Survey optimism** — The CFO Survey (Duke Fuqua / Richmond Fed /
  Atlanta Fed), quarterly press releases at
  `fuqua.duke.edu/duke-fuqua-insights/`; live-manual, see
  `data/pulse/manual/README.md`. Long-run CFO-expectations literature
  lineage: Graham & Harvey's survey work (1990s onward); the 2025Q1 tariff
  episode (66.0→62.1) and 2026Q3 reading (60.3) are the seeded episodes.

## Standing rules honored throughout

Polars only (no pandas); public data only (no proprietary feeds — BofA's
Fund Manager Survey was evaluated and excluded on exactly this ground);
raw data preserved under `data/pulse/` per-indicator CSVs; failed, missing,
or stale inputs mark their indicator honestly (`error`/absence) rather
than being averaged over; manual values exist only with row-level
provenance; straight chart lines (no smoothing fiction) — card text and
citations cite papers and institutions neutrally.
