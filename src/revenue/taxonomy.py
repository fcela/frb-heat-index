"""Taxonomy mapping: firm native segments -> 9 standard business lines.

Quality codes: D = direct, A = approximate, U = unmappable (emit null, never
fabricate). Source: issue #23 research, verified against latest 10-Ks.

A separate "placement_fees" line exists for PJT (outside the 9-line taxonomy).
"""

import re

# --- Accession ordering ----------------------------------------------------
#
# EDGAR accession numbers are <filer-id>-<YY>-<sequence>, where the filer-id
# is the CIK of whoever transmitted the filing (the company itself or a
# filing agent). Raw string comparison is therefore NOT chronological
# across prefixes: "0000019617-25-000123" (self-filed 2025) sorts BEFORE
# "0001193125-18-036702" (agent-filed 2018) because the 10-digit prefix
# dominates. Every dedupe that keeps "the later filing's restated value"
# must order by (filing year, sequence) instead.

_ACC_RE = re.compile(r"^\d{1,10}-(\d{2})-(\d{1,6})$")


def accession_filing_year(two_digit: int) -> int:
    """Two-digit accession year -> 1950-2049 filing year (pivot at 50)."""
    return 1900 + two_digit if two_digit >= 50 else 2000 + two_digit


def accession_order_key(accession: str) -> tuple:
    """Chronological sort key for an accession number.

    (filing_year, sequence, raw string). Malformed or empty accessions
    (e.g. the "" recorded on derived Q4 cells) sort earliest, so any
    real filing beats them.
    """
    m = _ACC_RE.match(accession or "")
    if not m:
        return (-1, -1, accession or "")
    return (accession_filing_year(int(m.group(1))), int(m.group(2)),
            accession)


def accession_order_exprs(col: str = "accession"):
    """Polars expressions equivalent to accession_order_key.

    Returns (year_expr, seq_expr) naming two comparable scalar columns:
    with_columns(year.alias("_acc_year"), seq.alias("_acc_seq")), then
    sort by them (descending for latest-filing-wins) alongside the raw
    column as a final deterministic tiebreak.
    """
    import polars as pl
    parts = pl.col(col).str.split_exact("-", 2)
    y = parts.struct.field("field_1").cast(pl.Int64, strict=False)
    s = parts.struct.field("field_2").cast(pl.Int64, strict=False)
    y = (pl.when(y.is_null()).then(pl.lit(-1, dtype=pl.Int64))
         .when(y >= 50).then(y + 1900)
         .otherwise(y + 2000))
    s = s.fill_null(-1)
    return y, s

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


# Canonical firm display name -> EDGAR CIK. Single source of truth for
# the whole revenue pipeline (download, download_q, parse_xbrl,
# parse_xbrl_q); the XBRL parsers use it to label raw CIK directories
# when no --firms mapping is supplied, so a rebuild without the ad-hoc
# JSON never silently degrades firm names to bare CIKs.
FIRM_CIKS = {
    "JPMorgan": "0000019617",
    "Bank of America": "0000070858",
    "Citigroup": "0000831001",
    "Goldman Sachs": "0000886982",
    "Morgan Stanley": "0000895421",
    "Wells Fargo": "0000072971",
    "Evercore": "0001360901",
    "Lazard": "0001311370",
    "Moelis": "0001596967",
    "PJT Partners": "0001626115",
    "Houlihan Lokey": "0001302215",
    "Jefferies": "0000096223",
}


def cik_firm_map() -> dict:
    """{cik: canonical firm name} for labeling raw CIK directories."""
    return {cik: name for name, cik in FIRM_CIKS.items()}

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
        # The post-reorg Banking division total mixes investment-banking
        # fees with Corporate Lending net-interest revenue and
        # Services/Markets revenue share (FY2025 10-K: $8,215M division
        # total vs $1,908M advisory fees in the MD&A IB-fees table), so
        # the division total is not a business-line value. The advisory /
        # ECM / DCM split comes from that MD&A table instead (parse_mda).
        ("=Banking", None, "U",
         "division total; includes corporate lending and revenue share, "
         "not a business line"),
        ("Markets", None, "U", "FI/Equities from MD&A"),
        # Exact match: the substring "Wealth" also appears in the
        # FY2021-22 intermediate segment "Personal Banking and Wealth
        # Management" (PBWM = US Personal Banking + Wealth; $25,140M in
        # 2020 -- two-thirds consumer banking), which leaked into the
        # awm line as Citi 2020 between no 2019 cell and 2021 $7,542M.
        # No 10-K reports a 2020 "Wealth" segment (the five-segment
        # structure's earliest restated comparative is 2021, FY2023
        # 10-K), so 2020 is a documented gap, never a PBWM splice.
        ("Personal Banking and Wealth Management", None, "U",
         "FY2021-22 intermediate segment = US Personal Banking + Wealth; "
         "not the Wealth line"),
        ("=Wealth", "awm", "D",
         "five-segment Wealth line; earliest restated comparative 2021"),
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
        # The revenue disaggregation note also carries "Interest Income
        # from Placement Fees and Other" -- interest earned on
        # placement-fee receivables, not placement fees. As a substring
        # it matched the placement rule and supplied the panel's
        # placement_fees cells for 2018-2025 ($7-17M/yr against true
        # fees of $103-217M/yr). Unmap it explicitly, and exact-match
        # the true line.
        ("Interest Income from Placement Fees and Other", None, "U",
         "interest income on placement-fee receivables; not placement fees"),
        ("=Placement Fees", "placement_fees", "D",
         "explicit line outside taxonomy"),
    ],
    "Houlihan Lokey": [
        # The advisory cell tracks the Corporate Finance segment only.
        # The other two segments are advisory-type businesses with no
        # standard taxonomy cell of their own; leaving them unmapped (U)
        # keeps them from clobbering the Corporate Finance value in the
        # build's quality-dedupe (D beats A).
        ("Corporate Finance", "advisory", "A", "mixes M&A with capital-markets advisory, split undisclosed"),
        ("Financial Advisory Services", "advisory", "A", "pre-FY2020 name for Corporate Finance"),
        ("Financial Restructuring", None, "U",
         "restructuring advisory; no standard taxonomy cell"),
        ("Financial and Valuation Advisory", None, "U",
         "valuation advisory; no standard taxonomy cell"),
    ],
    "Jefferies": [
        # Jefferies' XBRL layer reports division-level segments only: the
        # post-reorg "Investment Banking And Capital Markets" segment is
        # advisory + underwriting + equities + fixed income in one total,
        # and the pre-reorg "Investment Banking, Capital Markets and Asset
        # Management" segment is the whole firm. Mapping either to
        # advisory produced impossible cells (annual 2017 $3,198M = whole-
        # firm revenue; quarterly advisory TTM $12,480M = IB&CM division
        # totals). Advisory comes from the MD&A/segment-note revenues-by-
        # source tables instead (parse_mda / parse_mda_q, quality D).
        ("Investment Banking", None, "U",
         "division total: advisory + underwriting + equities + fixed "
         "income (or the whole pre-reorg firm); advisory comes from the "
         "revenues-by-source tables, never this segment"),
        ("Capital Markets", None, "U", "FICC largely buried; only $7M separately disclosed"),
        # Leucadia-era filings (CIK 0000096223 was Leucadia National
        # Corp until the 2018 Jefferies Financial Group rename) report
        # the parent's own segments alongside the Jefferies segment:
        # "Leucadia Asset Management and Berkadia Transferred" is the
        # holding company's other-investments portfolio ($49.6M in
        # fiscal 2017), not an asset-management business line -- it
        # leaked into the awm slot via the "Asset Management"
        # substring. Fiscal 2013-2019 have no separable Jefferies AM
        # segment (the Jefferies/IBCM one-segment totals are unmapped
        # above), so those years stay documented gaps.
        ("Leucadia Asset Management", None, "U",
         "Leucadia-era parent segment (other investments incl. Berkadia "
         "stakes); not the Jefferies asset-management business line"),
        ("Asset Management", "awm", "A", ""),
    ],
}


def map_quality(firm, segment_native):
    """Return (taxonomy_line or None, quality, note) for a native segment.

    Rules are substring matches, except a rule starting with "=" which
    requires an exact (case-insensitive) match. Use "=" when the substring
    would over-match (e.g. Citi "Banking" vs "Global Consumer Banking").
    """
    seg = (segment_native or "")
    # Geographic splits (" | Asia", " | EMEA", ...) are not business lines;
    # never map them, even if the prefix substring-matches a rule.
    if " | " in seg:
        return None, "U", f"geographic split, not a business line: '{segment_native}'"
    seg_low = seg.lower()
    rules = _MAPPINGS.get(firm, [])
    for substr, line, quality, note in rules:
        if substr.startswith("="):
            if seg_low == substr[1:].lower():
                return line, quality, note
        elif substr.lower() in seg_low:
            return line, quality, note
    return None, "U", f"no mapping rule for segment '{segment_native}'"
