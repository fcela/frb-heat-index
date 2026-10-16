"""XBRL R-file segment revenue parser for the bank revenue tracker.

Pipeline per 10-K filing:
  1. parse_filing_summary() reads FilingSummary.xml and lists the rendered
     reports; find_segment_reports() keeps the business-segment R-files.
  2. Each R-file's tables are parsed (HTML via _TableParser; pre-v3 XML
     InstanceReport R-files via parse_segment_xml). Three layouts are
     handled: segment sections as row blocks (member-linked or
     "[Member]"-text headers), segments pivoted into header columns, and
     single-segment tables. The revenue line ("Total net revenues" and
     filer-specific variants) is extracted for every reported year, with
     the $M scale read from the title or inferred from magnitude.
  3. build_segments_csv() walks the raw download tree and writes
     data/revenue/segments_annual.csv.

Idempotent and non-destructive: raw files are only read, never modified.
Run: python3 -m src.revenue.parse_xbrl [--raw DIR] [--out FILE]
"""

import argparse
import html
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO / "data" / "revenue" / "raw"
OUT_CSV = REPO / "data" / "revenue" / "segments_annual.csv"

# Normalized row labels treated as the segment revenue line, in preference
# order. "Total net revenues" wins when both are present for a segment.
# The singular "total net revenue" appears in pre-2013 filings; the bare
# "revenues" is the segment revenue line in some filers' layouts; BofA uses
# "total revenue, net of interest expense", Citi "revenues, net of interest
# expense", Wells Fargo "total revenue".
REVENUE_LABELS = ["total net revenues", "net revenues", "total net revenue",
                  "net revenue",
                  "revenues", "total revenue", "total revenues",
                  "total revenue, net of interest expense",
                  "total revenues, net of interest expense",
                  "revenues, net of interest expense"]

# Firms whose segment revenue appears as named fee lines rather than a
# "Total net revenues" row (single-segment filers). The fee-type header
# sets the segment; the following generic revenue row carries the values.
EXTRA_LABELS = {
    "PJT Partners": ["advisory fees", "placement fees",
                     "revenues from contracts with customers"],
}

# Firms whose segment tables report revenue only as components: sum these
# line items per (segment, year) when no direct revenue label is present.
# (Older Wells Fargo filings lack "Total revenue".)
REVENUE_SUMS = {
    "Wells Fargo": ("net interest income", "noninterest income"),
}

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
UNIT_RE = re.compile(r"\$\W*in\s*(millions|thousands|billions)", re.I)
UNIT_SCALE = {"millions": 1.0, "thousands": 0.001, "billions": 1000.0}
PAREN_NEG = re.compile(r"^\(\s*(.*?)\s*\)$")
NUM_RE = re.compile(r"[-+]?[0-9][0-9,]*(\.[0-9]+)?")


def parse_filing_summary(path):
    """Return [{file, short_name, long_name}] for every rendered report.

    Pre-v3 FilingSummary files name the XML R-file in XmlFileName rather
    than HtmlFileName; both are honored.
    """
    root = ET.parse(str(path)).getroot()
    reports = []
    for rep in root.iter("Report"):
        reports.append({
            "file": ((rep.findtext("HtmlFileName") or "").strip()
                     or (rep.findtext("XmlFileName") or "").strip()),
            "short_name": (rep.findtext("ShortName") or "").strip(),
            "long_name": (rep.findtext("LongName") or "").strip(),
        })
    return reports


def find_segment_reports(reports, firm=None):
    """Keep the R-files covering business segment information.

    Matches "Business Segments", the older "Business Segment Information"
    phrasing, and filers like Morgan Stanley that title the note "Segment,
    Geographic and Revenue Information". Geographic breakdowns ("...
    Geographic ...", "... By Region"), loan-portfolio segments, and
    goodwill-by-segment reports are excluded: the tracker follows business
    segments, not regions.

    Single-segment filers may disclose revenue by type instead of a segment
    note (PJT's "Disaggregation of Revenue"); firm-specific patterns cover
    those.
    """
    # Firm-specific report patterns for filers without a segment note.
    firm_patterns = {
        "PJT Partners": ["disaggregation of revenue"],
    }
    patterns = firm_patterns.get(firm, [])
    out = []
    for r in reports:
        s = r["short_name"].lower()
        if "segment" not in s and not any(p in s for p in patterns):
            continue
        # Exclude geographic sub-reports ("... - Net Revenues by Region
        # (Details)", "... By Geographic Region (Detail)", "... by
        # Geographic Area (Details)"). A main note that merely mentions
        # geography (Morgan Stanley's "Segment, Geographic and Revenue
        # Information") is kept.
        if "by region" in s or "by geographic" in s:
            continue
        # Geographic sub-reports ("Schedule of Geographical Distribution of
        # Revenue", "... Revenue and Assets by Geographical ..."). A main
        # note titled "... Segment and Geographical Information" (Houlihan
        # Lokey) is kept — only the geographic breakdown is excluded.
        if "geographical distribution" in s or "by geographical" in s:
            continue
        if "portfolio segment" in s or "goodwill" in s:
            continue
        # Credit-loss allowance by segment is not a business-segment note.
        if "allowance for credit losses" in s or "allowance for loan" in s:
            continue
        if r["file"]:
            out.append(r)
    return out


class _TableParser(HTMLParser):
    """Collect top-level <table> elements.

    Each table is a list of rows; each row is a list of (text, member_id)
    cells. Colspans are expanded by repeating the cell. member_id is the
    XBRL concept reference from the cell's link (e.g.
    "defref_us-gaap_StatementBusinessSegmentsAxis=ms_InstitutionalSecuritiesSegmentMember"),
    or None. A member_id containing "Member" marks segment section headers.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._depth = 0
        self._rows = None
        self._row = None
        self._cell = None
        self._cell_member = None
        self._colspan = 1

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
        elif tag == "tr" and self._depth == 1 and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_member = None
            self._colspan = 1
            for k, v in attrs:
                if k == "colspan":
                    try:
                        self._colspan = max(1, int(v))
                    except (TypeError, ValueError):
                        pass
        elif tag == "a" and self._cell is not None:
            for k, v in attrs:
                if k == "onclick" and v and "Member" in v:
                    m = re.search(r"defref_[A-Za-z0-9_.-]+=([A-Za-z0-9_.-]+)",
                                  v)
                    self._cell_member = m.group(0) if m else True

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth == 1:
                if self._rows is not None:
                    self.tables.append(self._rows)
                self._rows = None
                self._row = None
                self._cell = None
            self._depth = max(0, self._depth - 1)
        elif tag == "tr" and self._row is not None and self._rows is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None \
                and self._row is not None:
            text = html.unescape("".join(self._cell))
            text = text.replace("\xa0", " ").strip()
            text = re.sub(r"\s+", " ", text)
            for _ in range(self._colspan):
                self._row.append((text, self._cell_member))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def extract_tables(html_text):
    """Return tables; each is a list of rows of (text, member_id) cells."""
    p = _TableParser()
    p.feed(html_text)
    return p.tables


def _norm_label(label):
    # Strip footnote markers like "[1]" / "(1)" and trailing asterisks.
    label = re.sub(r"\[\d+\]", "", label)
    label = re.sub(r"\(\d+\)$", "", label).rstrip(" *\u2020\u2021")
    return label.strip().lower()


def _parse_value(text):
    """'$ 44,724' -> 44724.0 ; '(1,113)' -> -1113.0 ; '—'/'' -> None."""
    t = text.strip().replace("$", "").strip()
    if not t or t in ("\u2014", "\u2013", "-", "--", "N/A", "n/a"):
        return None
    m = PAREN_NEG.match(t)
    sign = 1.0
    if m:
        sign = -1.0
        t = m.group(1)
    m = NUM_RE.search(t.replace(",", ""))
    if not m:
        return None
    return sign * float(m.group(0))


def _table_scale(table_text, raw_values):
    """Detect the $M scale for a table.

    Explicit "$ in Millions/Thousands/Billions" wins. Otherwise infer from
    magnitude: these firms' segment revenues in $M never exceed ~1e6
    (=$1T); raw-dollar figures always do. (Pre-v3 XML R-files carry an
    explicit <Scale> and bypass this.)
    """
    m = UNIT_RE.search(table_text)
    if m:
        return UNIT_SCALE[m.group(1).lower()]
    mx = max((abs(v) for v in raw_values), default=0.0)
    return 1.0 / 1e6 if mx > 1e6 else 1.0


def _cell_year(text):
    m = YEAR_RE.search(text)
    if m and 2000 <= int(m.group(0)) <= 2035:
        return int(m.group(0))
    return None


def _header_columns(table):
    """Locate the year header row; return (index, [year-or-None per cell]).

    Picks the row (among the first 10) with the most distinct years.
    Colspans are already expanded by _TableParser, so each entry maps to
    one data column.
    """
    best, best_idx, best_cols = -1, None, []
    for i, row in enumerate(table[:10]):
        cols = [_cell_year(text) for text, _ in row]
        distinct = {y for y in cols if y}
        if len(distinct) >= 2 and len(distinct) > best:
            best, best_idx, best_cols = len(distinct), i, cols
    return best_idx, best_cols


def _clean_segment_name(text):
    # "Operating Segments | Consumer & Community Banking" -> the member name.
    # Three-part headers ("Operating Segments | Citicorp | North America")
    # are sub-breakdowns: keep the full sub-path so the row can't be
    # mistaken for a top-level segment. Literal "[Member]" suffixes
    # ("Deposits Segment [Member]") are stripped.
    parts = [p.strip() for p in text.split("|")]
    parts = [p for p in parts if p]
    if not parts:
        return re.sub(r"\s+", " ", text).strip()
    if len(parts) >= 3:
        name = " | ".join(parts[1:])
    else:
        name = parts[-1]
    name = re.sub(r"\s+segment\s*\[member\]$", "", name, flags=re.I)
    name = re.sub(r"\s*\[member\]$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip()


# Generic axis labels that are never segment names on their own.
_GENERIC_AXIS_LABELS = {"operating segments", "business segments",
                        "business segment", "segments", "segment",
                        "consolidation items"}

# Member IDs that denote an axis default rather than a real segment.
_GENERIC_MEMBERS = {"operatingsegments", "consolidationitems",
                    "businesssegments", "statementbusinesssegments",
                    "segments"}


def _humanize_member_id(defref_id):
    """Turn an XBRL member concept into a display name.

    "defref_us-gaap_StatementBusinessSegmentsAxis=ms_InstitutionalSecuritiesSegmentMember"
    -> "Institutional Securities". Returns None for axis defaults.
    """
    if not isinstance(defref_id, str) or "=" not in defref_id:
        return None
    member = defref_id.split("=", 1)[1]
    member = re.sub(r"Member$", "", member)
    member = re.sub(r"^[a-z]+(?:-[a-z]+)?_", "", member)  # ms_, us-gaap_, srt_
    member = re.sub(r"Segment$", "", member)
    if not member or len(member) < 3 or member.lower() in _GENERIC_MEMBERS:
        return None
    name = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", member)
    return name.strip() or None


def _segment_name(displayed, member_id):
    """Resolve the segment name for a section header.

    Prefers the displayed text (which filers word best, e.g. "Global
    Banking & Markets"); falls back to the humanized XBRL member concept
    when the text is an abbreviation ("IS | Operating Segments") or a
    generic axis label.
    """
    cleaned = _clean_segment_name(displayed)
    if cleaned and cleaned.lower() not in _GENERIC_AXIS_LABELS and not (
            cleaned.isupper() and len(cleaned) <= 5):
        return cleaned
    human = _humanize_member_id(member_id)
    return human or cleaned


def _pivot_member(text):
    """Extract a segment member from a pivoted header cell.

    "Dec. 31, 2012 Consumer & Community Banking [Member]" ->
    "Consumer & Community Banking". Returns None for consolidated columns,
    range columns (Minimum/Maximum), and non-member cells.
    """
    if "[Member]" not in text:
        return None
    m = re.search(
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s*"
        r"\d{1,2},?\s*\d{4}\s*(.+?)\s*(?:\[Member\])", text)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(1)).strip()
    if not name or "minimum" in name.lower() or "maximum" in name.lower():
        return None
    return name


def parse_segment_table(html_text, extra_labels=(), sum_labels=()):
    """Extract segment revenue rows from one R-file's HTML.

    Returns a list of {segment_native, year, value_m, label, consolidated}.
    Segment section headers are rows whose label cell links to a segment
    member concept and whose value cells are empty; the member name is the
    text after any "Axis |" prefix. Rows before the first segment header
    are the consolidated block (consolidated=True).

    sum_labels: when no direct revenue label matches, these component line
    items are summed per (segment, year) instead.
    """
    records = _parse_with_labels(html_text, REVENUE_LABELS, extra_labels)
    if not records and sum_labels:
        comp = _parse_with_labels(html_text, list(sum_labels), ())
        # Sum components per (segment, year). All components share the
        # table's unit, so value_m adds directly.
        totals = {}
        for r in comp:
            key = (r["segment_native"], r["year"], r["consolidated"])
            if key not in totals:
                totals[key] = dict(r)
                totals[key]["label"] = " + ".join(sum_labels)
            else:
                totals[key]["value_m"] += r["value_m"]
        records = list(totals.values())
    return records


def _parse_with_labels(html_text, revenue_labels, extra_labels):
    records = []
    labels = list(revenue_labels) + [l for l in extra_labels
                                     if l not in revenue_labels]
    for table in extract_tables(html_text):
        if not table:
            continue
        flat = " ".join(" ".join(text for text, _ in r) for r in table)
        hdr_idx, col_years = _header_columns(table)
        if hdr_idx is None:
            continue
        # Align header cells to data columns. The header row covers the
        # value columns; the offset is the difference between the data-row
        # and header lengths (label column, plus any dimension column the
        # header skips, as in Morgan Stanley's layout).
        rev_rows = [r for r in table
                    if r and r[0][0] and _norm_label(r[0][0]) in labels]
        if not rev_rows:
            continue
        data_len = Counter(len(r) for r in rev_rows).most_common(1)[0][0]
        off = data_len - len(col_years)
        if off not in (0, 1, 2):
            continue  # unrecognized layout; caught by the coverage check
        # year -> data-column indices holding that year's values, in order.
        year_cols = {}
        for j, y in enumerate(col_years):
            if y:
                year_cols.setdefault(y, []).append(off + j)
        years = sorted(year_cols)
        t_recs = []  # raw (unscaled) records for this table
        # Column-pivot layout: segments live in the header cells
        # ("Dec. 31, 2012 Consumer & Community Banking [Member]"), one
        # column per (segment, year). Gated on the header row mentioning
        # "segment" so geographic breakdowns can't slip in.
        header_texts = [text for text, _ in table[hdr_idx]]
        pivot = None
        if "segment" in " ".join(header_texts).lower():
            members = [_pivot_member(t) for t in header_texts]
            if any(members):
                pivot = [(m, y) for m, y in zip(members, col_years)]
        if pivot:
            for row in table:
                if not row or not row[0][0]:
                    continue
                label = row[0][0]
                if _norm_label(label) not in labels:
                    continue
                for j, (member, year) in enumerate(pivot):
                    if not member or not year:
                        continue
                    c = off + j
                    if c >= len(row):
                        continue
                    value = _parse_value(row[c][0])
                    if value is None:
                        continue
                    t_recs.append({
                        "segment_native": member,
                        "year": year,
                        "value_raw": value,
                        "label": label.strip(),
                        "consolidated": False,
                    })
        else:
            seg_member = None      # from member-linked section headers
            seg_text = None        # from plain-text section headers (fallback)
            member_headers_seen = False
            text_headers_seen = False
            for row in table:
                if not row or not row[0][0]:
                    continue
                label, member_id = row[0]
                norm = _norm_label(label)
                # Segment section header: label cell links to a member
                # concept and every value cell is empty. Checked before the
                # revenue-label match so a header whose text names a fee
                # line (PJT's "Advisory Fees") sets the segment for the
                # following value rows instead of being consumed as an
                # (empty) revenue row. Pre-v3 R-files write the member as
                # literal text ("Deposits Segment [Member]") instead of a
                # link. Plain-text titles ("Segment Operating Results") are
                # section labels, not segments, and are never treated as
                # headers.
                value_cells = [c[0] for c in row[1:]]
                if value_cells and all(c == "" for c in value_cells):
                    # Discontinued operations and reconciling items are not
                    # business segments.
                    low = label.lower()
                    if "discontinued" in low or "reconciling" in low:
                        continue
                    if member_id:
                        seg_member = _segment_name(label, member_id)
                        member_headers_seen = True
                    elif "[member]" in label.lower():
                        seg_text = _clean_segment_name(label)
                        text_headers_seen = True
                    continue
                if norm in labels:
                    for year in years:
                        value = None
                        for c in year_cols[year]:
                            if c < len(row):
                                v = _parse_value(row[c][0])
                                if v is not None:
                                    value = v
                                    break
                        if value is None:
                            continue
                        t_recs.append({
                            "seg_member": seg_member,
                            "seg_text": seg_text,
                            "year": year,
                            "value_raw": value,
                            "label": label.strip(),
                            "consolidated": False,
                        })
                    continue
            # Prefer member-linked attribution; fall back to plain-text headers
            # only when the table has no member-linked headers at all (so
            # abstracts like "Segment results and reconciliation" can never
            # hijack rows when real segment headers exist).
            for r in t_recs:
                if member_headers_seen:
                    seg = r.pop("seg_member")
                    r.pop("seg_text", None)
                    r["segment_native"] = seg
                    r["consolidated"] = seg is None
                else:
                    seg = r.pop("seg_text")
                    r.pop("seg_member", None)
                    r["segment_native"] = seg
                    r["consolidated"] = text_headers_seen and seg is None
        scale = _table_scale(flat, [r["value_raw"] for r in t_recs])
        for r in t_recs:
            r["value_m"] = r.pop("value_raw") * scale
            records.append(r)
    return records


SEG_MEMBER_RE = re.compile(
    r"\{[^}]*StatementBusinessSegmentsAxis[^}]*\}\s*:\s*(.+?)\s*\[Member\]",
    re.S)
PERIOD_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})")


def parse_segment_xml(xml_text, extra_labels=()):
    """Parse a pre-v3 InstanceReport R-file (R*.xml, ~2010-2012 filings).

    Layout: <Column> KeyNames carry the segment dimension and period
    ("{us-gaap_StatementBusinessSegmentsAxis} : <Segment> [Member] |
    1/1/2010 - 12/31/2010 ..."); <Row>s carry <Label> and <Cell>s whose
    <RoundedNumericAmount> is scaled by the column <Scale>. Returns records
    in the same shape as parse_segment_table.
    """
    labels = REVENUE_LABELS + [l for l in extra_labels
                               if l not in REVENUE_LABELS]
    columns = []
    for cm in re.finditer(r"<Column>(.*?)</Column>", xml_text, re.S):
        block = cm.group(1)
        key = re.search(r"<KeyName>(.*?)</KeyName>", block, re.S)
        key = key.group(1) if key else ""
        key = html.unescape(re.sub(r"\s+", " ", key)).strip()
        member = SEG_MEMBER_RE.search(key)
        period = PERIOD_RE.search(key)
        scale = re.search(r"<Scale>(-?\d+)</Scale>", block)
        columns.append({
            "segment": member.group(1).strip() if member else None,
            "year": int(period.group(6)) if period else None,
            "scale": int(scale.group(1)) if scale else 0,
        })
    records = []
    for rm in re.finditer(r"<Row>(.*?)</Row>", xml_text, re.S):
        row = rm.group(1)
        if "<IsAbstractGroupTitle>true</IsAbstractGroupTitle>" in row:
            continue
        lbl = re.search(r"<Label>(.*?)</Label>", row, re.S)
        label = (re.sub(r"<[^>]+>", "", lbl.group(1)).strip() if lbl else "")
        if _norm_label(label) not in labels:
            continue
        cells = re.search(r"<Cells>(.*?)</Cells>", row, re.S)
        if not cells:
            continue
        for cm in re.finditer(r"<Cell>(.*?)</Cell>", cells.group(1), re.S):
            cell = cm.group(1)
            cid = re.search(r"<Id>(\d+)</Id>", cell)
            if not cid:
                continue
            idx = int(cid.group(1)) - 1
            if idx >= len(columns):
                continue
            col = columns[idx]
            if col["segment"] is None or col["year"] is None:
                continue
            if "<IsNumeric>true</IsNumeric>" not in cell:
                continue
            amt = re.search(r"<RoundedNumericAmount>(.*?)</RoundedNumericAmount>",
                            cell)
            if not amt:
                continue
            try:
                value_usd = float(amt.group(1)) * (10 ** col["scale"])
            except ValueError:
                continue
            records.append({
                "segment_native": col["segment"],
                "year": col["year"],
                "value_m": value_usd / 1e6,
                "label": label,
                "consolidated": False,
            })
    return records


def _rfile_rank(short_name):
    s = short_name.lower()
    if "operating results" in s or "(detail)" in s:
        return 0
    if s == "business segments":
        return 1
    return 2


def parse_filing_dir(firm, cik, accession, filing_dir, segment_default=None):
    """Parse all segment R-files of one downloaded filing.

    Returns records with firm/cik/accession/r_file/source attached. When
    several R-files yield a row for the same (segment, year), the R-file is
    preferred by _rfile_rank (detail / operating-results reports first).
    """
    filing_dir = Path(filing_dir)
    fs = filing_dir / "FilingSummary.xml"
    if not fs.exists():
        return []
    reports = find_segment_reports(parse_filing_summary(fs), firm=firm)
    extra = EXTRA_LABELS.get(firm, [])
    sum_labels = REVENUE_SUMS.get(firm, ())
    by_key = {}
    for rep in reports:
        path = filing_dir / rep["file"]
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        if path.suffix.lower() == ".xml":
            recs = parse_segment_xml(text, extra_labels=extra)
        else:
            recs = parse_segment_table(text, extra_labels=extra,
                                       sum_labels=sum_labels)
        rank = _rfile_rank(rep["short_name"])
        for r in recs:
            if r.get("consolidated"):
                continue  # consolidated block of a multi-segment table
            seg = r["segment_native"] or segment_default or firm
            label = _norm_label(r["label"])
            key = (seg, r["year"], label)
            cand = {
                "firm": firm,
                "cik": cik,
                "segment_native": seg,
                "year": r["year"],
                "net_revenue_m": r["value_m"],
                "accession": accession,
                "r_file": rep["file"],
                "source": "XBRL R-file",
                "_rank": rank,
            }
            prev = by_key.get(key)
            if prev is None or rank < prev["_rank"]:
                by_key[key] = cand
    # Prefer "Total net revenues" over "Net revenues" per (segment, year).
    # Distinct fee lines (e.g. PJT's advisory vs placement fees) are kept
    # separately: only the two generic revenue labels compete.
    final = {}
    for (seg, year, label), cand in by_key.items():
        group = "revenue" if label in REVENUE_LABELS else label
        key = (seg, year, group)
        prev = final.get(key)
        if prev is None or label == "total net revenues":
            final[key] = cand
    out = []
    for cand in final.values():
        cand.pop("_rank", None)
        out.append(cand)
    return sorted(out, key=lambda r: (r["segment_native"], r["year"]))


def build_segments_csv(raw_root=RAW_ROOT, out_path=OUT_CSV,
                       firms_ciks=None, since_year=2010):
    """Walk the raw download tree and write segments_annual.csv."""
    raw_root = Path(raw_root)
    records = []
    for cik_dir in sorted(raw_root.iterdir()):
        if not cik_dir.is_dir():
            continue
        cik = cik_dir.name
        firm = (firms_ciks or {}).get(cik, cik)
        for acc_dir in sorted(cik_dir.iterdir()):
            if not acc_dir.is_dir():
                continue
            records.extend(
                parse_filing_dir(firm, cik, acc_dir.name, acc_dir))
    df = pl.DataFrame(records) if records else pl.DataFrame(
        [], schema={"firm": pl.String, "cik": pl.String,
                    "segment_native": pl.String, "year": pl.Int64,
                    "net_revenue_m": pl.Float64, "accession": pl.String,
                    "r_file": pl.String, "source": pl.String})
    df = df.filter(pl.col("year") >= since_year)
    # Restatements: the same (firm, segment, year) appears in several
    # filings; the later filing's value wins. Accession numbers sort
    # chronologically.
    df = df.sort(["firm", "segment_native", "year", "accession"])
    df = df.unique(subset=["firm", "segment_native", "year"], keep="last")
    df = df.sort(["firm", "segment_native", "year"])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(out_path)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(RAW_ROOT))
    ap.add_argument("--out", default=str(OUT_CSV))
    ap.add_argument("--firms", default="",
                    help="JSON file mapping CIK -> firm display name")
    args = ap.parse_args()
    firms_ciks = None
    if args.firms:
        import json
        firms_ciks = {v: k for k, v in json.load(open(args.firms)).items()}
    df = build_segments_csv(args.raw, args.out, firms_ciks)
    print(f"wrote {args.out}: {len(df)} rows, "
          f"{df['firm'].n_unique() if len(df) else 0} firms, "
          f"years {df['year'].min() if len(df) else '-'}"
          f"-{df['year'].max() if len(df) else '-'}")


if __name__ == "__main__":
    main()
