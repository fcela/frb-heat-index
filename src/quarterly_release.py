"""Validate and rebuild the published U.S. heat index from versioned inputs."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import polars as pl

from src import make_site, run_pipeline
from src.release_manifest import write_manifest

REPO = Path(__file__).resolve().parents[1]
PANEL_INPUTS = ("panel_quarterly.csv", "panel_complete.csv",
                "panel_complete_flags.csv", "nowcast_provenance.csv", "_meta.json")
SCORE_NAMES = ("components.csv", "aggregate.csv", "indicators_standardized.csv")
SITE_NAMES = ("aggregate.json", "components.json", "indicators.json", "clustered.json",
              "hierarchy.json", "meta.json", "indicators_meta.json")
RELEASE_OUTPUTS = (
    *(f"data/{name}" for name in (*SCORE_NAMES, "indicators_meta.json", "release_manifest.json")),
    *(f"docs/data/{name}" for name in SITE_NAMES),
    "docs/index.html",
)


def _read_panel(path: Path) -> pl.DataFrame:
    panel = pl.read_csv(path, schema_overrides={c: pl.Float64 for c in run_pipeline.SPEC},
                       null_values=["NaN"])
    quarters = panel["quarter"].to_list()
    if not quarters or panel.columns[0] != "quarter" or set(panel.columns[1:]) != set(run_pipeline.SPEC):
        raise ValueError(f"unexpected panel schema in {path.name}")
    if any(not q or len(q) != 6 or not q[:4].isdigit() or q[4] != "Q" or q[-1] not in "1234"
           for q in quarters):
        raise ValueError(f"invalid quarter labels in {path.name}")
    positions = [int(q[:4]) * 4 + int(q[-1]) for q in quarters]
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError(f"quarters are not contiguous in {path.name}")
    for column in panel.columns[1:]:
        if panel[column].is_infinite().any():
            raise ValueError(f"non-finite value in {path.name}: {column}")
    return panel


def read_provenance(data: Path) -> tuple[list[str], dict]:
    with (data / "nowcast_provenance.csv").open(newline="") as f:
        rows = csv.DictReader(f)
        records = {}
        for row in rows:
            key = row["quarter"], row["indicator"]
            source = row["value_source"]
            if (key in records or not math.isfinite(float(row["value"])) or
                    not source or source in {"observed", "missing", "unextended"} or
                    not row["selection_detail"]):
                raise ValueError(f"invalid nowcast provenance for {key}")
            records[key] = row
        return rows.fieldnames, records


def _rows(panel: pl.DataFrame) -> dict:
    return {row["quarter"]: row for row in panel.iter_rows(named=True)}


def reconcile_observations(data: Path) -> None:
    """Apply new observations, retaining estimates for cells still awaiting data."""
    observed_panel = _read_panel(data / "panel_quarterly.csv")
    observed = _rows(observed_panel)
    completed = _rows(_read_panel(data / "panel_complete.csv"))
    old_flags = _rows(pl.read_csv(data / "panel_complete_flags.csv"))
    fields, records = read_provenance(data)
    panel_rows, flags = [], []
    for q in sorted(observed.keys() | completed.keys()):
        row, flag = {"quarter": q}, {"quarter": q}
        for indicator in observed_panel.columns[1:]:
            key = q, indicator
            value = observed.get(q, {}).get(indicator)
            if value is not None:
                records.pop(key, None)
                source = "observed"
            elif key in records:
                value = float(records[key]["value"])
                source = records[key]["value_source"]
            else:
                source = old_flags.get(q, {}).get(indicator, "missing")
                if source != "unextended":
                    source = "missing"
            row[indicator], flag[indicator] = value, source
        panel_rows.append(row)
        flags.append(flag)
    pl.DataFrame(panel_rows, infer_schema_length=None).write_csv(data / "panel_complete.csv")
    pl.DataFrame(flags).write_csv(data / "panel_complete_flags.csv")
    with (data / "nowcast_provenance.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records.values())


def validate_panel(data: Path) -> tuple[pl.DataFrame, dict]:
    observed = _rows(_read_panel(data / "panel_quarterly.csv"))
    panel = _read_panel(data / "panel_complete.csv")
    completed = _rows(panel)
    flag_panel = pl.read_csv(data / "panel_complete_flags.csv")
    if (set(flag_panel.columns) != set(panel.columns) or list(completed) != flag_panel["quarter"].to_list()
            or not observed.keys() <= completed.keys()):
        raise ValueError("observed panel, completed panel, and flags disagree on coverage")
    _, provenance = read_provenance(data)
    meta = json.loads((data / "_meta.json").read_text())
    observed_end = max(observed)
    published_end = max(completed)
    nowcast_end = max((q for q, _ in provenance), default=None)
    as_of = dt.date.fromisoformat(meta["built_at_utc"][:10])
    if (observed_end != meta["last_built_quarter"] or
            meta.get("nowcast_through") != nowcast_end or
            published_end != max(observed_end, nowcast_end or observed_end) or
            published_end > run_pipeline.last_full_quarter(as_of) or
            meta["n_indicators"] != len(run_pipeline.SPEC) or
            meta["n_components"] != len(run_pipeline.COMPONENTS)):
        raise ValueError("release metadata disagrees with the panel")

    for q, flags in _rows(flag_panel).items():
        for indicator in run_pipeline.SPEC:
            observation = observed.get(q, {}).get(indicator)
            value, flag = completed[q][indicator], flags[indicator]
            record = provenance.pop((q, indicator), None)
            if observation is not None:
                valid = (record is None and flag == "observed" and value is not None
                         and math.isclose(value, observation, rel_tol=1e-12, abs_tol=1e-12))
            elif record is not None:
                valid = (flag == record["value_source"] and value is not None
                         and math.isclose(value, float(record["value"]), rel_tol=1e-12, abs_tol=1e-12))
            else:
                valid = value is None and flag in {"missing", "unextended"}
            if not valid:
                raise ValueError(f"panel and provenance disagree for {q}: {indicator}")
    if provenance:
        raise ValueError(f"provenance outside the panel: {next(iter(provenance))}")
    return panel, meta


def build_release(root: Path) -> None:
    data = root / "data"
    docs = root / "docs"
    panel, meta = validate_panel(data)
    scores = run_pipeline.run_pipeline(panel)
    aggregate = scores[1]["Aggregate index"]
    if aggregate.null_count() or not aggregate.is_finite().all():
        raise ValueError("a quarter has no finite aggregate score")
    for name, frame in zip(SCORE_NAMES, scores):
        frame.write_csv(data / name)
    make_site.build(data, docs / "data")
    shutil.copyfile(docs / "data" / "indicators_meta.json", data / "indicators_meta.json")
    write_manifest(root, data / "release_manifest.json", "python -m src.quarterly_release",
                   meta["built_at_utc"], panel["quarter"][-1],
                   [*(data / name for name in PANEL_INPUTS), data / "proxy_catalog.csv"])


@contextmanager
def staged_checkout(root: Path):
    """Keep candidate inputs and outputs outside the published paths."""
    with tempfile.TemporaryDirectory(prefix=".frb-quarterly-", dir=root) as directory:
        stage = Path(directory)
        shutil.copytree(root / "data", stage / "data", ignore=shutil.ignore_patterns("raw"))
        (stage / "docs").mkdir()
        shutil.copyfile(root / "docs" / "index.html", stage / "docs" / "index.html")
        yield stage


def publish(root: Path, stage: Path, files: tuple[str, ...]) -> None:
    """Replace validated files and roll back if a replacement fails."""
    backups = stage / "previous"
    changes = []
    for name in files:
        source, dest = stage / name, root / name
        if dest.is_file() and source.read_bytes() == dest.read_bytes():
            continue
        backup = None
        if dest.exists():
            backup = backups / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, backup)
        changes.append((source, dest, backup))
    installed = []
    try:
        for source, dest, backup in changes:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, dest)
            installed.append((dest, backup))
    except BaseException:
        for dest, backup in reversed(installed):
            if backup is None:
                dest.unlink()
            else:
                os.replace(backup, dest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    root = parser.parse_args().root.resolve()
    with staged_checkout(root) as stage:
        build_release(stage)
        publish(root, stage, RELEASE_OUTPUTS)


if __name__ == "__main__":
    main()
