#!/usr/bin/env python3
"""Quarterly incremental updater for the FRB financial-heat index dataset.

Pipeline (all Polars; no pandas):
  1. Read data/_meta.json -> last_built_quarter.
  2. Target quarters: every quarter after last_built_quarter up to the latest
     COMPLETE quarter. Complete means
       (a) the quarter ended more than LAG_DAYS (75) ago -- covers the ~40-45
           day Y-9C filing lag plus margin, and
       (b) RAGGED-EDGE GUARD: every required source has full-quarter data --
           each FRED series (frequency inferred from the data itself) has an
           observation covering the quarter (D/M: within 7 days of quarter
           end; Q: dated inside the quarter), and the quarter's Y-9C bulk file
           is downloadable.
     The first quarter failing any check TRUNCATES the update: a partial
     quarter is never published. (A partial-quarter value once treated as a
     full quarter produced a 7.6-sigma end-of-sample artifact.)
  3. Fetch ONLY new data into work/raw/ (the base build's raw dir; never
     re-downloads history):
       - FRED: keyless https://fred.stlouisfed.org/graph/fredgraph.csv?id=<id>
         (verified keyless; no API key anywhere) for every id in
         fetch_series.FRED_IDS, via the base's own curl downloader;
       - Y-9C: the quarter's bulk file only. NOTE: the Chicago Fed stopped
         publishing recent quarters in 2021; current bulk files come from the
         NIC Financial Data Download
         (https://www.ffiec.gov/npw/FinancialReport/ReturnBHCFZipFiles
          ?zipfilename=BHCF<yyyymmdd>.ZIP -- verified 200 from CI-like
         egress), converted from caret- to comma-delimited and stored as
         work/raw/y9c/bhcf<yy><mm>.csv, the name build_panel.read_y9c
         expects. Pre-2021Q2 quarters still use the Chicago Fed URL.
       - Other fetch_series.SOURCES entries with kind="auto" (small).
  4. Rebuild through the base build's own code path (no duplicated logic):
       - build_panel.build(raw, start, end) -> new-quarter fragment, appended
         to data/panel_quarterly.csv with Polars (new quarters only; history
         is never rewritten, so FRED vintages can't revise the past);
       - run_pipeline.main() -> data/components.csv, data/aggregate.csv;
       - make_site.main()   -> docs/data/*.json (meta.json copies _meta.json,
         so staleness flags reach the site banner automatically).
  5. Tail-spike check on the rebuilt panel (|new - trailing mean| > 3 *
     trailing std flags an advisory warning).
  6. Update data/_meta.json (last_built_quarter, built_at_utc, per-source
     status incl. staleness flags) and write a markdown run report.

Failure policy (graceful degradation): any source unreachable -> keep last
good data, record staleness in data/_meta.json, exit 0. The run never fails
on fetch problems; the GitHub issue log shows what was skipped.

Usage:
  python .github/scripts/update.py [--dry-run] [--repo PATH] [--report PATH]
      [--github-output PATH] [--today YYYY-MM-DD]
  --dry-run: simulate one new quarter with every fetch mocked (no network at
      all, nothing written) and print what WOULD happen.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    import polars as pl
except ImportError:  # pragma: no cover
    print("update.py requires polars (`pip install polars`)", file=sys.stderr)
    sys.exit(2)

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

LAG_DAYS = 75
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CHICAGO_FED_URL = (
    "https://www.chicagofed.org/~/media/others/banking/"
    "financial-institution-reports/bhc-data/bhcf{yy}{mm}.csv"
)
NIC_URL = (
    "https://www.ffiec.gov/npw/FinancialReport/ReturnBHCFZipFiles"
    "?zipfilename=BHCF{yyyymmdd}.ZIP"
)
Q_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}

# Indicator columns derived from Y-9C (must be non-null for a new quarter
# to be accepted; they are the binding incremental constraint).
Y9C_INDICATORS = [
    "Bank Leverage (Total common equity to total assets)",
    "Maturity Mismatch (Loans as a percent of deposits)",
    "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets",
    "Bank Leverage (Tier 1 common equity ratio)",
    "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)",
]

# --------------------------------------------------------------------------
# Quarter helpers
# --------------------------------------------------------------------------

_Q_RE = re.compile(r"^(\d{4})Q([1-4])$")


def parse_quarter(s: str) -> tuple[int, int]:
    m = _Q_RE.match(s.strip())
    if not m:
        raise ValueError(f"bad quarter string: {s!r} (expected YYYYQN)")
    return int(m.group(1)), int(m.group(2))


def fmt_quarter(year: int, q: int) -> str:
    return f"{year}Q{q}"


def next_quarter(year: int, q: int) -> tuple[int, int]:
    return (year + 1, 1) if q == 4 else (year, q + 1)


def quarter_end(year: int, q: int) -> dt.date:
    m = Q_END_MONTH[q]
    return dt.date(year, m, calendar.monthrange(year, m)[1])


def quarter_start(year: int, q: int) -> dt.date:
    return dt.date(year, Q_END_MONTH[q] - 2, 1)


def latest_complete_quarter(today: dt.date) -> tuple[int, int]:
    y, q = today.year, (today.month - 1) // 3 + 1
    while (today - quarter_end(y, q)).days <= LAG_DAYS:
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
    return y, q


def target_quarters(last_built: str, today: dt.date) -> list[str]:
    y, q = next_quarter(*parse_quarter(last_built))
    ey, eq = latest_complete_quarter(today)
    out = []
    while (y, q) <= (ey, eq):
        out.append(fmt_quarter(y, q))
        y, q = next_quarter(y, q)
    return out


def y9c_report_date(year: int, q: int) -> str:
    return quarter_end(year, q).strftime("%Y%m%d")


# --------------------------------------------------------------------------
# Network layer (curl, like the base build; mockable for --dry-run)
# --------------------------------------------------------------------------

class Net:
    """Fetch primitives. dry_run=True mocks every byte (no network)."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.calls: list[str] = []
        # dry-run canned state: the simulated quarter is fully present
        self._mock_tail: dict[str, tuple[str, str]] = {}

    def mock_fred(self, sid: str, last_obs: str, freq: str) -> None:
        self._mock_tail[sid] = (last_obs, freq)

    # -- generic ------------------------------------------------------
    def curl_dl(self, url: str, dest: Path, min_bytes: int = 100) -> None:
        self.calls.append(f"GET {url} -> {dest}")
        if self.dry_run:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        from fetch_series import _curl_dl  # base build's own downloader

        _curl_dl(url, dest, min_bytes=min_bytes)

    # -- FRED ----------------------------------------------------------
    def fred_csv_path(self, raw: Path, sid: str) -> Path:
        return raw / f"fred_{sid}.csv"

    def ensure_fred(self, raw: Path, sid: str) -> None:
        """Download (or refresh) the full-history CSV for one FRED series."""
        self.curl_dl(f"{FRED_URL}?id={sid}", self.fred_csv_path(raw, sid))

    def fred_tail(self, raw: Path, sid: str) -> tuple[dt.date | None, str]:
        """(last observation date, inferred frequency D/M/Q) from the CSV."""
        if self.dry_run:
            last, freq = self._mock_tail.get(sid, ("2026-03-31", "D"))
            return dt.date.fromisoformat(last), freq
        p = self.fred_csv_path(raw, sid)
        df = pl.read_csv(p, null_values=[".", ""])
        date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
        dates = (
            df.select(pl.col(date_col).str.to_date().alias("d"))
            .drop_nulls().sort("d").tail(15)["d"].to_list()
        )
        if not dates:
            return None, "?"
        diffs = [(b - a).days for a, b in zip(dates, dates[1:])]
        med = sorted(diffs)[len(diffs) // 2] if diffs else 1
        freq = "D" if med <= 10 else ("M" if med <= 45 else "Q")
        return dates[-1], freq

    # -- Y-9C -----------------------------------------------------------
    def y9c_source(self, year: int, q: int) -> tuple[str, str]:
        """(download url, kind) for a quarter's Y-9C bulk file."""
        if year > 2021 or (year == 2021 and q > 1):
            # Chicago Fed stopped publishing recent quarters in 2021; the NIC
            # Financial Data Download is the current bulk source.
            return NIC_URL.format(yyyymmdd=y9c_report_date(year, q)), "nic-zip"
        yy, mm = f"{year % 100:02d}", f"{q * 3:02d}"
        return CHICAGO_FED_URL.format(yy=yy, mm=mm), "chicago-csv"

    def y9c_available(self, year: int, q: int) -> bool:
        if self.dry_run:
            return True
        url, _ = self.y9c_source(year, q)
        tmp = Path(tempfile.mkdtemp()) / "probe"
        try:
            from fetch_series import _curl_dl

            _curl_dl(url, tmp, tries=2, min_bytes=100)
            return True
        except Exception:
            return False
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    def y9c_fetch(self, year: int, q: int, dest: Path) -> None:
        """Fetch one quarter's Y-9C bulk file, stored comma-delimited as
        build_panel.read_y9c expects (work/raw/y9c/bhcf<yy><mm>.csv)."""
        self.calls.append(f"Y9C {fmt_quarter(year, q)} -> {dest}")
        if self.dry_run:
            return
        url, kind = self.y9c_source(year, q)
        tmp = Path(tempfile.mkdtemp())
        try:
            from fetch_series import _curl_dl
            from systemic.fetch_y9c import nic_text_to_csv

            if kind == "nic-zip":
                zp = tmp / "bhcf.zip"
                _curl_dl(url, zp, min_bytes=100_000)
                with zipfile.ZipFile(zp) as z:
                    raw_text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
                # Shared caret->comma converter (quote processing OFF: NIC
                # files have no quoting convention; see its docstring for the
                # 2023Q4 incident).
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(nic_text_to_csv(raw_text), newline="")
            else:
                _curl_dl(url, dest, min_bytes=100_000)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Ragged-edge guard
# --------------------------------------------------------------------------

def check_quarter_complete(
    year: int, q: int, raw: Path, fred_ids: list[str], net: Net
) -> tuple[bool, list[str]]:
    """True only if every required source has full-quarter data for (y, q)."""
    notes: list[str] = []
    qend = quarter_end(year, q)
    qstart = quarter_start(year, q)
    for sid in fred_ids:
        try:
            last, freq = net.fred_tail(raw, sid)
        except Exception as e:
            notes.append(f"FRED {sid}: unreadable ({e}); quarter not verifiable")
            return False, notes
        if last is None:
            notes.append(f"FRED {sid}: no observations at all")
            return False, notes
        if freq == "Q":
            ok = qstart <= last <= qend
        else:  # D/M: allow a trading-day gap at quarter end
            ok = last >= qend - dt.timedelta(days=7)
        if not ok:
            notes.append(
                f"FRED {sid} (~{freq}): last obs {last} does not cover "
                f"{fmt_quarter(year, q)} (ends {qend})"
            )
            return False, notes
    if not net.y9c_available(year, q):
        notes.append(f"Y-9C: bulk file for {fmt_quarter(year, q)} not downloadable")
        return False, notes
    notes.append(f"{fmt_quarter(year, q)}: complete in all required sources")
    return True, notes


def truncate_at_ragged_edge(
    targets: list[str], raw: Path, fred_ids: list[str], net: Net
) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    log: list[str] = []
    for t in targets:
        y, q = parse_quarter(t)
        complete, notes = check_quarter_complete(y, q, raw, fred_ids, net)
        log.extend(notes)
        if not complete:
            log.append(f"RAGGED-EDGE TRUNCATION: stopping before {t} (partial quarter)")
            break
        ok.append(t)
    return ok, log


# --------------------------------------------------------------------------
# Fetch stage
# --------------------------------------------------------------------------

def fetch_new_data(
    quarters: list[str],
    raw: Path,
    fred_ids: list[str],
    auto_sources: list[tuple[str, str]],
    net: Net,
    log: list[str],
) -> dict[str, str]:
    """Fetch raw inputs for the new quarters. Returns {group: status}."""
    status: dict[str, str] = {}
    # FRED full histories (small; builders need history for y/y shifts).
    fred_ok, fred_bad = 0, []
    for sid in fred_ids:
        try:
            net.ensure_fred(raw, sid)
            fred_ok += 1
        except Exception as e:
            fred_bad.append(f"{sid}: {e}")
    status["fred"] = (
        f"ok ({fred_ok}/{len(fred_ids)} series)"
        if not fred_bad else f"PARTIAL ({fred_ok}/{len(fred_ids)}): {fred_bad[:3]}"
    )
    log.append(f"FRED: {status['fred']}")
    # Other small auto sources (NIPA etc.).
    other_bad = []
    for fn, url in auto_sources:
        try:
            net.curl_dl(url, raw / fn)
        except Exception as e:
            other_bad.append(f"{fn}: {e}")
    status["other_auto"] = "ok" if not other_bad else f"PARTIAL: {other_bad[:3]}"
    # Y-9C: new quarters only -- never the full history.
    y9c_bad = []
    for t in quarters:
        y, q = parse_quarter(t)
        dest = raw / "y9c" / f"bhcf{y % 100:02d}{q * 3:02d}.csv"
        try:
            net.y9c_fetch(y, q, dest)
        except Exception as e:
            y9c_bad.append(f"{t}: {e}")
    status["y9c"] = (
        f"ok ({len(quarters)} quarters)"
        if not y9c_bad else f"PARTIAL: {y9c_bad}"
    )
    log.append(f"Y-9C: {status['y9c']}")
    return status


# --------------------------------------------------------------------------
# Rebuild through the base pipeline (imported; no duplicated logic)
# --------------------------------------------------------------------------

def _call_main(mod, argv: list[str]):
    """Call an imported base module's main(), shielding it from our argv
    (the base mains parse sys.argv with argparse)."""
    saved = sys.argv
    sys.argv = argv
    try:
        return mod.main()
    finally:
        sys.argv = saved


def refresh_systemic(repo: Path, raw: Path, dry_run: bool, log: list[str]) -> None:
    """Rebuild the two journal-publication systemic series (SRISK, Granger)
    from public inputs, then publish them to data/systemic/.

    build_panel reads the committed data/systemic/*.csv (via _systemic_frame),
    so without this refresh any new quarter would join to nulls. Fail-soft:
    on any error the previously committed series are kept and the new
    quarter simply has nulls for these two indicators (honest missingness,
    never fabricated values).
    """
    if dry_run:
        log.append("DRY-RUN: would rebuild data/systemic/*.csv "
                   "(src/systemic/build_srisk.py, build_granger.py)")
        return
    sys.path.insert(0, str(repo / "src"))
    import shutil
    try:
        import importlib
        srisk_mod = importlib.import_module("systemic.build_srisk")
        granger_mod = importlib.import_module("systemic.build_granger")
        log.append("rebuilding systemic series (SRISK, Granger) ...")
        _call_main(srisk_mod, ["build_srisk.py"])
        _call_main(granger_mod, ["build_granger.py"])
        staged = raw / "systemic"
        dest = repo / "data" / "systemic"
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("srisk_quarterly.csv", "granger_quarterly.csv"):
            src = staged / name
            if src.exists():
                shutil.copy2(src, dest / name)
                log.append(f"published {dest / name}")
            else:
                log.append(f"WARNING: systemic builder did not stage {src}; "
                           f"keeping previously committed {dest / name}")
    except Exception as e:  # noqa: BLE001 - fail-soft per workflow policy
        log.append(f"WARNING: systemic refresh failed ({type(e).__name__}: {e}); "
                   "keeping previously committed data/systemic/*.csv")



def rebuild(repo: Path, raw: Path, start: str, end: str,
            dry_run: bool, log: list[str]) -> pl.DataFrame:
    """Build the new-quarter fragment and append it to the panel."""
    if dry_run:
        log.append(
            f"DRY-RUN: would build_panel.build(raw, {start}, {end}) -> fragment, "
            "vstack onto data/panel_quarterly.csv (new quarters only)"
        )
        log.append("DRY-RUN: would run_pipeline.main() then make_site.main()")
        return pl.DataFrame({"quarter": [start, end]})
    sys.path.insert(0, str(repo / "src"))
    import build_panel

    frag = build_panel.build(raw, start, end, skip_missing=True)
    log.append(f"fragment: {frag.height} quarters x {frag.width - 1} indicators")
    panel_path = repo / "data" / "panel_quarterly.csv"
    panel = pl.read_csv(panel_path)
    have = set(panel["quarter"].to_list())
    new_rows = frag.filter(~pl.col("quarter").is_in(have)).sort("quarter")
    if new_rows.height == 0:
        log.append("no new quarter rows to append (already present)")
        return new_rows
    # Refuse quarters whose Y-9C leg produced nothing (binding constraint).
    bad = []
    for t in new_rows["quarter"].to_list():
        row = new_rows.filter(pl.col("quarter") == t)
        if all(row[c].is_null().all() for c in Y9C_INDICATORS if c in row.columns):
            bad.append(t)
    if bad:
        raise RuntimeError(
            f"fragment has no Y-9C indicators for {bad}; refusing partial quarter"
        )
    panel = pl.concat([panel, new_rows], how="diagonal").sort("quarter")
    panel.write_csv(panel_path)
    log.append(f"appended {new_rows.height} quarters -> {panel_path} "
               f"({panel.height} total)")
    return new_rows


def run_transforms(repo: Path, log: list[str]) -> None:
    sys.path.insert(0, str(repo / "src"))
    import run_pipeline
    import make_site

    log.append("running run_pipeline.main() ...")
    _call_main(run_pipeline, ["run_pipeline.py"])
    log.append("running make_site.main() ...")
    _call_main(make_site, ["make_site.py"])


def tail_spike_check(repo: Path, new_quarters: list[str], log: list[str]) -> list[str]:
    """Advisory: flag |new - trailing mean| > 3 * trailing std per indicator."""
    warnings: list[str] = []
    p = repo / "data" / "panel_quarterly.csv"
    df = pl.read_csv(p)
    num_cols = [c for c in df.columns if c != "quarter" and df[c].dtype.is_numeric()]
    hist = df.filter(~pl.col("quarter").is_in(new_quarters)).tail(8)
    new = df.filter(pl.col("quarter").is_in(new_quarters))
    if hist.height == 0 or new.height == 0:
        return warnings
    for c in num_cols:
        mu, sd = hist[c].mean(), hist[c].std()
        if not sd:
            continue
        for qq, v in new.select(["quarter", c]).iter_rows():
            if v is None:
                continue
            z = abs(v - mu) / sd
            if z > 3:
                warnings.append(
                    f"SPIKE-WARN {c} {qq}: {v:.4g} vs trailing mean {mu:.4g} (z={z:.1f})"
                )
    log.extend(warnings)
    return warnings


# --------------------------------------------------------------------------
# Meta + report
# --------------------------------------------------------------------------

def update_meta(repo: Path, new_last: str, source_status: dict,
                stale: list[str], dry_run: bool) -> dict:
    mp = repo / "data" / "_meta.json"
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    meta["last_built_quarter"] = new_last
    meta["built_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    meta["sources"] = source_status
    meta["stale_sources"] = stale
    if not dry_run:
        mp.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def write_report(path: Path | None, **kw) -> str:
    completed = kw["completed"]
    last_built = kw["last_built"]
    title = (
        f"Quarterly update {completed[-1]}: data advanced {last_built} -> {completed[-1]}"
        if completed else
        f"Quarterly update {last_built}: no new complete quarters"
    )
    L = [f"# {title}", "",
         f"- run_at_utc: {kw['run_at']}", f"- mode: {kw['mode']}",
         f"- last_built_quarter (before): {last_built}",
         f"- target quarters (>{LAG_DAYS}d old): {', '.join(kw['targets']) or 'none'}",
         f"- quarters completed: {', '.join(completed) or 'none'}",
         f"- stale sources: {', '.join(kw['stale']) or 'none'}",
         "", "## Ragged-edge guard", ""]
    L += [f"- {l}" for l in kw["guard_log"]] or ["- (no checks ran)"]
    L += ["", "## Fetch status", ""]
    for src, st in kw["fetch_status"].items():
        L.append(f"- {src}: {st}")
    if kw["manual_notes"]:
        L += ["", "## Manual sources (not auto-fetched)", ""]
        L += [f"- {m}" for m in kw["manual_notes"]]
    if kw["spike_warnings"]:
        L += ["", "## Tail spike warnings (advisory)", ""]
        L += [f"- {w}" for w in kw["spike_warnings"]]
    L += ["", "## Actions", ""]
    L += [f"- {a}" for a in kw["actions"]] or ["- none"]
    text = "\n".join(L) + "\n"
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return title


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Quarterly incremental data updater")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--report", default=None)
    ap.add_argument("--github-output", default=None)
    ap.add_argument("--today", default=None)
    args = ap.parse_args(argv)

    script = Path(__file__).resolve()
    repo = Path(args.repo).resolve() if args.repo else script.parents[2]
    raw = repo / "work" / "raw"
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    run_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    mode = "dry-run" if args.dry_run else "live"

    sys.path.insert(0, str(repo / "src"))
    try:
        import fetch_series
    except ImportError:
        if not args.dry_run:
            print(f"ERROR: src/fetch_series.py not found under {repo}", file=sys.stderr)
            return 2
        fetch_series = None  # type: ignore

    meta_path = repo / "data" / "_meta.json"
    if not meta_path.exists() and not args.dry_run:
        print(f"ERROR: {meta_path} not found", file=sys.stderr)
        return 2
    meta = (json.loads(meta_path.read_text()) if meta_path.exists()
            else {"last_built_quarter": "2025Q4", "n_indicators": 43, "n_components": 14})
    last_built = meta["last_built_quarter"]

    fred_ids = list(fetch_series.FRED_IDS) if fetch_series else ["DGS10", "VIXCLS", "GDP"]
    auto_sources = ([(fn, url) for fn, url, kind in fetch_series.SOURCES
                     if kind == "auto" and not fn.startswith("fred_")]
                    if fetch_series else [])
    manual_notes = ([f"{fn} <- {url or '(place by hand)'}"
                     for fn, url, kind in fetch_series.SOURCES if kind != "auto"]
                    if fetch_series else [])

    net = Net(dry_run=args.dry_run)
    targets = target_quarters(last_built, today)
    if args.dry_run:
        # Simulate exactly one new quarter with fully mocked fetches.
        sim = fmt_quarter(*next_quarter(*parse_quarter(last_built)))
        targets = [sim]
        for sid in fred_ids:
            net.mock_fred(sid, quarter_end(*parse_quarter(sim)).isoformat(), "D")

    guard_log = [f"LAG rule: quarter complete only if ended >{LAG_DAYS}d ago "
                 f"(today={today.isoformat()})"]
    # The guard reads FRED tails, so ensure the CSVs exist first (real run).
    if not args.dry_run:
        raw.mkdir(parents=True, exist_ok=True)
        for sid in fred_ids:
            try:
                net.ensure_fred(raw, sid)
            except Exception as e:
                guard_log.append(f"FRED {sid}: download failed ({e})")
    completed, trunc_log = truncate_at_ragged_edge(targets, raw, fred_ids, net)
    if args.dry_run:
        trunc_log = [l + " (simulated --dry-run)" for l in trunc_log]
    guard_log += trunc_log

    actions: list[str] = []
    fetch_status: dict[str, str] = {}
    source_status: dict = {}
    stale: list[str] = []
    spike_warnings: list[str] = []
    run_status = "ok"
    meta_backup = meta_path.read_bytes() if meta_path.exists() else None

    try:
        if not completed:
            actions.append("no complete new quarters; data unchanged")
            fetch_status = {"fred": "not run", "y9c": "not run"}
        else:
            fetch_status = fetch_new_data(completed, raw, fred_ids,
                                          auto_sources, net, actions)
            if args.dry_run:
                actions.append(
                    f"DRY-RUN: would fetch for {', '.join(completed)}: "
                    f"{len(fred_ids)} FRED series + {len(auto_sources)} other auto "
                    f"sources + Y-9C bulk files -> work/raw/")
            refresh_systemic(repo, raw, args.dry_run, actions)
            new_rows = rebuild(repo, raw, completed[0], completed[-1],
                               args.dry_run, actions)
            if not args.dry_run:
                new_last = completed[-1]
                # _meta.json BEFORE make_site so meta.json picks up staleness.
                for src, st in fetch_status.items():
                    source_status[src] = {
                        "status": "ok" if st.startswith("ok") else "degraded",
                        "last_ok_quarter": new_last if st.startswith("ok") else last_built,
                        "detail": st,
                    }
                if manual_notes:
                    source_status["manual"] = {
                        "status": "manual", "last_ok_quarter": last_built,
                        "detail": f"{len(manual_notes)} sources need hand placement",
                    }
                stale = sorted(s for s, v in source_status.items()
                               if v["status"] != "ok")
                update_meta(repo, new_last, source_status, stale, False)
                actions.append(f"updated {meta_path} -> last_built_quarter={new_last}")
                run_transforms(repo, actions)
                spike_warnings = tail_spike_check(repo, completed, actions)
    except Exception as e:  # graceful degradation: never fail the run
        run_status = f"degraded: {type(e).__name__}: {e}"
        actions.append(f"ERROR (degraded, exit 0): {e}")
        if meta_backup is not None and not args.dry_run:
            meta_path.write_bytes(meta_backup)
            actions.append("restored data/_meta.json from backup")
        stale.append("pipeline")

    if args.dry_run:
        # Dry-run bookkeeping only (nothing was written).
        for src in ["fred", "y9c", "other_auto"]:
            source_status[src] = {"status": "ok (simulated)",
                                  "last_ok_quarter": completed[-1] if completed else last_built,
                                  "detail": ""}
        if manual_notes:
            source_status["manual"] = {"status": "manual (simulated)",
                                       "last_ok_quarter": last_built, "detail": ""}
        print(f"DRY-RUN plan: would advance {last_built} -> "
              f"{completed[-1] if completed else '(none)'}")
        print(f"DRY-RUN plan: would fetch {len(fred_ids)} FRED series "
              f"+ {len(auto_sources)} auto sources + Y-9C for "
              f"{', '.join(completed) or 'none'} -> work/raw/")
        print("DRY-RUN plan: would append fragment to data/panel_quarterly.csv, "
              "run run_pipeline.main() + make_site.main(), update data/_meta.json")

    title = write_report(Path(args.report) if args.report else None,
                         mode=mode, run_at=run_at, last_built=last_built,
                         targets=targets, completed=completed,
                         guard_log=guard_log, fetch_status=fetch_status,
                         manual_notes=manual_notes if not args.dry_run else [],
                         source_status=source_status,
                         stale=sorted(set(stale)),
                         spike_warnings=spike_warnings, actions=actions)

    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"update_title={title}\n")
            f.write(f"new_quarters={','.join(completed)}\n")
            f.write(f"run_status={run_status}\n")

    print(f"done: {title} [status={run_status}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
