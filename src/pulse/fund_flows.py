"""Net flows to long-term mutual funds & ETFs -- MONTHLY, USD millions.

FREQUENCY DEVIATION (read first): the catalog specs this indicator as weekly,
but no continuous public WEEKLY series is feasible, so this fetcher delivers
the continuous MONTHLY series and the catalog wiring should reflect monthly:

  * ICI's site bot-walls automated download (Akamai 403 for curl, including
    the combined_flows_data_<YYYY>.xls workbooks), so the direct URL is not
    curl-reachable. Scraping the release pages is authorized but each page
    renders only the latest 5 weeks -- not a time series.
  * Each workbook's "Estimated weekly fund flows" block is a rolling 6-8
    week window after the last closed month. Public archives (Wayback
    Machine) hold only a handful of snapshots per year, so the recoverable
    weekly fragments cover ~60 scattered weeks out of 600+ -- unusable.
  * The same workbooks' "Monthly fund flows" block (ACTUAL monthly net new
    cash flow + ETF net issuance, a superior methodology to the weekly
    estimates) IS continuous across archived workbooks: 2014-01 through
    2026-05, 149 months, no gaps, via latest-crawl-wins (ICI revises).

Method: discover combined_flows_data_<YYYY>.xls URLs via the Wayback CDX
API, take each file's latest HTTP-200 Excel snapshot, parse the monthly
"Total LT MF and ETF flows" column ($ millions), and union months keeping
the value from the latest crawl. A live download of the current-year
workbook is attempted first in case ICI's bot-wall lifts.

Validation: Mar-2020 -$340.7B (COVID dash-for-cash), Dec-2018 -$133.8B
(Q4-2018 selloff); typical months +/-$150B. Overlap revisions across files
are small (max $9.4B on one month) vs the signal.

Requires the `fastexcel` package (polars read_excel backend for legacy .xls).
Source: Investment Company Institute weekly statistical release workbooks,
via web.archive.org snapshots. Units: USD millions, month-end dates.
"""

import io
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone

import polars as pl

SHEET = "Weekly MF & ETF Public Report"
# Fallback if CDX discovery ever returns nothing (researched 2026-10-15).
FALLBACK_URLS = [
    "https://www.ici.org/info/combined_flows_data_2016.xls",
    "https://www.ici.org/system/files/attachments/info/combined_flows_data_2018.xls",
    "https://www.ici.org/info/combined_flows_data_2020.xls",
    "https://www.ici.org/system/files/2022-01/combined_flows_data_2022.xls",
    "https://www.ici.org/system/files/2022-11/combined_flows_data_2022_2.xls",
    "https://www.ici.org/combined_flows_data_2024.xls",
    "https://www.ici.org/combined_flows_data_2025.xls",
    "https://www.ici.org/combined_flows_data_2026.xls",
]
FILE_RE = re.compile(r"combined_flows_data_\d{4}(_\d)?\.xls$", re.IGNORECASE)


def _curl(url: str, timeout: int, binary: bool = False, retries: int = 3):
    last = None
    for attempt in range(retries):
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0", url],
            capture_output=True,
        )
        if r.returncode == 0 and len(r.stdout) > 0:
            return r.stdout if binary else r.stdout.decode("utf-8", "replace")
        last = f"rc={r.returncode}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"curl failed for {url} after {retries} tries ({last})")


def _discover_files() -> list:
    """Find archived ICI combined-flows workbook URLs via CDX."""
    q = (
        "https://web.archive.org/cdx/search/cdx?url=ici.org&matchType=domain"
        "&output=text&fl=original&collapse=urlkey&limit=500"
        "&filter=original:.*combined_flows_data.*\\.xls.*"
    )
    out = _curl(q, timeout=120)
    urls = []
    for line in out.splitlines():
        u = line.strip().strip('"')
        if FILE_RE.search(u) and "etf_flows" not in u.lower() and u not in urls:
            urls.append(u)
    return urls


def _snapshots(file_url: str) -> list:
    """All (timestamp, status, mimetype) captures for one file URL."""
    q = (
        "https://web.archive.org/cdx/search/cdx?url="
        + urllib.parse.quote(file_url, safe="")
        + "&output=text&fl=timestamp,statuscode,mimetype&limit=100"
    )
    out = _curl(q, timeout=90)
    snaps = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            snaps.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return snaps


def _parse_monthly(xls: bytes) -> pl.DataFrame:
    """Extract the monthly 'Total LT MF and ETF flows' block ($ millions)."""
    try:
        df = pl.read_excel(io.BytesIO(xls), sheet_name=SHEET, has_header=False)
    except Exception:
        # Fall back to the first sheet if the name ever changes.
        df = pl.read_excel(io.BytesIO(xls), sheet_id=1, has_header=False)
    c0 = df[df.columns[0]].to_list()
    c1 = df[df.columns[1]].to_list()

    def find(pred):
        for i, v in enumerate(c0):
            if v and pred(str(v)):
                return i
        raise RuntimeError("monthly/weekly section markers not found in ICI workbook")

    m0 = find(lambda s: s.strip().lower() == "monthly fund flows")
    w0 = find(lambda s: "estimated weekly" in s.lower())
    if w0 <= m0:
        raise RuntimeError("unexpected ICI workbook layout")

    dates, values = [], []
    for i in range(m0 + 1, w0):
        ds, vs = c0[i], c1[i]
        if not ds or "/" not in str(ds):
            continue
        try:
            d = datetime.strptime(str(ds).strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        txt = str(vs).strip().replace(",", "")
        neg = txt.startswith("(") and txt.endswith(")")
        txt = txt.strip("()")
        try:
            v = -float(txt) if neg else float(txt)
        except ValueError:
            continue
        dates.append(d)
        values.append(v)
    if not dates:
        raise RuntimeError("no monthly rows parsed from ICI workbook")
    return pl.DataFrame({"date": dates, "value": values}).sort("date")


def _download_snapshot(file_url: str, ts: str) -> bytes:
    raw = f"https://web.archive.org/web/{ts}id_/{file_url}"
    blob = _curl(raw, timeout=120, binary=True)
    if len(blob) < 1000 or not blob[:8].startswith(b"\xd0\xcf\x11\xe0"):
        raise RuntimeError(f"snapshot {ts} of {file_url} is not an .xls file")
    return blob


def fetch_fund_flows() -> pl.DataFrame:
    """Fetch monthly net flows to long-term MFs + ETFs ($ millions), ascending."""
    try:
        urls = _discover_files()
    except RuntimeError:
        urls = []
    if not urls:
        urls = FALLBACK_URLS

    tagged = []  # (crawl_ts, date, value)
    # Live current-year workbook first (wins revisions if ICI unblocks curl).
    year = datetime.now(timezone.utc).year
    live = f"https://www.ici.org/combined_flows_data_{year}.xls"
    try:
        blob = _curl(live, timeout=60, binary=True)
        if len(blob) > 1000 and blob[:8].startswith(b"\xd0\xcf\x11\xe0"):
            m = _parse_monthly(blob)
            now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            tagged += [(now, d, v) for d, v in zip(m["date"], m["value"])]
    except RuntimeError:
        pass

    for url in urls:
        try:
            snaps = [s for s in _snapshots(url) if s[1] == "200" and "excel" in s[2].lower()]
        except RuntimeError:
            continue
        if not snaps:
            continue
        ts = max(s[0] for s in snaps)
        try:
            m = _parse_monthly(_download_snapshot(url, ts))
        except RuntimeError:
            continue
        tagged += [(ts, d, v) for d, v in zip(m["date"], m["value"])]

    if not tagged:
        raise RuntimeError("fund_flows: no ICI workbook snapshots yielded data")

    best = {}
    for ts, d, v in tagged:
        key = (d.year, d.month)
        if key not in best or ts > best[key][0]:
            best[key] = (ts, d, v)
    df = (
        pl.DataFrame(
            {"date": [b[1] for b in best.values()], "value": [float(b[2]) for b in best.values()]}
        )
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )
    if df.height == 0:
        raise RuntimeError("fund_flows: empty series after union")
    if df.select(pl.col("value").is_finite().all()).item() is not True:
        raise RuntimeError("non-finite values in fund flows")
    return df


if __name__ == "__main__":
    df = fetch_fund_flows()
    print(df.height, df["date"].min(), df["date"].max(), round(df["value"].last(), 1))
