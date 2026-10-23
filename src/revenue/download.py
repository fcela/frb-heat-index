"""EDGAR download layer for the bank revenue tracker.

For each of the 12 firms: list all 10-K / 10-K/A accession numbers filed since
2010, then download for each filing:
  - FilingSummary.xml
  - every "Business Segments" R-file listed in FilingSummary.xml
  - the main XBRL instance document (.xbrl)

Raw files are saved under data/revenue/raw/<cik>/<accession>/ and are NEVER
deleted or moved by the parser. Downloads are idempotent: files already on
disk (non-empty) are skipped.

EDGAR etiquette: requests carry a descriptive User-Agent, at most 10 requests
per second, with a polite pause between requests. ~12 firms x ~16 filings is a
few hundred requests; the pacing below stays well under the limit.

Run: python3 -m src.revenue.download [--firms JPMorgan,...] [--since 2010-01-01]
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.revenue.parse_xbrl import find_segment_reports  # noqa: E402
from src.revenue.taxonomy import FIRM_CIKS as FIRMS  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RAW_ROOT = REPO / "data" / "revenue" / "raw"

USER_AGENT = ("frb-heat-index/1.0 (bank revenue tracker research; "
              "contact frb-heat-index@example.com)")
# Notes on EDGAR's request filtering (verified 2026-10-16):
# - www.sec.gov's WAF rejects User-Agents without an "@" contact token.
# - data.sec.gov's WAF rejects User-Agents containing URLs.
# example.com is a reserved documentation domain (RFC 2606): the address is
# a placeholder, not a real contact.
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
    "Host": "data.sec.gov",
}
ARCH_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
    "Host": "www.sec.gov",
}

DATA_SEC = "https://data.sec.gov"
ARCH = "https://www.sec.gov"

PAUSE = 1.0  # seconds between requests (~1 req/s); EDGAR allows 10/s but
# throttles aggressively, so stay well below the cap.
THROTTLE_PAUSE = 60  # wait this long when a 403 signals throttling
MAX_RETRIES = 6


def _throttled(url, status, attempt):
    print(f"  throttled ({status}) on {url}; backing off "
          f"{THROTTLE_PAUSE * (attempt + 1)}s", flush=True)
    time.sleep(THROTTLE_PAUSE * (attempt + 1))


def sec_get(url, host_header, timeout=60):
    """GET with retries and a polite pause; returns the response text.

    EDGAR answers 403 both for bad requests and for throttling. A 403 is
    treated as throttling (long backoff); anything else non-200 is fatal.
    """
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=host_header, timeout=timeout)
            if r.status_code == 200:
                time.sleep(PAUSE)
                return r.text
            if r.status_code in (403, 429, 500, 502, 503, 504):
                _throttled(url, r.status_code, attempt)
                continue
            raise RuntimeError(f"GET {url} -> HTTP {r.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            print(f"  connection error ({e}); retrying", flush=True)
            time.sleep(PAUSE * 5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} retries")


def _filings_from_blob(blob):
    """Extract 10-K / 10-K/A entries.

    The main submissions blob nests the arrays under filings.recent; the
    archive blobs (CIK{cik}-submissions-NNN.json) carry the same arrays flat
    at the top level.
    """
    recent = blob.get("filings", {}).get("recent") or blob
    forms = recent.get("form", [])
    out = []
    for i, form in enumerate(forms):
        if form not in ("10-K", "10-K/A"):
            continue
        out.append({
            "form": form,
            "accession": recent["accessionNumber"][i],
            "filing_date": recent["filingDate"][i],
            "primary_doc": recent["primaryDocument"][i],
        })
    return out


def list_10k_filings(cik, since="2010-01-01"):
    """All 10-K / 10-K/A filings for a CIK filed on/after `since` (ISO date)."""
    blob = json.loads(sec_get(f"{DATA_SEC}/submissions/CIK{cik}.json", HEADERS))
    filings = _filings_from_blob(blob)
    # Older filings live in archive blobs listed under "files".
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


def _fetch_bytes(url, host_header):
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=host_header, timeout=120)
            if r.status_code == 200:
                time.sleep(PAUSE)
                return r.content
            if r.status_code in (403, 429, 500, 502, 503, 504):
                _throttled(url, r.status_code, attempt)
                continue
            raise RuntimeError(f"GET {url} -> HTTP {r.status_code}")
        except (requests.ConnectionError, requests.Timeout) as e:
            print(f"  connection error ({e}); retrying", flush=True)
            time.sleep(PAUSE * 5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} retries")


def _save(dest, url, host_header):
    if dest.exists() and dest.stat().st_size > 0:
        return "skip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_fetch_bytes(url, host_header))
    return "fetch"


def _instance_from_index(cik, acc_nodash, base):
    """Find the main XBRL instance via the filing's directory listing.

    EDGAR's per-filing index.json now 404s (verified 2026-10-17); the
    directory listing itself returns 200 and carries the same file table.
    Modern filings use {prefix}-{yyyymmdd}.xbrl; pre-2013 filings use
    {prefix}-{yyyymmdd}.xml. Linkbases (*_cal/_def/_lab/_pre) and R-files
    are excluded.
    """
    try:
        html = sec_get(base, ARCH_HEADERS)
    except RuntimeError:
        return None
    names = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)
        if not cells:
            continue
        n = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if n:
            names.append(n)
    cands = []
    htm_xml = []
    for n in names:
        if "_htm" in n:
            # Inline-XBRL filings have no standalone instance; the
            # extracted XBRL (_htm.xml) is the closest equivalent.
            if n.endswith("_htm.xml"):
                htm_xml.append(n)
            continue
        if n == "FilingSummary.xml":
            continue
        if n.endswith(".xbrl"):
            cands.append(n)
        elif re.match(r".+-\d{8}\.xml$", n) and not re.search(
                r"_(cal|def|lab|pre)\.xml$", n):
            cands.append(n)
    if not cands:
        cands = htm_xml
    if not cands:
        return None
    # Prefer the {prefix}-{yyyymmdd} primary instance.
    for name in cands:
        stem = name.rsplit(".", 1)[0]
        if len(stem.split("-")) == 2 and stem.split("-")[1].isdigit():
            return name
    return sorted(cands)[0]


def download_filing(cik, filing, dest_dir=None):
    """Download FilingSummary.xml, segment R-files, and the XBRL instance.

    Returns a dict of what was fetched vs skipped.
    """
    acc = filing["accession"]
    acc_nodash = acc.replace("-", "")
    cik_noint = str(int(cik))
    base = f"{ARCH}/Archives/edgar/data/{cik_noint}/{acc_nodash}/"
    dest = Path(dest_dir) if dest_dir else RAW_ROOT / cik / acc
    dest.mkdir(parents=True, exist_ok=True)
    stats = {"fetch": 0, "skip": 0}

    def tally(s):
        stats[s] += 1

    # FilingSummary.xml locates the "Business Segments" R-files. Older
    # FilingSummary versions (< 3) name the XML R-file in XmlFileName
    # instead of HtmlFileName.
    fs = dest / "FilingSummary.xml"
    tally(_save(fs, base + "FilingSummary.xml", ARCH_HEADERS))
    reports = []
    if fs.stat().st_size > 0:
        all_reports = []
        root = ET.fromstring(fs.read_text())
        for rep in root.iter("Report"):
            all_reports.append({
                "file": ((rep.findtext("HtmlFileName") or "").strip()
                         or (rep.findtext("XmlFileName") or "").strip()),
                "short_name": (rep.findtext("ShortName") or "").strip(),
                "long_name": (rep.findtext("LongName") or "").strip(),
            })
        reports = find_segment_reports(
            all_reports,
            firm=next((f for f, c in FIRMS.items() if c == cik), None))

    for rep in reports:
        if rep["file"]:
            tally(_save(dest / rep["file"], base + rep["file"], ARCH_HEADERS))

    # Main XBRL instance, located via the filing's index.json (the
    # Report/@instance attribute is unreliable across filers).
    instance = _instance_from_index(cik, acc_nodash, base)
    if instance:
        try:
            tally(_save(dest / instance, base + instance, ARCH_HEADERS))
        except RuntimeError as e:
            print(f"  WARNING: instance {instance} not found ({e})",
                  flush=True)
            instance = None

    return {"stats": stats, "reports": reports, "instance": instance}


def download_all(since="2010-01-01", firms=None, data_root=None):
    """Download all 10-K filings for the selected firms. Returns a summary."""
    global RAW_ROOT
    if data_root:
        RAW_ROOT = Path(data_root)
    firms = firms or list(FIRMS)
    summary = {}
    for firm in firms:
        cik = FIRMS[firm]
        filings = list_10k_filings(cik, since=since)
        print(f"{firm} ({cik}): {len(filings)} 10-K filings since {since}",
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
    args = ap.parse_args()
    firms = [f.strip() for f in args.firms.split(",") if f.strip()]
    summary = download_all(since=args.since, firms=firms)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
