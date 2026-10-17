"""Batch-fetch missing 10-K main documents for the MD&A build.

Tries EDGAR directly, then the Wayback fallback, per filing. Idempotent:
skips accessions that already have a main doc on disk. Logs to stdout.

Run in background: python3 src/revenue/fetch_main_docs.py [--log FILE]
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.revenue.parse_mda import (  # noqa: E402
    FIRMS,
    ARCHIVE_BASE,
    EDGAR_ARCHIVES,
    RateLimiter,
    _main_docs_present,
    fetch_filing,
)

log = logging.getLogger("fetch_main_docs")

RAW_ROOT = Path(__file__).resolve().parents[2] / "data" / "revenue" / "raw"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--accessions", default="/tmp/mda_accessions_tree.json")
    ap.add_argument("--firms", default=",".join(FIRMS))
    ap.add_argument("--log", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.log:
        fh = logging.FileHandler(args.log)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
        logging.getLogger("parse_mda").addHandler(fh)

    accs = json.loads(Path(args.accessions).read_text())
    firms = [f.strip() for f in args.firms.split(",") if f.strip()]
    # Be polite: 2s between filings, on top of the per-request limiter.
    lim = RateLimiter(min_interval=0.5)
    ok = fail = skipped = 0
    for firm in firms:
        cik = FIRMS[firm]["cik"]
        for acc in accs.get(firm, []):
            dest = RAW_ROOT / cik / acc
            if _main_docs_present(dest):
                skipped += 1
                continue
            done = False
            # Wayback first: EDGAR's WAF intermittently 403s index.json
            # for 10-K paths (verified 2026-10-17); the archive holds the
            # same bytes. EDGAR direct is the fallback.
            for base in (ARCHIVE_BASE, EDGAR_ARCHIVES):
                try:
                    p = fetch_filing(cik, acc, RAW_ROOT, lim, base)
                    log.info("OK %s %s via %s (%d bytes)", firm, acc,
                             base.split("/")[2], p.stat().st_size)
                    ok += 1
                    done = True
                    break
                except Exception as e:  # noqa: BLE001 -- try next mirror
                    log.warning("FAIL %s %s via %s: %s", firm, acc,
                                base.split("/")[2], str(e)[:120])
            if not done:
                fail += 1
            time.sleep(2.0)
    log.info("done: %d fetched, %d skipped, %d failed", ok, skipped, fail)


if __name__ == "__main__":
    main()
