"""Market-pulse indicator definitions.

Each indicator maps to one row of the leading-indicators research report.
status: "live" (fetchable from a verified public source) or "pending-sourcing"
(no confirmed public series; documented honestly, never silently dropped).
"""

SEGMENTS = {
    "ib": {
        "title": "Investment Banking",
        "subtitle": "M&A / ECM / DCM deal flow",
        "indicators": {
            "ma_announced": {
                "title": "Announced M&A deal value",
                "why": "Deals are announced months before close, locking in advisory and financing fees well ahead of reported revenue.",
                "source": "LSEG Deals Intelligence monthly M&A review",
                "frequency": "Monthly",
                "status": "pending-sourcing",
                "note": "No confirmed public time series; LSEG publishes press-release summaries only.",
            },
            "ipo_pipeline": {
                "title": "IPO pipeline — S-1 filing counts",
                "why": "Registration filings precede pricing by ~1-3 months, signaling ECM fee flow before it books.",
                "source": "SEC EDGAR full-text search",
                "frequency": "Weekly",
                "status": "pending-sourcing",
                "note": "EDGAR API is public but a filing-count pipeline is not yet built.",
            },
            "hy_issuance": {
                "title": "High-yield bond issuance",
                "why": "HY windows open and shut quickly; issuance surges/shutdowns lead DCM fees and flag shifts in credit risk appetite.",
                "source": "SIFMA Research",
                "frequency": "Monthly",
                "status": "pending-sourcing",
                "note": "SIFMA publishes free research; a clean public time series is unconfirmed.",
            },
            "ib_fee_wallet": {
                "title": "IB fee league-table trackers",
                "why": "Fee estimates from announced/completed deals publish weeks before banks' quarterly filings.",
                "source": "LSEG Deals Intelligence quarterly IB Review",
                "frequency": "Quarterly",
                "status": "pending-sourcing",
                "note": "Press-release summaries only; no public series.",
            },
        },
    },
    "ficc": {
        "title": "FICC",
        "subtitle": "Rates, credit, mortgages, commodities",
        "indicators": {
            "move_index": {
                "title": "MOVE Index (rates implied vol)",
                "why": "Implied Treasury vol reprices ahead of policy surprises and duration-driven deleveraging.",
                "source": "ICE BofA (licensed)",
                "frequency": "Daily",
                "status": "pending-sourcing",
                "note": "Licensed index; no free public daily series confirmed.",
            },
            "hy_oas": {
                "title": "US High Yield OAS",
                "why": "HY spreads price default/liquidity risk months before realized defaults.",
                "source": "FRED: BAMLH0A0HYM2 (ICE BofA)",
                "frequency": "Daily",
                "status": "live",
                "fred_id": "BAMLH0A0HYM2",
                "unit": "percentage points",
            },
            "ig_oas": {
                "title": "US Investment Grade OAS",
                "why": "IG spread companion to HY; investment-grade stress gauge.",
                "source": "FRED: BAMLC0A0CM (ICE BofA)",
                "frequency": "Daily",
                "status": "live",
                "fred_id": "BAMLC0A0CM",
                "unit": "percentage points",
            },
            "mbs_spread": {
                "title": "Agency MBS spread (30Y mortgage − 30Y Treasury)",
                "why": "MBS widening signals dealer balance-sheet constraints and convexity-hedging pressure before broad funding stress.",
                "source": "Computed: FRED OBMMIC30YF minus DGS30",
                "frequency": "Weekly",
                "status": "live",
                "computed_from": ["OBMMIC30YF", "DGS30"],
                "unit": "percentage points",
            },
            "wti_spot": {
                "title": "WTI crude spot",
                "why": "Real-economy demand signal orthogonal to the heat map's price/stock quarterly metrics.",
                "source": "FRED: DCOILWTICO",
                "frequency": "Daily",
                "status": "live",
                "fred_id": "DCOILWTICO",
                "unit": "USD/barrel",
                "note": "Calendar spread (prompt − 12M futures) needs a futures curve; spot is the verified public component.",
            },
        },
    },
    "equities": {
        "title": "Equities",
        "subtitle": "Positioning, valuation, supply, factor rotation",
        "indicators": {
            "put_call": {
                "title": "CBOE equity put/call ratio",
                "why": "Options-positioning extremes flag crowded sentiment 1-several weeks before price turns.",
                "source": "CBOE daily market statistics",
                "frequency": "Daily",
                "status": "pending-sourcing",
                "note": "Free historical CSV frozen at 2019-10-04; current data requires scraping the daily page.",
            },
            "cape_zscore": {
                "title": "Cyclically adjusted P/E vs 10-yr mean",
                "why": "Starting valuation is the most robust predictor of medium-horizon returns.",
                "source": "Yale / Shiller online dataset",
                "frequency": "Monthly",
                "status": "pending-sourcing",
                "note": "Yale file unreachable/stale as of 2026-10; no confirmed current public series.",
            },
            "equity_issuance": {
                "title": "US IPO + follow-on issuance volume",
                "why": "Treasurers time equity supply to peak demand; issuance surges cluster near cycle tops.",
                "source": "SIFMA quarterly Capital Markets Charts",
                "frequency": "Quarterly",
                "status": "pending-sourcing",
                "note": "Free charts; a clean public time series is unconfirmed.",
            },
            "factor_rotation": {
                "title": "High-beta minus low-vol 60-day relative strength",
                "why": "Rotations into high-beta leadership precede broad index turns as late-cycle risk appetite peaks.",
                "source": "Self-computed from ETF closes",
                "frequency": "Daily",
                "status": "pending-sourcing",
                "note": "Needs a reliable free ETF price feed; tested sources are bot-walled.",
            },
        },
    },
    "awm": {
        "title": "Asset & Wealth Management",
        "subtitle": "Flows, cash parking, market beta, private markets",
        "indicators": {
            "fund_flows": {
                "title": "Net flows to long-term mutual funds & ETFs",
                "why": "Redemption/allocation shifts move AUM and fee bases weeks before reported revenue.",
                "source": "ICI weekly estimated flows",
                "frequency": "Weekly",
                "status": "pending-sourcing",
                "note": "ICI publishes weekly Excel workbooks; direct download URL unconfirmed (page is bot-walled).",
            },
            "mmf_assets": {
                "title": "Money-market fund total assets",
                "why": "Sharp MMF inflows signal flight-to-cash and liquidity hoarding, presaging risk-asset outflows.",
                "source": "FRED: MMMFFAQ027S (ICI)",
                "frequency": "Weekly",
                "status": "live",
                "fred_id": "MMMFFAQ027S",
                "unit": "USD millions",
            },
            "sp500": {
                "title": "S&P 500 level",
                "why": "Market beta drives most AUM change; drawdowns lead AUM erosion and fee declines.",
                "source": "FRED: SP500",
                "frequency": "Daily",
                "status": "live",
                "fred_id": "SP500",
                "unit": "index",
            },
        },
    },
    "firmwide": {
        "title": "Firm-wide",
        "subtitle": "Funding, liquidity, leverage, counterparty",
        "indicators": {
            "cp_ois": {
                "title": "3M CP–OIS spread",
                "why": "CP is the marginal unsecured funding market; spreads reprice within days as risk appetite erodes.",
                "source": "Constructed: Fed commercial paper rates − 3M OIS",
                "frequency": "Daily",
                "status": "pending-sourcing",
                "note": "No confirmed public 3M OIS series; spread cannot be built from verified inputs.",
            },
            "fra_ois": {
                "title": "3M×3M FRA–OIS spread",
                "why": "Prices forward unsecured term-funding risk plus embedded bank counterparty credit.",
                "source": "Bloomberg/Refinitiv (subscription)",
                "frequency": "Daily",
                "status": "pending-sourcing",
                "note": "No free public feed confirmed.",
            },
            "margin_debt": {
                "title": "FINRA margin debt (debit balances)",
                "why": "Client leverage builds/unwinds ahead of forced deleveraging; peaks precede volatility cascades.",
                "source": "FINRA Margin Statistics (Rule 4521)",
                "frequency": "Monthly",
                "status": "live",
                "unit": "USD millions",
            },
            "margin_debt": {
                "title": "FINRA margin debt (debit balances)",
                "why": "Client leverage builds/unwinds ahead of forced deleveraging; peaks precede volatility cascades.",
                "source": "FINRA Margin Statistics (Rule 4521)",
                "frequency": "Monthly",
                "status": "live",
                "unit": "USD millions",
            },
            "dealer_cds": {
                "title": "Dealer 5Y senior CDS (average)",
                "why": "Market-implied default intensity of counterparties widens before CVA losses or rating actions.",
                "source": "Markit/S&P (subscription)",
                "frequency": "Daily",
                "status": "pending-sourcing",
                "note": "No free public feed confirmed.",
            },
        },
    },
    "plumbing": {
        "title": "Funding Plumbing",
        "subtitle": "Overnight funding, settlement, and wholesale liquidity",
        "indicators": {
            "sofr_iorb": {
                "title": "SOFR\u2013IORB spread",
                "why": "The spread of overnight secured funding to the Fed's administered rate is the corridor's thermometer: when SOFR sits below IORB, reserves are abundant and cash is seeking alternatives.",
                "source": "FRED: SOFR \u2212 IORB",
                "frequency": "Daily",
                "status": "live",
                "fred_computed": ["SOFR", "IORB"],
                "unit": "percentage points",
                "note": "IORB starts 2021-07-29, which limits the backfill.",
            },
            "on_rrp": {
                "title": "ON RRP take-up",
                "why": "Cash parked in the Fed's overnight reverse repo facility is cash not funding markets; a collapse toward zero means cash is no longer seeking the Fed's balance sheet, i.e. reserves are no longer abundant.",
                "source": "FRED: RRPONTTLD",
                "frequency": "Daily",
                "status": "live",
                "fred_id": "RRPONTTLD",
                "unit": "USD billions",
            },
            "dealer_fails": {
                "title": "Primary dealer fails-to-deliver",
                "why": "Fails-to-deliver spike when Treasury settlement chains seize \u2014 the observable footprint of settlement stress in the world's deepest market.",
                "source": "NY Fed primary-dealer statistics (11 Treasury fails-to-deliver series, summed)",
                "frequency": "Weekly",
                "status": "live",
                "unit": "USD millions",
                "note": "Sum of 11 Treasury fails-to-deliver codes (-TD); values of \"*\" are suppressed below the confidentiality threshold and treated as 0.",
            },
            "fhlb": {
                "title": "FHLB advances",
                "why": "Federal Home Loan Bank advances are banks' wholesale funding tap of first resort when deposits flee \u2014 a surge flags liquidity stress before it reaches the discount window.",
                "source": "FHLB Office of Finance Combined Financial Reports (CFR)",
                "frequency": "Quarterly",
                "status": "live",
                "unit": "USD billions",
                "note": "Parsed from CFR PDFs (MD&A advances figure). Older CFRs live under different URL paths and are not chased, so the series covers the quarters the current URL pattern reaches (2025Q2 onward as of 2026-10); unpublished quarters are skipped.",
            },
            "treasury_basis": {
                "title": "Treasury cash\u2013futures basis (proxy)",
                "why": "A wider (more positive) basis means cash Treasuries cheapen relative to futures \u2014 the classic footprint of dealer balance-sheet constraints.",
                "source": "Computed: FRED DGS10 minus Yahoo ZN=F implied yield",
                "frequency": "Daily",
                "status": "live-proxy",
                "unit": "basis points",
                "note": "Proxy: futures-implied yield of a hypothetical 6%-coupon 10-year note at the quoted futures price. Abstracts from the cheapest-to-deliver option, delivery timing, and repo financing effects.",
            },
            "xccy_basis": {
                "title": "EUR/USD cross-currency basis (CIP construction)",
                "why": "A negative basis means synthetic dollar funding via EUR costs more than direct dollars \u2014 the offshore dollar-shortage thermometer.",
                "source": "Constructed: CME Euro FX futures + FRED DEXUSEU, DGS3MO, IR3TIB01EZM156N (Du\u2013Tepper\u2013Verdelhan)",
                "frequency": "Monthly",
                "status": "live-experimental",
                "unit": "basis points",
                "note": "Experimental DIY construction; EUR leg only; series starts 2025-04 (Yahoo delists expired futures contracts). The FRED Euribor input is stale and the T-bill leg is not banks' marginal funding rate \u2014 watch changes, not levels.",
            },
        },
    },
    "positioning": {
        "title": "Positioning & Microstructure",
        "subtitle": "Who is crowded, and where prices detach from fundamentals",
        "indicators": {
            "cftc_cot": {
                "title": "Speculative rate-futures z-scores",
                "why": "Leveraged-fund net positioning at extremes has preceded sharp rate repricings \u2014 crowded trades unwind violently.",
                "source": "CFTC Traders in Financial Futures (TFF)",
                "frequency": "Weekly",
                "status": "live",
                "unit": "z-score",
                "note": "Four contracts (UST 2Y/5Y/10Y notes, 3M SOFR): leveraged-fund net (long \u2212 short) z-scored against its trailing 104-week window, current week excluded.",
            },
            "etf_dislocation": {
                "title": "Corporate bond ETF dislocation (proxy)",
                "why": "An ETF trading far below its own recent average price is the observable footprint of a price/NAV dislocation (e.g. March 2020, when LQD/HYG traded at steep discounts and mean-reverted).",
                "source": "Yahoo Finance: LQD, HYG vs trailing 20-day mean",
                "frequency": "Daily",
                "status": "live-proxy",
                "unit": "percent",
                "note": "Proxy: (close \u2212 20d MA) / 20d MA. The true (market price \u2212 NAV)/NAV leg is not published in machine-readable form \u2014 documented as a proxy, not the accounting discount.",
            },
            "cds_bond_basis": {
                "title": "Excess Bond Premium (CDS\u2013bond basis proxy)",
                "why": "The excess bond premium isolates the non-default (liquidity/funding) component of corporate spreads \u2014 exactly what the CDS\u2013bond basis measures (Bai & Collin-Dufresne, 2019).",
                "source": "Federal Reserve FEDS Notes EBP series (Gilchrist\u2013Zakraj\u0161ek methodology)",
                "frequency": "Monthly",
                "status": "live-proxy",
                "unit": "percentage points",
                "note": "Direction matches dealer-quoted basis (spikes when funding stress widens it); levels are NOT comparable to dealer CDS quotes \u2014 compare deviations/spikes, not absolute levels.",
            },
            "insider_selling": {
                "title": "Bank insider net selling",
                "why": "Insiders sell ahead of bad news more often than they buy ahead of good \u2014 persistent net open-market selling by bank officers is a sentiment gauge for the sector.",
                "source": "OpenInsider (SEC Form 4 filings): JPM, BAC, C, WFC, GS, MS, USB, PNC",
                "frequency": "Monthly",
                "status": "live",
                "unit": "USD millions",
                "note": "Each ticker page lists only recent transactions, so the trailing ~12 months are the usable window. Amendments/duplicates are not de-duplicated \u2014 treat the level as approximate, the month-to-month changes as the signal.",
            },
        },
    },
    "filings": {
        "title": "Filings & Behavior",
        "subtitle": "What banks report and what they say",
        "indicators": {
            "htm_unrealized": {
                "title": "Bank HTM unrealized losses",
                "why": "Unrealized losses on held-to-maturity securities are the unrealized-capital hole that forced sales crystallize \u2014 the SVB lesson in aggregate.",
                "source": "FDIC Quarterly Banking Profile (QBP), aggregate workbook",
                "frequency": "Quarterly",
                "status": "live",
                "unit": "USD billions",
                "note": "Balance-sheet row \"Held to maturity on non-equity securities unrealized gain/loss\"; reported as gain/loss in $ millions (losses negative), sign-flipped so positive values are losses.",
            },
            "n_mfp": {
                "title": "MMF repo counterparty concentration",
                "why": "Repo is the marginal funding market; when a handful of dealers dominate money-fund repo, a single counterparty wobble propagates.",
                "source": "SEC DERA Form N-MFP monthly data sets",
                "frequency": "Monthly",
                "status": "live",
                "unit": "percent",
                "note": "Top-3 dealer share \u2248 27.13% under the verified methodology (sponsored-repo rows attributed to the named sponsor dealer; residual unmapped volume excluded from the dealer ranking) \u2014 supersedes the earlier 14.6% print, which used a different attribution.",
            },
            "earnings_calls": {
                "title": "Bank earnings-call credit language",
                "why": "Management language on credit quality, reserves, and provisions shifts before the charge-offs show up in the numbers.",
                "source": "Motley Fool earnings-call transcripts (JPM, BAC, WFC)",
                "frequency": "Quarterly",
                "status": "live-experimental",
                "unit": "score per 10k words",
                "note": "v1 methodology proof, not a production time series: verified on Q3 2026 calls (JPM 7.6 / BAC 11.6 / WFC 8.9 per 10k words); the framework caught JPMorgan's actual $539m reserve build.",
            },
        },
    },
    "crossmarket": {
        "title": "Cross-Market",
        "subtitle": "Commodity term structure as a real-economy gauge",
        "indicators": {
            "wti_calendar_spread": {
                "title": "WTI calendar spread (prompt\u221212M)",
                "why": "Backwardation means a tight physical market and low inventories; contango means glut. The slope of the curve is the physical market's stress gauge.",
                "source": "Yahoo Finance: CL=F front month minus the contract expiring ~12 months later",
                "frequency": "Daily",
                "status": "live",
                "unit": "USD/barrel",
                "note": "Trailing ~12 months of history only \u2014 Yahoo delists expired contracts, so the 12M leg exists only where the contract is still listed.",
            },
        },
    },
}


def all_indicators():
    for seg_key, seg in SEGMENTS.items():
        for ind_key, ind in seg["indicators"].items():
            yield seg_key, ind_key, ind


FETCHABLE_STATUSES = ("live", "live-proxy", "live-experimental")


def live_indicators():
    return [(s, k, i) for s, k, i in all_indicators() if i["status"] in FETCHABLE_STATUSES]
