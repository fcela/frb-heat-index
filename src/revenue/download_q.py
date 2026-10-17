"""EDGAR download layer for quarterly filings (10-Q / 10-Q/A).

Mirrors src/revenue/download.py for 10-Qs: for each of the 12 firms, list
all 10-Q / 10-Q/A accessions filed since 2010-01-01, then download per
filing:
  - FilingSummary.xml
  - every business-segment R-file listed in FilingSummary.xml
  - the main XBRL instance document (.xbrl)

Raw files are saved under data/revenue/raw/<cik>/<accession>/ alongside
the annual raws (10-Q accessions are disjoint from 10-K ones) and are
NEVER deleted or moved. Downloads are idempotent: files already on disk
(non-empty) are skipped.

Same EDGAR etiquette as download.py: descriptive User-Agent, ~1 req/s.

Run: python3 -m src.revenue.download_q [--firms JPMorgan,...]
       [--since 2010-01-01] [--log FILE]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.revenue.download import (  # noqa: E402
    ARCH,
    ARCH_HEADERS,
    DATA_SEC,
    FIRMS,
    HEADERS,
    RAW_ROOT,
    download_filing,
    find_segment_reports,
    sec_get,
)

Q_FORMS = ("10-Q", "10-Q/A")


def _filings_from_blob(blob, forms=Q_FORMS):
    """Extract 10-Q / 10-Q/A entries (same blob layout as 10-K listing)."""
    recent = blob.get("filings", {}).get("recent") or blob
    fforms = recent.get("form", [])
    out = []
    for i, form in enumerate(fforms):
        if form not in forms:
            continue
        out.append({
            "form": form,
            "accession": recent["accessionNumber"][i],
            "filing_date": recent["filingDate"][i],
            "primary_doc": recent["primaryDocument"][i],
            "report_date": recent.get("reportDate", [None] * len(fforms))[i],
        })
    return out


def list_10q_filings(cik, since="2010-01-01"):
    """All 10-Q / 10-Q/A filings for a CIK filed on/after `since`."""
    blob = json.loads(sec_get(f"{DATA_SEC}/submissions/CIK{cik}.json", HEADERS))
    filings = _filings_from_blob(blob)
    for f in blob.get("filings", {}).get("files", []):
        arch = json.loads(
            sec_get(f"{DATA_SEC}/submissions/{f['name']}", HEADERS))
        filings.extend(_filings_from_blob(arch))
    seen, deduped = set(), []
    for fl in filings:
        if fl["accession"] not in seen and fl["filing_date"] >= since:
            seen.add(fl["accession"])
            deduped.append(fl)
    return sorted(deduped, key=lambda x: x["filing_date"])


def download_all_q(since="2010-01-01", firms=None, data_root=None):
    """Download all 10-Q filings for the selected firms. Returns a summary."""
    import src.revenue.download as dl
    if data_root:
        dl.RAW_ROOT = Path(data_root)
    firms = firms or list(FIRMS)
    summary = {}
    for firm in firms:
        cik = FIRMS[firm]
        filings = list_10q_filings(cik, since=since)
        print(f"{firm} ({cik}): {len(filings)} 10-Q filings since {since}",
              flush=True)
        fetched = skipped = 0
        rfiles = 0
        for fl in filings:
            try:
                res = download_filing(cik, fl)
            except Exception as e:  # keep going; record the failure
                summary.setdefault("errors", []).append(
                    {"firm": firm, "cik": cik,
                     "accession": fl["accession"], "error": str(e)})
                print(f"  ERROR {fl['accession']}: {e}", flush=True)
                continue
            fetched += res["stats"]["fetch"]
            skipped += res["stats"]["skip"]
            rfiles += len(res["reports"])
            print(f"  {fl['filing_date']} {fl['form']} {fl['accession']}: "
                  f"{len(res['reports'])} segment R-files, "
                  f"instance={res['instance']}", flush=True)
        summary[firm] = {"cik": cik, "n_filings": len(filings),
                         "files_fetched": fetched, "files_skipped": skipped,
                         "segment_rfiles": rfiles}
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firms", default=",".join(FIRMS),
                    help="comma-separated firm names (default: all 12)")
    ap.add_argument("--since", default="2010-01-01")
    ap.add_argument("--log", default=None,
                    help="also append progress to this file")
    args = ap.parse_args()
    if args.log:
        logf = open(args.log, "a")
        _real_stdout = sys.stdout

        class _Tee:
            def write(self, s):
                _real_stdout.write(s)
                logf.write(s)
                logf.flush()

            def flush(self):
                _real_stdout.flush()
                logf.flush()

        sys.stdout = _Tee()
    firms = [f.strip() for f in args.firms.split(",") if f.strip()]
    summary = download_all_q(since=args.since, firms=firms)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
