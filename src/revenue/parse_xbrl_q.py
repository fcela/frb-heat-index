"""Quarterly XBRL segment parser for 10-Q filings.

10-Q segment R-files use (duration, end-date) columns -- e.g. "3 Months
Ended" / "Mar. 31, 2026" -- rather than the annual year columns of 10-K
R-files. This module extracts segment revenue per period, preferring
3-month (QTD) durations; the builder derives missing quarters from YTD
durations (6/9 months) with validation.

Pipeline per 10-Q filing:
  1. parse_filing_summary() + find_segment_reports() locate the segment
     R-files (same as the annual layer).
  2. _period_columns() reads the duration row ("3/6/9 Months Ended") and
     the end-date row ("Sep. 30, 2018") to label each data column.
  3. Segment sections are member-linked headers (same as annual); the
     revenue line ("Total net revenues" and filer variants) is extracted
     per (duration, end date).

Output records: {firm, cik, segment_native, period_end (ISO date),
duration_months, net_revenue_m, accession, r_file, source}. The builder
(build_revenue_q.py) maps periods to calendar quarters and derives
missing QTD quarters from YTD durations.

Idempotent and non-destructive: raw files are only read, never modified.
Run: python3 -m src.revenue.parse_xbrl_q [--raw DIR] [--out FILE]
"""

import argparse
import re
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.revenue.parse_xbrl import (  # noqa: E402
    extract_tables,
    parse_filing_summary,
    find_segment_reports,
    _parse_value,
    _table_scale,
    _norm_label,
    _segment_name,
    _clean_segment_name,
    REVENUE_LABELS,
    EXTRA_LABELS,
    REVENUE_SUMS,
)

REPO = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO / "data" / "revenue" / "raw"
OUT_CSV = REPO / "data" / "revenue" / "segments_quarterly.csv"

DURATION_RE = re.compile(r"(\d+)\s*months?\s*ended", re.I)
DATE_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*"
    r"(\d{1,2}),?\s*(\d{4})", re.I)
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _parse_duration(text):
    """'3 Months Ended' -> 3 ; None when not a duration cell."""
    m = DURATION_RE.search(text or "")
    return int(m.group(1)) if m else None


def _parse_end_date(text):
    """'Sep. 30, 2018' -> '2018-09-30' ; None when not a date cell."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    mon = MONTHS[m.group(1).lower()[:3]]
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def _period_columns(table):
    """Label each data column with (duration_months, period_end).

    The duration row ("3/6/9 Months Ended") and the end-date row
    ("Sep. 30, 2018") are the first two rows carrying those markers.
    Returns a list of (duration, end_date) per column, or None when the
    table has no period headers (not a quarterly segment table).
    """
    dur_row = date_row = None
    for i, row in enumerate(table[:8]):
        texts = [c[0] for c in row]
        if dur_row is None and any(_parse_duration(t) for t in texts):
            dur_row = i
        if date_row is None and any(_parse_end_date(t) for t in texts):
            date_row = i
        if dur_row is not None and date_row is not None:
            break
    if dur_row is None or date_row is None:
        return None
    durs = [_parse_duration(c[0]) for c in table[dur_row]]
    dates = [_parse_end_date(c[0]) for c in table[date_row]]
    # Forward-fill: duration/date cells often span merged columns; the
    # parser expands colspans, but some filers leave blanks.
    cols = []
    last_dur = last_date = None
    for du, da in zip(durs, dates):
        last_dur = du or last_dur
        last_date = da or last_date
        cols.append((last_dur, last_date))
    # The label column has no period; drop leading (None, None) entries.
    while cols and cols[0] == (None, None):
        cols.pop(0)
    if not any(d and dt for d, dt in cols):
        return None
    return cols


def parse_segment_table_q(html_text, extra_labels=(), sum_labels=()):
    """Extract segment revenue per period from one 10-Q R-file.

    Returns [{segment_native, duration_months, period_end, value_m,
    label, consolidated}].
    """
    labels = list(REVENUE_LABELS) + [l for l in extra_labels
                                     if l not in REVENUE_LABELS]
    records = []
    for table in extract_tables(html_text):
        if not table:
            continue
        cols = _period_columns(table)
        if not cols:
            continue
        flat = " ".join(" ".join(t for t, _ in r) for r in table)
        # Revenue rows: label cell matches a revenue label.
        rev_idx = [i for i, r in enumerate(table)
                   if r and r[0][0] and _norm_label(r[0][0]) in labels]
        if not rev_idx:
            if sum_labels:
                comp = parse_segment_table_q_sum(
                    table, cols, flat, list(sum_labels))
                records.extend(comp)
            continue
        # Segment sections: member-linked headers with empty value cells
        # (same convention as the annual parser).
        seg_member = None
        seg_text = None
        member_headers_seen = False
        text_headers_seen = False
        t_recs = []
        for i, row in enumerate(table):
            if not row or not row[0][0]:
                continue
            label, member_id = row[0]
            norm = _norm_label(label)
            value_cells = [c[0] for c in row[1:]]
            if value_cells and all(c == "" for c in value_cells):
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
                for j, (dur, end) in enumerate(cols):
                    if not dur or not end:
                        continue
                    c = j + 1  # column 0 is the label
                    if c >= len(row):
                        continue
                    value = _parse_value(row[c][0])
                    if value is None:
                        continue
                    t_recs.append({
                        "seg_member": seg_member,
                        "seg_text": seg_text,
                        "duration_months": dur,
                        "period_end": end,
                        "value_raw": value,
                        "label": label.strip(),
                    })
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


def parse_segment_table_q_sum(table, cols, flat, sum_labels):
    """Sum component lines per (segment, period) when no revenue label."""
    # Reuse the revenue-row machinery with the component labels.
    recs = []
    labels = [_norm_label(l) for l in sum_labels]
    seg_member = None
    seg_text = None
    member_headers_seen = False
    text_headers_seen = False
    t_recs = []
    for row in table:
        if not row or not row[0][0]:
            continue
        label, member_id = row[0]
        norm = _norm_label(label)
        value_cells = [c[0] for c in row[1:]]
        if value_cells and all(c == "" for c in value_cells):
            if member_id:
                seg_member = _segment_name(label, member_id)
                member_headers_seen = True
            elif "[member]" in label.lower():
                seg_text = _clean_segment_name(label)
                text_headers_seen = True
            continue
        if norm in labels:
            for j, (dur, end) in enumerate(cols):
                if not dur or not end:
                    continue
                c = j + 1
                if c >= len(row):
                    continue
                value = _parse_value(row[c][0])
                if value is None:
                    continue
                t_recs.append({
                    "seg_member": seg_member, "seg_text": seg_text,
                    "duration_months": dur, "period_end": end,
                    "value_raw": value, "label": label.strip(),
                })
    for r in t_recs:
        seg = r.pop("seg_member") if member_headers_seen else r.pop(
            "seg_text")
        r.pop("seg_member", None)
        r.pop("seg_text", None)
        r["segment_native"] = seg
        r["consolidated"] = seg is None
    scale = _table_scale(flat, [r["value_raw"] for r in t_recs])
    totals = {}
    for r in t_recs:
        key = (r["segment_native"], r["duration_months"], r["period_end"],
               r["consolidated"])
        if key not in totals:
            totals[key] = {**r, "value_m": r["value_raw"] * scale,
                           "label": " + ".join(sum_labels)}
        else:
            totals[key]["value_m"] += r["value_raw"] * scale
    for r in totals.values():
        r.pop("value_raw", None)
    return list(totals.values())


def parse_filing_dir_q(firm, cik, accession, filing_dir,
                       segment_default=None):
    """Parse all segment R-files of one downloaded 10-Q filing."""
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
            # Pre-v3 XML R-files: periods are in the KeyName; reuse the
            # annual XML parser then keep only sub-annual durations.
            # (Covered when such filings are encountered.)
            continue
        recs = parse_segment_table_q(text, extra_labels=extra,
                                     sum_labels=sum_labels)
        for r in recs:
            if r.get("consolidated"):
                continue
            seg = r["segment_native"] or segment_default or firm
            key = (seg, r["duration_months"], r["period_end"],
                   _norm_label(r["label"]))
            cand = {
                "firm": firm,
                "cik": cik,
                "segment_native": seg,
                "duration_months": r["duration_months"],
                "period_end": r["period_end"],
                "net_revenue_m": r["value_m"],
                "accession": accession,
                "r_file": rep["file"],
                "source": "XBRL R-file (10-Q)",
            }
            prev = by_key.get(key)
            # Later R-files in the report list don't override; keep first.
            if prev is None:
                by_key[key] = cand
    out = sorted(by_key.values(),
                 key=lambda r: (r["segment_native"], r["period_end"]))
    # Restatements: same (firm, segment, duration, period) in several
    # filings; the later filing wins. Accession sorts chronologically.
    return out


def build_segments_quarterly_csv(raw_root=RAW_ROOT, out_path=OUT_CSV,
                                 firms_ciks=None, since="2010-01-01"):
    """Walk the raw tree's 10-Q filings and write segments_quarterly.csv.

    10-Q accessions are those whose XBRL instance date is not the firm's
    fiscal year-end. (10-K accessions are skipped; the annual layer owns
    them.)
    """
    import re as _re
    raw_root = Path(raw_root)
    fye = {"0000096223": "1130", "0001302215": "0331"}
    records = []
    for cik_dir in sorted(raw_root.iterdir()):
        if not cik_dir.is_dir():
            continue
        cik = cik_dir.name
        firm = (firms_ciks or {}).get(cik, cik)
        for acc_dir in sorted(cik_dir.iterdir()):
            if not acc_dir.is_dir():
                continue
            # 10-Q vs 10-K: instance date month-day != fiscal year-end.
            is_q = False
            for p in acc_dir.iterdir():
                if p.suffix.lower() not in (".xbrl", ".xml"):
                    continue
                if p.name.startswith("R") or p.name == "FilingSummary.xml":
                    continue
                m = _re.search(r"(\d{4})(\d{2})(\d{2})", p.name)
                if m and m.group(2) + m.group(3) != fye.get(cik, "1231"):
                    is_q = True
                    break
            if not is_q:
                continue
            records.extend(
                parse_filing_dir_q(firm, cik, acc_dir.name, acc_dir))
    df = pl.DataFrame(records) if records else pl.DataFrame(
        [], schema={"firm": pl.String, "cik": pl.String,
                    "segment_native": pl.String,
                    "duration_months": pl.Int64, "period_end": pl.String,
                    "net_revenue_m": pl.Float64, "accession": pl.String,
                    "r_file": pl.String, "source": pl.String})
    df = df.filter(pl.col("period_end") >= since)
    df = df.sort(["firm", "segment_native", "duration_months", "period_end",
                  "accession"])
    df = df.unique(
        subset=["firm", "segment_native", "duration_months", "period_end"],
        keep="last")
    df = df.sort(["firm", "segment_native", "period_end"])
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
    ap.add_argument("--since", default="2010-01-01")
    args = ap.parse_args()
    firms_ciks = None
    if args.firms:
        import json
        firms_ciks = {v: k for k, v in json.load(open(args.firms)).items()}
    df = build_segments_quarterly_csv(args.raw, args.out, firms_ciks,
                                      since=args.since)
    print(f"wrote {args.out}: {len(df)} rows")


if __name__ == "__main__":
    main()
