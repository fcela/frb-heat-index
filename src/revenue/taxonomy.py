"""Taxonomy mapping: firm native segments -> 9 standard business lines.

Quality codes: D = direct, A = approximate, U = unmappable (emit null, never
fabricate). Source: issue #23 research, verified against latest 10-Ks.

A separate "placement_fees" line exists for PJT (outside the 9-line taxonomy).
"""

TAXONOMY_LINES = [
    "advisory",
    "ecm",
    "dcm",
    "ecm_dcm_combined",  # underwriting disclosed only combined (EVR, JEF)
    "ficc",
    "equities",
    "awm",
    "private_banking",
    "awm_incentive_fees",
    "awm_investments",
    "placement_fees",  # PJT only; outside the core taxonomy
]

# Ticker/code -> canonical firm name (parsers may emit either).
FIRM_ALIASES = {
    "BAC": "Bank of America",
    "C": "Citigroup",
    "EVR": "Evercore",
    "GS": "Goldman Sachs",
    "HL": "Houlihan Lokey",
    "JEF": "Jefferies",
    "JPM": "JPMorgan",
    "LAZ": "Lazard",
    "MC": "Moelis",
    "MS": "Morgan Stanley",
    "PJT": "PJT Partners",
    "WFC": "Wells Fargo",
}


def canonical_firm(name):
    """Normalize a firm code or name to the canonical full name."""
    return FIRM_ALIASES.get((name or "").strip(), (name or "").strip())

# firm -> [(native segment substring match, taxonomy_line, quality, note)]
_MAPPINGS = {
    "JPMorgan": [
        ("Banking & Payments", None, "U", "IB fees by type come from XBRL R105"),
        ("Investment Bank", None, "U", "pre-2012 segment; splits from filings"),
        ("Commercial & Investment Bank", None, "U", "pre-2024 combined CIB; splits from R105/MD&A"),
        ("Corporate & Investment Bank", None, "U", "pre-2024 CIB; splits from R105/MD&A"),
        ("Markets", None, "U", "FI/Equities split from XBRL; see subsplits"),
        ("Asset & Wealth Management", "awm", "D", ""),
        ("Asset Management", "awm", "D", "pre-2016 segment name"),
        ("Global Private Bank", "private_banking", "D", "within AWM"),
        ("Corporate/Private Equity", "awm_investments", "A", "private equity investments"),
    ],
    "Bank of America": [
        ("Global Banking", None, "U", "IB fees by segment from XBRL R173"),
        ("Global Markets", None, "U", "FICC/equities from MD&A"),
        ("Global Wealth", "awm", "D", "GWIM"),
    ],
    "Citigroup": [
        ("Banking", "advisory", "A", "advisory+underwriting; splits from MD&A"),
        ("Markets", None, "U", "FI/Equities from MD&A"),
        ("Wealth", "awm", "D", ""),
        ("Services", None, "U", "not in taxonomy"),
        ("US Personal Banking", None, "U", "not in taxonomy"),
    ],
    "Goldman Sachs": [
        ("Global Banking & Markets", None, "U", "splits from MD&A tables"),
        ("Global Markets", None, "U", "pre-2020 segment; splits from MD&A"),
        ("Investment Banking - Financial Advisory", "advisory", "D", "XBRL sub-segment"),
        ("Investment Banking - Debt Underwriting", "dcm", "D", "XBRL sub-segment"),
        ("Investment Banking - Equity Underwriting", "ecm", "D", "XBRL sub-segment"),
        ("Investment Banking - Underwriting", "ecm_dcm_combined", "D", "XBRL sub-segment, combined"),
        ("Investment Banking", None, "U", "pre-2020 segment; splits from MD&A"),
        ("Institutional Client Services - Fixed Income, Currency and Commodities Client Execution", "ficc", "D", "XBRL sub-segment"),
        ("Institutional Client Services - Equities", "equities", "D", "XBRL sub-segment"),
        ("Institutional Client Services - Equities Client Execution", "equities", "D", "XBRL sub-segment"),
        ("Institutional Client Services - Commissions and Fees", "equities", "A", "commissions, mostly equities"),
        ("Institutional Client Services - Securities Services", None, "U", "securities services; not in taxonomy"),
        ("Institutional Client Services", None, "U", "pre-2020 segment; splits from MD&A"),
        ("Investment Management - Incentive Fees", "awm_incentive_fees", "D", "XBRL sub-segment"),
        ("Investment Management - Management and Other Fees", "awm", "D", "XBRL sub-segment"),
        ("Investment Management - Transaction Revenues", "awm", "A", "transactional AWM revenue"),
        ("Investment Management", None, "U", "pre-2020 segment; splits from sub-segments"),
        ("Asset & Wealth Management", "awm", "D", ""),
        ("Asset Management", "awm", "D", "pre-2020 segment name"),
        ("Consumer & Wealth Management", "private_banking", "A", "includes Marcus consumer; mostly wealth"),
        ("Investing and Lending", "awm_investments", "D", "principal investments"),
        ("Investing and Lending - Debt Securities and Loans", "awm_investments", "D", "XBRL sub-segment"),
        ("Investing and Lending - Equity Securities", "awm_investments", "D", "XBRL sub-segment"),
        ("Investing and Lending - Other", "awm_investments", "D", "XBRL sub-segment"),
        ("Platform Solutions", None, "U", "not in taxonomy"),
    ],
    "Morgan Stanley": [
        ("Institutional Securities", None, "U", "advisory/underwriting from XBRL R189; FICC/equities from MD&A"),
        ("Wealth Management", "awm", "A", "includes private banking, not split"),
        ("Investment Management", "awm", "A", "asset management incl. incentive approx"),
    ],
    "Wells Fargo": [
        ("Corporate and Investment Banking", None, "U", "no IB/markets splits disclosed; IB fees by segment from XBRL R151"),
        ("Wealth and Investment Management", "awm", "A", ""),
    ],
    "Evercore": [
        ("Investment Banking", "advisory", "D", "ECM+DCM combined underwriting; split unmappable"),
        ("Investment Management", "awm", "D", ""),
    ],
    "Lazard": [
        ("Financial Advisory", "advisory", "D", ""),
        ("Asset Management", "awm", "D", "incentive fees disclosed within"),
    ],
    "Moelis": [
        ("", "advisory", "D", "single segment; firm does not allocate by advice type"),
    ],
    "PJT Partners": [
        ("Advisory Fees", "advisory", "D", ""),
        ("Placement Fees", "placement_fees", "D", "explicit line outside taxonomy"),
    ],
    "Houlihan Lokey": [
        ("Corporate Finance", "advisory", "A", "mixes M&A with capital-markets advisory, split undisclosed"),
        ("Financial Advisory Services", "advisory", "A", "pre-FY2020 name for Corporate Finance"),
        ("Financial Restructuring", "advisory", "D", ""),
        ("Financial and Valuation Advisory", "advisory", "D", ""),
    ],
    "Jefferies": [
        ("Investment Banking", "advisory", "A", "IB+capital markets combined; splits partial"),
        ("Capital Markets", None, "U", "FICC largely buried; only $7M separately disclosed"),
        ("Asset Management", "awm", "A", ""),
    ],
}


def map_quality(firm, segment_native):
    """Return (taxonomy_line or None, quality, note) for a native segment."""
    rules = _MAPPINGS.get(firm, [])
    seg = (segment_native or "").lower()
    for substr, line, quality, note in rules:
        if substr.lower() in seg:
            return line, quality, note
    return None, "U", f"no mapping rule for segment '{segment_native}'"
