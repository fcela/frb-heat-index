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
    _nearby_text,
    _preceding_text,
    _table_text,
    _nearest_unit,
    _value_slots,
    detect_unit_multiplier,
    filing_fiscal_year,
    locate_tables,
    normalize_label,
    parse_money,
)


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
# Citi 10-Qs head the QTD group "Second Quarter" (no month).
_QTD_ALT_RE = re.compile(r"\b(first|second|third)\s+quarter\b", re.I)

MONTH_Q = {
    "january": 1, "february": 1, "march": 1,
    "april": 2, "may": 2, "june": 2,
    "july": 3, "august": 3, "september": 3,
    "october": 4, "november": 4, "december": 4,
}
ORDINAL_Q = {"first": 1, "second": 2, "third": 3}


def detect_qtd_periods(table) -> list[tuple[int, int]]:
    """Ordered (year, quarter) for the table's QTD column groups.

    Finds the "Three months ended {Month}" header, maps the month to a
    quarter, then reads the year cells under its colspan span. Returns
    e.g. [(2024, 2), (2023, 2)]. Raises YearColumnError when no QTD
    header or no years are found.
    """
    rows = table.find_all("tr")
    for ri, tr in enumerate(rows[:10]):
        header_text = tr.get_text(" ", strip=True)
        m = _QTD_RE.search(header_text)
        quarter = None
        if m:
            quarter = MONTH_Q.get(m.group(1).lower())
        else:
            ma = _QTD_ALT_RE.search(header_text)
            if ma:
                quarter = ORDINAL_Q.get(ma.group(1).lower())
        if quarter is None:
            continue
        # Locate the header cell and its column span.
        cells = tr.find_all(["th", "td"])
        start = None
        span = None
        pos = 0
        for c in cells:
            cs = int(c.get("colspan", 1))
            ct = c.get_text(" ", strip=True)
            if _QTD_RE.search(ct) or _QTD_ALT_RE.search(ct):
                start, span = pos, cs
                break
            pos += cs
        if start is None:
            continue
        # Next rows: collect years within the span.
        years: list[int] = []
        for nxt in rows[ri + 1:ri + 4]:
            pos = 0
            for c in nxt.find_all(["th", "td"]):
                cs = int(c.get("colspan", 1))
                if start <= pos < start + span:
                    ym = _YEAR.search(c.get_text(" ", strip=True))
                    if ym:
                        y = int(ym.group(0))
                        if 1990 <= y <= 2100 and y not in years:
                            years.append(y)
                pos += cs
            if years:
                break
        if not years:
            raise YearColumnError(
                "QTD header found but no years under its span")
        return [(y, quarter) for y in years]
    raise YearColumnError("no 'Three months ended' header in table")


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
        slots = _value_slots(cells[1:])
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
                log.warning("%s %s: '%s': %s; skipping table",
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
    """Accessions of 10-Q filings (quarter-end != Dec 31) with raw dirs."""
    out = []
    for d in sorted((RAW_ROOT / cik).iterdir()):
        xmls = [p for p in d.iterdir()
                if p.suffix in (".xml", ".xbrl") and "htm" in p.name
                and not p.name.startswith("R")]
        if not xmls:
            continue
        if any("1231" in p.name for p in xmls):
            continue  # 10-K
        out.append(d.name)
    return out


def dedupe_records(records: list[dict]) -> list[dict]:
    """Dedupe (firm, line, year, quarter): later filing wins."""
    best: dict[tuple, dict] = {}
    for r in records:
        key = (r["firm"], r["taxonomy_line"], r["year"], r["quarter"])
        # Accession sorts chronologically; later filing wins restatements.
        if key not in best or r["accession"] > best[key]["accession"]:
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
    ap.add_argument("--firms", default="JPM,MS,BAC,C",
                    help="comma-separated firm codes (default: the four "
                         "division-reporters)")
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
