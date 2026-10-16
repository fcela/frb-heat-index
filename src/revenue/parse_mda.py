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
here (MS advisory/underwriting, JPM IB fees by type, BofA IB fees by
segment, WFC IB fees by segment). Lines a firm does not disclose are
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

USER_AGENT = ("frb-heat-index/1.0 (public research; "
              "contact: https://github.com/fcela/frb-heat-index)")

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
            anchors=[r"institutional securities"],
            rows=[
                # Advisory/underwriting is XBRL-covered (R189) -- skipped.
                # "Equity"/"Fixed income" label BOTH the underwriting and
                # the sales & trading rows; the sales & trading rows come
                # last, hence occurrence="last".
                RowSpec("ficc", [r"fixed income"], exclude=[r"underwriting"],
                        occurrence="last"),
                RowSpec("equities", [r"equity"], exclude=[r"underwriting"],
                        occurrence="last"),
            ],
        ),
    ],
    "C": [
        TableSpec(
            description="Banking business metrics - investment banking fees",
            anchors=[r"business metrics"],
            rows=[
                RowSpec("advisory", [r"advisory"]),
                RowSpec("ecm", [r"equity underwriting"]),
                RowSpec("dcm", [r"debt underwriting"]),
            ],
        ),
        TableSpec(
            description="Markets revenue by component",
            anchors=[r"revenue by component"],
            rows=[
                RowSpec("ficc", [r"fixed income markets?"]),
                RowSpec("equities", [r"equity markets?"]),
            ],
        ),
    ],
    "BAC": [
        TableSpec(
            description="Global Markets sales and trading revenue",
            anchors=[r"sales and trading"],
            rows=[
                RowSpec("ficc",
                        [r"fixed income,?\s+currencies( and|,)?\s+commodities",
                         r"ficc"]),
                RowSpec("equities", [r"equities"]),
            ],
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
            description="Revenue disaggregation - Financial Advisory",
            anchors=[r"financial advisory"],
            rows=[
                RowSpec("advisory", [r"financial advisory"]),
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
}

# JPMorgan's Fixed Income / Equities split is expected in the XBRL R-files
# (taxonomy: direct). JPM is therefore not in FIRM_SPECS; the build script
# verifies the R-file coverage and only then skips the MD&A layer.


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


def _http_get(url: str, limiter: RateLimiter) -> bytes:
    import urllib.request
    limiter.wait()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001 -- surfaced with context below
        raise RuntimeError(f"GET failed for {url}: {e}") from e


def _filing_urls(cik: str, accession: str, base: str = EDGAR_ARCHIVES
                 ) -> tuple[str, str]:
    """Return (index_json_url, directory_url) for a filing."""
    cik_num = str(int(cik))  # EDGAR paths drop leading zeros
    nodash = accession.replace("-", "")
    dir_url = f"{base}/{cik_num}/{nodash}"
    return f"{dir_url}/{accession}-index.json", dir_url


def _primary_document(index: dict) -> str:
    """Pick the 10-K main HTML document from a filing index listing."""
    items = index.get("directory", {}).get("item", [])
    candidates = [i["name"] for i in items
                  if i["name"].lower().endswith((".htm", ".html"))]
    # Prefer an explicit 10-K form tag when present.
    for it in index.get("items", []):
        name = it.get("fileName", "")
        if it.get("form") in ("10-K", "10-K/A") and name.lower().endswith(
                (".htm", ".html")):
            return name
    # Fallback: largest .htm that is not the filing index itself.
    docs = [c for c in candidates if "index" not in c.lower()]
    if not docs:
        raise TableNotFoundError(
            "no HTML document found in filing index listing")
    return docs[0]


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
        return dest
    index_url, dir_url = _filing_urls(cik, accession, base)
    log.info("fetch index: %s", index_url)
    index = json.loads(_http_get(index_url, limiter).decode("utf-8",
                                                            "replace"))
    doc_name = _primary_document(index)
    dest = dest_dir / doc_name
    if dest.exists():
        return dest
    doc_url = f"{dir_url}/{doc_name}"
    log.info("fetch document: %s", doc_url)
    dest.write_bytes(_http_get(doc_url, limiter))
    return dest


_ARCHIVE_DOC_EXCLUDE = re.compile(r"index\.htm|exhibit|formxof", re.I)


def _maybe_decompress(data: bytes) -> bytes:
    """Decompress gzip transfer-encoded archive captures."""
    if data[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(data)
    return data


def _archive_main_doc(cik: str, accession: str,
                      limiter: RateLimiter) -> tuple[str, str, str]:
    """Locate a filing's main 10-K document via the Wayback CDX API.

    Returns (timestamp, original_url, doc_name). The main document is the
    largest HTML file in the filing directory that is not the filing
    index or an exhibit.
    """
    import urllib.parse
    cik_num = str(int(cik))
    nodash = accession.replace("-", "")
    q = ("https://web.archive.org/cdx/search/cdx?url="
         + urllib.parse.quote(f"sec.gov/Archives/edgar/data/{cik_num}/"
                              f"{nodash}/*", safe="")
         + "&output=text&fl=timestamp,original,length"
           "&collapse=urlkey&limit=400")
    best = None
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
        if best is None or length > best[3]:
            best = (ts, orig, name, length)
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
    return t


def _table_text(table) -> str:
    return table.get_text(" ", strip=True)


def _nearby_text(table, chars: int = 1500) -> str:
    """Text of preceding siblings/headings, for anchor + unit detection."""
    parts = []
    node = table
    for _ in range(6):
        node = node.find_previous_sibling()
        if node is None:
            break
        if getattr(node, "get_text", None):
            parts.append(node.get_text(" ", strip=True))
    # Also walk up: section headings often sit above the table's parent.
    parent = table.parent
    for _ in range(2):
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


def locate_table(soup: BeautifulSoup, spec: TableSpec,
                 firm: str, accession: str):
    """Find the MD&A table matching a spec's anchors.

    A table qualifies only if (a) its own text or nearby heading text
    matches one of the anchors AND (b) it contains every required row
    label. This avoids latching onto the first table that merely mentions
    the anchor (e.g. a firmwide summary) while the detailed table sits
    further down.

    Raises TableNotFoundError with a diagnostic message when nothing
    matches -- never returns a guessed table.
    """
    n_tables = len(soup.find_all("table"))
    anchor_hits = 0
    for anchor in spec.anchors:
        rx = re.compile(anchor, re.IGNORECASE)
        for table in soup.find_all("table"):
            hay = _table_text(table) + " || " + _nearby_text(table)
            if not rx.search(hay):
                continue
            anchor_hits += 1
            labels = _row_labels(table)
            if all(any(rs.matches(lb) for lb in labels) for rs in spec.rows):
                return table
    raise TableNotFoundError(
        f"{firm} {accession}: MD&A table '{spec.description}' not found. "
        f"{anchor_hits} table(s) matched the anchors "
        f"{spec.anchors} but none contained all required rows "
        f"{[rs.line for rs in spec.rows]} (checked {n_tables} tables: "
        f"table text + nearby headings). The filing's MD&A layout likely "
        f"changed; update FIRM_SPECS anchors/rows rather than guessing.")


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
    for tr in rows[:6]:
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


def detect_unit_multiplier(table) -> float:
    """USD-millions multiplier from the table's stated units.

    Markers inside the table text win over nearby headings (a neighboring
    section may use different units, e.g. "in billions" for balance-sheet
    data next to an "in millions" revenue table).
    """
    nearby = _nearby_text(table, 800)
    for hay in (_table_text(table)[:4000], nearby):
        for rx, mult in _UNIT_RES:
            if rx.search(hay):
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
        # MD&A layout convention: year values come first, % change /
        # variance columns trail. Take the first len(years) slots.
        for year, val in zip(years, slots[:len(years)]):
            if val is None:
                continue  # undisclosed cell: emit nothing, never estimate
            out.append({"taxonomy_line": rs.line, "year": year,
                        "net_revenue_m": round(val * unit_mult, 3)})
    return out


def parse_filing(firm: str, cik: str, accession: str,
                 html_path: Path) -> list[dict]:
    """Scrape all spec'd MD&A tables from one saved 10-K document."""
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(html_path.read_bytes(), "lxml")
    records = []
    for spec in FIRM_SPECS[firm]:
        table = locate_table(soup, spec, firm, accession)
        try:
            years = detect_years(table)
        except YearColumnError:
            years = []  # year-band layout (Jefferies); resolved in extract
        unit_mult = detect_unit_multiplier(table)
        rows = extract_spec_rows(table, spec, years, unit_mult)
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
