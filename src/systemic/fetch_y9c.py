#!/usr/bin/env python3
"""Download missing Y-9C bulk quarters (2021Q4-2026Q2) from the NIC.

Mirrors .github/scripts/update.py's y9c_fetch: NIC zip -> caret-delimited
-> comma-delimited work/raw/y9c/bhcf<yy><mm>.csv.
"""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

csv.field_size_limit(sys.maxsize)

RAW = Path(__file__).resolve().parent.parent.parent / "work" / "raw" / "y9c"
NIC_URL = ("https://www.ffiec.gov/npw/FinancialReport/ReturnBHCFZipFiles"
           "?zipfilename=BHCF{yyyymmdd}.ZIP")
UA = "Mozilla/5.0 (compatible; frb-heat-index/1.0)"

QUARTERS = [
    (2021, 4), (2022, 1), (2022, 2), (2022, 3), (2022, 4),
    (2023, 1), (2023, 2), (2023, 3), (2023, 4),
    (2024, 1), (2024, 2), (2024, 3), (2024, 4),
    (2025, 1), (2025, 2), (2025, 3), (2025, 4),
    (2026, 1), (2026, 2),
]
Q_END = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}


def curl_dl(url: str, dest: Path, tries: int = 4) -> None:
    for k in range(tries):
        try:
            subprocess.run(["curl", "-sSL", "--max-time", "600", "--retry", "2",
                            "-A", UA, "-o", str(dest), url],
                           check=True, timeout=660)
            if dest.stat().st_size >= 100_000:
                return
        except Exception as e:  # noqa: BLE001
            print(f"    retry {k + 1}: {e}", file=sys.stderr)
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"download failed: {url}")


def nic_text_to_csv(raw_text: str) -> str:
    """Convert a caret-delimited NIC bulk file to standard CSV text.

    NIC files are caret-delimited with NO quoting convention: embedded double
    quotes in free-text memo items are literal data. Parsing with a CSV quote
    character makes the reader swallow carets/newlines inside a "..." span,
    corrupting the row structure (one unterminated memo field merges every
    following line into one row -- this is what silently dropped 2023Q4 from
    the Y-9C panel). Quote processing is therefore OFF for the caret read;
    csv.writer then re-quotes fields containing commas per standard CSV.
    """
    out = io.StringIO()
    w = csv.writer(out)
    for row in csv.reader(io.StringIO(raw_text), delimiter="^", quotechar=None):
        w.writerow(row)
    return out.getvalue()


def fetch_one(year: int, q: int) -> None:
    yyyymmdd = f"{year}{Q_END[q]}"
    dest = RAW / f"bhcf{year % 100:02d}{q * 3:02d}.csv"
    if dest.exists() and dest.stat().st_size > 100_000:
        print(f"  {year}Q{q}: cached")
        return
    url = NIC_URL.format(yyyymmdd=yyyymmdd)
    tmp = Path(tempfile.mkdtemp())
    try:
        zp = tmp / "bhcf.zip"
        curl_dl(url, zp)
        with zipfile.ZipFile(zp) as z:
            raw_text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(nic_text_to_csv(raw_text), newline="")
        print(f"  {year}Q{q}: {dest.stat().st_size / 1e6:.1f} MB")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    for year, q in QUARTERS:
        try:
            fetch_one(year, q)
        except Exception as e:  # noqa: BLE001
            print(f"  {year}Q{q}: FAILED: {e}", file=sys.stderr)
            return 1
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
