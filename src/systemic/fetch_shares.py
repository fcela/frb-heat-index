#!/usr/bin/env python3
"""Fetch quarterly shares outstanding from SEC EDGAR XBRL companyfacts.

Endpoint: https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json
Public, no key. Rate limit 10 req/s (we use ~2/s).

Concept: dei:EntityCommonStockSharesOutstanding, units "shares".
One CSV per ticker under work/raw/edgar/: end_date,value (latest filing
wins on duplicate end dates).

Covers ~2009+ (XBRL era). Pre-2009 shares are split-adjusted backward
carries from the earliest XBRL observation (see build_srisk.py).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from systemic.universe import SRISK_FIRMS  # noqa: E402

UA = {"User-Agent": "frb-heat-index research contact@example.com"}
RAW = Path(__file__).resolve().parent.parent.parent / "work" / "raw" / "edgar"


def fetch_shares(cik: int) -> list[tuple[str, float]]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    units = (d.get("facts", {}).get("dei", {})
             .get("EntityCommonStockSharesOutstanding", {}).get("units", {}))
    obs = units.get("shares", [])
    # latest filing wins per end date
    best: dict[str, tuple[str, float]] = {}
    for o in obs:
        end, filed = o.get("end"), o.get("filed", "")
        try:
            v = float(o["val"])
        except (KeyError, TypeError, ValueError):
            continue
        if end and (end not in best or filed > best[end][0]):
            best[end] = (filed, v)
    return sorted((e, v) for e, (_, v) in best.items())


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    for ticker, (_, cik) in sorted(SRISK_FIRMS.items()):
        dest = RAW / f"shares_{ticker}.csv"
        if dest.exists():
            print(f"  {ticker}: cached")
            ok += 1
            continue
        try:
            rows = fetch_shares(cik)
        except Exception as e:  # noqa: BLE001
            print(f"  {ticker}: failed: {e}", file=sys.stderr)
            fail += 1
            continue
        if not rows:
            print(f"  {ticker}: no shares data", file=sys.stderr)
            fail += 1
            continue
        with open(dest, "w") as f:
            f.write("end_date,shares\n")
            for e, v in rows:
                f.write(f"{e},{v:.0f}\n")
        print(f"  {ticker}: {len(rows)} obs ({rows[0][0]}..{rows[-1][0]})")
        ok += 1
        time.sleep(0.6)
    print(f"done: {ok} ok, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
