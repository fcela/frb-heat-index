import csv
import json
import shutil
from pathlib import Path

import pytest

from src.spain import build_spain

REPO = Path(__file__).resolve().parents[1]


def test_offline_release_and_quarter_coverage(tmp_path, monkeypatch):
    def fail_fetch(*args, **kwargs):
        pytest.fail("offline replay attempted a source fetch")

    monkeypatch.setattr(build_spain.fetch, "_get", fail_fetch)
    shutil.copytree(REPO / "data/spain", tmp_path / "data/spain")
    assert build_spain.main(["--no-fetch", "--root", str(tmp_path)]) == 0
    outputs = ["docs/data/spain.json", "docs/data/spain_meta.json", "docs/spain/index.html",
               *(f"data/spain/{name}" for name in ("panel_quarterly.csv", "components.csv",
                  "headline.csv", "coverage.csv", "panel_percentiles.csv", "release_manifest.json"))]
    for name in outputs:
        assert (tmp_path / name).read_bytes() == (REPO / name).read_bytes(), name

    bundle = json.loads((tmp_path / "docs/data/spain.json").read_text())
    counts = [sum(v is not None for v in row) for row in zip(*bundle["components"].values())]
    assert bundle["headline_component_count"] == counts
    with (tmp_path / "data/spain/coverage.csv").open(newline="") as f:
        assert [int(row["component_count"]) for row in csv.DictReader(f)] == counts
    common = [values for values in bundle["components"].values() if None not in values[-2:]]
    matched = bundle["latest_same_component_comparison"]
    assert matched["component_count"] == len(common)
    assert matched["previous_headline"] == pytest.approx(sum(v[-2] for v in common) / len(common))
    assert matched["current_headline"] == pytest.approx(sum(v[-1] for v in common) / len(common))
