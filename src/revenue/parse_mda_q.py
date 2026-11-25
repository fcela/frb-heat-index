"""Layer 2Q: quarterly MD&A sub-business-line scraper.

Mirrors parse_mda.py (Layer 2) for 10-Q filings. The same FIRM_SPECS
table locators apply -- 10-Q MD&A tables use the same titles and row
labels -- but column headers are quarterly periods ("Three months ended
June 30, 2024") instead of fiscal years.

Output: data/revenue/subsplits_quarterly.csv with columns
firm, cik, taxonomy_line, year, quarter, net_revenue_m, source, accession
where source is "mda_q:<table description>".

Only the "Three months ended" (QTD) column groups are extracted; "Six
months ended" (YTD) groups are skipped -- the quarterly builder takes
QTD values directly. A 10-Q table may carry two QTD year-columns (e.g.
2024 and 2023 for the same quarter); both are emitted.

Scope notes (same as Layer 2): firms/lines covered by the XBRL layer are
not scraped; undisclosed splits emit nothing, never estimated.

Run: python3 -m src.revenue.parse_mda_q --help
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Iterable

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.revenue.parse_mda import (  # noqa: E402
    FIRM_SPECS,
    FIRMS,
    RAW_ROOT,
    RowSpec,
    TableSpec,
    TableNotFoundError,
    UnitAmbiguousError,
    YearColumnError,
    _money_slots,
    _nearby_text,
    _preceding_text,
    _table_text,
    _nearest_unit,
    detect_unit_multiplier,
    dir_form_type,
    filing_fiscal_year,
    locate_tables,
    normalize_label,
    parse_money,
)
from src.revenue.taxonomy import accession_order_key  # noqa: E402


def detect_unit_multiplier_q(table) -> float:
    """Unit multiplier for 10-Q tables.

    Falls back to searching the full table text (not cut at the first
    data row) when the standard detection fails: quarterly headers like
    "Three Months Ended September 30" contain a day number ("30") that
    the shared cutter mistakes for a money value, cutting before the
    "(Dollars in millions)" marker.
    """
    try:
        return detect_unit_multiplier(table)
    except UnitAmbiguousError:
        pass
    # Fallback: full table text, then nearby/preceding.
    for hay in (_table_text(table)[:4000], _nearby_text(table, 3000),
                _preceding_text(table, 3000)):
        mult = _nearest_unit(hay)
        if mult is not None:
            log.info("unit fallback: using %s from full table text", mult)
            return mult
    raise UnitAmbiguousError(
        "dollar units not stated near 10-Q MD&A table")

log = logging.getLogger("parse_mda_q")

OUT_CSV = RAW_ROOT.parent / "subsplits_quarterly.csv"

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_QTD_RE = re.compile(r"three months ended\s+([a-z]+)(?:\s+\d{1,2})?", re.I)
# Header variant without the month in the same cell ("For the Three
# Months Ended" over a "February 29, 2020" row below): Jefferies 10-Qs.
_QTD_LOOSE_RE = re.compile(r"three\s+months?\s+ended", re.I)
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\.?\s*\d{1,2},?\s*((19|20)\d{2})\b", re.I)
# Citi 10-Qs head the QTD group "Second Quarter" (no month).
_QTD_ALT_RE = re.compile(r"\b(first|second|third)\s+quarter\b", re.I)
# A table whose own header rows declare a 6/9/12-month period is a YTD
# (or annual) table, never a QTD table with a missing header.
_YTD_DECL_RE = re.compile(r"(?:six|nine|twelve)\s+months?\s+ended", re.I)

MONTH_Q = {
    "january": 1, "february": 1, "march": 1,
    "april": 2, "may": 2, "june": 2,
    "july": 3, "august": 3, "september": 3,
    "october": 4, "november": 4, "december": 4,
}
ORDINAL_Q = {"first": 1, "second": 2, "third": 3}


def detect_qtd_periods(table) -> list[tuple[int, int]]:
    """Ordered (year, quarter) for the table's QTD column groups.

    Finds a QTD header, then reads the period cells at or below the
    header cell's colspan span. Three header layouts are handled:
      - "Three Months Ended August 31," (month in the header cell):
        quarter from the month; bare year cells below.
      - "First/Second/Third Quarter" (Citi): quarter from the ordinal;
        bare year cells below.
      - "For the Three Months Ended" with the month split into the row
        below ("February 29, 2020"): Jefferies 10-Qs; each period cell
        carries its own month and year, so the quarter is taken per
        cell (the comparative period can even end in a different month,
        e.g. "May 31, 2019" vs "June 30, 2018" across Jefferies' 2018
        fiscal-year-end transition).
    When the table head declares no 6/9/12-month group, the whole period
    row is scanned instead of just the header cell's span: Jefferies
    10-Qs sit the "For the Three Months Ended" header over broken
    colspans that cover only the current period, excluding the
    comparative period's cells.
    Returns e.g. [(2024, 2), (2023, 2)]. Raises YearColumnError when no
    QTD header or no periods are found.
    """
    rows = table.find_all("tr")
    for ri, tr in enumerate(rows[:10]):
        header_text = tr.get_text(" ", strip=True)
        quarter = None
        m = _QTD_RE.search(header_text)
        if m:
            quarter = MONTH_Q.get(m.group(1).lower())
        elif _QTD_ALT_RE.search(header_text):
            m = _QTD_ALT_RE.search(header_text)
            quarter = ORDINAL_Q.get(m.group(1).lower())
        elif not _QTD_LOOSE_RE.search(header_text):
            continue
        cells = tr.find_all(["th", "td"])
        start = None
        span = None
        pos = 0
        for c in cells:
            cs = int(c.get("colspan", 1))
            ct = c.get_text(" ", strip=True)
            if _QTD_RE.search(ct) or _QTD_ALT_RE.search(ct) \
                    or _QTD_LOOSE_RE.search(ct):
                start, span = pos, cs
                break
            pos += cs
        if start is None:
            continue
        # Span discipline: a table whose head declares 6/9/12-month
        # groups must respect the QTD header cell's colspan span (the
        # other groups' period cells sit in the same rows). A table
        # with no other duration group can sit over broken colspans
        # that exclude the comparative period (Jefferies 10-Qs: the
        # "For the Three Months Ended" header covers only the current
        # period), so the whole period row is scanned instead.
        strict = bool(_YTD_DECL_RE.search(
            " ".join(tr.get_text(" ", strip=True)
                     for tr in rows[:10])))
        # Next rows: collect periods.
        for nxt in rows[ri + 1:ri + 4]:
            periods: list[tuple[int, int]] = []
            pos = 0
            for c in nxt.find_all(["th", "td"]):
                cs = int(c.get("colspan", 1))
                if not strict or (start <= pos < start + span):
                    ct = c.get_text(" ", strip=True)
                    mdm = _MONTH_DAY_YEAR_RE.search(ct)
                    if mdm:
                        # Full month-day-year cell: quarter from its
                        # own month (loose headers, mixed-month rows).
                        y = int(mdm.group(2))
                        q = MONTH_Q.get(mdm.group(1).lower())
                        if 1990 <= y <= 2100 and q \
                                and (y, q) not in periods:
                            periods.append((y, q))
                    else:
                        ym = _YEAR.search(ct)
                        if ym and quarter is not None:
                            y = int(ym.group(0))
                            if 1990 <= y <= 2100 \
                                    and (y, quarter) not in periods:
                                periods.append((y, quarter))
                pos += cs
            if periods:
                return periods
        raise YearColumnError(
            "QTD header found but no years under its span")
    raise YearColumnError("no 'Three months ended' header in table")


def preceding_qtd_periods(table, max_hops: int = 3):
    """QTD periods from the nearest preceding table that states them.

    Citi's FY2025+ 10-Qs move the period headers ("Third Quarter ... 2025
    2024 % Change") out of the detail tables into the preceding summary
    table; the detail table repeats only the row labels and values. When
    a spec'd table carries no QTD header of its own, fall back to the
    nearest preceding table in the same document that has one. Returns
    None when no nearby table states the periods.
    """
    node = table
    for _ in range(max_hops):
        node = node.find_previous("table")
        if node is None:
            return None
        try:
            return detect_qtd_periods(node)
        except YearColumnError:
            continue
    return None


def extract_spec_rows_q(table, spec: TableSpec,
                        periods: list[tuple[int, int]],
                        unit_mult: float) -> list[dict]:
    """Extract (year, quarter, line, value_m) rows for one table spec.

    ``periods`` is the ordered (year, quarter) list from
    detect_qtd_periods(); values are zipped positionally against the
    row's first len(periods) slots (the QTD column group comes first in
    10-Q tables; YTD groups trail and are ignored).
    """
    found: dict[str, list] = {rs.line: [] for rs in spec.rows}
    for tr in table.find_all("tr"):
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
            f"{missing}")
    out = []
    for rs in spec.rows:
        tr = found[rs.line][-1] if rs.occurrence == "last" \
            else found[rs.line][0]
        cells = tr.find_all(["th", "td"])
        # _money_slots drops the interleaved "% of Net Revenues" / "%
        # Change" columns so the period values stay positionally zip-able.
        slots = _money_slots(cells[1:])
        if len(slots) < len(periods):
            log.warning("table '%s': row '%s' has %d slots for %d periods; "
                        "skipping row", spec.description, rs.line,
                        len(slots), len(periods))
            continue
        for (year, quarter), val in zip(periods, slots[:len(periods)]):
            if val is None:
                continue
            out.append({"taxonomy_line": rs.line, "year": year,
                        "quarter": quarter,
                        "net_revenue_m": round(val * unit_mult, 3)})
    return out


def parse_filing_q(firm: str, cik: str, accession: str,
                   html_path: Path) -> list[dict]:
    """Scrape all spec'd MD&A tables from one saved 10-Q document."""
    import warnings
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_path.read_bytes(), "lxml")
    fiscal_year = filing_fiscal_year(soup, cik, accession)
    records = []
    for spec in FIRM_SPECS[firm]:
        if spec.annual_only:
            log.info("%s %s: '%s' is annual-only; skipping in 10-Q parse",
                     firm, accession, spec.description)
            continue
        if spec.fiscal_years is not None and fiscal_year is not None:
            if not (spec.fiscal_years[0] <= fiscal_year
                    <= spec.fiscal_years[1]):
                log.info("%s %s: skipping '%s' (fiscal %d outside %s)",
                         firm, accession, spec.description, fiscal_year,
                         spec.fiscal_years)
                continue
        try:
            tables = locate_tables(soup, spec, firm, accession)
        except TableNotFoundError:
            log.info("%s %s: '%s' not found; skipping",
                     firm, accession, spec.description)
            continue
        if not spec.multi and len(tables) > 1:
            tables = tables[:1]
        for table in tables:
            try:
                periods = detect_qtd_periods(table)
            except YearColumnError as e:
                # New-format Citi 10-Qs (FY2025 Q3+) keep the period
                # headers in the preceding summary table; without the
                # fallback the whole table is skipped and the XBRL
                # division total (e.g. Banking, incl. corporate lending)
                # leaks in as the only source for the quarter.
                # But a table that declares its OWN 6/9/12-month period
                # is a YTD table, not a header-less detail table: never
                # let it inherit the preceding table's QTD periods
                # (Jefferies' 10-Qs repeat the revenues-by-source tables
                # for the nine-month period; inheriting would emit 9M
                # values as QTD).
                head = " ".join(
                    tr.get_text(" ", strip=True)
                    for tr in table.find_all("tr")[:10])
                if _YTD_DECL_RE.search(head):
                    log.info("%s %s: '%s': table declares a 6/9/12-month "
                             "period; skipping YTD table",
                             firm, accession, spec.description)
                    continue
                periods = preceding_qtd_periods(table)
                if periods is None:
                    log.warning("%s %s: '%s': %s; no nearby QTD header "
                                "either; skipping table",
                                firm, accession, spec.description, e)
                    continue
            unit_mult = detect_unit_multiplier_q(table)
            rows = extract_spec_rows_q(table, spec, periods, unit_mult)
            if rows and unit_mult == 1000.0 and max(
                    abs(r["net_revenue_m"]) for r in rows) > 300_000:
                log.warning("%s %s: '%s': 'billions' gives absurd values; "
                            "retrying as millions",
                            firm, accession, spec.description)
                rows = extract_spec_rows_q(table, spec, periods, 1.0)
            # Derived lines (same as annual).
            if spec.derived and rows:
                by_pq: dict[tuple[int, int], dict[str, float]] = {}
                for r in rows:
                    by_pq.setdefault(
                        (r["year"], r["quarter"]), {})[r["taxonomy_line"]] = \
                        r["net_revenue_m"]
                for target, comps in spec.derived.items():
                    for (yr, q), vals in by_pq.items():
                        if all(c in vals for c in comps):
                            rows.append({
                                "taxonomy_line": target,
                                "year": yr, "quarter": q,
                                "net_revenue_m": sum(vals[c] for c in comps),
                            })
                comp_lines = {c for comps in spec.derived.values()
                              for c in comps}
                rows = [r for r in rows
                        if r["taxonomy_line"] not in comp_lines]
            source = f"mda_q:{spec.description}"
            for r in rows:
                records.append({"firm": firm, "cik": cik, **r,
                                "source": source, "accession": accession})
            log.info("%s %s: '%s' -> %d cells",
                     firm, accession, spec.description, len(rows))
    return records


def _main_doc_path(cik: str, accession: str) -> Path | None:
    """The saved 10-Q main HTML document, if present."""
    d = RAW_ROOT / cik / accession
    if not d.is_dir():
        return None
    for p in sorted(d.iterdir()):
        n = p.name.lower()
        if p.suffix.lower() not in (".htm", ".html"):
            continue
        if n.startswith("r") or n == "filingsummary.htm":
            continue
        if p.stat().st_size > 50_000:
            return p
    return None


def enumerate_10q(cik: str) -> list[str]:
    """Accessions of 10-Q filings with raw dirs.

    Form type comes from the XBRL instance's dei:DocumentType (definitive).
    The historical fallback -- an instance *filename* carrying a period
    date other than the fiscal year-end (1231 for calendar filers, 1130
    for Jefferies Financial Group) -- both included 10-Ks (whose filenames
    can lack the period date, e.g. BofA's 2019-2020 10-K instances, so
    they were mistaken for 10-Qs) and missed genuine 10-Qs (filing-agent
    instance names like GS's "d105283d10q_htm.xml" carry no period date,
    so they were dropped). Those misses are why GS's quarterly MD&A layer
    stopped at 2019Q1. The fallback survives only for pre-XBRL-era
    accessions whose instances state no document type.
    """
    out = []
    for d in sorted((RAW_ROOT / cik).iterdir()):
        form = dir_form_type(d)
        if form is not None:
            if form.upper().startswith("10-Q"):
                out.append(d.name)
            continue
        xmls = [p for p in d.iterdir()
                if p.suffix in (".xml", ".xbrl") and "htm" in p.name
                and not p.name.startswith("R")]
        if not xmls:
            continue
        if any(("1231" in p.name) or ("1130" in p.name) for p in xmls):
            continue  # 10-K
        out.append(d.name)
    return out


def dedupe_records(records: list[dict]) -> list[dict]:
    """Dedupe (firm, line, year, quarter): later filing wins.

    "Later" is the accession's (filing year, sequence) -- accession
    strings do not sort chronologically across filing-agent prefixes
    (GS's self-filed "0000886982-23-*" string-sorts before its
    agent-filed "0001193125-22-*", which would let an original-vintage
    QTD beat the later restated comparative).
    """
    best: dict[tuple, dict] = {}
    for r in records:
        key = (r["firm"], r["taxonomy_line"], r["year"], r["quarter"])
        if (key not in best
                or accession_order_key(r["accession"])
                > accession_order_key(best[key]["accession"])):
            best[key] = r
    return sorted(best.values(),
                  key=lambda r: (r["firm"], r["taxonomy_line"],
                                 r["year"], r["quarter"]))


def build_csv(firms: Iterable[str],
              accessions: dict[str, list[str]] | None = None) -> Path:
    """Scrape 10-Q MD&A tables and write subsplits_quarterly.csv."""
    from src.revenue.parse_mda import FIRMS as MDA_FIRMS
    records = []
    for firm in firms:
        cik = MDA_FIRMS[firm]["cik"]
        accs = accessions.get(firm) if accessions else enumerate_10q(cik)
        for acc in accs:
            html = _main_doc_path(cik, acc)
            if html is None:
                log.warning("%s %s: no main doc; skipping", firm, acc)
                continue
            try:
                recs = parse_filing_q(firm, cik, acc, html)
            except Exception as e:  # noqa: BLE001 -- keep going
                log.warning("%s %s: parse failed: %s", firm, acc, e)
                continue
            records.extend(recs)
    records = dedupe_records(records)
    df = pl.DataFrame(records, schema={
        "firm": pl.String, "cik": pl.String, "taxonomy_line": pl.String,
        "year": pl.Int64, "quarter": pl.Int64, "net_revenue_m": pl.Float64,
        "source": pl.String, "accession": pl.String,
    })
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(OUT_CSV)
    log.info("wrote %s: %d cells", OUT_CSV, len(df))
    return OUT_CSV


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--firms", default="JPM,MS,BAC,C,JEF,GS",
                    help="comma-separated firm codes (default: the four "
                         "division-reporters plus Jefferies and Goldman "
                         "Sachs; GS's post-2020 FICC/equities sub-lines "
                         "exist only in its 10-Q MD&A, not in XBRL)")
    ap.add_argument("--accessions", default=None,
                    help="JSON file mapping firm -> [accessions]")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")
    firms = [f.strip() for f in args.firms.split(",") if f.strip()]
    accessions = None
    if args.accessions:
        import json
        accessions = json.loads(Path(args.accessions).read_text())
    build_csv(firms, accessions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
