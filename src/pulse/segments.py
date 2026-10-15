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
}


def all_indicators():
    for seg_key, seg in SEGMENTS.items():
        for ind_key, ind in seg["indicators"].items():
            yield seg_key, ind_key, ind


def live_indicators():
    return [(s, k, i) for s, k, i in all_indicators() if i["status"] == "live"]
