#!/usr/bin/env python3
"""Check for eligible U.S. quarters and publish a validated index release."""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import math
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src import build_panel, fetch_series, quarterly_release  # noqa: E402
from src.systemic.fetch_y9c import NIC_URL, nic_text_to_csv  # noqa: E402

LAG_DAYS = 75
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
REQUIRED_FRED = {
    "D": ("DGS10", "VIXCLS"),
    "M": ("BAA", "CSUSHPISA", "CUSR0000SEHA", "DPI", "DSPI", "PSAVERT",
          "REVOLSL", "TOTALSL"),
    "Q": ("BCNSDODNS", "DRTSCILM", "SUBLPDHMSGLGNQ", "EXHE2C1Q8NP", "GDP",
          "GDPDEF", "HHMSDODNS", "MDSP", "TCMILBSNNB", "TDSP"),
}
Y9C_REQUIRED = (
    "Bank Leverage (Total common equity to total assets)",
    "Maturity Mismatch (Loans as a percent of deposits)",
    "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets",
    "Bank Leverage (Tier 1 common equity ratio)",
    "Bank Leverage (Total Capital Ratio)",
    "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)",
)
SYSTEMIC_NAMES = ("srisk", "granger")


class SourceUnavailable(Exception):
    """A required source has yet to provide usable data for the quarter."""


def quarter_end(year: int, q: int) -> dt.date:
    month = 3 * q
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def quarter_bounds(quarter: str) -> tuple[dt.date, dt.date]:
    year, q = build_panel.parse_q(quarter)
    return dt.date(year, 3 * q - 2, 1), quarter_end(year, q)


def target_quarters(last_built: str, today: dt.date) -> list[str]:
    last = build_panel.last_full_quarter(today)
    if last <= last_built:
        return []
    return [q for q in build_panel.quarter_range(last_built, last)[1:]
            if (today - quarter_bounds(q)[1]).days > LAG_DAYS]


def check_fred_coverage(quarter: str, frequency: str, dates: list[dt.date]) -> bool:
    start, end = quarter_bounds(quarter)
    inside = [d for d in dates if start <= d <= end]
    if not inside:
        return False
    if frequency == "Q":
        return True
    if frequency == "M":
        return {d.month for d in inside} == set(range(start.month, end.month + 1))
    allowance = dt.timedelta(days=7)
    weekdays = sum((start + dt.timedelta(days=i)).weekday() < 5
                   for i in range((end - start).days + 1))
    observed = {d for d in inside if d.weekday() < 5}
    return (len(observed) >= 0.8 * weekdays and
            min(inside) <= start + allowance and max(inside) >= end - allowance)


def fetch_fred(raw: Path, series_id: str) -> list[dt.date]:
    try:
        fetch_series._curl_dl(f"{FRED_URL}?id={series_id}", raw / f"fred_{series_id}.csv",
                              tries=2)
        return build_panel.read_fred_monthly(raw, series_id).filter(
            pl.col("value").is_finite())["date"].to_list()
    except (RuntimeError, OSError, ValueError, pl.exceptions.PolarsError) as exc:
        raise SourceUnavailable(f"FRED {series_id}: {exc}") from exc


def fetch_y9c(raw: Path, quarter: str) -> None:
    year, q = build_panel.parse_q(quarter)
    dest = raw / "y9c" / y9c_filename(quarter)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if (year, q) <= (2021, 1):
            fetch_series._curl_dl(f"{fetch_series.Y9C_BASE}/{dest.name}", dest,
                                  tries=2, min_bytes=100_000)
        else:
            date = quarter_end(year, q).strftime("%Y%m%d")
            url = NIC_URL.format(yyyymmdd=date)
            archive = dest.with_suffix(".zip")
            fetch_series._curl_dl(url, archive, tries=2, min_bytes=100_000)
            with zipfile.ZipFile(archive) as z:
                members = [name for name in z.namelist() if not name.endswith("/")]
                if len(members) != 1:
                    raise ValueError(f"expected one report, received {members}")
                report = z.read(members[0]).decode("utf-8", errors="replace")
            dest.write_text(nic_text_to_csv(report), newline="")
    except (RuntimeError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise SourceUnavailable(f"Y-9C {quarter}: {exc}") from exc


def y9c_filename(quarter: str) -> str:
    year, q = build_panel.parse_q(quarter)
    return f"bhcf{year % 100:02d}{3 * q:02d}.csv"


def ready_quarters(raw: Path, targets: list[str], notes: list[str],
                   local_raw: Path) -> list[str]:
    dates = {}
    for ids in REQUIRED_FRED.values():
        for sid in ids:
            try:
                dates[sid] = fetch_fred(raw, sid)
            except SourceUnavailable as exc:
                notes.append(str(exc))
    ready = []
    for quarter in targets:
        missing = [sid for freq, ids in REQUIRED_FRED.items() for sid in ids
                   if not check_fred_coverage(quarter, freq, dates.get(sid, []))]
        if missing:
            notes.append(f"{quarter} deferred: incomplete FRED inputs {', '.join(missing)}")
            break
        try:
            file = y9c_filename(quarter)
            cached = local_raw / "y9c" / file
            if cached.is_file():
                (raw / "y9c").mkdir(exist_ok=True)
                shutil.copyfile(cached, raw / "y9c" / file)
                notes.append(f"Y-9C {quarter}: using local report {file}")
            else:
                fetch_y9c(raw, quarter)
        except SourceUnavailable as exc:
            notes.append(str(exc))
            break
        ready.append(quarter)
    return ready


def fetch_optional(repo: Path, raw: Path, notes: list[str]) -> list[str]:
    """Bring available supplementary inputs into the candidate build."""
    missing = []
    fetched = {sid for ids in REQUIRED_FRED.values() for sid in ids}
    for name, url, kind in fetch_series.SOURCES:
        if name.startswith("fred_") and name[5:-4] in fetched:
            continue
        if kind == "manual" or name == "ie_data.xls":
            continue
        try:
            fetch_series._curl_dl(url, raw / name, tries=2, min_bytes=100)
        except (RuntimeError, OSError) as exc:
            missing.append(name)
            notes.append(f"Optional input {name}: {exc}")

    local_raw = repo / "work" / "raw"
    try:
        archive = raw / "z1_csv.zip"
        fetch_series._curl_dl(fetch_series.Z1_URL, archive, tries=2, min_bytes=100)
        fetch_series.extract_z1(archive, raw)
    except (OSError, RuntimeError, ValueError, KeyError, StopIteration, zipfile.BadZipFile) as exc:
        missing.append("Z.1 bulk series")
        notes.append(f"Optional Z.1 bulk series: {exc}")

    for name in ("ie_data_ep.csv", "ebp_raw.csv", "bis_cre_us.csv", "sloos_cre.csv",
                 "fico_annual.csv", "yahoo", "edgar"):
        source = local_raw / name
        if source.exists():
            (raw / name).symlink_to(source, target_is_directory=source.is_dir())
        else:
            missing.append(name)
    return missing


def refresh_systemic(repo: Path, stage: Path, raw: Path, notes: list[str]) -> None:
    if not (raw / "yahoo").is_dir():
        return
    shutil.copytree(repo / "src", stage / "src", ignore=shutil.ignore_patterns("__pycache__"))
    for name in SYSTEMIC_NAMES:
        if name == "srisk" and not (raw / "edgar").is_dir():
            continue
        command = [sys.executable, str(stage / "src" / "systemic" / f"build_{name}.py")]
        try:
            subprocess.run(command, cwd=stage, check=True)
            source = raw / "systemic" / f"{name}_quarterly.csv"
            dest = stage / "data" / "systemic" / source.name
            previous = pl.read_csv(dest)
            candidate = pl.read_csv(source)
            new = candidate.filter(~pl.col("quarter").is_in(previous["quarter"].to_list()))
            if new.height:
                pl.concat([previous, new], how="diagonal_relaxed").sort("quarter").write_csv(dest)
        except (subprocess.CalledProcessError, OSError, pl.exceptions.PolarsError) as exc:
            notes.append(f"{name} refresh deferred: {exc}")


def append_observed(data: Path, raw: Path, quarters: list[str]) -> None:
    fragment = build_panel.build(raw, quarters[0], quarters[-1], skip_missing=True)
    if fragment["quarter"].to_list() != quarters:
        raise ValueError("observed builder returned unexpected quarters")
    for row in fragment.iter_rows(named=True):
        bad = [c for c in Y9C_REQUIRED if row[c] is None or not math.isfinite(row[c])]
        if bad:
            raise ValueError(f"{row['quarter']} is missing required Y-9C values: {bad}")

    path = data / "panel_quarterly.csv"
    previous = pl.read_csv(path)
    if previous["quarter"][-1] >= quarters[0]:
        raise ValueError("candidate quarters overlap the observed panel")
    fragment = fragment.select(previous.columns).with_columns(
        pl.col(pl.Float64).fill_nan(None))
    pl.concat([previous, fragment], how="vertical_relaxed").write_csv(path)


def update_release(repo: Path, targets: list[str], notes: list[str]) -> list[str]:
    with quarterly_release.staged_checkout(repo) as stage:
        data = stage / "data"
        quarterly_release.validate_panel(data)
        raw = stage / "work" / "raw"
        raw.mkdir(parents=True)
        ready = ready_quarters(raw, targets, notes, repo / "work" / "raw")
        if not ready:
            return []
        missing = fetch_optional(repo, raw, notes)
        refresh_systemic(REPO, stage, raw, notes)
        append_observed(data, raw, ready)
        quarterly_release.reconcile_observations(data)
        meta_path = data / "_meta.json"
        meta = json.loads(meta_path.read_text())
        _, records = quarterly_release.read_provenance(data)
        remaining = {q for q, _ in records}
        meta.pop("nowcast_through", None)
        if remaining:
            meta["nowcast_through"] = max(remaining)
        meta.update(last_built_quarter=ready[-1],
                    built_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    stale_sources=sorted(missing),
                    sources={"required": {"status": "ok", "last_ok_quarter": ready[-1]}})
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        quarterly_release.build_release(stage)
        files = (*(f"data/{name}" for name in quarterly_release.PANEL_INPUTS),
                 *(f"data/systemic/{name}_quarterly.csv" for name in SYSTEMIC_NAMES),
                 *quarterly_release.RELEASE_OUTPUTS)
        quarterly_release.publish(repo, stage, files)
        return ready


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--dry-run", action="store_true", help="show eligible quarters without fetching")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    meta = json.loads((repo / "data" / "_meta.json").read_text())
    last = meta["last_built_quarter"]
    targets = target_quarters(last, args.today)
    notes = [f"Eligible after {last} on {args.today}: {', '.join(targets) or 'none'}"]
    published = []
    status = "waiting"
    if args.dry_run:
        status = "dry-run"
    elif targets:
        try:
            published = update_release(repo, targets, notes)
            if published:
                status = "published"
        except Exception as exc:
            status = "failed"
            notes.append(f"{type(exc).__name__}: {exc}")
    title = f"U.S. heat index release {published[-1] if published else last} ({status})"
    report = f"# {title}\n\n" + "".join(f"- {note}\n" for note in notes)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report)
    if args.github_output:
        with args.github_output.open("a") as out:
            out.write(f"update_title={title}\nnew_quarters={','.join(published)}\nrun_status={status}\n")
    print(report)
    return int(status == "failed")


if __name__ == "__main__":
    sys.exit(main())
