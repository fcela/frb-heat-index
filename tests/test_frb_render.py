"""Render regression tests for the FRB heat-index page (docs/frb/).

The clustered-view render harness (tests/fixtures/frb/render-harness.mjs)
boots the real docs/assets/app.js under a stubbed DOM + Plot against the
committed site bundles (docs/data/*.json, zero network): default hierarchy
view untouched, the Hierarchy<->Clustered toggle, 46 indicator rows in the
shipped clustered order, every (row, quarter) cell, the dendrogram aligned
to the row order, component chips + legend, tooltips, and hash routing.

Skips (per test) when node is unavailable.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "frb"
NODE = shutil.which("node")


def run_node(script, *args):
    r = subprocess.run(
        [NODE, str(script), *(str(a) for a in args)],
        capture_output=True, text=True, cwd=REPO, timeout=120,
    )
    assert r.returncode == 0, f"node failed:\n{r.stdout}\n{r.stderr}"
    return r.stdout


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_app_js_syntax():
    r = subprocess.run([NODE, "--check", str(REPO / "docs/assets/app.js")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"node --check failed:\n{r.stderr}"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_subpage_app_js_syntax():
    for rel in ("docs/pulse/app.js", "docs/predmarkets/app.js", "docs/revenue/app.js"):
        r = subprocess.run([NODE, "--check", str(REPO / rel)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"node --check failed for {rel}:\n{r.stderr}"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_clustered_view_renders(tmp_path):
    out = run_node(FIX / "render-harness.mjs", REPO, tmp_path)
    res = json.loads(out)
    assert res["errors"] == [], res["errors"]
    meta = json.loads((REPO / "docs/data/meta.json").read_text())
    cluster = json.loads((REPO / "docs/data/clustered.json").read_text())
    n = len(cluster["leaves"])
    assert res["rows"] == n
    assert res["cells"] + res["missing"] == n * meta["n_quarters"]
    assert res["dendroV"] == n - 1
    assert res["dendroH"] == 2 * (n - 1)
    assert res["cutRules"] == len(cluster["cut"]) - 1
    assert res["legendComponents"] == meta["n_components"]
    # indicator heat map (hierarchy view): every indicator in hierarchy
    # order, full grid, group markings for every component + category
    hier = json.loads((REPO / "docs/data/hierarchy.json").read_text())
    n_ind = sum(len(comp["indicators"])
                for cat in hier["categories"] for comp in cat["components"])
    n_comp = sum(len(cat["components"]) for cat in hier["categories"])
    assert res["indRows"] == n_ind
    assert res["indCells"] + res["indMissing"] == n_ind * meta["n_quarters"]
    assert res["indCompRules"] == n_comp - 1
    assert res["indCatRules"] == len(hier["categories"]) - 1


if __name__ == "__main__":
    if NODE is None:
        print("skip frb render tests: node not installed")
    else:
        test_app_js_syntax()
        test_subpage_app_js_syntax()
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            test_clustered_view_renders(Path(td))
        print("all frb render tests passed")
