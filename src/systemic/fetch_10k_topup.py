#!/usr/bin/env python3
"""Fetch only the missing 10-K anchors (top-up run)."""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from systemic.universe import SRISK_FIRMS  # noqa: E402
from systemic.fetch_10k_anchors import fetch_anchor, RAW, OUT  # noqa: E402

MISSING_TICKERS = ["AXP", "BNY", "COF", "FITB", "KEY", "PNC", "TFC", "WFC",
                   "RF", "HBAN", "USB", "SCHW", "JPM", "BAC", "C", "GS", "MS",
                   "STT", "AXP"]
MISSING_TICKERS = sorted(set(MISSING_TICKERS))
YEARS = list(range(1995, 2004))


def main() -> int:
    done: dict[tuple[str, int], int] = {}
    if OUT.exists():
        with open(OUT) as f:
            for row in csv.DictReader(f):
                done[(row["ticker"], int(row["fy"]))] = int(row["shares"])
    n_new = 0
    for ticker in MISSING_TICKERS:
        _, cik = SRISK_FIRMS[ticker]
        for fy in YEARS:
            if (ticker, fy) in done:
                continue
            try:
                v = fetch_anchor(ticker, cik, fy)
            except Exception as e:  # noqa: BLE001
                print(f"  {ticker} {fy}: error {e}", file=sys.stderr)
                v = None
            if v and v >= 1_000_000:
                done[(ticker, fy)] = v
                n_new += 1
                print(f"  {ticker} {fy}: {v:,}", flush=True)
            else:
                print(f"  {ticker} {fy}: NOT FOUND", file=sys.stderr, flush=True)
            time.sleep(0.7)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "fy", "shares"])
        for (t, fy), v in sorted(done.items()):
            w.writerow([t, fy, v])
    print(f"done: {len(done)} anchors ({n_new} new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
