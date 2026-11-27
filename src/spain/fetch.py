"""Fetchers for the Spain heat index.

Each fetcher exercises one free public route live and returns a
{period: value} dict in the series' native frequency, where a period
is "YYYY-MM" (monthly), "YYYYQn" (quarterly) or "YYYY-MM-DD" (daily).
Fetched series are archived under data/spain/ as slim date,value CSVs
(date = first day of the period), one file per series, matching the
archive convention of the other sections in this repository.

No value is ever interpolated, filled or spliced: a period the source
does not publish is simply absent from the dict.
"""
import csv
import io
import json
import urllib.request
from pathlib import Path

UA = {"User-Agent": "frb-heat-index spain build (public data fetch)"}

MONTHS_ES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
             "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}


def _get(url: str) -> bytes:
    import http.client
    import time
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except (http.client.IncompleteRead, ConnectionError,
                TimeoutError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


def _sdmx_csv(text: str):
    return list(csv.DictReader(io.StringIO(text)))


def fetch_bis_sdmx(url: str) -> dict:
    """BIS SDMX CSV with TIME_PERIOD 'YYYY-Qn' -> {'YYYYQn': value}."""
    rows = _sdmx_csv(_get(url).decode("utf-8"))
    out = {}
    for r in rows:
        tp = r["TIME_PERIOD"].strip()
        y, q = tp.split("-Q")
        try:
            out[f"{y}Q{q}"] = float(r["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
    return out


def fetch_bis_tc(url: str) -> dict:
    """BIS total credit: keep only the percent-of-GDP unit (367)."""
    rows = _sdmx_csv(_get(url).decode("utf-8"))
    out = {}
    for r in rows:
        if r.get("UNIT_MEASURE") != "367":
            continue
        tp = r["TIME_PERIOD"].strip()
        y, q = tp.split("-Q")
        try:
            v = float(r["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
        key = f"{y}Q{q}"
        if key in out and abs(out[key] - v) > 1e-9:
            raise ValueError(f"BIS TC duplicate mismatch at {key}: "
                             f"{out[key]} vs {v}")
        out[key] = v
    return out


def fetch_bis_cpp(url: str) -> dict:
    """BIS commercial property prices (WS_CPP) for Spain.

    The response carries several property-type / collection pairs;
    keep only the headline national series — all property types
    (RE_TYPE 'A'), whole-country collection ('V') — and return the
    nominal index {'YYYYQn': value}. The build computes the
    four-quarter change from the index.
    """
    rows = _sdmx_csv(_get(url).decode("utf-8"))
    out = {}
    for r in rows:
        if r.get("RE_TYPE") != "A" or r.get("COLLECTION") != "V":
            continue
        tp = r["TIME_PERIOD"].strip()
        y, q = tp.split("-Q")
        try:
            out[f"{y}Q{q}"] = float(r["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
    return out


def fetch_fred(url: str) -> dict:
    """fredgraph CSV -> native frequency keyed by observation date."""
    rows = list(csv.reader(io.StringIO(_get(url).decode("utf-8"))))
    sid = rows[0][1]
    out = {}
    for date, val in rows[1:]:
        if val in (".", "", None):
            continue
        y, m, _ = date.split("-")
        if sid.startswith("QES"):  # quarterly BIS property prices via FRED
            out[f"{y}Q{(int(m) - 1) // 3 + 1}"] = float(val)
        else:
            out[f"{y}-{m}"] = float(val)
    return out


def fetch_bde_column(url: str, alias: str) -> dict:
    """One column of a Banco de España Boletín Estadístico table CSV.

    The table CSVs carry six metadata rows (series code, sequential
    number, alias, description, units, frequency), then one row per
    period labelled 'DIC 1989' style, then a NOTAS row. Monthly tables
    key as 'YYYY-MM'; quarterly tables are labelled by quarter-end
    month and key as 'YYYYQn'. '_' is the publisher's missing marker.
    """
    text = _get(url).decode("latin-1")
    rows = list(csv.reader(io.StringIO(text)))
    alias_row = next(i for i, r in enumerate(rows)
                     if r and r[0].startswith("ALIAS"))
    freq_row = next(i for i, r in enumerate(rows)
                    if r and r[0].startswith("FRECUENCIA"))
    col = rows[alias_row].index(alias)
    quarterly = rows[freq_row][col].strip().upper().startswith("TRIMESTRAL")
    out = {}
    for r in rows[freq_row + 1:]:
        label = r[0].strip() if r else ""
        parts = label.split()
        if len(parts) != 2 or parts[0] not in MONTHS_ES:
            continue
        month, year = MONTHS_ES[parts[0]], int(parts[1])
        raw = r[col].strip() if col < len(r) else ""
        if raw in ("_", "", "-"):
            continue
        try:
            v = float(raw)
        except ValueError:  # Spanish format: '.' thousands, ',' decimals
            v = float(raw.replace(".", "").replace(",", "."))
        if quarterly:
            out[f"{year}Q{(month - 1) // 3 + 1}"] = v
        else:
            out[f"{year}-{month:02d}"] = v
    return out


def fetch_ecb_ciss(url: str) -> dict:
    """ECB SDMX CSV (daily CISS) -> {'YYYY-MM-DD': value}."""
    rows = _sdmx_csv(_get(url).decode("utf-8"))
    out = {}
    for r in rows:
        try:
            out[r["TIME_PERIOD"].strip()] = float(r["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
    return out


def fetch_ecb_sdmx_q(url: str) -> dict:
    """ECB SDMX CSV with TIME_PERIOD 'YYYY-Qn' -> {'YYYYQn': value}.

    Used for the Bank Lending Survey; the CISS fetcher above keys by
    the raw daily period instead.
    """
    rows = _sdmx_csv(_get(url).decode("utf-8"))
    out = {}
    for r in rows:
        tp = r["TIME_PERIOD"].strip()
        y, q = tp.split("-Q")
        try:
            out[f"{y}Q{q}"] = float(r["OBS_VALUE"])
        except (TypeError, ValueError):
            continue
    return out


def fetch_eurostat(url: str) -> dict:
    """Eurostat statistics API (JSON-stat) -> {'YYYYQn': value}.

    The query URL must pin every dimension except time to a single
    value (the gov_10q_ggnfa queries in series.py pin freq, unit,
    s_adj, sector, na_item and geo), so the flat JSON-stat index is
    the time index plus a constant offset this resolves from the
    dimension sizes.
    """
    data = json.loads(_get(url).decode("utf-8"))
    ids, size = data["id"], data["size"]
    strides, acc = [], 1
    for s in reversed(size):
        strides.insert(0, acc)
        acc *= s
    tidx = data["dimension"]["time"]["category"]["index"]
    out = {}
    for t, ti in tidx.items():
        coords = [0] * len(ids)
        coords[ids.index("time")] = ti
        flat = sum(c * s for c, s in zip(coords, strides))
        v = data["value"].get(str(flat))
        if v is not None:
            y, q = t.split("-Q")
            out[f"{y}Q{q}"] = float(v)
    return out


def fetch_eurostat_m(url: str) -> dict:
    """Eurostat statistics API (JSON-stat) -> {'YYYY-MM': value}.

    Monthly counterpart of fetch_eurostat: the query URL must pin
    every dimension except time to a single value.
    """
    data = json.loads(_get(url).decode("utf-8"))
    ids, size = data["id"], data["size"]
    strides, acc = [], 1
    for s in reversed(size):
        strides.insert(0, acc)
        acc *= s
    tidx = data["dimension"]["time"]["category"]["index"]
    out = {}
    for t, ti in tidx.items():
        if "-Q" in t or len(t) != 7:
            continue
        coords = [0] * len(ids)
        coords[ids.index("time")] = ti
        flat = sum(c * s for c, s in zip(coords, strides))
        v = data["value"].get(str(flat))
        if v is not None:
            out[t] = float(v)
    return out


def fetch_ine(url: str) -> dict:
    """INE tempus DATOS_SERIE JSON -> {'YYYYQn': value}."""
    data = json.loads(_get(url).decode("utf-8"))
    out = {}
    for obs in data.get("Data", []):
        if obs.get("Valor") is None:
            continue
        year = int(obs["Anyo"])
        period = str(obs.get("T3_Periodo", ""))
        if period.startswith("T"):
            out[f"{year}Q{period[1:]}"] = float(obs["Valor"])
    return out


def period_start(period: str) -> str:
    if "Q" in period:
        y, q = period.split("Q")
        return f"{y}-{(int(q) - 1) * 3 + 1:02d}-01"
    if len(period) == 7:
        return period + "-01"
    return period


def write_archive(archive_dir: Path, name: str, series: dict) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    lines = ["date,value"]
    for p in sorted(series):
        lines.append(f"{period_start(p)},{series[p]!r}")
    (archive_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_archive(path: Path, frequency: str) -> dict[str, float]:
    """Restore the native period labels from a normalized source CSV."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = csv.DictReader(f)
        result = {}
        for row in rows:
            date = row["date"]
            if frequency == "Q":
                date = f"{date[:4]}Q{(int(date[5:7]) - 1) // 3 + 1}"
            elif frequency == "M":
                date = date[:7]
            if date in result:
                raise ValueError(f"duplicate period {date} in {path}")
            result[date] = float(row["value"])
    return result
