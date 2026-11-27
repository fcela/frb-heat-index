import csv
import datetime as dt
import importlib.util
import json
import shutil
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src import quarterly_release as release

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("quarterly_update", REPO / ".github/scripts/update.py")
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)


def copy_release(path):
    shutil.copytree(REPO / "data", path / "data", ignore=shutil.ignore_patterns("raw"))
    (path / "docs").mkdir()
    shutil.copyfile(REPO / "docs/index.html", path / "docs/index.html")


def test_replay_observed_and_nowcast_release(tmp_path):
    copy_release(tmp_path)
    release.build_release(tmp_path)
    for name in release.SCORE_NAMES:
        assert_frame_equal(pl.read_csv(tmp_path / "data" / name),
                           pl.read_csv(REPO / "data" / name), abs_tol=1e-12, rel_tol=1e-12)
    for name in ("data/release_manifest.json", "docs/index.html", "docs/data/meta.json",
                 "docs/data/hierarchy.json", "docs/data/indicators_meta.json"):
        assert (tmp_path / name).read_bytes() == (REPO / name).read_bytes(), name
    for name in ("aggregate.json", "components.json", "indicators.json", "clustered.json"):
        want = json.loads((REPO / "docs/data" / name).read_text())
        got = json.loads((tmp_path / "docs/data" / name).read_text())
        assert_json_close(got, want)


def assert_json_close(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            assert_json_close(actual[key], expected[key])
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for a, e in zip(actual, expected):
            assert_json_close(a, e)
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=1e-12, rel=1e-12)
    else:
        assert actual == expected


def test_fred_gate_checks_values_inside_the_target_quarter(tmp_path, monkeypatch):
    def download(url, dest, **kwargs):
        dest.write_text("observation_date,TEST\n2026-01-01,1\n2026-04-01,.\n2026-07-01,3\n")

    monkeypatch.setattr(update.fetch_series, "_curl_dl", download)
    dates = update.fetch_fred(tmp_path, "TEST")
    assert update.check_fred_coverage("2026Q1", "Q", dates)
    assert not update.check_fred_coverage("2026Q2", "Q", dates)
    assert update.check_fred_coverage("2026Q3", "Q", dates)

    dates = [dt.date(2026, month, 1) for month in (4, 5, 6, 7)]
    assert update.check_fred_coverage("2026Q2", "M", dates)
    assert not update.check_fred_coverage("2026Q2", "M", [dates[0], *dates[2:]])
    daily = [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(91)]
    daily = [d for d in daily if d.weekday() < 5] + [dt.date(2026, 9, 30)]
    assert update.check_fred_coverage("2026Q2", "D", daily)
    assert not update.check_fred_coverage("2026Q2", "D", [daily[0], daily[-2]])
    assert not update.check_fred_coverage("2026Q3", "D", daily)


def test_weekly_check_picks_up_each_eligible_quarter():
    assert update.target_quarters("2026Q2", dt.date(2026, 12, 14)) == []
    assert update.target_quarters("2026Q2", dt.date(2026, 12, 16)) == ["2026Q3"]
    assert update.target_quarters("2026Q1", dt.date(2026, 12, 16)) == ["2026Q2", "2026Q3"]


def test_updater_replaces_estimates_only_when_observations_arrive(tmp_path, monkeypatch):
    copy_release(tmp_path)
    monkeypatch.setattr(update, "ready_quarters", lambda raw, targets, notes, local_raw: targets)
    monkeypatch.setattr(update, "fetch_optional", lambda *args: [])
    monkeypatch.setattr(update, "refresh_systemic", lambda *args: None)
    observed_file = tmp_path / "data/panel_quarterly.csv"
    observed = pl.read_csv(observed_file).filter(pl.col("quarter") <= "2025Q4")
    observed.write_csv(observed_file)
    forecast_quarter = "2026Q1"
    observed_name, retained_name = "Real Debt Growth of Nonfinancial Businesses", "Ratio of Household Savings to GDP"
    estimates = {observed_name: 2.0, retained_name: 4.0}
    forecast_row = {c: estimates.get(c) for c in observed.columns}
    forecast_row["quarter"] = forecast_quarter
    pl.concat([observed, pl.DataFrame([forecast_row])], how="vertical_relaxed").write_csv(
        tmp_path / "data/panel_complete.csv")
    flags_path = tmp_path / "data/panel_complete_flags.csv"
    flags = pl.read_csv(flags_path).filter(pl.col("quarter") <= "2025Q4")
    forecast_flags = {c: "assumption-mean" if c in estimates else "missing" for c in flags.columns}
    forecast_flags["quarter"] = forecast_quarter
    pl.concat([flags, pl.DataFrame([forecast_flags])]).write_csv(flags_path)
    with (tmp_path / "data/nowcast_provenance.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("quarter", "indicator", "value_source", "value", "selection_detail"))
        for name, value in estimates.items():
            writer.writerow((forecast_quarter, name, "assumption-mean", value, "fixture"))
    meta_path = tmp_path / "data/_meta.json"
    meta = json.loads(meta_path.read_text())
    meta.update(last_built_quarter="2025Q4", nowcast_through=forecast_quarter,
                built_at_utc="2026-05-01T00:00:00Z")
    meta_path.write_text(json.dumps(meta))

    def fragment(_raw, start, end, **kwargs):
        assert start == end == forecast_quarter
        row = {c: None for c in observed.columns}
        row.update(quarter=start, **{c: 10.0 for c in update.Y9C_REQUIRED})
        row[observed_name] = 12.0
        row[retained_name] = None
        return pl.DataFrame([row])

    monkeypatch.setattr(update.build_panel, "build", fragment)
    real_build = release.build_release

    def fail_after_build(stage):
        real_build(stage)
        raise RuntimeError("injected pre-publication failure")

    watched = (*release.RELEASE_OUTPUTS, *(f"data/{name}" for name in release.PANEL_INPUTS))
    before = {name: (tmp_path / name).read_bytes() if (tmp_path / name).exists() else None
              for name in watched}
    monkeypatch.setattr(release, "build_release", fail_after_build)
    with pytest.raises(RuntimeError, match="injected pre-publication failure"):
        update.update_release(tmp_path, [forecast_quarter], [])
    for name, contents in before.items():
        path = tmp_path / name
        assert (path.read_bytes() if path.exists() else None) == contents, name

    monkeypatch.setattr(release, "build_release", real_build)
    assert update.update_release(tmp_path, [forecast_quarter], []) == [forecast_quarter]
    panel, meta = release.validate_panel(tmp_path / "data")
    assert panel[observed_name][-1] == 12.0
    assert panel[retained_name][-1] == estimates[retained_name]
    _, remaining = release.read_provenance(tmp_path / "data")
    assert (forecast_quarter, observed_name) not in remaining
    assert meta["last_built_quarter"] == forecast_quarter


def test_file_replacement_rolls_back_on_error(tmp_path, monkeypatch):
    stage = tmp_path / "candidate"
    stage.mkdir()
    for name in ("a", "b", "c"):
        (stage / name).write_text("new")
    (tmp_path / "a").write_text("old")
    (tmp_path / "c").write_text("old")
    replace = release.os.replace

    def fail_last(source, dest):
        if source == stage / "c":
            raise OSError("failed replacement")
        replace(source, dest)

    monkeypatch.setattr(release.os, "replace", fail_last)
    with pytest.raises(OSError, match="failed replacement"):
        release.publish(tmp_path, stage, ("a", "b", "c"))
    assert (tmp_path / "a").read_text() == "old"
    assert not (tmp_path / "b").exists()
    assert (tmp_path / "c").read_text() == "old"


def test_y9c_uses_target_quarter_and_hcr_reporters(tmp_path):
    quarter = "2026Q2"
    path = tmp_path / "y9c" / update.y9c_filename(quarter)
    path.parent.mkdir()
    row = dict.fromkeys(update.build_panel.Y9C_ITEMS, 0)
    row.update(RSSD9001=1, RSSD9999=20260630, BHCK2170=1_000, BHCK3217=100,
               BHCK2122=400, BHDM6631=800, BHCAP859=90, BHCA8274=100,
               BHCAP863=35, BHCAA223=900)
    other_quarter = dict(row, RSSD9001=2, RSSD9999=20260331, BHCAP859=200)
    non_hcr = dict(row, RSSD9001=3, BHCAP859=None, BHCAA223=2_000)
    pl.DataFrame([row, other_quarter, non_hcr]).write_csv(path)
    series = update.build_panel.b_y9c(tmp_path, quarter, quarter)
    assert series["Bank Leverage (Tier 1 common equity ratio)"]["value"].item() == 10.0
    assert series["Bank Leverage (Total Capital Ratio)"]["value"].item() == 15.0
    assert series["Maturity Mismatch (Loans as a percent of deposits)"]["value"].item() == 50.0
