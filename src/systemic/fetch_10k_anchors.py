#!/usr/bin/env python3
"""Fetch 10-K cover shares-outstanding anchors (FY2004-FY2010).

The XBRL backward carry misses crisis-era share issuances (Citi, BAC,
...), which materially understates 2008 SRISK. Annual 10-K anchors fix
the critical window. One CSV: work/raw/edgar/shares_10k_anchors.csv
(ticker, fy, shares).

Method per (firm, fy):
- EDGAR full-text search for the 10-K filed Jan-Apr of fy+1.
- Download the main 10-K document, regex the cover:
  "Number of shares of ... common stock outstanding ...: N"
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from systemic.universe import SRISK_FIRMS  # noqa: E402

UA = {"User-Agent": "frb-heat-index research contact@example.com"}
RAW = Path(__file__).resolve().parent.parent.parent / "work" / "raw" / "edgar"
OUT = RAW / "shares_10k_anchors.csv"
YEARS = list(range(2004, 2011))

COVER_PATTERNS = [
    # "Number of shares of common stock outstanding on January 31, 2005: 3,553,701,118"
    re.compile(
        r"number of shares of (?:the registrant'?s? )?common stock outstanding"
        r"[^.]{0,120}?([\d,]{7,})",
        re.IGNORECASE,
    ),
    # "As of February 2, 2009, Citigroup had 5,476,213,462 shares of common stock outstanding"
    re.compile(
        r"had ([\d,]{7,}) shares of (?:the registrant'?s? )?common stock outstanding",
        re.IGNORECASE,
    ),
    # "there were outstanding 10 shares of the issuer's common stock"
    re.compile(
        r"there were outstanding ([\d,]{1,}) shares of .*?common stock",
        re.IGNORECASE,
    ),
    # "there were 6,401,387,626 shares of Common Stock outstanding"
    re.compile(
        r"there were ([\d,]{7,}) shares of .*?common stock outstanding",
        re.IGNORECASE,
    ),
    # "At January 31, 2009, 4,237,777,218 shares of common stock were outstanding"
    re.compile(
        r"([\d,]{7,}) shares of (?:the registrant'?s? )?common stock were outstanding",
        re.IGNORECASE,
    ),
]


def get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def find_10k(cik: int, fy: int) -> tuple[str, str] | None:
    """(accession_nodash, cik_dir) for the FY 10-K."""
    url = (f"https://efts.sec.gov/LATEST/search-index?forms=10-K"
           f"&dateRange=custom&startdt={fy + 1}-01-01&enddt={fy + 1}-05-01"
           f"&ciks={cik:010d}")
    d = json.loads(get(url, timeout=30))
    hits = d.get("hits", {}).get("hits", [])
    for h in hits:
        s = h.get("_source", {})
        if s.get("form") == "10-K" and str(s.get("ciks", [""])[0]).lstrip("0") == str(cik):
            adsh = s["adsh"].replace("-", "")
            return adsh, f"{cik}"
    return None


def parse_cover_shares(doc: bytes) -> int | None:
    import html
    t = doc.decode("utf-8", errors="ignore")
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = re.sub(r"\s+", " ", t)
    # Cover is at the very top for standard 10-Ks, elsewhere for
    # "annual report" style; take the earliest match of any pattern.
    best: tuple[int, int] | None = None
    for pat in COVER_PATTERNS:
        for m in pat.finditer(t):
            v = int(m.group(1).replace(",", ""))
            if best is None or m.start() < best[0]:
                best = (m.start(), v)
    return best[1] if best else None


def fetch_anchor(ticker: str, cik: int, fy: int) -> int | None:
    found = find_10k(cik, fy)
    if not found:
        return None
    adsh, cikdir = found
    idx = json.loads(get(
        f"https://www.sec.gov/Archives/edgar/data/{cikdir}/{adsh}/index.json"))
    # main 10-K document: prefer *10vk*/*10k* names, else largest non-exhibit doc
    # (.htm preferred; fall back to .txt for pre-1996 filings)
    cands = [it for it in idx["directory"]["item"]
             if it["name"].lower().endswith((".htm", ".html", ".txt"))
             and "exv" not in it["name"].lower()
             and not it["name"].lower().startswith("ex")
             and "index" not in it["name"].lower()]
    if not cands:
        return None
    tenk = [c for c in cands if "10vk" in c["name"].lower() or "10-k" in c["name"].lower()]
    if not tenk:
        # prefer .htm over .txt, then largest
        htms = [c for c in cands if c["name"].lower().endswith((".htm", ".html"))]
        tenk = htms or cands
    doc_name = sorted(tenk, key=lambda x: int(x.get("size", 0) or 0),
                      reverse=True)[0]["name"]
    doc = get(f"https://www.sec.gov/Archives/edgar/data/{cikdir}/{adsh}/{doc_name}",
              timeout=120)
    return parse_cover_shares(doc)


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    done: dict[tuple[str, int], int] = {}
    if OUT.exists():
        import csv
        with open(OUT) as f:
            for row in csv.DictReader(f):
                done[(row["ticker"], int(row["fy"]))] = int(row["shares"])
    n_new = 0
    for ticker, (_, cik) in sorted(SRISK_FIRMS.items()):
        for fy in YEARS:
            if (ticker, fy) in done:
                continue
            try:
                v = fetch_anchor(ticker, cik, fy)
            except Exception as e:  # noqa: BLE001
                print(f"  {ticker} {fy}: error {e}", file=sys.stderr)
                v = None
            if v:
                if v < 1_000_000:
                    # private-subsidiary filing (e.g. GMAC's "10 shares");
                    # not a meaningful public share count
                    print(f"  {ticker} {fy}: implausible ({v:,}), skipped", file=sys.stderr)
                else:
                    done[(ticker, fy)] = v
                    n_new += 1
                    print(f"  {ticker} {fy}: {v:,}")
            else:
                print(f"  {ticker} {fy}: NOT FOUND", file=sys.stderr)
            time.sleep(0.7)
    import csv
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "fy", "shares"])
        for (t, fy), v in sorted(done.items()):
            w.writerow([t, fy, v])
    print(f"done: {len(done)} anchors ({n_new} new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
