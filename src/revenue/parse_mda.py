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
            anchors=[r"investment banking fees"],
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity underwriting"]),
                RowSpec("dcm", [r"debt underwriting"]),
            ],
        ),
        TableSpec(
            description="Global Banking & Markets net revenues",
            anchors=[r"global banking.+markets"],
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
            # Validated 2015+; the 2010-2014 10-Ks split S&T and IB into
            # separate tables (see pre-2015 specs below).
            fiscal_years=(2015, 2100),
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
            # The same IS income statement table: the FIRST "Equity" /
            # "Fixed income" rows are the underwriting splits (the
            # investment-banking section precedes sales & trading), and
            # "Advisory" appears once. Validated 2015-2025; older filings
            # use the pre-2015 specs below.
            description="Institutional Securities investment banking",
            anchors=[r"institutional securities",
                     r"income statement information"],
            fiscal_years=(2015, 2100),
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity"], occurrence="first"),
                RowSpec("dcm", [r"fixed income"], occurrence="first"),
            ],
        ),
        TableSpec(
            # Pre-2015 era (FY2010-FY2014 filings): Sales & Trading net
            # revenues are in a standalone table titled e.g. "Sales and
            # Trading Net Revenues" with rows "Equity" and "Fixed income
            # and commodities". Validated on FY2012 10-K.
            description="Institutional Securities sales and trading (pre-2015)",
            anchors=[r"sales and trading net revenues"],
            fiscal_years=(2010, 2014),
            rows=[
                RowSpec("ficc", [r"fixed income and commodities"]),
                RowSpec("equities", [r"^equity$"]),
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
            # and omit year headers (columns are FY, FY-1, FY-2).
            anchors=[r"business metrics", r"investment banking fees"],
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
            # (BofA does not split underwriting further). Validated 2016+.
            description="Noninterest income by business segment",
            anchors=[r"noninterest income by business segment"],
            require_text=[r"total corporation"],
            column_layout="segment_major",
            fiscal_years=(2016, 2100),
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
            # Jefferies discloses the split in the segment note's ASC 606
            # revenue disaggregation (year band rows, $ in thousands), not
            # in a year-column MD&A table. Underwriting is ECM+DCM
            # combined; Fixed income is the only separately disclosed FICC
            # line (single-digit $M; the rest is buried).
            description="Investment Banking and Capital Markets "
                        "revenues by source",
            anchors=[r"investment banking"],
            # Jefferies repeats the disaggregation once per fiscal year;
            # extract from every matching table.
            multi=True,
            rows=[
                RowSpec("advisory",
                        [r"investment banking\s*[–—-]\s*advisory"]),
                RowSpec("ecm_dcm_combined",
                        [r"investment banking\s*[–—-]\s*underwriting"]),
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


def _http_get(url: str, limiter: RateLimiter, retries: int = 6) -> bytes:
    import requests
    # requests (not urllib): EDGAR's WAF 403s urllib's fingerprint on
    # some paths (verified 2026-10-17). Headers mirror download.py.
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip",
               "Host": "www.sec.gov" if "www.sec.gov" in url
                       else "web.archive.org"}
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
    """Value of one slot: first money cell, or None if explicitly missing."""
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


def _value_slots(cells: list) -> list[float | None]:
    """One entry per slot (see _row_slots); None = explicitly missing."""
    return [_slot_value(s) for s in _row_slots(cells)]


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
    for rs in spec.rows:
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
        slots = _value_slots(cells[1:])
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


def _instance_fiscal_year(cik: str, accession: str) -> int | None:
    """Fiscal year from the accession's XBRL instance filename.

    e.g. "c-20251231_htm.xml" -> 2025. R-files and FilingSummary.xml
    carry no date and are skipped.
    """
    d = RAW_ROOT / cik / accession
    if not d.is_dir():
        return None
    for p in sorted(d.iterdir()):
        if p.name.startswith("R") or p.name == "FilingSummary.xml":
            continue
        if p.suffix.lower() not in (".xbrl", ".xml"):
            continue
        m = re.search(r"(\d{4})(\d{2})(\d{2})", p.name)
        if m:
            return int(m.group(1))
    return None


def filing_fiscal_year(soup: BeautifulSoup, cik: str = "",
                       accession: str = "") -> int | None:
    """The 10-K's fiscal year.

    From the cover ("For the fiscal year ended December 31, 2025"),
    falling back to the accession's XBRL instance filename. Returns None
    when neither yields a year.
    """
    text = soup.get_text(" ", strip=True)[:6000]
    m = _FY_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    if cik and accession:
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

def enumerate_10k(cik: str, since: str = "2011-01-01",
                  limiter: RateLimiter | None = None) -> list[dict]:
    """List 10-K accessions for a CIK via EDGAR full-text search.

    Used instead of the submissions API so environments without
    data.sec.gov access can still enumerate filings.
    """
    import urllib.request
    import urllib.parse
    limiter = limiter or RateLimiter()
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
    """
    best: dict[tuple, dict] = {}
    for r in records:
        key = (r["firm"], r["taxonomy_line"], r["year"])
        prev = best.get(key)
        if prev is None or (r["accession"],) >= (prev["accession"],):
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
              mirror: str = "edgar") -> pl.DataFrame:
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
                else [f["accession"] for f in enumerate_10k(cik, limiter=limiter)])
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
                         "default: enumerate via EDGAR full-text search")
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
