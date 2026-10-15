#!/usr/bin/env python3
"""Fetch monthly price histories from Yahoo Finance's public chart API.

Endpoint: https://query1.finance.yahoo.com/v8/finance/chart/<T>
No API key, no login. Stores one JSON per ticker under work/raw/yahoo/.

Tickers: the SRISK BHC panel + the Granger connectedness panel + ^GSPC
(S&P 500, the market index for beta estimation).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from systemic.universe import GRANGER_TICKERS, SRISK_FIRMS  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; frb-heat-index/1.0)"}
RAW = Path(__file__).resolve().parent.parent.parent / "work" / "raw" / "yahoo"


def fetch(ticker: str, dest: Path) -> bool:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?period1=0&period2=9999999999&interval=1mo&events=div%2Csplit")
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"  {ticker}: fetch failed: {e}", file=sys.stderr)
        return False
    try:
        d = json.loads(body)
        res = d["chart"]["result"]
    except Exception:  # noqa: BLE001
        print(f"  {ticker}: bad response", file=sys.stderr)
        return False
    if not res:
        print(f"  {ticker}: no data", file=sys.stderr)
        return False
    dest.write_bytes(body)
    n = len(res[0]["timestamp"])
    print(f"  {ticker}: {n} months")
    return True


def main() -> int:
    tickers = sorted(set(SRISK_FIRMS) | set(GRANGER_TICKERS) | {"^GSPC"})
    RAW.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    for t in tickers:
        dest = RAW / f"{t.replace('^', 'IDX_')}.json"
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"  {t}: cached")
            ok += 1
            continue
        if fetch(t, dest):
            ok += 1
        else:
            fail += 1
        time.sleep(1.0)  # be polite
    print(f"done: {ok} ok, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
