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
    _rfile_rank,
    _table_scale,
    _norm_label,
    _segment_name,
    _clean_segment_name,
    REVENUE_LABELS,
    EXTRA_LABELS,
    REVENUE_SUMS,
)
from src.revenue.parse_mda import dir_form_type  # noqa: E402
from src.revenue.taxonomy import accession_order_exprs, cik_firm_map  # noqa: E402

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
    Returns a list of (column_index, duration, period_end); column_index
    is the absolute cell index in the table row, so value extraction
    works even when the label cell spans multiple columns. Returns None
    when the table has no period headers (not a quarterly segment table).
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
    # The date row often lacks the label-column cell the duration row has;
    # align from the right so durations and dates pair correctly.
    if len(dates) < len(durs):
        dates = [None] * (len(durs) - len(dates)) + dates
    elif len(durs) < len(dates):
        durs = [None] * (len(dates) - len(durs)) + durs
    # Forward-fill: duration/date cells often span merged columns; the
    # parser expands colspans, but some filers leave blanks. Never invent
    # a duration for a point-in-time column: an explicit date with no
    # duration is a balance-sheet column (e.g. "Dec. 31, 2024" assets),
    # not a period. (Regression: Citi 2025-Q3 10-Q labeled its Dec-31-2024
    # point-in-time column (9, 2024-12-31), shifting 9M values into the
    # wrong periods and producing a bogus annual-derived Q4.)
    cols = []
    last_dur = last_date = None
    for idx, (du, da) in enumerate(zip(durs, dates)):
        if du is None and da is not None:
            cols.append((idx, None, da))
            continue
        last_dur = du or last_dur
        last_date = da or last_date
        cols.append((idx, last_dur, last_date))
    # The label column(s) have no period; drop leading (None, None).
    while cols and cols[0][1] is None and cols[0][2] is None:
        cols.pop(0)
    cols = [(i, d, dt) for (i, d, dt) in cols if d and dt]
    if not cols:
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
                for c, dur, end in cols:
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
            for c, dur, end in cols:
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
        # ASC 606 revenue-disaggregation notes rank below every segment
        # note (see parse_xbrl._rfile_rank): they describe contract
        # revenue, not segment revenue. Wells Fargo's 10-Qs carry both
        # notes; the 606 note's WIM block has "Net interest income" but
        # no bare "noninterest income" row, so the sum collapsed to
        # NII-only (~$1.1B/quarter) and beat the segment note's full
        # NII + noninterest income (~$3.9B/quarter) on report order --
        # the quarterly face of the 2017 annual hole.
        rank = _rfile_rank(rep["short_name"])
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
            # Same (segment, duration, period, label) from two R-files:
            # the better-ranked R-file wins (report order is not a
            # preference).
            if prev is None or rank < prev[1]:
                by_key[key] = (cand, rank)
    # Label preference: one (segment, duration, period) can carry
    # several generic revenue labels from the same filing -- e.g.
    # Jefferies' segment note reports both "Total revenues" and "Net
    # revenues" rows (gross vs net of interest expense). Keep the
    # record whose label ranks highest in REVENUE_LABELS ("total net
    # revenues" first ... "total revenues" last), the same preference
    # the extraction documents, so the global restatement dedupe never
    # has to break a same-filing label tie arbitrarily. Synthesized
    # sum-labels ("a + b") rank below every direct label. Distinct fee
    # lines (PJT "Advisory Fees" vs "Placement Fees") sit under their
    # own segment names and never compete here.
    # R-file rank outranks label preference: a segment note's summed
    # NII + noninterest income (the only form Wells Fargo's segment
    # tables disclose revenue in) must beat a 606 note's bare
    # "Revenues" row even though "revenues" labels earlier in
    # REVENUE_LABELS. PJT-style fee rows keep coming from their
    # disaggregation notes only because no better-ranked R-file
    # produces the same key.
    extra_norm = [_norm_label(e) for e in extra]

    def _label_rank(norm):
        if norm in REVENUE_LABELS:
            return REVENUE_LABELS.index(norm)
        if norm in extra_norm:
            return len(REVENUE_LABELS)
        return len(REVENUE_LABELS) + 1

    best: dict[tuple, tuple] = {}
    for (seg, dur, end, norm), (cand, rank) in by_key.items():
        key = (seg, dur, end)
        prev = best.get(key)
        if prev is None or (rank, _label_rank(norm)) < prev[1]:
            best[key] = (cand, (rank, _label_rank(norm)))
    out = sorted((cand for cand, _ in best.values()),
                 key=lambda r: (r["segment_native"], r["period_end"]))
    # Restatements: same (firm, segment, duration, period) in several
    # filings; the later filing wins (see build_segments_quarterly_csv
    # for the cross-filing ordering).
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
            # 10-Q vs 10-K: the XBRL instance's dei:DocumentType is
            # definitive. The historical fallback -- an 8-digit period
            # date in the instance *filename* differing from the fiscal
            # year-end -- misses whole filing-agent naming conventions
            # (GS's 2019Q2-2022Q2 10-Qs are "d105283d10q_htm.xml"), which
            # silently dropped those filings from the quarterly XBRL
            # layer and is why GS's quarterly panel stopped at 2019Q1.
            # Keep the fallback only for accessions whose instances
            # state no document type (pre-XBRL-era filings).
            form = dir_form_type(acc_dir)
            if form is not None:
                is_q = form.upper().startswith("10-Q")
            else:
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
    # Canonicalize label-only renames (e.g. JPM "Asset Management" ->
    # "Asset & Wealth Management") BEFORE dedup, so restated series
    # collide on the canonical name and the later filing wins.
    try:
        from src.revenue.reorg_registry import canonical_segment
        df = df.with_columns(
            pl.struct(["firm", "segment_native"]).map_elements(
                lambda r: canonical_segment(r["firm"], r["segment_native"]),
                return_dtype=pl.String,
            ).alias("segment_native")
        )
    except ImportError:
        pass
    # Restatements: the same (firm, segment, duration, period) appears
    # in several filings; the later filing's value wins. "Later" is the
    # accession's (filing year, sequence) -- accession strings do not
    # sort chronologically across filing-agent prefixes.
    acc_y, acc_s = accession_order_exprs("accession")
    df = df.with_columns(acc_y.alias("_acc_year"), acc_s.alias("_acc_seq"))
    df = df.sort(["firm", "segment_native", "duration_months", "period_end",
                  "_acc_year", "_acc_seq", "accession"],
                 maintain_order=True)
    df = df.unique(
        subset=["firm", "segment_native", "duration_months", "period_end"],
        keep="last")
    df = df.drop(["_acc_year", "_acc_seq"])
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
                    help="JSON file mapping CIK -> firm display name "
                         "(default: the built-in taxonomy CIK map)")
    ap.add_argument("--since", default="2010-01-01")
    args = ap.parse_args()
    firms_ciks = cik_firm_map()
    if args.firms:
        import json
        firms_ciks = {v: k for k, v in json.load(open(args.firms)).items()}
    df = build_segments_quarterly_csv(args.raw, args.out, firms_ciks,
                                      since=args.since)
    print(f"wrote {args.out}: {len(df)} rows")


if __name__ == "__main__":
    main()
