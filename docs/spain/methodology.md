# Spain heat index — methodology, sources, and the analogy

What this section computes, from what data, and — because the section
exists by analogy with the U.S. index on this site — an explicit
account of what the analogy carries over, what it cannot, and what it
had to add. This page is the design record; the build
(`src/spain/build_spain.py`, registry in `src/spain/series.py`) is
checked against it.

The section is an experiment. It is **not linked** from the landing
page or the navigation and is reachable only by direct URL.

## Construction

Same construction as the U.S. index, with two stated differences.

- **Quarterly panel**, 1999Q1 to the last quarter the sources reach.
- Each indicator is oriented so that higher means more vulnerable
  (the sign is stated per indicator in `docs/data/spain_meta.json`),
  then **percentile-transformed with the empirical CDF (midrank)
  against its own history**: a value of 0.9 means the reading is
  higher than 90% of that indicator's own past observations.
- A **component** is the mean of its available indicator percentiles
  that quarter. The **headline** is the mean of the components that
  have at least one available indicator. Components whose indicators
  are all source-pending do not enter the headline.
- The headline chart and `data/spain/coverage.csv` report how many
  components contribute each quarter. The latest same-component
  comparison averages only the components present in both quarters.
  The indicators available within each component can still differ.
- Monthly stock series enter at the quarter-end month; monthly flow
  and price series enter as quarterly means; ratios to GDP use the
  trailing four quarters of nominal GDP. A quarter enters only when
  its inputs exist.

Difference 1: **percentiles are computed against Spanish history
only.** A Spanish reading is never ranked against the U.S. sample;
the two indices are separately scaled and their levels are not
comparable point for point.

Difference 2: **series start at the euro changeover (1999) or at
their natural later start — never earlier, and never spliced.**
Several sources publish pre-1999 values converted from pesetas; the
conversion is a currency change, not a data break the panel should
smooth over, so pre-1999 observations are excluded even where the
source offers them. The INE house price index starts in 2007Q1 and
enters there; nothing is back-filled from another index.

**No interpolation, no filling, no splicing anywhere.** A quarter a
source does not publish is a missing value in
`data/spain/panel_quarterly.csv`, the percentile is missing, and the
component mean is taken over the indicators that do exist — the same
available-case rule as the U.S. panel.

Spain's sample contains a full systemic crisis (2010–13). That is a
feature of the calibration: the top of the 0–1 scale is anchored by
an episode in which the sovereign–bank loop forced a European
financial assistance programme for bank recapitalisation (2012), not
by a statistical tail.

The second build (this version) widened the nexus component from
two legs to five and added three further indicators elsewhere; the
additions and their reasoning are stated in the sections where
they belong, and the nexus changes are stated in the nexus section
below. The fourth build ran the proxy pass over the remaining
source-pending indicators: it widened the nexus component from
five legs to ten, brought commercial real estate into the headline
for the first time, and converted four source-pending indicators
into labelled proxies under the standard stated in "Partial, and
labelled as proxies" below. Three indicators remain
source-pending, each with a sharpened reason.

The fifth build was a refresh and retry pass. Every route was
re-exercised live; no source had advanced beyond the archived
snapshots, and the panel still ends at 2026Q2 — only 10 of the 35
live indicators had a 2026Q3 observation, short of the 60% rule —
so the panel and the headline are unchanged. The blocked routes
were retried with fresh evidence: the ECB yield-curve endpoint
now answers, and the corporate-spread, equity-yield and
contributors-per-pensioner outcomes are stated in their sections
below. The component heat map's rows are ordered by hierarchical
clustering of the component series (average linkage on pairwise
correlations), so components that heat and cool together sit
together; every other presentation of the components keeps
framework order.

One consequence is worth stating plainly, because it decides how
the headline should be read. The headline is a **vulnerability**
gauge, and vulnerability crests at the boom top: the headline peaks
at the boom top (2008Q3 in the fourth build, at 0.77, with 2007Q4
reading 0.76), when the credit gap, the funding gap, bank leverage
and household dissaving were jointly at their extremes — the same
pre-crisis peak shape as the U.S. index. The 2010–13 window is when
that vulnerability *realised*: short-term wholesale funding peaks
there (2012, on its Eurosystem leg), price volatility crests with
the 2008–09 global stress, and the nexus component's price leg —
the Bono–Bund spread — peaks in 2012. The nexus component as a
whole crests later, in 2016, because the second build added the
debt-stock legs (public and regional debt to GDP, the interest
burden, holdings measured against bank capital), and Spain's debt
stock kept rising after the spread normalised. A
reader looking for "when was Spain in crisis" should read those
components; a reader looking for "when was Spain most exposed"
should read the headline. The panel's last quarter is the latest
quarter in which at least 60% of the live indicators have an
observation, so the tail never rests on a minority of fast sources.

## What transfers, and the Spanish source for it

The U.S. framework's sector logic — banks, households, firms, real
estate, funding markets — is not U.S.-specific. These components
transfer, each with a Spanish source of the same statistical family
as the U.S. one:

- **Credit cycle** — the BIS credit-to-GDP gap and debt service
  ratio for Spain. Same source family, same published construction
  as the U.S. pair (BIS Data Portal, SDMX). This is the cleanest
  transfer in the section.
- **Bank leverage** — capital and reserves over the total balance
  sheet of Spanish deposit institutions (Banco de España Boletín
  Estadístico table 8.32), the balance-sheet counterpart of the U.S.
  Y-9C equity ratios. The CET1 ratio sits beside it from the fourth
  build, as a labelled proxy: the ECB's supervisory statistics
  publish a quarterly CET1 ratio for Spanish significant
  institutions from 2015Q2 (11.5–13.7% over its history). The Banco
  de España's own system solvency ratio is published only in
  Financial Stability Report tables; the significant-institution
  sample covers most system assets but is a changing SSM sample,
  and the series starts after the crisis it would need to judge.
- **Maturity transformation** — the loans-to-deposits ratio of
  deposit institutions (BdE tables 8.33 and 8.34: loans to non-bank
  residents over deposits of other resident sectors). In Spain this
  is not a minor funding detail: the pre-2012 system lent far beyond
  its deposit base, and the gap was the balance-sheet fact the
  crisis ran through. The balance-sheet-tables variant peaks at
  133% in 2006Q3; the Banco de España's stability-report variant,
  which adds securitised loans back and strips repos from deposits,
  prints higher — the difference is definitional, and the panel
  uses the two published aggregates with no adjustment.
- **Size/concentration** — financial institutions' total liabilities
  to GDP from the Banco de España financial accounts (the CFEE, the
  Z.1 counterpart). The top-5 asset share was verified series by
  series in the fourth build: the ECB structural indicators (SSI)
  series exists — 40.3% of system assets in 1999 rising to 67.4% in
  2025 — but the dataset is annual throughout, and an annual series
  cannot enter a quarterly panel without filling, so it stays
  source-pending as an explicit frequency decision point. The
  component also carries **tourism
  receipts to GDP** (BdE balance of payments, table 17.4): a
  structural single-sector exposure, not a cyclical heat gauge.
  Tourism is the largest single export sector of the Spanish
  economy — about 12% of GDP on the INE tourism satellite account
  including indirect effects — and the balance-of-payments receipts
  series is the quarterly measurable part of that exposure. It is
  ranked here as a concentration measure of the economy's external
  earnings base; its percentile reads high whenever receipts are
  large relative to Spain's own past, including the record shares
  of 2023–25. No counterpart exists in the U.S. framework. The
  fourth build added a second tourism leg, a labelled proxy:
  hospitality employment as a share of total employment (Eurostat
  LFS, NACE division I) — the labour-market face of the same
  concentration, which the receipts series misses in a
  price-driven boom; NACE I is the measurable core of tourism
  activity, not its whole footprint, as the series definition
  states.
- **Home mortgages** — housing loans to households over GDP (BdE
  table 8.19) and total household credit to GDP (BIS). See below for
  the part of the U.S. mortgage block that does *not* transfer.
- **Consumer credit** — MFI consumer credit to households over GDP
  (BdE table 8.19). The fourth build added the **temporary-
  employment share** (Eurostat LFS) to this component. A labour
  series belongs in a financial heat index on the household-income
  channel: Spain's dual labour market concentrates employment risk
  on temporary contracts — about a third of employees at the boom,
  a quarter in 2019, and a step down to the mid-teens after the
  2021 labour reform — and temporary workers are the first incomes
  lost in a downturn, which is when consumer credit goes bad. The
  leg gauges the fragility of the income stream the component's
  credit stock is serviced from, not the credit itself.
- **Nonfinancial business debt** — total credit to non-financial
  corporations over GDP (BIS).
- **Net saving** — households' net financial saving over GDP from
  the financial accounts (BdE table 2.7), labelled a **proxy**: it
  is the financial-accounts counterpart of the U.S. NIPA net saving
  rate, not the national-accounts saving rate itself.
- **Residential real estate** — house price growth from the BIS
  residential property price index (via FRED) and from the INE
  house price index (IPV, from 2007). The fourth build converted
  the component's two source-pending gauges into labelled proxies.
  **Price-to-income** is constructed: the INE house price index
  level (IPV769) divided by household gross disposable income per
  employed person (Eurostat quarterly sector accounts over LFS
  employment). It stands in for the Banco de España's published
  effort rate — price of a standard dwelling over gross household
  income, published in a workbook with no series route — and
  differs from it as the series definition states: an index
  numerator rather than a dwelling price, a per-worker rather than
  per-household denominator, and entry in 2008Q1 rather than at
  the boom's peak quarters. **Construction** is an activity proxy:
  residential building permits by useful floor area (Eurostat
  short-term statistics, from the Ministerio de Transportes
  visados), quarterly mean of monthly. The concept the slot exists
  for is credit to construction firms; that stock still has no
  machine-readable route (the Boletín CSV service carries no
  activity breakdown, and the ECB's BSI branch estimates are
  published only as euro-area aggregates), so the proxy measures
  the building pipeline the credit financed — it led the boom and
  collapsed by more than 95% to 2013 — and is silent on the legacy
  construction-debt overhang, as its definition states.
- **Business credit** — the ECB Bank Lending Survey's Spain
  breakdown is the SLOOS counterpart: net percentage of banks
  tightening credit standards on loans to enterprises, published
  from 2003Q1 (ECB BLS dataflow; the Spain key resolved in the
  second build, and the component now enters the headline). The
  price counterpart arrived in the fourth build as a labelled
  proxy: the TEDR new-lending rate to non-financial corporations
  (Boletín Estadístico table 19.3) minus the euro-area 3-month
  interbank rate — about 350 basis points at its 2013Q4 crisis
  peak and about 110 at its 2024 trough. It is labelled because
  the benchmark is a 3-month money-market rate, not the
  matched-maturity swap of the reference concept (no free swap
  route was resolved; the ECB yield-curve dataflow, exercised
  directly in the fifth build, publishes government par yields,
  not swap rates), so the margin carries
  a term-premium component, as its definition states. It is a
  bank-lending margin, not a corporate bond spread.
- **Short-term wholesale funding** — debt securities issued by MFIs
  over total liabilities (BdE table 8.3), with the Spanish twist
  below.

## Spain-specific: the sovereign–bank nexus

The U.S. index has no sovereign-risk component, correctly: the
Treasury is the safe asset, and U.S. vulnerability does not run
through the sovereign's funding cost. Spain is the opposite case.
The 2010–13 crisis was a loop: sovereign spreads widened, the value
of the sovereign paper on bank balance sheets fell, bank funding
costs followed the sovereign's, and the state's contingent liability
to the banks widened the spread again. A Spanish heat index without
this component would miss the channel its own calibration crisis
used. The component has ten legs — a price, bank balance-sheet
quantities, the sovereign's own stock and burden, and, from the
fourth build, the holder structure of the debt, its refinancing
structure, the whole economy's external balance sheet, and the
regional financing channel:

- **10-year Bono–Bund spread** (OECD long-term rates via FRED,
  quarterly mean of monthly). Spain prices against the Bund, not
  against itself; the spread is the market's price of Spanish
  sovereign risk over the euro area's safe asset. It peaked at
  roughly 600 basis points in mid-2012.
- **Credit institutions' holdings of Spanish general government
  debt securities over their capital and reserves** (BdE tables
  8.5 and 8.3): the quantity leg, measured against loss-absorbing
  capacity, which is the form in which the loop bites — a
  mark-to-market loss on the sovereign book falls on the capital
  that must absorb it. The first build divided by the total
  balance sheet instead; the capital-relative form replaces it.
  The levels carry the story: about 145% at the panel's start in
  1999 (pre-euro portfolios were sovereign-heavy and capital was
  thin), 37% at the boom top in 2007, 84% by the end of 2014, and
  about 96% at the panel's end — above the crisis reading, because
  banks kept absorbing sovereign issuance while capital grew
  slowly. The securities book is used, not loans to government,
  because it is the part that marks to market.
- **General government gross debt (EDP) to GDP** (BdE table
  11.5): the stock the spread is pricing. It fell to 36% of GDP in
  2007, crossed 100% in 2014, and peaked at 124% in 2021.
- **Regional government (Comunidades Autónomas) debt (EDP) to
  GDP** (BdE table 11.5), a separate leg: the regional layer is a
  Spain singularity. The regions deliver health and education,
  borrow in their own name, and when markets closed to them in
  2012–13 were refinanced through the central government's
  regional liquidity fund (FLA), which moved the risk rather than
  removing it. The stock crested in the mid-20s percent of GDP in
  2015–17; the ratio's peak (27% in 2021) is a denominator effect,
  when GDP collapsed and the regions borrowed through COVID.
- **Interest payments over total government revenue** (Eurostat
  quarterly government accounts, gov_10q_ggnfa; trailing four
  quarters): the flow cost of the stock — the share of each year's
  revenue committed to creditors before any policy is funded. It
  peaked in the crisis window at about 9% of revenue in 2014; the
  panel's first year reads higher still (about 10%), because Spain
  entered the euro with pre-convergence interest rates and the
  burden fell for a decade before the crisis raised it again.
- **Non-resident holdings of general government debt, share of
  total** (BdE table 11.13, EDP debt by holder). The external-
  dependence leg, and the holder-side counterpart of the spread:
  the share was at its highest, about 51%, in 2007; foreign holders
  sold through the spread crisis and the share troughed at roughly
  a third around 2012, with domestic banks and later the
  Eurosystem absorbing the stock; it has since recovered to near
  its 2007 level. A Spain-specific gauge because the sovereign's
  funding fragility in 2011–12 was precisely a foreign-holder
  strike; its sign is not ambiguous in this slot, where it
  measures who must be persuaded to roll the stock over.
- **Banco de España holdings of general government debt, share of
  total** (BdE table 11.13). The Banco de España holds the
  Eurosystem's asset-purchase portfolio of Spanish debt, so this
  share is the dependence on official European financing as a
  stock: negligible before 2015, over a quarter of the debt at the
  2022 peak, and falling since as the portfolio runs off. It is
  oriented as dependence, consistent with this index's treatment
  of Eurosystem borrowing in the funding component, with the
  offset stated on the series: a captive official holder also
  stabilises the market it dominates, and the gauge reads the
  dependence, not the net of the two.
- **Debt with residual maturity up to one year, share of total**
  (BdE table 11.13, securities plus loans). The refinancing-
  structure leg: the share of the stock that must be rolled within
  a year is the speed at which a funding strike reaches the
  sovereign. It was a fifth to a quarter of the stock at the
  panel's start and has trended down to the low teens — the
  Tesoro's maturity-lengthening, read from the residual-maturity
  table because the Tesoro's own average-life statistics are
  published as dated report files with no stable series route.
- **Net international investment position to GDP** (BdE tables
  17.21 and 2.1). The whole economy's net external balance sheet,
  and a first-order Spain gauge with no counterpart in the U.S.
  panel: the boom's current-account deficits were financed abroad,
  and the position sat between −85% and −97% of GDP from 2007 to
  the mid-2010s before improving to about −40%. It sits in this
  component because the nexus is where this index keeps the
  external-funding legs — the sovereign's holder structure above
  is the same dependence read on one issuer.
- **Regional debt financed by the state financing funds, share of
  regional debt** (BdE table 13.7). The sharp form of the regional
  leg: the stock above says how much the regions owe; this share
  says who funds them. The funds (the FLA and its successors) were
  created in 2012 when markets closed to the regions; the share
  passed half of all regional debt in the mid-2010s and peaks
  above 60% in 2024 — regional credit risk converted into a direct
  claim on the sovereign. The Banco de España publishes the loans
  as zero before 2012, when the instrument did not exist.

Because the legs mix a price with stocks, the component's own peak
is a stock peak, not the 2012 spread event: it crests in 2016
(2016Q1, at 0.69 in the fourth build), with the 2012–13 window
reading 0.52–0.55 — lower than the second build's 0.60–0.67,
because two of the fourth build's legs (the Banco de España
holdings share and the FLA share) are near zero before the
official-sector rescues they measure and so read cold in 2012 by
construction. The spread leg itself is at its maximum in that
window; the dilution is a composition fact, stated here rather
than smoothed over.

The funding component carries the second Spanish twist: **net
borrowing from the Eurosystem** (Banco de España monetary-policy
lending to resident MFIs, BdE table 7.3, over total MFI liabilities)
sits inside Short-term wholesale funding. When market funding closed
in 2011–12 and again in 2020, the Eurosystem replaced it; in Spain,
central-bank borrowing is a funding-fragility reading, not a
separate monetary story.

## Partial, and labelled as proxies

- **Price volatility** — the ECB's Composite Indicator of Systemic
  Stress **for Spain** (CISS, ECB Data Portal; the Spain key
  resolves, so this is the domestic series, not the euro-area
  aggregate). It is a market-price composite, with the standing
  caveat of price-based gauges: it reads calm until it doesn't.
- **Net saving** — the financial-accounts proxy described above,
  labelled in the metadata and on the page.
- **Equity valuation** — source-pending. No Shiller CAPE exists for
  Spain; the substitute concept is an IBEX earnings or dividend
  yield. The fourth build checked the ECB routes dataset by
  dataset: BME publishes index statistics as report files, the ECB
  FM dataflow is a security-level feed whose data-type codelist
  carries no aggregate dividend- or earnings-yield series for a
  Spanish or euro-area index, and the SEC accounts carry dividend
  transactions, not yields. The fifth build re-checked BME: its
  published IBEX factsheet carries returns, not a valuation
  yield, and BME supplies historical information and other
  formats through its paid Market Data service, with
  redistribution of its website information requiring prior
  permission. The component stays empty rather than
  substituting a price level for a valuation yield.
- **Corporate spreads** — source-pending, and when sourced it will
  be a **labelled euro-area proxy**: Spain's corporate bond market
  is too thin for a clean domestic OAS, and Spanish issuers fund at
  euro-area spreads plus a sovereign-linked premium the nexus
  component already measures. No route was resolved in the fourth
  build either: the ICE BofA euro-area OAS is licensed, the ECB FM
  dataflow publishes no aggregate euro-area corporate spread
  series, and a spread constructed against the ECB yield curve
  failed at the YC data endpoint, which returned errors and
  dropped connections on every data query from the build network
  while its catalogue endpoints answered normally. The fifth build
  retried the construction: the YC data endpoint now answers
  (SDMX 2.1; the par-yield tenors were exercised directly), so a
  benchmark leg exists, but the corporate leg still has none —
  FRED carries euro members of the ICE BofA family (the Euro High
  Yield OAS), but ICE's terms as published on FRED prohibit
  reproduction of the data and this section republishes its
  values, and the ECB publishes no aggregate corporate yield (the
  FM feed is security-level; the YC curve is government par
  yields only).
- **Commercial real estate prices** — live from the fourth build.
  Earlier builds recorded the BIS commercial property price
  statistics as portal downloads only; the SDMX route (WS_CPP)
  resolves, and the panel carries the four-quarter change of the
  Spain index (all property types, whole country). Two limits are
  stated on the series: the index starts in 2014Q1, so the gauge
  reads post-bust momentum and is silent on the 2007–13 commercial
  collapse; and the ECB's own commercial property datasets carry
  no Spain series. With this leg the component enters the headline
  for the first time.
- The fourth build's other proxies — the CET1 significant-
  institution ratio, the permits activity proxy, the constructed
  price-to-income ratio, the TEDR lending margin, and the
  hospitality employment share — are each labelled in the metadata
  and on the page, with the concept each stands in for and where
  it can mislead stated in its series definition and in the
  component sections above. That labelling is the admissibility
  standard: free and machine-readable, quarterly without filling
  or splicing, and honest about what it measures.

## What does not transfer, and why

- **Private credit** — omitted, deliberately, and not for lack of
  data. The U.S. component exists because U.S. private credit grew
  into a systemic channel (over a trillion dollars of largely
  unregulated lending intermediated through banks and insurers). In
  Spain the sector is an order of magnitude smaller relative to the
  financial system, bank-intermediated lending remains the marginal
  channel, and there is no comparable hidden-leverage loop to
  measure. Filling the slot with a tiny series would manufacture a
  component whose movements mean nothing. If the sector grows into
  a channel, the component should be added then, with this
  paragraph rewritten.
- **The GSE / FICO / piggyback mortgage block** — there are no GSEs
  and no FICO scores in Spain. Spanish mortgage risk runs through a
  different mechanism: most mortgages are variable-rate, resetting
  off 12-month Euribor, so household debt service reprices within a
  year of a policy tightening. The substitute gauge is the
  **variable-rate share of new housing loans**, sourced in the
  second build from the Banco de España TEDR new-business volumes
  by initial rate fixation (table 19.12) — the ECB MIR dataset
  publishes Spain's rates by fixation period but not the volumes —
  and the debt-service consequences are already visible in the
  BIS DSR. The share was 94% of new volume at the boom in 2006 and
  is under 10% at the panel's end: the repricing channel that
  defined Spanish mortgage risk in 2008 has largely closed for new
  lending, whatever the stock still holds.
- **Hedge fund leverage and SCOOS** — no comparable Spanish sector
  or survey exists at a scale that would make the series systemic.
- **SRISK and Granger-network measures** — research builds with no
  published Spanish series; the U.S. versions are computed from
  U.S. market and filing data that have no Spanish counterpart
  pipeline here.
- **The Gilchrist–Zakrajšek spread and excess bond premium** —
  Fed-built U.S. series, constructed from U.S. corporate bond
  microdata. No Spanish construction exists.

## Considered and not included

Two series were considered for the second build and excluded on
judgement, not for lack of a source:

- **Foreign ownership share of the IBEX 35** (published by BME /
  CNMV). The series exists, but its sign as a vulnerability is
  ambiguous: a high foreign share can be read as dependence on
  flight-prone holders, and with equal warrant as a confidence
  signal — foreign investors hold Spanish equities because they
  judge them sound. The percentile machinery needs an orientation
  the evidence does not supply, so the series is excluded rather
  than forced in with an arbitrary sign.
- **Average rent.** The INE rental price index is experimental
  and short; it cannot carry a percentile against a history that
  includes the calibration crisis. Housing access pressure is
  carried meanwhile by the price gauges and, when sourced, the
  price-to-income ratio.

Five further series were worked in the fourth build and excluded,
each for a stated route or frequency reason:

- **Contributors per pensioner** (Seguridad Social affiliation over
  pensions in payment). A defining Spanish fiscal idiosyncrasy,
  but neither leg has a stable machine-readable series route: both
  are disseminated as monthly workbooks with rotating file names
  behind the ministry's web portal, plus a query tool. The fifth
  build retried through the national open-data catalog
  (datos.gob.es): the catalog exposes the Tesorería's affiliation
  datasets, but their distributions are PXWeb saved queries
  published as bounded period chunks (2008–2025, with successor
  chunks under new identifiers), and the PXWeb server's REST API
  is not enabled — so the route's identity still rotates as time
  passes, the same disqualifying property as the workbook names.
  A ratio of two unroutable series cannot ship; it is recorded
  here as the first candidate if a series route appears.
- **Energy trade deficit to GDP.** Spain's energy-import
  dependence has no measurable route in the panel's sources: the
  Banco de España's balance-of-payments CSV tables carry no energy
  goods split, customs trade-by-chapter data has no stable series
  API, and Eurostat's energy trade datasets are volumes, not a
  value balance.
- **Outstanding-stock variable-rate share of mortgages.** The
  natural complement to the new-flow leg: the Banco de España's
  TEDR outstanding-amount tables (chapter 19) split the stock by
  original maturity, not by interest-rate fixation, so the stock
  share is not published in routable form. The new-flow leg stands
  alone.
- **Tourist dwellings (viviendas de uso turístico).** The INE's
  experimental statistic is disseminated as workbooks on its
  experimental hub, with a methodology change inside its short
  life — no series route, and splicing across the change is
  excluded by the panel's rules.
- **Housing supply balance** (completions or permits per new
  household). The household-formation denominator is annual only
  (INE household projections and the continuous household survey),
  so the ratio cannot be built quarterly without filling; the
  permits activity proxy carries the construction thread.

## Sources and routes

All sources are free and public. Every route below is exercised by
the build; the fetched series are archived under `data/spain/`, one
file per series, and a series that cannot be fetched ships as
source-pending in `docs/data/spain_meta.json` with the reason —
never silently substituted.

- **BIS Data Portal (SDMX)** — credit-to-GDP gap (`WS_CREDIT_GAP`,
  Q.ES.P.A.C), debt service ratio (`WS_DSR`, Q.ES.P), total credit
  to households and to non-financial corporations (`WS_TC`, percent
  of GDP unit); commercial property prices for Spain (`WS_CPP`,
  all property types, whole country) from the fourth build.
- **Banco de España, Boletín Estadístico table CSVs** — MFI balance
  sheet (tables 8.3, 8.4, 8.5, 8.9), loan purposes (8.19), Banco de
  España balance sheet (7.3), financial accounts (2.5, 2.7),
  national accounts GDP (2.1), EDP debt by subsector (11.5),
  balance of payments travel receipts (17.4) and TEDR new-business
  volumes by initial rate fixation (19.12). Fourth build: EDP debt
  by holder and residual maturity (11.13), regional EDP debt by
  instrument including the state financing funds (13.7),
  international investment position (17.21) and the TEDR
  new-business rate to non-financial corporations (19.3). Monthly
  tables enter at quarter-end; the publisher's `_` missing marker
  is honoured as missing.
- **INE** — house price index annual change (IPV948) and index
  level (IPV769) via the INE tempus JSON API.
- **ECB Data Portal (SDMX)** — CISS for Spain
  (`CISS.D.ES.Z0Z.4F.EC.SS_CIN.IDX`), daily, quarterly mean; Bank
  Lending Survey credit standards for enterprises, Spain
  (`BLS.Q.ES.ALL.O.E.Z.B3.ST.S.FNET`), quarterly; supervisory
  statistics CET1 ratio for Spanish significant institutions
  (`SUP.Q.ES...I4008...SII...PCT`), quarterly, from the fourth
  build.
- **Eurostat** — quarterly non-financial accounts for general
  government (`gov_10q_ggnfa`): interest expenditure (D.41) and
  total revenue (TR) for Spain, trailing four quarters. Fourth
  build: building permits index (`sts_cobp_m`, residential floor
  area), household gross disposable income (`nasq_10_nf_tr`, B6G),
  LFS employment by NACE (`lfsq_egan2`) and the temporary-
  employment share (`lfsq_etpga`).
- **FRED (fredgraph)** — OECD 10-year yields for Spain and Germany
  (IRLTLT01ESM156N, IRLTLT01DEM156N), the BIS residential
  property price index for Spain (QESN628BIS), and the OECD
  euro-area 3-month interbank rate (IR3TIB01EZM156N) from the
  fourth build.

## Cadence

Experimental: the section is rebuilt when the build is run
(`python3 -m src.spain.build_spain`). It is not yet on a scheduled
refresh; the page states its build date and the panel's last
quarter, and a stale page should be read as an experiment snapshot,
not as a live monitor.
