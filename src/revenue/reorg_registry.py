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

# Firms whose fiscal year does not end December 31 (verified in each
# firm's 10-K DocumentFiscalYearEnd; all other covered firms are 12-31).
# A fiscal year is labeled by the calendar year of its END date, so
# Houlihan Lokey FY2024 = Apr 2023-Mar 2024 and Jefferies FY2020 =
# Dec 2019-Nov 2020. Regime boundaries must compare a reorg's effective
# date against that fiscal year end, never against Dec 31: the Jefferies
# reorg effective 2019-12-01 lands in fiscal 2020, so fiscal 2019 (which
# ended 2019-11-30) belongs to the old regime. Jefferies moved to a
# Nov 30 year end in 2019; that fiscal-calendar choice is documented
# here and the data is not restated onto a December calendar.
FISCAL_YEAR_ENDS = {
    "Houlihan Lokey": "-03-31",
    "Jefferies": "-11-30",
}

# Fiscal quarter-end month-days for firms in FISCAL_YEAR_ENDS whose
# quarters are not calendar quarters (Jefferies: Q1 ends late February,
# ..., Q4 ends November 30). Firms not listed here use calendar
# quarter ends (Houlihan Lokey's quarters end Dec/Mar/Jun/Sep, which
# are calendar quarter ends).
FISCAL_QUARTER_ENDS = {
    "Jefferies": {1: "02-28", 2: "05-31", 3: "08-31", 4: "11-30"},
}


def fiscal_year_end(firm, year):
    """ISO date of the firm's fiscal-year end for the labeled year."""
    suffix = FISCAL_YEAR_ENDS.get(firm, "-12-31")
    return f"{int(year)}{suffix}"


def fiscal_quarter_end(firm, year, quarter):
    """ISO date of the firm's quarter end for the labeled quarter."""
    month_end = FISCAL_QUARTER_ENDS.get(firm, {}).get(int(quarter))
    if month_end is None:
        month_end = {1: "03-31", 2: "06-30", 3: "09-30",
                     4: "12-31"}[int(quarter)]
    return f"{int(year)}-{month_end}"


# Firms expected to have zero registry entries (asserted in tests).
NO_REORG_FIRMS = ["Lazard", "Moelis", "PJT Partners"]

# Label-only renames: (firm, old_substring, new_substring, effective_date).
# A rename is not a break; the two labels denote the same economic segment
# and must be canonicalized before series are keyed, or parallel series
# collide on the taxonomy line.
RENAMES = [
    ("JPMorgan", "Asset Management", "Asset & Wealth Management", "2016-01-01"),
]


def canonical_segment(firm, segment_native):
    """Map a pre-rename segment label to its post-rename label.

    Label-only renames denote the same economic segment; canonicalizing
    lets quarterly and annual builders key series consistently across the
    rename date.
    """
    seg = segment_native or ""
    for f, old, new, _ in RENAMES:
        if f == firm and old.lower() in seg.lower():
            # Replace the old label with the new one, preserving the rest.
            idx = seg.lower().find(old.lower())
            seg = seg[:idx] + new + seg[idx + len(old):]
            break
    return seg


def _breaks_for(firm):
    return sorted(
        (r for r in REORGS if r["firm"] == firm and r["type"] == "reorg"),
        key=lambda r: r["effective"],
    )


def regime_for(firm, year):
    """Regime label for a fiscal year, e.g. 'r0', 'r1', ... Breaks increment.

    A reorg takes effect for the first fiscal year whose END date falls on
    or after the effective date (fiscal_year_end, not Dec 31): the
    Jefferies reorg of 2019-12-01 lands in fiscal 2020, so fiscal 2019
    (ended 2019-11-30) stays in the old regime.
    """
    breaks = _breaks_for(firm)
    idx = sum(1 for b in breaks
              if b["effective"] <= fiscal_year_end(firm, year))
    return f"r{idx}"


def regime_for_quarter(firm, year, quarter):
    """Regime label for a fiscal quarter.

    Reorgs land in specific quarters (e.g. JPM 2024-04-01 -> Q2 2024 is
    the first quarter of the new regime); values are never spliced
    across the break. The quarter's end date decides the regime; for
    firms in FISCAL_QUARTER_ENDS that is the fiscal quarter end.
    """
    q_end = fiscal_quarter_end(firm, year, quarter)
    breaks = _breaks_for(firm)
    idx = sum(1 for b in breaks if b["effective"] <= q_end)
    return f"r{idx}"


def is_break(firm, year_a, year_b):
    """True if a reorg break falls between two fiscal years."""
    return regime_for(firm, year_a) != regime_for(firm, year_b)


def is_break_q(firm, year_a, q_a, year_b, q_b):
    """True if a reorg break falls between two fiscal quarters."""
    return regime_for_quarter(firm, year_a, q_a) != regime_for_quarter(
        firm, year_b, q_b)
