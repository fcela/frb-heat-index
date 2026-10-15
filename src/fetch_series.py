#!/usr/bin/env python3
"""Download the raw inputs for the Fed heat-map replication into work/raw/.

This is stage 1 of the quarterly pipeline: raw -> work/raw/.
Stage 2 (src/build_panel.py) turns these files into data/panel_quarterly.csv;
stage 3 (src/run_pipeline.py) turns the panel into the index.

Everything here uses plain HTTPS downloads (curl, stdlib only); no API keys.
Files already present are skipped unless --force is given. Sources that need a
manual download (flagged below) are reported, not fetched.

Usage:
    python src/fetch_series.py [--raw-dir work/raw] [--force]
                                [--start-year 1990] [--end-year 2014]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

UA = "frb-heat-index/1.0 (replication build; contact via repo issues)"
HERE = Path(__file__).resolve().parent.parent
DEFAULT_RAW = HERE / "work" / "raw"


def _curl_dl(url: str, dest: Path, tries: int = 4, min_bytes: int = 1) -> None:
    last: object = None
    for k in range(tries):
        try:
            subprocess.run(
                ["curl", "-sSL", "--max-time", "600", "--retry", "2",
                 "-A", UA, "-o", str(dest), url],
                check=True, timeout=660,
            )
            if dest.stat().st_size >= min_bytes:
                return
            last = "empty file"
        except Exception as e:  # noqa: BLE001 - retry transient network errors
            last = e
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"download failed for {url}: {last}")


# ---------------------------------------------------------------------------
# Source manifest.
# Each entry: (local filename, url, kind) where kind is "auto" (curl works) or
# "manual" (needs a human download; only reported).
# ---------------------------------------------------------------------------

FRED_IDS = [
    # equity / spreads / volatility
    "DGS10", "BAA", "VIXCLS",
    # SLOOS (Senior Loan Officer Opinion Survey)
    "DRTSCILM", "DRTSPM",
    # housing
    "CSUSHPISA", "CUSR0000SEHA",
    # consumer / mortgage credit
    "TOTALSL", "TDSP", "HHMSDODNS", "MDSP", "REVOLSL", "DPI", "EXHOSL",
    # saving
    "PSAVERT", "DSPI",
    # bank leverage (round-1 commercial-bank proxy; Y-9C replaces it)
    "EQTA",
    # macro denominators
    "GDP", "GDPDEF",
    # Z.1 financial accounts via FRED (levels; $M or $B as documented)
    "NCBDBIQ027S",      # nonfinancial corporate business debt securities+loans ($M)
    "TCMILBSNNB",       # nonfinancial noncorporate business debt securities+loans ($M)
    "BOGZ1FL794190005Q",  # domestic financial sectors, total liabilities
    "BOGZ1FA674190005Q",  # issuers of asset-backed securities, liability transactions
    "BOGZ1FL664090005Q",  # security brokers/dealers, total financial assets
    "BOGZ1FL664190005Q",  # security brokers/dealers, total liabilities
    "BOGZ1FL662051003Q",  # brokers/dealers, repo assets (reverse repos)
    "BOGZ1FL662151003Q",  # brokers/dealers, repo liabilities
    "BOGZ1FL794141005Q",  # financial sectors, short-term loans liabilities
    "BOGZ1FL793169175Q",  # financial sectors, open market paper liabilities
    "BOGZ1FL792050005Q",  # financial sectors, fed funds & repo assets
    "BOGZ1FL793069175Q",  # financial sectors, open market paper assets
    "BOGZ1FL792150005Q",  # financial sectors, fed funds & repo liabilities
    "BOGZ1FL634090005Q",  # money market funds, total financial assets
    "BOGZ1FL793063005Q",  # financial sectors, corporate & foreign bond assets
    "BOGZ1FL794035005Q",  # financial sectors, total loan assets
    "BOGZ1FL794090005Q",  # financial sectors, total financial assets
    "BOGZ1FL104090103Q",  # nonfinancial corporate business, total assets
    "BOGZ1FL893065125Q",  # all sectors, home equity loans (closed-end junior liens), asset level ($M)
]

SOURCES: list[tuple[str, str, str]] = (
    [("fred_%s.csv" % sid,
       f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}",
       "auto") for sid in FRED_IDS]
    + [
        # Long-run equity valuations (E/P): university-hosted monthly file.
        # NOTE: the hosted file froze at 2023Q1; the E/P series stop there.
        ("ie_data.xls",
         "http://www.econ.yale.edu/~shiller/data/ie_data.xls",
         "auto"),
        # BEA NIPA quarterly bulk (saving, interest/cash-flow constructions).
        ("NipaDataQ.txt",
         "https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt",
         "auto"),
        # Fed-posted monthly excess-bond-premium / GZ credit-spread file
        # (1973+). Permanent URL (verified 2026-10-15); if it moves again,
        # place the file manually as ebp_raw.csv.
        ("ebp_raw.csv",
         "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv",
         "manual"),
        # BIS commercial property price statistics, US, quarterly
        # (series Q:US:0:A:0:2:6:0). Download the CSV from the BIS portal.
        ("bis_cre_us.csv",
         "https://www.bis.org/statistics/pp/pp_detailed.htm",
         "manual"),
        # SLOOS historical tables (XML archive) for the CRE lending-standards
        # splice: discontinued SUBLPDRCS_N.Q plus the three successor series
        # (construction & land development, nonfarm nonresidential, multifamily).
        ("sloos_history.xml",
         "https://www.federalreserve.gov/data/sloos/sloos_xml.htm",
         "manual"),
        # GSE annual credit supplements: UPB-weighted mean origination FICO,
        # single-family acquisitions (manual transcription to fico_annual.csv
        # with columns year,fico).
        ("fico_annual.csv", "", "manual"),
    ]
)

Y9C_BASE = ("https://www.chicagofed.org/~/media/others/banking/"
            "financial-institution-reports/bhc-data")


def y9c_files(start_year: int, end_year: int) -> list[tuple[str, str]]:
    files = []
    for y in range(start_year, end_year + 1):
        yy = f"{y % 100:02d}"
        for q in (1, 2, 3, 4):
            mm = f"{q * 3:02d}"
            fn = f"bhcf{yy}{mm}.csv"
            files.append((f"y9c/{fn}", f"{Y9C_BASE}/{fn}"))
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the file already exists")
    ap.add_argument("--start-year", type=int, default=1990)
    ap.add_argument("--end-year", type=int, default=2014)
    ap.add_argument("--no-y9c", action="store_true",
                    help="skip the (large) Y-9C bulk downloads")
    args = ap.parse_args()

    raw = Path(args.raw_dir)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "y9c").mkdir(exist_ok=True)

    ok, failed, manual = [], [], []
    jobs = list(SOURCES)
    if not args.no_y9c:
        jobs += [(fn, url, "auto") for fn, url in
                 y9c_files(args.start_year, args.end_year)]

    for fn, url, kind in jobs:
        dest = raw / fn
        if dest.exists() and dest.stat().st_size > 0 and not args.force:
            ok.append(fn)
            continue
        if kind == "manual" or not url:
            manual.append(fn)
            print(f"MANUAL  {fn}  <- {url or '(place by hand)'}")
            continue
        try:
            print(f"GET     {fn}", flush=True)
            _curl_dl(url, dest, min_bytes=100)
            ok.append(fn)
        except Exception as e:  # noqa: BLE001 - report and continue
            failed.append((fn, str(e)))
            print(f"FAILED  {fn}: {e}")

    print(f"\n{len(ok)} present, {len(failed)} failed, {len(manual)} manual")
    for fn, e in failed:
        print(f"  failed: {fn}: {e}")
    if manual:
        print("manual downloads needed:")
        for fn in manual:
            print(f"  {fn}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
