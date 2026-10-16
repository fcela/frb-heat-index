"""Per-firm segment reorganization / break registry.

Source: issue #23, verified from firms' own disclosure. Types:
  - "reorg": segment composition changed -> BREAK, never splice across it.
  - "rename": label only -> not a break, safe to chain.

regime_for(firm, year) returns the regime label active for that fiscal year.
"""

REORGS = [
    # JPMorgan
    {"firm": "JPMorgan", "effective": "2012-01-01", "type": "reorg",
     "desc": "Corporate & Investment Bank formed"},
    {"firm": "JPMorgan", "effective": "2016-01-01", "type": "rename",
     "desc": "Asset Management renamed Asset & Wealth Management (label only)"},
    {"firm": "JPMorgan", "effective": "2024-04-01", "type": "reorg",
     "desc": "CIB + Commercial Banking -> Commercial & Investment Bank"},
    # Bank of America
    {"firm": "Bank of America", "effective": "2012-01-01", "type": "reorg",
     "desc": "Segment realignment Jan 2012"},
    {"firm": "Bank of America", "effective": "2015-01-01", "type": "reorg",
     "desc": "Legacy Assets & Servicing carved out"},
    {"firm": "Bank of America", "effective": "2016-04-01", "type": "reorg",
     "desc": "LAS eliminated"},
    # Citigroup
    {"firm": "Citigroup", "effective": "2009-01-01", "type": "reorg",
     "desc": "Citicorp / Citi Holdings split"},
    {"firm": "Citigroup", "effective": "2017-01-01", "type": "reorg",
     "desc": "Citi Holdings folded into Citicorp"},
    {"firm": "Citigroup", "effective": "2022-01-01", "type": "reorg",
     "desc": "Segment realignment Q1 2022"},
    {"firm": "Citigroup", "effective": "2023-10-01", "type": "reorg",
     "desc": "Five-segment structure (Services/Markets/Banking/Wealth/USPB)"},
    # Goldman Sachs
    {"firm": "Goldman Sachs", "effective": "2020-01-01", "type": "reorg",
     "desc": "Segment realignment Q1 2020"},
    {"firm": "Goldman Sachs", "effective": "2022-10-01", "type": "reorg",
     "desc": "GBM / AWM / Platform Solutions"},
    {"firm": "Goldman Sachs", "effective": "2025-10-01", "type": "reorg",
     "desc": "Transaction Banking moved into GBM (compositional)"},
    # Morgan Stanley
    {"firm": "Morgan Stanley", "effective": "2013-04-01", "type": "rename",
     "desc": "Q2 2013 rename only; composition unchanged"},
    # Wells Fargo
    {"firm": "Wells Fargo", "effective": "2020-10-01", "type": "reorg",
     "desc": "Four-segment structure Q4 2020"},
    # Evercore
    {"firm": "Evercore", "effective": "2022-10-01", "type": "rename",
     "desc": "Q4 2022 rename only"},
    # Houlihan Lokey
    {"firm": "Houlihan Lokey", "effective": "2020-04-01", "type": "rename",
     "desc": "FY2020 rename only"},
    # Jefferies (fiscal year ends Nov 30; Q1 FY2020 ~ Dec 2019)
    {"firm": "Jefferies", "effective": "2019-12-01", "type": "reorg",
     "desc": "IB&CM split from Asset Management; merchant banking wound down"},
    # Lazard, Moelis, PJT Partners: no reorgs in scope.
]

# Firms expected to have zero registry entries (asserted in tests).
NO_REORG_FIRMS = ["Lazard", "Moelis", "PJT Partners"]


def _breaks_for(firm):
    return sorted(
        (r for r in REORGS if r["firm"] == firm and r["type"] == "reorg"),
        key=lambda r: r["effective"],
    )


def regime_for(firm, year):
    """Regime label for a fiscal year, e.g. 'r0', 'r1', ... Breaks increment."""
    breaks = _breaks_for(firm)
    idx = sum(1 for b in breaks if b["effective"] <= f"{int(year)}-12-31")
    return f"r{idx}"


def is_break(firm, year_a, year_b):
    """True if a reorg break falls between two fiscal years."""
    return regime_for(firm, year_a) != regime_for(firm, year_b)
