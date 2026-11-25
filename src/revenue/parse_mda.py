"""Layer 2: MD&A sub-business-line scraper for the bank revenue tracker.

The XBRL R-file layer (Layer 1) captures segment *totals*. Sub-business
splits -- advisory vs ECM vs DCM, FICC vs equities -- are mostly MD&A-text
only, so this module scrapes them from 10-K MD&A tables.

Pipeline
--------
1. ``fetch_filing`` downloads a 10-K main HTML document to
   ``data/revenue/raw/<cik>/<accession>/`` (skips files already on disk).
2. Per-firm table locators (``FIRM_SPECS``) find MD&A tables by anchor text
   and tolerant row-label regexes -- never by fixed positions, since tables
   move between years.
3. Year columns are detected from header cells (multi-year 10-K columns are
   the norm); values are normalized to USD millions.
4. Overlapping filings are deduped: the later filing's restated value wins,
   and the choice is logged.

Output: ``data/revenue/subsplits_annual.csv`` with columns
``firm, cik, taxonomy_line, year, net_revenue_m, source, accession`` where
``source`` is ``"mda:<table description>"``.

Scope notes
-----------
Firms/lines already covered by the XBRL layer are deliberately NOT scraped
here (WFC IB fees by segment). Division-reporters whose business-line
splits live only in MD&A tables (JPM CIB, MS Institutional Securities,
BofA Global Banking / Global Markets) ARE scraped here -- the XBRL layer
captures their division totals, and a reconciliation step checks the MD&A
splits against those totals. Lines a firm does not disclose are
emitted as nothing -- never estimated, never fabricated.

Run:  python3 -m src.revenue.parse_mda --help
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import polars as pl
from bs4 import BeautifulSoup

from src.revenue.taxonomy import accession_order_key, accession_order_exprs  # noqa: E402

log = logging.getLogger("parse_mda")

REPO = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO / "data" / "revenue" / "raw"
OUT_CSV = REPO / "data" / "revenue" / "subsplits_annual.csv"

EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
# Fallback document mirror for environments where EDGAR itself is
# unreachable (the Internet Archive's copy of the exact same filing).
ARCHIVE_BASE = "https://web.archive.org/web/2026id_/https://www.sec.gov/Archives/edgar/data"

USER_AGENT = ("frb-heat-index/1.0 (bank revenue tracker research; "
              "contact frb-heat-index@example.com)")

# EDGAR fair-access guidance: at most 10 requests/second.
MIN_REQUEST_INTERVAL = 0.15

FIRMS: dict[str, dict[str, str]] = {
    "GS":  {"cik": "0000886982", "name": "Goldman Sachs"},
    "MS":  {"cik": "0000895421", "name": "Morgan Stanley"},
    "C":   {"cik": "0000831001", "name": "Citigroup"},
    "BAC": {"cik": "0000070858", "name": "Bank of America"},
    "JPM": {"cik": "0000019617", "name": "JPMorgan Chase"},
    "JEF": {"cik": "0000096223", "name": "Jefferies"},
    "EVR": {"cik": "0001360901", "name": "Evercore"},
    "LAZ": {"cik": "0001311370", "name": "Lazard"},
    "MC":  {"cik": "0001596967", "name": "Moelis"},
    "PJT": {"cik": "0001626115", "name": "PJT Partners"},
    "HL":  {"cik": "0001302215", "name": "Houlihan Lokey"},
}

# Canonical taxonomy lines plus the documented combined lines. Combined
# underwriting (ECM+DCM disclosed as one number) and placement fees have
# no single taxonomy cell; they are recorded under explicit combined
# labels rather than forced into a cell or split by guesswork.
COMBINED_LINES = {"ecm_dcm_combined", "placement_fees"}


class TableNotFoundError(RuntimeError):
    """Raised when an expected MD&A table cannot be located in a filing."""


class UnitAmbiguousError(RuntimeError):
    """Raised when a table's dollar units cannot be determined."""


class YearColumnError(RuntimeError):
    """Raised when year columns cannot be detected in a table."""


@dataclass
class RowSpec:
    """One extractable line: taxonomy label + row-label matchers."""
    line: str
    # Regexes matched against the start of the normalized row label; a
    # trailing parenthetical (e.g. "(Equity Capital Markets (ECM))" or a
    # "(1)" footnote the normalizer missed) is allowed, nothing else.
    # The first matching row wins, unless occurrence="last".
    patterns: list[str]
    # Optional regexes that disqualify a candidate row label.
    exclude: list[str] = field(default_factory=list)
    # "first" (default) or "last": which matching row to take when a label
    # repeats inside one table (e.g. Morgan Stanley's "Equity"/"Fixed
    # income" appear under both underwriting and sales & trading; the
    # sales & trading rows come last).
    occurrence: str = "first"
    # When True the row only PINS the table (its presence is required so
    # locate_tables can reject near-miss tables that share the extracted
    # rows' labels); no record is emitted for it. E.g. Morgan Stanley's
    # S&T "By Business" table is told from the investment-banking summary
    # (same bare "Equity"/"Fixed income" rows) by its "Other" row.
    pin_only: bool = False

    # Matches "<pattern>" or "<pattern> (<anything>)" at label start.
    _TAIL = r"(\s*\(.*\))?\s*$"

    def matches(self, label: str) -> bool:
        if any(re.search(x, label) for x in self.exclude):
            return False
        return any(re.match(p + self._TAIL, label) for p in self.patterns)


@dataclass
class TableSpec:
    """One MD&A table to scrape: anchor + rows to extract."""
    # Human description; becomes the "mda:<desc>" source tag.
    description: str
    # Regexes; a table qualifies if its own text or nearby heading text
    # matches ANY anchor (tried in order; first hit wins).
    anchors: list[str]
    rows: list[RowSpec]
    # When True, extract from EVERY matching table (Jefferies repeats its
    # disaggregation once per year). Otherwise only the first match is
    # used, avoiding false positives from unrelated tables that happen to
    # share row labels.
    multi: bool = False
    # Wide-table column layout. "year_major" (default): a row's slots form
    # one group per year (e.g. Jefferies' [IB&CM, Asset Mgmt, Total] x 3
    # years); the first sub-column of each year's group is taken.
    # "segment_major": a row's slots form one group per segment, each
    # group holding all year columns (e.g. BofA's "Noninterest Income by
    # Business Segment": [Total Corp, Consumer, GWIM, ...] x 3 years);
    # the FIRST segment group's year columns are taken, so the table
    # must lead with the consolidated total (see require_text).
    column_layout: str = "year_major"
    # Regexes that must ALL match the table text + nearby headings
    # (in addition to the anchor). Used to pin the segment-major first
    # block to the consolidated total.
    require_text: list[str] = field(default_factory=list)
    # Fiscal-year window this spec applies to (inclusive), e.g. (2010,
    # 2022) for a table that only exists in pre-reorg filings. None =
    # all years. parse_filing skips specs outside the filing's fiscal
    # year (logs it); specs that apply but find no table still raise.
    fiscal_years: tuple[int, int] | None = None
    # When True and the table carries no year headers (some filers omit
    # them), fall back to the filing's fiscal-year triple [FY, FY-1,
    # FY-2] in column order. Only for tables whose columns are known to
    # be the three most recent fiscal years.
    implied_years: bool = False
    # Derived lines: {target_line: [component_line, ...]}. After the
    # listed rows are extracted, the target is set to the sum of the
    # components (per year). The components must themselves be in
    # `rows` (they are emitted too, then dropped if not in the
    # taxonomy). Used e.g. for BofA pre-2016 where ECM+DCM are separate
    # "Debt issuance"/"Equity issuance" rows.
    derived: dict[str, list[str]] = field(default_factory=dict)
    # For segment_major tables: which segment block to take (0-indexed).
    # Default 0 (first block). BofA pre-2016 "Investment Banking Fees"
    # leads with Global Banking; the firmwide Total Corporation block
    # is second (segment_block=1).
    segment_block: int = 0
    # When True, only the annual scraper (parse_mda) runs this spec;
    # the quarterly scraper (parse_mda_q) skips it. Used for Jefferies'
    # ASC 606 disaggregation note: the annual advisory cell comes from
    # it, but the quarterly advisory scrape is advisory-only by scope
    # (underwriting/equities/fixed income stay annual until a separate
    # quarterly line is spec'd).
    annual_only: bool = False
    # Column mapping by header grid instead of positional slots. The
    # first header row carrying 2+ year labels pins each year to a
    # colspan-accumulated grid-column range; a data row's value for a
    # year is the first parseable money cell inside that range. For
    # inline-XBRL "Amount / % of Net Revenues" tables whose percent
    # cells carry no "%" marker in some rows (Jefferies FY2022+ net
    # revenues summaries): positional slotting leaks the percent into
    # the value stream (or collapses the whole flat row to one slot),
    # while the header grid stays correct because the percent column
    # always follows its year's Amount column.
    grid_columns: bool = False


# ---------------------------------------------------------------------------
# Per-firm table specifications.
# ---------------------------------------------------------------------------
#
# Conventions:
# - Row patterns are matched against labels normalized to lowercase with
#   collapsed whitespace and footnote markers stripped; a trailing
#   parenthetical is tolerated (e.g. Citi's "Equity underwriting (Equity
#   Capital Markets (ECM))").
# - locate_table() requires EVERY listed row to be present, so the anchor
#   only needs to be approximately right; the row check disambiguates.
# - XBRL-covered splits are NOT listed here (MS advisory/underwriting,
#   JPM IB fees by type, BofA IB fees by segment, WFC IB fees by segment).
# - Nothing is emitted for undisclosed splits -- see module docstring.
# - Labels/values below were validated against the firms' FY2024 10-Ks.

FIRM_SPECS: dict[str, list[TableSpec]] = {
    "GS": [
        TableSpec(
            # In recent filings the IB-fee split and the FICC/Equities
            # split sit in one "Global Banking & Markets operating
            # results" table; older filings may split them -- either way
            # both specs resolve.
            description="Investment banking fees",
            # 2022+ filings title the table "Investment banking fees";
            # the 2019-2021 filings (four-segment regime) title it
            # "Investment Banking" but keep the same fee rows, so fall
            # back to the generic "net revenues" heading there.
            # require_text pins the equity-underwriting row so the anchor
            # cannot drift onto an unrelated net-revenues table.
            anchors=[r"investment banking fees", r"net revenues"],
            require_text=[r"equity underwriting"],
            rows=[
                # 2019-2021 filings label the row "Financial advisory";
                # 2022+ filings "Advisory".
                RowSpec("advisory", [r"advisory", r"financial advisory"]),
                RowSpec("ecm", [r"equity underwriting"]),
                RowSpec("dcm", [r"debt underwriting"]),
            ],
        ),
        TableSpec(
            # Four-segment regime (Investment Banking / Global Markets /
            # Asset Management / Consumer & Wealth Management), reported
            # from Q1 2020; the segment's MD&A table carries the
            # FICC / Equities rows. The FY2019 10-K (filed February
            # 2020) ALREADY presented the realigned structure with
            # restated 2017-2019 comparatives -- it is the realignment's
            # first annual filing -- so fiscal 2019 is in the window and
            # supplies the restated 2017/2018 cells. The FY2020/FY2021
            # 10-Ks keep the same table, so this spec also supplies the
            # annual 2020-2021 comparatives their tables carry. 2022+
            # filings use the Global Banking & Markets spec below.
            #
            # The heading phrase is not always within reach of the table
            # (the FY2020 10-K's table sits well below its heading), and
            # the anchor "global markets" alone also lands on the
            # point-in-time inventory table, so the generic "net revenues"
            # heading plus the FICC/Equities-intermediation require_text
            # is what selects the net-revenues table.
            description="Global Markets net revenues",
            anchors=[r"global markets", r"net revenues"],
            require_text=[r"intermediation"],
            fiscal_years=(2019, 2021),
            rows=[
                RowSpec("ficc", [r"ficc"]),
                RowSpec("equities", [r"equities"]),
            ],
        ),
        TableSpec(
            # Four-segment regime only: through the FY2021 10-K the IB
            # operating results table carries a total "Underwriting" row
            # (equity + debt underwriting), which is the restated-basis
            # ecm_dcm_combined. The restated 2017-2018 comparatives
            # ($3,915M / $3,986M) replace the pre-reorg XBRL rows
            # ($4,183M / $4,355M) in the panel; the GBM regime (2022+)
            # stops disclosing the combined row (only "Investment
            # banking fees"), so the line is era-bounded at the
            # registered 2022-10 reorg. require_text pins the IB
            # operating results table (its "Corporate lending" row is
            # unique to this regime's layout).
            description="Investment Banking underwriting "
                        "(four-segment era)",
            anchors=[r"investment banking fees", r"net revenues"],
            require_text=[r"corporate lending"],
            fiscal_years=(2019, 2021),
            rows=[
                RowSpec("ecm_dcm_combined", [r"underwriting"]),
            ],
        ),
        TableSpec(
            description="Global Banking & Markets net revenues",
            anchors=[r"global banking.+markets", r"net revenues"],
            require_text=[r"intermediation"],
            fiscal_years=(2022, 2100),
            rows=[
                RowSpec("ficc", [r"ficc"]),
                RowSpec("equities", [r"equities"]),
            ],
        ),
    ],
    "MS": [
        TableSpec(
            description="Institutional Securities income statement",
            anchors=[r"institutional securities",
                     r"income statement information"],
            # Validated FY2021+ filings: the income statement table
            # carries the sales & trading "Equity (1)"/"Fixed income (1)"
            # rows after the underwriting rows. Earlier filings split S&T
            # into standalone tables (fiscal 2018-2020 filings: "By
            # Business"; 2016-2017: GAAP/non-GAAP; <=2015: "Fixed income
            # and commodities") and this spec would land on the
            # investment-banking summary fragment, whose bare
            # "Equity"/"Fixed income" rows are the UNDERWRITING split --
            # the equities/ECM and FICC/DCM clone failure mode. The
            # fiscal-2020 filing raises here (fragmented layout) and is
            # skipped; its comparatives come from the fiscal-2021 filing.
            fiscal_years=(2021, 2100),
            rows=[
                # "Equity"/"Fixed income" label BOTH the underwriting and
                # the sales & trading rows; the sales & trading rows come
                # last, hence occurrence="last".
                RowSpec("ficc", [r"fixed income"], exclude=[r"underwriting"],
                        occurrence="last"),
                RowSpec("equities", [r"equity"], exclude=[r"underwriting"],
                        occurrence="last"),
            ],
        ),
        TableSpec(
            # The investment-banking fee rows of the IS income statement,
            # fragment era (fiscal 2017-2018 filings): the MD&A summary
            # fragment carries "Advisory" plus BARE "Equity"/"Fixed
            # income" underwriting rows. Fiscal 2015-2016 filings use
            # suffixed labels ("Equity underwriting revenues") and are
            # served by the pre-2015 IB spec's window; fiscal 2019-2020
            # filings fragment the income statement so no spec applies
            # (their cells come from later filings' comparatives).
            description="Institutional Securities investment banking "
                        "(fragment era)",
            anchors=[r"institutional securities",
                     r"income statement information"],
            fiscal_years=(2017, 2018),
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity"], occurrence="first"),
                RowSpec("dcm", [r"fixed income"], occurrence="first"),
            ],
        ),
        TableSpec(
            # The same fee rows in the full income statement table
            # (fiscal 2021+ filings): the FIRST "Equity"/"Fixed income"
            # rows are the underwriting splits (the investment-banking
            # section precedes sales & trading), and "Advisory" appears
            # once.
            description="Institutional Securities investment banking",
            anchors=[r"institutional securities",
                     r"income statement information"],
            fiscal_years=(2021, 2100),
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity"], occurrence="first"),
                RowSpec("dcm", [r"fixed income"], occurrence="first"),
            ],
        ),
        TableSpec(
            # S&T by asset class. Four layout eras, one spec each below:
            #   <= FY2015 filings: "Sales and Trading Net Revenues by
            #     Business" table, rows "Equity" / "Fixed income and
            #     commodities" (this spec; validated FY2012 and FY2015).
            #   FY2016-FY2017 filings: GAAP/non-GAAP table, rows
            #     "Equity—U.S. GAAP" / "Fixed income—U.S. GAAP".
            #   FY2018-FY2020 filings: "By Business" summary, bare
            #     "Equity"/"Fixed income" rows plus an "Other" pin.
            # FY2021+ filings put the asset-class split in the income
            # statement table (first spec above).
            description="Institutional Securities sales and trading (pre-2015)",
            anchors=[r"sales and trading net revenues"],
            fiscal_years=(2010, 2015),
            rows=[
                RowSpec("ficc", [r"fixed income and commodities"]),
                RowSpec("equities", [r"^equity$"]),
            ],
        ),
        TableSpec(
            # FY2016-FY2017 filings: S&T net revenues by asset class in a
            # GAAP/non-GAAP table ("Equity—U.S. GAAP" 8,037 = the same
            # number the FY2018 filing's By-Business table carries for
            # 2016, so the eras chain without a jump). The GAAP rows are
            # taken; the non-GAAP rows (DVA/FVA-adjusted) never match the
            # patterns. Validated on the FY2016 and FY2017 10-Ks.
            description="Institutional Securities sales and trading "
                        "(GAAP/non-GAAP)",
            anchors=[r"u\.s\.\s*gaap", r"sales and trading"],
            fiscal_years=(2016, 2017),
            rows=[
                # \W* separator: lxml decodes these filings' em-dash as
                # the control char U+0097 (declared-charset quirk), so a
                # literal dash class misses the rows.
                RowSpec("ficc", [r"fixed income\W*u\.s\.\s*gaap"]),
                RowSpec("equities", [r"equity\W*u\.s\.\s*gaap"]),
            ],
        ),
        TableSpec(
            # FY2018-FY2020 filings: S&T by asset class in the "By
            # Business" summary table (bare "Equity"/"Fixed income" rows,
            # "$ in millions"). The investment-banking summary fragment
            # in the same filings carries the same bare "Equity"/"Fixed
            # income" labels for the UNDERWRITING split, so the "Other"
            # pin row (present only in the S&T table) and the
            # millions-unit require_text keep this spec off it -- the
            # pin also keeps it off the "$ in billions" AUM tables, whose
            # rows are Equity/Fixed income/Alternative/Other/Liquidity
            # (the "Other" there is a suffix, never a bare row).
            # Validated on the FY2018, FY2019 and FY2020 10-Ks.
            description="Institutional Securities sales and trading "
                        "(By Business)",
            anchors=[r"by business", r"sales and trading", r"in\s+millions"],
            require_text=[r"in\s+millions"],
            fiscal_years=(2018, 2020),
            rows=[
                RowSpec("ficc", [r"^fixed income$"]),
                RowSpec("equities", [r"^equity$"]),
                RowSpec("_st_other_pin", [r"^other$"], pin_only=True),
            ],
        ),
        TableSpec(
            # Pre-2015 era: Investment banking revenues in a standalone
            # table with "Advisory revenues", "Equity underwriting
            # revenues", "Fixed income underwriting revenues".
            # Validated on FY2012 10-K.
            description="Institutional Securities investment banking (pre-2015)",
            anchors=[r"investment banking.*revenues",
                     r"advisory revenues"],
            fiscal_years=(2010, 2014),
            rows=[
                RowSpec("advisory", [r"advisory revenues"]),
                RowSpec("ecm", [r"equity underwriting revenues"]),
                RowSpec("dcm", [r"fixed income underwriting revenues"]),
            ],
        ),
    ],
    "C": [
        TableSpec(
            description="Banking business metrics - investment banking fees",
            # "Business metrics" phrasing is FY2021-2023; the FY2024+
            # five-segment 10-Ks title the table "Investment banking fees"
            # and omit year headers (columns are FY, FY-1, FY-2). From the
            # 2026-Q2 10-Q on, the metric was renamed "Investment banking
            # revenues" (adds other fee revenue, principal transactions
            # and loan NII to the fee components); the Advisory / ECM /
            # DCM rows continue, and the prior-year comparatives moved by
            # under 1% (FY2025-Q2 advisory fees $408M vs restated $407M),
            # so the same rows remain the advisory/ECM/DCM source.
            anchors=[r"business metrics", r"investment banking fees",
                     r"investment banking revenues"],
            fiscal_years=(2023, 2100),
            implied_years=True,
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity underwriting"]),
                RowSpec("dcm", [r"debt underwriting"]),
            ],
        ),
        TableSpec(
            description="Markets revenue by component",
            # FY2021-2023: "Revenue by component"; FY2024+: "Revenue by
            # line of business" (same Fixed income / Equity markets rows).
            anchors=[r"revenue by component",
                     r"revenue by line of business"],
            fiscal_years=(2023, 2100),
            rows=[
                RowSpec("ficc", [r"fixed income markets?"]),
                RowSpec("equities", [r"equity markets?"]),
            ],
        ),
        TableSpec(
            # ICG era (FY2010-FY2022 filings): "ICG Revenue Details" carries
            # both the investment-banking split and the headline
            # ex-CVA/DVA Fixed income / Equity markets rows. The markets
            # rows repeat (CVA/DVA adjustments, then detail breakdowns);
            # the FIRST occurrence is the headline business measure.
            description="ICG revenue details",
            anchors=[r"icg revenue details"],
            fiscal_years=(2010, 2022),
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity underwriting"]),
                RowSpec("dcm", [r"debt underwriting"]),
                RowSpec("ficc", [r"fixed income markets"],
                        occurrence="first"),
                RowSpec("equities", [r"equity markets"],
                        occurrence="first"),
            ],
        ),
    ],
    "BAC": [
        TableSpec(
            description="Global Markets sales and trading revenue",
            anchors=[r"sales and trading"],
            # Validated 2010+; the "Fixed income, currencies and
            # commodities"/"Equities" labels are stable across eras.
            fiscal_years=(2010, 2100),
            rows=[
                # Label is hyphenated: "Fixed-income, currencies and
                # commodities". The first (GAAP, incl. net DVA) block is
                # taken; the "excluding net DVA" non-GAAP block is skipped.
                RowSpec("ficc",
                        [r"fixed[- ]income,?\s+currencies( and|,)?\s+"
                         r"commodities",
                         r"ficc"]),
                RowSpec("equities", [r"equities"]),
            ],
        ),
        TableSpec(
            # "Noninterest Income by Business Segment": segment-major wide
            # table ([Total Corp, Consumer, GWIM, ...] x year columns).
            # The FIRST block is the consolidated total (require_text pins
            # it); "Financial advisory services" is the firmwide advisory
            # line and "Underwriting income" the combined ECM+DCM line
            # (BofA does not split underwriting further). The table was
            # introduced in the FY2018 10-K (its columns reach back to
            # 2016); the FY2016/FY2017 10-Ks do not carry it, and an
            # in-window miss aborted those filings wholesale, taking
            # their sales-and-trading rows with it (the 2016 FICC /
            # equities hole). Window = filing years, 2018+.
            description="Noninterest income by business segment",
            anchors=[r"noninterest income by business segment"],
            require_text=[r"total corporation"],
            column_layout="segment_major",
            fiscal_years=(2018, 2100),
            rows=[
                RowSpec("advisory", [r"financial advisory services"]),
                RowSpec("ecm_dcm_combined", [r"underwriting income"]),
            ],
        ),
        TableSpec(
            # Pre-2016 era (FY2010-FY2015 filings): "Investment Banking
            # Fees" table with "Advisory", "Debt issuance", "Equity
            # issuance" rows; columns are [Global Banking, Total
            # Corporation] x [FY, FY-1]. The Total Corporation block is
            # SECOND (segment_block=1); it is the firmwide measure.
            # ECM+DCM are separate rows, summed via `derived`. Validated
            # on FY2012.
            description="Investment banking fees (pre-2016)",
            anchors=[r"investment banking fees"],
            require_text=[r"total corporation"],
            column_layout="segment_major",
            segment_block=1,
            fiscal_years=(2010, 2015),
            rows=[
                RowSpec("advisory", [r"^advisory$"]),
                # Temporary component lines, summed into ecm_dcm_combined.
                RowSpec("_debt_issuance", [r"debt issuance"]),
                RowSpec("_equity_issuance", [r"equity issuance"]),
            ],
            derived={"ecm_dcm_combined": ["_debt_issuance",
                                           "_equity_issuance"]},
        ),
    ],
    "JEF": [
        TableSpec(
            # Leucadia-era 10-Ks (FY2014-FY2017, CIK 0000096223 was
            # Leucadia National Corp until the 2018 Jefferies Financial
            # Group rename) summarize the Jefferies segment's net
            # revenues by source in an MD&A table with an explicit
            # "Advisory" row: "The following provides a summary of net
            # revenues by source ... (in thousands)". This is the only
            # advisory disclosure covering FY2013-FY2017 -- the ASC 606
            # disaggregation note first appears in the FY2019 10-K and
            # reaches back only to the 2018 transition period -- so this
            # spec backfills 2013-2017 (2013 is the stub period from the
            # Jefferies acquisition through December 31, 2013). The
            # "total investment banking" anchor phrase is the table's
            # own row text, for vintages whose nearby heading differs.
            description="Jefferies net revenues by source - advisory",
            anchors=[r"net revenues by source",
                     r"total\s+investment\s+banking"],
            # FY2013+ filings carry the table (Leucadia's 2010-2012 10-Ks
            # predate the Jefferies acquisition and do not). In JFG-era
            # 10-Ks this duplicates the ASC 606 note's years; dedupe
            # keeps the note's cells (spec listed later, same accession),
            # so this spec effectively supplies the years the note does
            # not reach: 2013-2017.
            fiscal_years=(2013, 2100),
            # 10-Qs carry the same table twice (three- and nine-month
            # periods); the quarterly scraper takes the QTD one and its
            # YTD-declaring twin is skipped by the 6/9/12-month guard.
            multi=True,
            rows=[
                RowSpec("advisory", [r"^advisory$"]),
            ],
        ),
        TableSpec(
            # Jefferies discloses advisory and underwriting splits in
            # the segment note's ASC 606 revenue disaggregation (year
            # band rows, $ in thousands), not in a year-column MD&A
            # table. Underwriting is ECM+DCM combined. Advisory and
            # underwriting fees are ASC 606 revenue in full, so the
            # note's rows carry the whole business line. The note's
            # "Equities"/"Fixed income" rows do NOT: trading revenue is
            # mostly fair-value (non-606), leaving a few $M of
            # contract revenue -- extracting them once understated both
            # lines by ~10-100x (the FY2020 note shows Equities $806M
            # vs the MD&A's $1,129M and Fixed income $16M vs
            # $1,341M). Those two lines come from the MD&A summary spec
            # below; this spec keeps only the fee rows. Annual only.
            description="Investment Banking and Capital Markets "
                        "revenues by source",
            anchors=[r"investment banking"],
            fiscal_years=(2019, 2100),
            annual_only=True,
            # Jefferies repeats the disaggregation once per fiscal year;
            # extract from every matching table.
            multi=True,
            rows=[
                RowSpec("advisory",
                        [r"investment banking\s*[–—-]\s*advisory"]),
                RowSpec("ecm_dcm_combined",
                        [r"investment banking\s*[–—-]\s*underwriting"]),
            ],
        ),
        TableSpec(
            # The MD&A "net revenues" summary table is the only
            # disclosure of Jefferies' FULL Equities and Fixed income
            # sales-and-trading revenue (the note spec above carries
            # only their contract-revenue subset). Two layouts:
            # FY2019-FY2021 10-Ks introduce the table with "The
            # following provides a summary of net revenues by source";
            # FY2022+ 10-Ks drop the phrase and style the columns
            # "Amount / % of Net Revenues". The ASC 606 note contains
            # neither phrase, so the anchors cannot land on it. Listed
            # after the note spec, so on any same-key overlap the
            # summary table's values win. The FY2019 10-K's table also
            # carries a 2017 comparative on the JFG/ICBM basis; it is
            # extracted (the panel has no pre-2017 Jefferies equities/
            # FICC cells -- the Leucadia-era summary is the Jefferies
            # segment of Leucadia National, a different basis that
            # would splice across the 2018 transition). Annual only.
            description="Investment Banking and Capital Markets "
                        "net revenues summary",
            anchors=[r"% of net revenues", r"net revenues by source"],
            fiscal_years=(2019, 2100),
            annual_only=True,
            multi=True,
            grid_columns=True,
            rows=[
                RowSpec("equities", [r"equities"]),
                RowSpec("ficc", [r"fixed income"]),
            ],
        ),
    ],
    "EVR": [
        TableSpec(
            description="Investment Banking & Equities revenues",
            anchors=[r"investment banking"],
            rows=[
                RowSpec("advisory", [r"advisory fees?"]),
                # Underwriting disclosed as ECM+DCM combined.
                RowSpec("ecm_dcm_combined", [r"underwriting fees?"]),
            ],
        ),
    ],
    "LAZ": [
        TableSpec(
            # Lazard's Financial Advisory segment adjusted operating
            # results (GAAP net revenue row, $ in thousands). No
            # underwriting/ECM/DCM split is disclosed.
            description="Financial Advisory adjusted operating results",
            anchors=[r"financial advisory segment"],
            rows=[
                RowSpec("advisory",
                        [r"net revenue\s*[-–—]\s*u\.?s\.?\s*gaap basis"]),
            ],
        ),
    ],
    "MC": [
        TableSpec(
            # Moelis does not allocate revenue by advice type (stated in
            # the 10-K); the single total-revenues line is the advisory
            # line.
            description="Consolidated results of operations - revenues",
            anchors=[r"results of operations"],
            rows=[
                RowSpec("advisory", [r"^(total )?revenues?$"]),
            ],
        ),
    ],
    "PJT": [
        TableSpec(
            description="Consolidated results of operations - revenues",
            anchors=[r"results of operations"],
            rows=[
                RowSpec("advisory", [r"advisory fees?"]),
                RowSpec("placement_fees", [r"placement fees?"]),
            ],
        ),
    ],
    "HL": [
        TableSpec(
            # Fiscal year ends March 31; header years are fiscal years.
            # Corporate Finance mixes M&A with capital-markets advisory
            # (undisclosed split); mapped to advisory.
            description="Revenues by segment",
            anchors=[r"revenues by segment"],
            rows=[
                RowSpec("advisory", [r"corporate finance"]),
            ],
        ),
    ],
    "JPM": [
        TableSpec(
            # CIB "Selected income statement data": revenue by business.
            # Fixed Income Markets / Equity Markets rows are the FICC /
            # Equities splits; the division label changed at the 2012 and
            # 2024 reorgs but the table/row wording did not.
            description="CIB revenue by business",
            anchors=[r"revenue by business"],
            rows=[
                RowSpec("ficc", [r"fixed income markets"]),
                RowSpec("equities", [r"equity markets"]),
            ],
        ),
        TableSpec(
            # CIB "Investment banking fees": advisory / equity / debt
            # underwriting. The notes-to-financial-statements table with
            # the same anchor phrases its rows as bare "Equity"/"Debt"
            # and is excluded by the row requirement (never force-fit).
            description="Investment banking fees",
            anchors=[r"investment banking fees"],
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity underwriting"]),
                RowSpec("dcm", [r"debt underwriting"]),
            ],
        ),
    ],
}


# ---------------------------------------------------------------------------
# Download layer
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, min_interval: float = MIN_REQUEST_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self):
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()


def _request_headers(url: str) -> dict[str, str]:
    """Request headers; the Host override only when it matches the host.

    The explicit Host header exists for the archive mirror behind
    some proxies; sending it for a different host (the old code sent
    Host: web.archive.org for every non-sec.gov URL) is a protocol
    violation that efts.sec.gov answers with HTTP 400.
    """
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if "www.sec.gov" in url:
        headers["Host"] = "www.sec.gov"
    elif "web.archive.org" in url:
        headers["Host"] = "web.archive.org"
    return headers


def _http_get(url: str, limiter: RateLimiter, retries: int = 6) -> bytes:
    import requests
    # requests (not urllib): EDGAR's WAF 403s urllib's fingerprint on
    # some paths (verified 2026-10-17). Headers mirror download.py.
    headers = _request_headers(url)
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get(url, headers=headers, timeout=60)
            if r.status_code == 200:
                return r.content
            # EDGAR answers 403 both for bad requests and for throttling;
            # treat it like download.py's sec_get: back off and retry.
            if r.status_code in (403, 429, 500, 502, 503, 504) \
                    and attempt < retries - 1:
                wait = 60 * (attempt + 1)
                log.warning("GET %s -> HTTP %s; backing off %ds "
                            "(attempt %d/%d)",
                            url, r.status_code, wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            raise RuntimeError(f"GET failed for {url}: HTTP {r.status_code}")
        except requests.RequestException as e:
            raise RuntimeError(f"GET failed for {url}: {e}") from e
    raise RuntimeError(f"GET failed for {url}: retries exhausted")


def _filing_urls(cik: str, accession: str, base: str = EDGAR_ARCHIVES
                 ) -> tuple[str, str]:
    """Return (directory_url, directory_url) for a filing.

    EDGAR's per-filing index.html intermittently 403s (verified
    2026-10-17); the directory listing itself returns 200 and carries
    the same [filename, size, date] table, so we parse that instead.
    """
    cik_num = str(int(cik))  # EDGAR paths drop leading zeros
    nodash = accession.replace("-", "")
    dir_url = f"{base}/{cik_num}/{nodash}"
    return dir_url, dir_url


def _primary_document(dir_html: str) -> str:
    """Pick the 10-K main HTML document from a filing directory listing.

    The listing table rows are [filename, size, date]. The main document
    is the largest .htm/.html that is not the filing index, an exhibit,
    or an XBRL R-file (Citi names its instance *.htm, excluded below).
    """
    best = None
    best_size = -1
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", dir_html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)
        if len(cells) < 2:
            continue
        name = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if not name.lower().endswith((".htm", ".html")):
            continue
        low = name.lower()
        if "index" in low or "exhibit" in low or low.startswith("ex-"):
            continue
        if re.match(r"^r\d+\.html?$", low):
            continue
        # Citi's XBRL instance is named like c-20231231.htm; the main
        # 10-K doc is far larger. Use size to disambiguate.
        try:
            size = int(re.sub(r"[^\d]", "", cells[1]))
        except ValueError:
            size = 0
        if size > best_size:
            best, best_size = name, size
    if best is None:
        raise TableNotFoundError(
            "no 10-K main document found in filing directory listing")
    return best


def _main_docs_present(dest_dir: Path) -> list[Path]:
    """Main 10-K documents already on disk.

    Excludes XBRL R-files (R*.htm), which the Layer-1 worker stores in
    the same directory layout -- they are not the filing's main document.
    """
    if not dest_dir.exists():
        return []
    return sorted(p for p in dest_dir.glob("*.htm*")
                  if not re.match(r"R\d+\.htm$", p.name, re.IGNORECASE)
                  and "index" not in p.name.lower())


def fetch_filing(cik: str, accession: str,
                 raw_root: Path = RAW_ROOT,
                 limiter: RateLimiter | None = None,
                 base: str = EDGAR_ARCHIVES) -> Path:
    """Download a 10-K main document; skip when already on disk.

    Layout: ``<raw_root>/<cik>/<accession>/<primary-doc>``. Returns the
    local path of the main document. With ``base=ARCHIVE_BASE`` the file
    is located via the Wayback CDX API (for networks where EDGAR itself
    is unreachable); the document bytes are the archived original.
    """
    limiter = limiter or RateLimiter()
    dest_dir = raw_root / cik / accession
    existing = _main_docs_present(dest_dir)
    if existing:
        log.info("skip download (exists): %s/%s", cik, accession)
        return existing[0]
    dest_dir.mkdir(parents=True, exist_ok=True)
    if base == ARCHIVE_BASE:
        ts, orig, doc_name = _archive_main_doc(cik, accession, limiter)
        dest = dest_dir / doc_name
        if dest.exists():
            return dest
        url = f"https://web.archive.org/web/{ts}id_/{orig}"
        log.info("fetch archived document: %s", url)
        dest.write_bytes(_maybe_decompress(_http_get(url, limiter)))
        _sanity_check(dest, cik, accession)
        return dest
    index_url, dir_url = _filing_urls(cik, accession, base)
    log.info("fetch directory listing: %s", index_url)
    dir_html = _http_get(index_url, limiter).decode("utf-8", "replace")
    doc_name = _primary_document(dir_html)
    dest = dest_dir / doc_name
    if dest.exists():
        return dest
    doc_url = f"{dir_url}/{doc_name}"
    log.info("fetch document: %s", doc_url)
    dest.write_bytes(_http_get(doc_url, limiter))
    _sanity_check(dest, cik, accession)
    return dest


_ARCHIVE_DOC_EXCLUDE = re.compile(
    r"index\.htm|exhibit|formxof|[_-]exh\d", re.I)


def _maybe_decompress(data: bytes) -> bytes:
    """Decompress gzip transfer-encoded archive captures."""
    if data[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(data)
    return data


def _sanity_check(path: Path, cik: str, accession: str) -> None:
    """Reject obviously-bad downloads (rate-limit pages, truncations)."""
    size = path.stat().st_size
    head = path.read_bytes()[:400].lower()
    bad_markers = (b"rate limit", b"too many requests",
                   b"undeclared automated tool")
    if size < 20_000 or any(m in head for m in bad_markers):
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{cik}/{accession}: downloaded document failed sanity check "
            f"({size} bytes); removed, will retry later")


def _archive_main_doc(cik: str, accession: str,
                      limiter: RateLimiter) -> tuple[str, str, str]:
    """Locate a filing's main 10-K document via the Wayback Machine.

    Returns (timestamp, original_url, doc_name). First tries the filing's
    archived index.html (same document choice as the EDGAR path); falls
    back to the largest archived HTML that is not an exhibit or the XBRL
    instance (Citi names its instance *.htm).
    """
    import urllib.parse
    cik_num = str(int(cik))
    nodash = accession.replace("-", "")
    # The directory listing (not the index.html, which EDGAR's WAF
    # intermittently 403s) carries the [filename, size, date] table.
    dir_orig = (f"https://www.sec.gov/Archives/edgar/data/{cik_num}/"
                f"{nodash}/")
    q = ("https://web.archive.org/cdx/search/cdx?url="
         + urllib.parse.quote(dir_orig, safe="")
         + "&output=text&fl=timestamp,original&limit=5")
    try:
        lines = _http_get(q, limiter).decode("utf-8",
                                             "replace").splitlines()
    except RuntimeError:
        lines = []
    for line in lines:
        parts = line.split(" ")
        if len(parts) < 2:
            continue
        ts, orig = parts[0], parts[1]
        try:
            dir_html = _maybe_decompress(
                _http_get(f"https://web.archive.org/web/{ts}id_/{orig}",
                          limiter)).decode("utf-8", "replace")
            doc_name = _primary_document(dir_html)
            doc_orig = (f"https://www.sec.gov/Archives/edgar/data/"
                        f"{cik_num}/{nodash}/{doc_name}")
            return ts, doc_orig, doc_name
        except Exception:  # noqa: BLE001 -- fall through to CDX heuristic
            continue
    # Fallback: largest archived HTML in the filing directory.
    q = ("https://web.archive.org/cdx/search/cdx?url="
         + urllib.parse.quote(f"sec.gov/Archives/edgar/data/{cik_num}/"
                              f"{nodash}/*", safe="")
         + "&output=text&fl=timestamp,original,length"
           "&collapse=urlkey&limit=400")
    best = None
    best_10k = None
    for line in _http_get(q, limiter).decode("utf-8", "replace").splitlines():
        parts = line.split(" ")
        if len(parts) < 3:
            continue
        ts, orig, length = parts[0], parts[1], parts[2]
        name = orig.rsplit("/", 1)[-1]
        if not name.lower().endswith((".htm", ".html")):
            continue
        if _ARCHIVE_DOC_EXCLUDE.search(name):
            continue
        try:
            length = int(length)
        except ValueError:
            length = 0
        cand = (ts, orig, name, length)
        if best is None or length > best[3]:
            best = cand
        # The 10-K main document usually carries "10k" in its name;
        # prefer such candidates over exhibits with generic names.
        if "10k" in name.lower() and (
                best_10k is None or length > best_10k[3]):
            best_10k = cand
    best = best_10k or best
    if best is None:
        raise RuntimeError(
            f"archive: no main document found for {cik}/{accession}")
    return best[0], best[1], best[2]


# ---------------------------------------------------------------------------
# HTML table parsing
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_FOOTNOTE = re.compile(r"\s*[\(\[]\d+[\)\]]\s*$")


def normalize_label(text: str) -> str:
    """Lowercase, collapse whitespace, strip footnote markers."""
    t = _WS.sub(" ", text).strip().lower()
    t = _FOOTNOTE.sub("", t).strip()
    # Strip stray trailing digits left by superscript footnotes, e.g.
    # "FICC 1" (but keep meaningful numbers inside labels).
    t = re.sub(r"\s+\d$", "", t)
    # Strip dot/dash leaders ("Advisory ........").
    t = re.sub(r"[\s.·…\-–—]{2,}$", "", t).strip()
    return t


def _table_text(table) -> str:
    return table.get_text(" ", strip=True)


def _nearby_text(table, chars: int = 1500) -> str:
    """Text of preceding siblings/headings, for anchor + unit detection."""
    parts = []
    node = table
    for _ in range(10):
        node = node.find_previous_sibling()
        if node is None:
            break
        if getattr(node, "get_text", None):
            parts.append(node.get_text(" ", strip=True))
    # Also walk up: section headings often sit above the table's parent.
    parent = table.parent
    for _ in range(4):
        if parent is None:
            break
        sib = parent.find_previous_sibling()
        if sib is not None and getattr(sib, "get_text", None):
            parts.append(sib.get_text(" ", strip=True))
        parent = parent.parent
    return " ".join(parts)[-chars:]


def _row_labels(table) -> list[str]:
    labels = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if cells:
            labels.append(normalize_label(cells[0].get_text(" ", strip=True)))
    return labels


def locate_tables(soup: BeautifulSoup, spec: TableSpec,
                  firm: str, accession: str) -> list:
    """Find ALL MD&A tables matching a spec's anchors + required rows.

    Most specs resolve to one table; some filers (Jefferies) repeat the
    table once per year, in which case every matching table is returned.
    Raises TableNotFoundError when nothing matches -- never guesses.
    """
    found = []
    n_tables = len(soup.find_all("table"))
    anchor_hits = 0
    require_rx = [re.compile(rx, re.IGNORECASE)
                  for rx in spec.require_text]
    for anchor in spec.anchors:
        rx = re.compile(anchor, re.IGNORECASE)
        for table in soup.find_all("table"):
            if table in found:
                continue
            hay = _table_text(table) + " || " + _nearby_text(table)
            if not rx.search(hay):
                continue
            if any(not r.search(hay) for r in require_rx):
                continue
            anchor_hits += 1
            labels = _row_labels(table)
            if all(any(rs.matches(lb) for lb in labels) for rs in spec.rows):
                found.append(table)
        if found:
            break
    if not found:
        raise TableNotFoundError(
            f"{firm} {accession}: MD&A table '{spec.description}' not found. "
            f"{anchor_hits} table(s) matched the anchors "
            f"{spec.anchors} but none contained all required rows "
            f"{[rs.line for rs in spec.rows]} (checked {n_tables} tables: "
            f"table text + nearby headings). "
            + (f"require_text={spec.require_text} filtered some matches. "
               if spec.require_text else "")
            + "The filing's MD&A layout likely "
            "changed; update FIRM_SPECS anchors/rows rather than guessing.")
    return found


def locate_table(soup: BeautifulSoup, spec: TableSpec,
                 firm: str, accession: str):
    """Find the MD&A table matching a spec (first of locate_tables)."""
    return locate_tables(soup, spec, firm, accession)[0]


_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def detect_years(table) -> list[int]:
    """Ordered fiscal years from a table's header rows.

    Returns the years left-to-right as they first appear in the first
    several rows, so multi-row headers ("Year Ended December 31," /
    "2024 2023 2022") and "% Change" year repeats are handled. Raises
    YearColumnError when no year is found.
    """
    years: list[int] = []
    rows = table.find_all("tr")
    for tr in rows[:10]:
        for cell in tr.find_all(["th", "td"]):
            for m in _YEAR.finditer(cell.get_text(" ", strip=True)):
                year = int(m.group(0))
                if 1990 <= year <= 2100 and year not in years:
                    years.append(year)
        if len(years) >= 2:
            break
    if not years:
        raise YearColumnError(
            "no year columns detected in table header rows; first rows: "
            + " | ".join(tr.get_text(" ", strip=True)[:80]
                         for tr in rows[:3]))
    return years


def detect_year_columns(table) -> dict[int, int]:
    """Back-compat shim: year -> first column index (see detect_years)."""
    cols: dict[int, int] = {}
    for tr in table.find_all("tr")[:6]:
        cells = tr.find_all(["th", "td"])
        for idx, cell in enumerate(cells):
            for m in _YEAR.finditer(cell.get_text(" ", strip=True)):
                year = int(m.group(0))
                if 1990 <= year <= 2100:
                    cols.setdefault(year, idx)
    return cols


def detect_year_grid(table) -> dict[int, tuple[int, int]]:
    """Year -> (start_col, end_col) from the first multi-year header row.

    Grid columns accumulate colspans, so a year header spanning an
    "Amount + % of Net Revenues" block covers both its value and its
    percent cells. Raises YearColumnError when no header row carries
    two or more year labels (grid_columns specs require the header).
    Rowspans are not tracked: the tables this serves carry their
    rowspan cells (e.g. a "% Change from Prior Year" corner cell) to
    the right of every year column, so flat accumulation stays
    aligned for the columns that matter.
    """
    for tr in table.find_all("tr")[:10]:
        cells = tr.find_all(["th", "td"])
        years: list[tuple[int, int, int]] = []  # (year, start, span)
        col = 0
        for cell in cells:
            span = max(int(cell.get("colspan") or 1), 1)
            m = _YEAR.search(cell.get_text(" ", strip=True))
            if m and 1990 <= int(m.group(0)) <= 2100:
                years.append((int(m.group(0)), col, span))
            col += span
        if len(years) >= 2:
            return {y: (c, c + s) for y, c, s in years}
    raise YearColumnError(
        "grid_columns spec found no header row with two or more year "
        "labels; first rows: " + " | ".join(
            tr.get_text(" ", strip=True)[:80]
            for tr in table.find_all("tr")[:3]))


_UNIT_RES = [
    (re.compile(r"\$\s*in\s+millions", re.I), 1.0),
    (re.compile(r"in\s+millions", re.I), 1.0),
    (re.compile(r"\$\s*in\s+thousands", re.I), 0.001),
    (re.compile(r"in\s+thousands", re.I), 0.001),
    (re.compile(r"\$\s*in\s+billions", re.I), 1000.0),
    (re.compile(r"in\s+billions", re.I), 1000.0),
]


def _preceding_text(table, chars=3000):
    """Text before the table in document order (up to `chars`)."""
    from bs4 import NavigableString
    parts = []
    total = 0
    for el in table.previous_elements:
        if total >= chars:
            break
        if isinstance(el, NavigableString):
            t = el.strip()
            if t:
                parts.append(t)
                total += len(t) + 1
    return " ".join(reversed(parts))  # document order


def _nearest_unit(hay: str) -> float | None:
    """Multiplier of the unit marker nearest the end of `hay`.

    `hay` is ordered so its end is nearest the table (head text, nearby
    tail, preceding document text).
    """
    best = None  # (position, multiplier)
    for rx, mult in _UNIT_RES:
        for m in rx.finditer(hay):
            if best is None or m.start() > best[0]:
                best = (m.start(), mult)
    return best[1] if best else None


def detect_unit_multiplier(table) -> float:
    """USD-millions multiplier from the table's stated units.

    Only markers BEFORE the first data row count: a table may contain
    later sections with different units (e.g. Citi's "Key drivers (in
    billions of dollars)" after an unstated-millions revenue split).
    Search order: table head, nearby headings, preceding document text
    (the section's declared unit, e.g. a segment income statement's "In
    millions of dollars" heading a sub-table). Within each tier the
    marker nearest the table wins.
    """
    text = _table_text(table)
    # Cut the table text at the first data row (first tr with a parseable
    # money value past the label cell). Bare 4-digit years don't count --
    # header rows like "2023 2022 2021" must not trigger the cut.
    _YEAR_CELL = re.compile(r"^(19|20)\d{2}$")
    cut = len(text)
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2 and any(
                parse_money(c.get_text(" ", strip=True)) is not None
                and not _YEAR_CELL.match(c.get_text(" ", strip=True))
                for c in cells[1:]):
            row_head = " ".join(c.get_text(" ", strip=True)
                                for c in cells[:2])[:60]
            i = text.find(row_head[:40])
            if i >= 0:
                cut = i
            break
    head = text[:cut]
    for hay in (head[:4000], _nearby_text(table, 3000),
                _preceding_text(table, 3000)):
        mult = _nearest_unit(hay)
        if mult is not None:
            return mult
    raise UnitAmbiguousError(
        "dollar units not stated near MD&A table (looked for "
        "'in millions/thousands/billions' in table text and nearby "
        "headings). Refusing to guess the scale.")


_MONEY = re.compile(r"^[^\d\(\-—–]*")


def parse_money(text: str) -> float | None:
    """Parse a money cell to a float. Returns None for missing values.

    Handles "$7,973", "(123)" negatives, em-dash/blank/"NM" placeholders.
    """
    t = _WS.sub(" ", text).strip()
    if not t or t in {"—", "–", "-", "--", "NM", "n/m", "N/M", "$", "$—"}:
        return None
    t = _MONEY.sub("", t).strip()  # drop leading $, etc. (may precede parens)
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    t = t.replace(",", "").strip()
    # Trailing footnote digits, e.g. "7,973 1".
    t = re.sub(r"\s+\d$", "", t).strip()
    if not t or t in {"—", "–", "-"}:
        return None
    try:
        val = float(t)
    except ValueError:
        return None
    return -val if neg else val


def _year_band_rows(table) -> list[tuple[int, object]]:
    """Split a table into (year, tr) using year band header rows.

    Some tables (e.g. Jefferies' revenue disaggregation) group rows under
    year sub-headers instead of year columns. A band row is a row whose
    text names exactly one 4-digit year and whose cells carry no money
    values (e.g. "Year Ended November 30, 2024" as a sub-header).
    """
    bands: list[tuple[int, object]] = []
    current: int | None = None
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row_text = " ".join(c.get_text(" ", strip=True) for c in cells)
        years = [m.group(0) for m in _YEAR.finditer(row_text)]
        has_value = any(
            parse_money(c.get_text(" ", strip=True)) is not None
            for c in cells[1:])
        if len(years) == 1 and not has_value:
            current = int(years[0])
            continue
        if current is not None:
            bands.append((current, tr))
    return bands


def _row_slots(cells: list) -> list[list[str]]:
    """Group a data row's post-label cells into per-column slots.

    Inline-XBRL MD&A tables separate logical columns with empty spacer
    cells and/or lone "$" cells (e.g. ``$ | 3,299 | '' | $ | 4,704``).
    A new slot starts at each "$" cell and after each empty cell, so a
    slot holds one logical column's content (value, footnote, or % change).
    """
    texts = [c.get_text(" ", strip=True) for c in cells]
    slots: list[list[str]] = []
    cur: list[str] = []
    for t in texts:
        if t == "$":
            if cur:
                slots.append(cur)
            cur = [t]
        elif t == "":
            if cur:
                slots.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        slots.append(cur)
    return slots


def _slot_value(slot: list[str]) -> float | None:
    """Value of one slot: first money cell, or None if explicitly missing.

    A slot holding a lone "%" marker cell is a percentage column (inline
    XBRL splits "36.8" and "%" into separate cells, e.g. Jefferies'
    "% of Net Revenues" columns), not a money value: the whole slot is
    skipped, not just the marker.
    """
    if any(t == "%" for t in slot):
        return None
    for t in slot:
        if t in {"$", ""} or "%" in t:
            continue
        if re.fullmatch(r"\d{1,2}", t):
            continue  # footnote-only cell, not a value
        v = parse_money(t)
        if v is not None:
            return v
        if t in {"—", "–", "-", "--", "NM", "n/m", "N/M"}:
            return None
    return None


def _money_slots(cells: list) -> list[float | None]:
    """Value slots with percent columns dropped (see _slot_value).

    Inline-XBRL tables interleave "% of Net Revenues" / "% Change"
    columns between the period value columns (Jefferies' revenues-by-
    source tables); a percent slot would push the following period's
    value out of the positional zip. Two layouts:

    - Split cells ("$ | 36.8 | %"): the percent is its own slot; a
      percent-tainted slot with at most one money cell is a percent
      column -- dropped entirely.
    - Separator-free rows ("$ 397,780 | 25.0 % | $ 338,567 | 19.5 %"):
      no "$"/empty split cells, so the whole row slots into one group
      holding values AND percent cells; there the percent cells are
      dropped and the money cells kept, or every period after the
      first would lose its value.
    """
    out: list[float | None] = []
    for s in _row_slots(cells):
        if not any("%" in t for t in s):
            out.append(_slot_value(s))
            continue
        monies = [parse_money(t) for t in s
                  if "%" not in t and not re.fullmatch(r"\d{1,2}", t)]
        monies = [m for m in monies if m is not None]
        if len(monies) > 1:
            out.extend(monies)  # separator-free row: keep the values
        # else: a percent column -- never a money value; dropped
    return out


def extract_spec_rows(table, spec: TableSpec,
                      years: list[int],
                      unit_mult: float) -> list[dict]:
    """Extract (year, line, value_m) rows for one table spec.

    ``years`` is the ordered year list from detect_years(); values are
    zipped positionally. A row whose slot count disagrees with the year
    count is skipped with a loud warning -- never force-fit.
    """
    # Collect candidate (tr) per line, honoring occurrence="last".
    found: dict[str, list] = {rs.line: [] for rs in spec.rows}
    band_mode = not years
    band_rows = _year_band_rows(table) if band_mode else []
    trs = [tr for _, tr in band_rows] if band_mode else table.find_all("tr")
    tr_year = {id(tr): y for y, tr in band_rows} if band_mode else {}
    for tr in trs:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        label = normalize_label(cells[0].get_text(" ", strip=True))
        if not label:
            continue
        for rs in spec.rows:
            if rs.matches(label):
                found[rs.line].append(tr)
    missing = [rs.line for rs in spec.rows if not found[rs.line]]
    if missing:
        raise TableNotFoundError(
            f"table '{spec.description}': rows not found for lines "
            f"{missing}. Labels seen (first col): "
            + str(_row_labels(table)[:25]))
    out = []
    # Header-grid column mapping (grid_columns specs): year -> column
    # range from the header row's colspans; see TableSpec.grid_columns.
    grid = None
    if spec.grid_columns:
        if band_mode:
            raise YearColumnError(
                f"table '{spec.description}': grid_columns spec on a "
                "year-band table (no year header row)")
        grid = detect_year_grid(table)
    for rs in spec.rows:
        if rs.pin_only:
            # Presence already enforced above (locate_tables/missing check
            # below); pins never emit records.
            if not found[rs.line]:
                raise TableNotFoundError(
                    f"table '{spec.description}': pin row '{rs.line}' "
                    "vanished between locate and extract")
            continue
        tr = found[rs.line][-1] if rs.occurrence == "last" else found[rs.line][0]
        cells = tr.find_all(["th", "td"])
        if band_mode:
            year = tr_year[id(tr)]
            # Year-band tables (Jefferies) lay out ASC 606 disaggregation
            # columns; the line total is the last parseable value cell.
            val = None
            for cell in reversed(cells[1:]):
                val = parse_money(cell.get_text(" ", strip=True))
                if val is not None:
                    break
            if val is not None:
                out.append({"taxonomy_line": rs.line, "year": year,
                            "net_revenue_m": round(val * unit_mult, 3)})
            continue
        if grid is not None:
            # Grid mode: each year's value is the first parseable money
            # cell within the year's header column range. The "$" split
            # cells and percent cells inside the range parse as None or
            # sit after the Amount cell, so the first hit is the value.
            for year in sorted(grid, key=lambda y: grid[y][0]):
                c0, c1 = grid[year]
                val = None
                col = 0
                for idx, cell in enumerate(cells):
                    span = max(int(cell.get("colspan") or 1), 1)
                    if idx > 0 and c0 <= col < c1:
                        v = parse_money(cell.get_text(" ", strip=True))
                        if v is not None:
                            val = v
                            break
                    col += span
                if val is not None:
                    out.append({"taxonomy_line": rs.line, "year": year,
                                "net_revenue_m": round(val * unit_mult, 3)})
            continue
        slots = _money_slots(cells[1:])
        if len(slots) < len(years):
            log.warning("table '%s': row '%s' has %d slots for %d years %s; "
                        "skipping row (not force-fitting)",
                        spec.description, rs.line, len(slots), len(years),
                        years)
            continue
        # Wide tables may repeat a column group per year (e.g. Jefferies'
        # FY2023 disaggregation: [IB&CM, Asset Mgmt, Total] x 3 years).
        # When slots are an integer multiple of years, the layout decides
        # which columns are the wanted years:
        #   year_major (default): groups are years -> first sub-column of
        #       each year's group (slots[::k]).
        #   segment_major: groups are segments, each holding all year
        #       columns -> the first segment group's columns (slots[:n]).
        if len(slots) > len(years) and len(slots) % len(years) == 0:
            k = len(slots) // len(years)
            if spec.column_layout == "segment_major":
                b = spec.segment_block
                start = b * len(years)
                log.info("table '%s': row '%s' has %d slots for %d years; "
                         "taking segment block %d's %d columns",
                         spec.description, rs.line, len(slots), len(years),
                         b, len(years))
                slots = slots[start:start + len(years)]
            else:
                log.info("table '%s': row '%s' has %d slots for %d years; "
                         "taking every %d-th (first sub-column per year)",
                         spec.description, rs.line, len(slots), len(years),
                         k)
                slots = slots[::k]
        # MD&A layout convention: year values come first, % change /
        # variance columns trail. Take the first len(years) slots.
        for year, val in zip(years, slots[:len(years)]):
            if val is None:
                continue  # undisclosed cell: emit nothing, never estimate
            out.append({"taxonomy_line": rs.line, "year": year,
                        "net_revenue_m": round(val * unit_mult, 3)})
    return out


_FY_RE = re.compile(
    r"for the fiscal year ended\s+[a-z]+\s+\d{1,2},?\s+(\d{4})"
    r"|for the year ended\s+[a-z]+\s+\d{1,2},?\s+(\d{4})", re.I)

# 10-Q covers: "For the quarterly period ended June 30, 2026". Without
# this, a 10-Q's fiscal year falls back to the instance filename, which
# is None for filing-agent instance names (GS's "d105283d10q_htm.xml"),
# and fiscal-year windows are then bypassed ("applied anyway") instead of
# enforced.
_FQ_RE = re.compile(
    r"quarterly period ended\s+[a-z]+\s+\d{1,2},?\s+(\d{4})", re.I)

_DT_RE = re.compile(rb"<dei:DocumentType[^>]*>\s*([^<]+?)\s*</")
_DPE_RE = re.compile(
    rb"<dei:DocumentPeriodEndDate[^>]*>\s*([^<]+?)\s*</")


def _instance_field(acc_dir, rx: re.Pattern) -> str | None:
    """First match of `rx` across an accession's XBRL instances.

    Scans instance files (skipping the R*.htm rendering files and
    FilingSummary.xml) and returns the first captured value, decoded.
    """
    acc_dir = Path(acc_dir)
    if not acc_dir.is_dir():
        return None
    for p in sorted(acc_dir.iterdir()):
        if p.name.startswith("R") or p.name == "FilingSummary.xml":
            continue
        if p.suffix.lower() not in (".xml", ".xbrl"):
            continue
        try:
            m = rx.search(p.read_bytes())
        except OSError:
            continue
        if m:
            return m.group(1).decode("utf-8", "replace").strip()
    return None


def dir_form_type(acc_dir) -> str | None:
    """'10-Q', '10-K', '10-Q/A'... from an accession directory.

    Reads dei:DocumentType from the accession's XBRL instance files
    (the first instance that states it). This is the definitive form
    type: instance *filenames* are not (Goldman Sachs's 2019-2022
    filings via a filing agent name instances "d105283d10q_htm.xml",
    with no period date, so filename heuristics cannot classify them).
    Returns None when no instance states a document type (e.g.
    pre-XBRL-era filings).
    """
    return _instance_field(acc_dir, _DT_RE)


def dir_period_end(acc_dir) -> str | None:
    """'2020-03-31' from an accession's dei:DocumentPeriodEndDate.

    The definitive period end for the filing; unlike instance filenames
    it survives filing-agent naming conventions. None for pre-XBRL-era
    accessions.
    """
    return _instance_field(acc_dir, _DPE_RE)


def instance_form_type(cik: str, accession: str) -> str | None:
    """dir_form_type for a (cik, accession) under RAW_ROOT."""
    return dir_form_type(RAW_ROOT / cik / accession)


def _instance_fiscal_year(cik: str, accession: str) -> int | None:
    """Fiscal year from the accession's XBRL instance filename.

    Filers name instances in both orders: "c-20251231_htm.xml" is
    YYYYMMDD, but Citi's older "c-12312019x10k_htm.xml" is MMDDYYYY.
    The reading that yields a plausible calendar year and month/day
    wins; R-files and FilingSummary.xml carry no date and are skipped.
    """
    d = RAW_ROOT / cik / accession
    if not d.is_dir():
        return None

    def _plausible(y: int, mo: int, dy: int) -> bool:
        return 1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= dy <= 31

    for p in sorted(d.iterdir()):
        if p.name.startswith("R") or p.name == "FilingSummary.xml":
            continue
        if p.suffix.lower() not in (".xbrl", ".xml"):
            continue
        m = re.search(r"\d{8}", p.name)
        if not m:
            continue
        run = m.group(0)
        if _plausible(int(run[:4]), int(run[4:6]), int(run[6:8])):
            return int(run[:4])
        if _plausible(int(run[4:]), int(run[:2]), int(run[2:4])):
            return int(run[4:])
    return None


def filing_fiscal_year(soup: BeautifulSoup, cik: str = "",
                       accession: str = "") -> int | None:
    """The filing's fiscal year.

    From the cover ("For the fiscal year ended December 31, 2025" for a
    10-K; "For the quarterly period ended March 31, 2026" for a 10-Q).
    The 10-K phrase is matched in the first 6,000 characters (its cover
    is near the top); 10-Q covers are matched over the whole document
    because front-loaded TOC/XBRL blocks can push them far down (GS's
    2020-2022 10-Q cover pages start ~200k characters in). Falls back to
    the accession's dei:DocumentPeriodEndDate year, then to the XBRL
    instance filename. Returns None when none yields a year.
    """
    text = soup.get_text(" ", strip=True)
    m = _FY_RE.search(text[:6000])
    if m:
        return int(m.group(1) or m.group(2))
    m = _FQ_RE.search(text)
    if m:
        return int(m.group(1))
    if cik and accession:
        pe = dir_period_end(RAW_ROOT / cik / accession)
        if pe and len(pe) >= 4 and pe[:4].isdigit():
            return int(pe[:4])
        return _instance_fiscal_year(cik, accession)
    return None


def parse_filing(firm: str, cik: str, accession: str,
                 html_path: Path) -> list[dict]:
    """Scrape all spec'd MD&A tables from one saved 10-K document."""
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(html_path.read_bytes(), "lxml")
    fiscal_year = filing_fiscal_year(soup, cik, accession)
    if fiscal_year is not None and not 1990 <= fiscal_year <= 2100:
        # A garbage year (e.g. filename misread) silently skipped every
        # spec once; treat it as unknown so specs apply and mismatches
        # fail loudly instead of contributing nothing.
        log.warning("%s %s: implausible fiscal year %s from filing "
                    "metadata; treating as unknown", firm, accession,
                    fiscal_year)
        fiscal_year = None
    records = []
    for spec in FIRM_SPECS[firm]:
        if spec.fiscal_years is not None:
            if fiscal_year is None:
                log.warning("%s %s: cannot determine fiscal year; "
                            "applying '%s' anyway", firm, accession,
                            spec.description)
            elif not (spec.fiscal_years[0] <= fiscal_year
                      <= spec.fiscal_years[1]):
                log.info("%s %s: skipping '%s' (fiscal %d outside %s)",
                         firm, accession, spec.description, fiscal_year,
                         spec.fiscal_years)
                continue
        tables = locate_tables(soup, spec, firm, accession)
        if not spec.multi and len(tables) > 1:
            log.info("%s %s: '%s' matched %d tables; using the first",
                     firm, accession, spec.description, len(tables))
            tables = tables[:1]
        for table in tables:
            try:
                years = detect_years(table)
            except YearColumnError:
                if spec.implied_years and fiscal_year is not None:
                    years = [fiscal_year, fiscal_year - 1, fiscal_year - 2]
                    log.info("%s %s: '%s' has no year headers; using "
                             "implied fiscal years %s", firm, accession,
                             spec.description, years)
                else:
                    years = []  # year-band layout; resolved in extract
            unit_mult = detect_unit_multiplier(table)
            rows = extract_spec_rows(table, spec, years, unit_mult)
            # Sanity: no business-line revenue exceeds $300B. A "billions"
            # marker picked up from a neighboring section (e.g. a balance-
            # sheet block) blows values past it; in that case the marker
            # did not apply to this table, so retry as millions -- loudly.
            if rows and unit_mult == 1000.0 and max(
                    abs(r["net_revenue_m"]) for r in rows) > 300_000:
                log.warning(
                    "%s %s: '%s': 'billions' unit gives absurd values "
                    "(max $%.0fM); retrying as millions",
                    firm, accession, spec.description,
                    max(abs(r["net_revenue_m"]) for r in rows))
                rows = extract_spec_rows(table, spec, years, 1.0)
            # Derived lines: sum components per year (e.g. BofA pre-2016
            # "Debt issuance" + "Equity issuance" -> ecm_dcm_combined).
            if spec.derived and rows:
                by_year: dict[int, dict[str, float]] = {}
                for r in rows:
                    by_year.setdefault(r["year"], {})[r["taxonomy_line"]] = \
                        r["net_revenue_m"]
                for target, comps in spec.derived.items():
                    for yr, vals in by_year.items():
                        if all(c in vals for c in comps):
                            rows.append({
                                "taxonomy_line": target,
                                "year": yr,
                                "net_revenue_m": sum(vals[c] for c in comps),
                            })
                # Drop the temporary component lines (underscore-prefixed).
                comp_lines = {c for comps in spec.derived.values()
                              for c in comps}
                rows = [r for r in rows
                        if r["taxonomy_line"] not in comp_lines]
            source = f"mda:{spec.description}"
            for r in rows:
                records.append({"firm": firm, "cik": cik, **r,
                                "source": source, "accession": accession})
            log.info("%s %s: '%s' -> %d cells (%s)",
                     firm, accession, spec.description, len(rows),
                     sorted({r['year'] for r in rows}))
    return records


# ---------------------------------------------------------------------------
# Filing enumeration + dedupe + output
# ---------------------------------------------------------------------------

def _enumerate_10k_submissions(cik: str, since: str,
                               limiter: RateLimiter) -> list[dict] | None:
    """List 10-K accessions for a CIK via the data.sec.gov submissions API.

    Returns None on any fetch/parse failure (caller falls back to
    full-text search). The API's `filings.recent` block holds only the
    latest ~1000 filings; older ones live in the continuation files
    listed under `filings.files`, which are fetched and merged.
    """
    try:
        data = json.loads(_http_get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            limiter).decode("utf-8"))
        pages = [data["filings"]["recent"]]
        for extra in data["filings"].get("files", []):
            pages.append(json.loads(_http_get(
                "https://data.sec.gov/submissions/" + extra["name"],
                limiter).decode("utf-8")))
    except Exception as e:  # noqa: BLE001 -- fall back, don't crash the run
        log.warning("submissions API failed for %s (%s); "
                    "falling back to full-text search", cik, e)
        return None
    out = []
    for page in pages:
        for acc, form, fdate, period in zip(
                page.get("accessionNumber", []),
                page.get("form", []),
                page.get("filingDate", []),
                page.get("reportDate", [])):
            if form != "10-K" or fdate < since:
                continue
            out.append({"accession": acc, "file_date": fdate,
                        "period_ending": period})
    return out


def enumerate_10k(cik: str, since: str = "2011-01-01",
                  limiter: RateLimiter | None = None) -> list[dict]:
    """List 10-K accessions for a CIK.

    Primary source: the data.sec.gov submissions API (complete and
    exact per-CIK form lists). Fallback: EDGAR full-text search by
    company name, for environments without data.sec.gov access. The
    fallback is a relevance-ranked name query capped at 100 hits --
    broad names ("Citigroup") drown the firm's own 10-Ks under
    same-named trusts' filings, so it must never be the primary.
    """
    limiter = limiter or RateLimiter()
    out = _enumerate_10k_submissions(cik, since, limiter)
    if out is None:
        import urllib.request
        import urllib.parse
        q = urllib.parse.quote(f'"{FIRMS[[k for k, v in FIRMS.items() if v["cik"] == cik][0]]["name"]}"')
        url = ("https://efts.sec.gov/LATEST/search-index?q=" + q
               + "&dateRange=custom&startdt=" + since
               + "&enddt=2100-01-01&forms=10-K&size=100")
        data = json.loads(_http_get(url, limiter).decode("utf-8"))
        out = []
        for h in data["hits"]["hits"]:
            s = h["_source"]
            if s.get("form") != "10-K":
                continue
            if cik.lstrip("0") not in [c.lstrip("0") for c in s.get("ciks", [])]:
                continue
            out.append({"accession": s["adsh"], "file_date": s["file_date"],
                        "period_ending": s.get("period_ending")})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: r["file_date"]):
        if r["accession"] not in seen:
            seen.add(r["accession"])
            uniq.append(r)
    return uniq


def dedupe_records(records: list[dict]) -> list[dict]:
    """Dedupe (firm, line, year) across overlapping filings.

    The later filing's restated value wins; every override is logged.
    "Later" is the accession's (filing year, sequence) — accession
    strings do not sort chronologically across filing-agent prefixes.
    """
    best: dict[tuple, dict] = {}
    for r in records:
        key = (r["firm"], r["taxonomy_line"], r["year"])
        prev = best.get(key)
        if prev is None or (accession_order_key(r["accession"])
                            >= accession_order_key(prev["accession"])):
            if prev is not None and prev["net_revenue_m"] != r["net_revenue_m"]:
                log.info("restatement: %s %s %s %s -> %s (%s supersedes %s)",
                         r["firm"], r["taxonomy_line"], r["year"],
                         prev["net_revenue_m"], r["net_revenue_m"],
                         r["accession"], prev["accession"])
            best[key] = r
    return sorted(best.values(),
                  key=lambda r: (r["firm"], r["taxonomy_line"], r["year"]))


def build_csv(firms: Iterable[str], accessions: dict[str, list[str]] | None,
              out_csv: Path = OUT_CSV,
              raw_root: Path = RAW_ROOT,
              mirror: str = "edgar",
              since: str = "2010-01-01") -> pl.DataFrame:
    """Fetch, parse, dedupe and write the MD&A subsplits CSV."""
    base = EDGAR_ARCHIVES if mirror == "edgar" else ARCHIVE_BASE
    limiter = RateLimiter()
    records: list[dict] = []
    for firm in firms:
        if firm not in FIRM_SPECS:
            log.warning("%s: no MD&A specs (XBRL-covered or unmappable); "
                        "skipping", firm)
            continue
        cik = FIRMS[firm]["cik"]
        accs = (accessions[firm] if accessions and firm in accessions
                else [f["accession"] for f in
                      enumerate_10k(cik, since=since, limiter=limiter)])
        for acc in accs:
            try:
                html_path = fetch_filing(cik, acc, raw_root, limiter, base)
            except Exception as e:  # noqa: BLE001 -- per-filing isolation
                log.error("%s %s: download failed: %s", firm, acc, e)
                continue
            try:
                records.extend(parse_filing(firm, cik, acc, html_path))
            except (TableNotFoundError, UnitAmbiguousError,
                    YearColumnError) as e:
                log.error("%s", e)
    deduped = dedupe_records(records)
    df = pl.DataFrame(deduped, schema={
        "firm": pl.String, "cik": pl.String, "taxonomy_line": pl.String,
        "year": pl.Int64, "net_revenue_m": pl.Float64,
        "source": pl.String, "accession": pl.String,
    }).sort(["firm", "taxonomy_line", "year"])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(out_csv)
    log.info("wrote %s: %d rows", out_csv, len(df))
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--firms", default=",".join(FIRM_SPECS),
                    help="comma-separated firm codes (default: all spec'd)")
    ap.add_argument("--accessions", default=None,
                    help="JSON file mapping firm -> [accessions]; "
                         "default: enumerate via the data.sec.gov "
                         "submissions API")
    ap.add_argument("--since", default="2010-01-01",
                    help="earliest filing date to enumerate (default "
                         "2010-01-01: the FY2009 10-Ks filed in early "
                         "2010 carry the restated 2007-2009 cells in "
                         "the committed panel)")
    ap.add_argument("--out", default=str(OUT_CSV))
    ap.add_argument("--raw-root", default=str(RAW_ROOT))
    ap.add_argument("--mirror", choices=["edgar", "archive"], default="edgar",
                    help="'archive' fetches the same filings via the "
                         "Internet Archive (for networks where EDGAR "
                         "itself is unreachable)")
    ap.add_argument("--max-filings", type=int, default=None,
                    help="cap filings per firm (most recent N)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    firms = [f.strip().upper() for f in args.firms.split(",") if f.strip()]
    accessions = None
    if args.accessions:
        accessions = json.loads(Path(args.accessions).read_text())
    if args.max_filings and accessions:
        accessions = {f: accs[-args.max_filings:]
                      for f, accs in accessions.items()}
    df = build_csv(firms, accessions, Path(args.out),
                   Path(args.raw_root), args.mirror)
    print(f"rows: {len(df)}, firms: {df['firm'].n_unique()}, "
          f"lines: {sorted(df['taxonomy_line'].unique())}, "
          f"years: {df['year'].min()}-{df['year'].max()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
