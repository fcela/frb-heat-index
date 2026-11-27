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
below.

One consequence is worth stating plainly, because it decides how
the headline should be read. The headline is a **vulnerability**
gauge, and vulnerability crests at the boom top: the headline peaks
in 2007, when the credit gap, the funding gap, bank leverage and
household dissaving were jointly at their extremes — the same
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
  Y-9C equity ratios. The CET1 ratio belongs beside it and is
  source-pending: supervisory statistics are published as report
  tables, and no stable machine-readable series route was resolved
  for this build.
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
  Z.1 counterpart). The top-5 asset share is source-pending (report
  tables, no series route). The component also carries **tourism
  receipts to GDP** (BdE balance of payments, table 17.4): a
  structural single-sector exposure, not a cyclical heat gauge.
  Tourism is the largest single export sector of the Spanish
  economy — about 12% of GDP on the INE tourism satellite account
  including indirect effects — and the balance-of-payments receipts
  series is the quarterly measurable part of that exposure. It is
  ranked here as a concentration measure of the economy's external
  earnings base; its percentile reads high whenever receipts are
  large relative to Spain's own past, including the record shares
  of 2023–25. No counterpart exists in the U.S. framework.
- **Home mortgages** — housing loans to households over GDP (BdE
  table 8.19) and total household credit to GDP (BIS). See below for
  the part of the U.S. mortgage block that does *not* transfer.
- **Consumer credit** — MFI consumer credit to households over GDP
  (BdE table 8.19).
- **Nonfinancial business debt** — total credit to non-financial
  corporations over GDP (BIS).
- **Net saving** — households' net financial saving over GDP from
  the financial accounts (BdE table 2.7), labelled a **proxy**: it
  is the financial-accounts counterpart of the U.S. NIPA net saving
  rate, not the national-accounts saving rate itself.
- **Residential real estate** — house price growth from the BIS
  residential property price index (via FRED) and from the INE
  house price index (IPV, from 2007). Price-to-income (BdE housing
  indicators) is source-pending: published in a workbook, no stable
  series route. **Credit to the construction sector** is also
  source-pending: the Banco de España compiles lending by economic
  activity from supervisory returns and publishes it in Financial
  Stability Report tables, but the Boletín Estadístico CSV service
  carries no activity-breakdown series, and no stable
  machine-readable route was resolved. It ships pending rather
  than substituting a household-purpose series for an
  activity-based business one.
- **Business credit** — the ECB Bank Lending Survey's Spain
  breakdown is the SLOOS counterpart: net percentage of banks
  tightening credit standards on loans to enterprises, published
  from 2003Q1 (ECB BLS dataflow; the Spain key resolved in the
  second build, and the component now enters the headline). The
  price counterpart — a TEDR new-lending spread for non-financial
  corporations — stays source-pending: the TEDR rate is published
  (Boletín Estadístico table 19.3), but a spread needs a
  matched-maturity market benchmark, and no free machine-readable
  euro swap benchmark route was resolved.
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
used. The component has five legs — a price, a quantity, and the
sovereign's own stock and burden:

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

Because the legs mix a price with stocks, the component's own peak
is a stock peak, not the 2012 spread event: it crests in 2016 at
0.79, with the 2012–13 window reading 0.60–0.67 — elevated, with
the spread leg at its maximum and the stocks still mid-range
against a history that runs to 2021.

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
  yield, and no stable free series route for one was resolved.
- **Corporate spreads** — source-pending, and when sourced it will
  be a **labelled euro-area proxy**: Spain's corporate bond market
  is too thin for a clean domestic OAS, and Spanish issuers fund at
  euro-area spreads plus a sovereign-linked premium the nexus
  component already measures.
- **Commercial real estate prices** — source-pending: the BIS
  commercial property price statistics are distributed as portal
  downloads rather than a stable SDMX series for Spain.

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

## Sources and routes

All sources are free and public. Every route below is exercised by
the build; the fetched series are archived under `data/spain/`, one
file per series, and a series that cannot be fetched ships as
source-pending in `docs/data/spain_meta.json` with the reason —
never silently substituted.

- **BIS Data Portal (SDMX)** — credit-to-GDP gap (`WS_CREDIT_GAP`,
  Q.ES.P.A.C), debt service ratio (`WS_DSR`, Q.ES.P), total credit
  to households and to non-financial corporations (`WS_TC`, percent
  of GDP unit).
- **Banco de España, Boletín Estadístico table CSVs** — MFI balance
  sheet (tables 8.3, 8.4, 8.5, 8.9), loan purposes (8.19), Banco de
  España balance sheet (7.3), financial accounts (2.5, 2.7),
  national accounts GDP (2.1), EDP debt by subsector (11.5),
  balance of payments travel receipts (17.4) and TEDR new-business
  volumes by initial rate fixation (19.12). Monthly tables enter
  at quarter-end; the publisher's `_` missing marker is honoured
  as missing.
- **INE** — house price index annual change (IPV948) via the INE
  tempus JSON API.
- **ECB Data Portal (SDMX)** — CISS for Spain
  (`CISS.D.ES.Z0Z.4F.EC.SS_CIN.IDX`), daily, quarterly mean; Bank
  Lending Survey credit standards for enterprises, Spain
  (`BLS.Q.ES.ALL.O.E.Z.B3.ST.S.FNET`), quarterly.
- **Eurostat** — quarterly non-financial accounts for general
  government (`gov_10q_ggnfa`): interest expenditure (D.41) and
  total revenue (TR) for Spain, trailing four quarters.
- **FRED (fredgraph)** — OECD 10-year yields for Spain and Germany
  (IRLTLT01ESM156N, IRLTLT01DEM156N) and the BIS residential
  property price index for Spain (QESN628BIS).

## Cadence

Experimental: the section is rebuilt when the build is run
(`python3 -m src.spain.build_spain`). It is not yet on a scheduled
refresh; the page states its build date and the panel's last
quarter, and a stale page should be read as an experiment snapshot,
not as a live monitor.
