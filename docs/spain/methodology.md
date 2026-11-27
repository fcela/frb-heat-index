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

One consequence is worth stating plainly, because it decides how
the headline should be read. The headline is a **vulnerability**
gauge, and vulnerability crests at the boom top: the headline peaks
in 2007, when the credit gap, the funding gap, bank leverage and
household dissaving were jointly at their extremes — the same
pre-crisis peak shape as the U.S. index. The 2010–13 window is when
that vulnerability *realised*, and it is the crisis-channel
components that peak there: the sovereign–bank nexus, short-term
wholesale funding (on its Eurosystem leg) and price volatility. A
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
  tables, no series route).
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
  series route.
- **Business credit** — the ECB Bank Lending Survey's Spain
  breakdown is the SLOOS counterpart, and BdE TEDR new-lending rates
  are the price counterpart. Both are source-pending in this build:
  the BLS Spain key in the ECB Data Portal and the TEDR new-business
  codes were not both resolved. The component ships empty rather
  than substituting a euro-area aggregate for a Spanish concept.
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
used. The component has two legs, a price and a quantity:

- **10-year Bono–Bund spread** (OECD long-term rates via FRED,
  quarterly mean of monthly). Spain prices against the Bund, not
  against itself; the spread is the market's price of Spanish
  sovereign risk over the euro area's safe asset. It peaked at
  roughly 600 basis points in mid-2012.
- **Domestic MFIs' holdings of Spanish general government debt
  securities** over the total MFI balance sheet (BdE tables 8.5 and
  8.3): how much of the banking system's assets are the sovereign's
  paper. The securities book is used, not loans to government,
  because it is the part that marks to market.

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
  **variable-rate share of new housing loans** (source-pending: the
  ECB MIR fixation breakdown), and the debt-service consequences
  are already visible in the BIS DSR.
- **Hedge fund leverage and SCOOS** — no comparable Spanish sector
  or survey exists at a scale that would make the series systemic.
- **SRISK and Granger-network measures** — research builds with no
  published Spanish series; the U.S. versions are computed from
  U.S. market and filing data that have no Spanish counterpart
  pipeline here.
- **The Gilchrist–Zakrajšek spread and excess bond premium** —
  Fed-built U.S. series, constructed from U.S. corporate bond
  microdata. No Spanish construction exists.

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
  España balance sheet (7.3), financial accounts (2.5, 2.7) and
  national accounts GDP (2.1). Monthly tables enter at quarter-end;
  the publisher's `_` missing marker is honoured as missing.
- **INE** — house price index annual change (IPV948) via the INE
  tempus JSON API.
- **ECB Data Portal (SDMX)** — CISS for Spain
  (`CISS.D.ES.Z0Z.4F.EC.SS_CIN.IDX`), daily, quarterly mean.
- **FRED (fredgraph)** — OECD 10-year yields for Spain and Germany
  (IRLTLT01ESM156N, IRLTLT01DEM156N) and the BIS residential
  property price index for Spain (QESN628BIS).

## Cadence

Experimental: the section is rebuilt when the build is run
(`python3 -m src.spain.build_spain`). It is not yet on a scheduled
refresh; the page states its build date and the panel's last
quarter, and a stale page should be read as an experiment snapshot,
not as a live monitor.
