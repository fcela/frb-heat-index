"""Firm universes for the two systemic-risk indicators.

SRISK_PANEL: 20 large US bank holding companies (survivors with public
price histories). RSSD9001 values verified against the NY Fed's public
PERMCO-RSSD link table (newyorkfed.org, crsp_20240930.csv) and top-filer
asset sizes in the Y-9C bulk files. CIKs verified against SEC
company_tickers.json.

Delisted crisis-era BHCs (Wachovia, Washington Mutual, National City)
and broker-dealers (Lehman, Bear Stearns, Merrill Lynch) have no public
price history on Yahoo Finance or Nasdaq and are excluded; the
survivorship limitation is documented in the proxy catalog.

GRANGER_PANEL: 30 large listed US financial institutions across banks,
broker-dealers and insurers for the connectedness indicator. Billio et
al. (2012) use 100 firms (25 per sector incl. hedge funds); hedge funds
have no public return data and delisted firms have no public price
history, so the replication uses 30 listed firms. Documented in the
proxy catalog.
"""

# ticker -> (rssd9001, cik) for the SRISK BHC panel.
# RSSDs verified via NY Fed PERMCO-RSSD link table; CIKs via SEC.
SRISK_FIRMS = {
    "JPM":  (1039502, 19617),    # JPMorgan Chase & Co
    "BAC":  (1073757, 70858),    # Bank of America Corp
    "C":    (1951350, 831001),   # Citigroup Inc
    "WFC":  (1120754, 72971),    # Wells Fargo & Co
    "GS":   (2380443, 886982),   # Goldman Sachs Group Inc
    "MS":   (2162966, 895421),   # Morgan Stanley
    "SCHW": (1026632, 316709),   # Charles Schwab Corp
    "USB":  (1119794, 36104),    # U.S. Bancorp
    "TFC":  (1074156, 92230),    # Truist Financial Corp
    "PNC":  (1069778, 713676),   # PNC Financial Services Group Inc
    "COF":  (2277860, 927628),   # Capital One Financial Corp
    "BNY":  (3587146, 1390777),  # Bank of New York Mellon Corp (ticker BK pre-2024)
    "STT":  (1111435, 93751),    # State Street Corp
    "AXP":  (1275216, 4962),     # American Express Co
    "FITB": (1070345, 35527),    # Fifth Third Bancorp
    "RF":   (3242838, 1281761),  # Regions Financial Corp
    "KEY":  (1068025, 91576),    # KeyCorp
    "HBAN": (1068191, 49196),    # Huntington Bancshares Inc
    "ALLY": (1562859, 40729),    # Ally Financial Inc
    "CFG":  (1132449, 759944),   # Citizens Financial Group Inc
}

# 30 tickers for the Granger-causality connectedness panel.
GRANGER_TICKERS = [
    # banks (14)
    "JPM", "BAC", "C", "WFC", "USB", "PNC", "TFC", "COF",
    "BNY", "STT", "FITB", "RF", "KEY", "HBAN",
    # broker-dealers (6)
    "GS", "MS", "SCHW", "RJF", "AMTD", "IBKR",
    # insurers (10)
    "BRK-B", "AIG", "MET", "PRU", "AFL", "ALL", "TRV", "CB", "PFG", "LNC",
]
