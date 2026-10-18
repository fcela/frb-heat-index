"""Regression tests for the revenue-tracker concentration + share-migration
analysis (docs/revenue/analysis.js, rendered by docs/revenue/app.js).

The math lives in a pure ESM module so node can exercise it directly against
the real site bundles; a DOM/Plot-stubbed harness renders every section for
both frequencies.
"""
import json
import shutil
import subprocess
from pathlib import Path

import polars as pl
import pytest

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "revenue_site"
NODE = shutil.which("node")


def run_node(script, *args, cwd=None):
    r = subprocess.run(
        [NODE, str(script), *(str(a) for a in args)],
        capture_output=True, text=True, cwd=cwd or REPO, timeout=120,
    )
    assert r.returncode == 0, f"node failed:\n{r.stderr}"
    return r.stdout


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_syntax():
    for f in ["docs/revenue/app.js", "docs/revenue/analysis.js"]:
        r = subprocess.run([NODE, "--check", str(REPO / f)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"node --check failed for {f}:\n{r.stderr}"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_ttm_transform():
    out = run_node(FIX / "ttm-check.mjs", REPO)
    assert "TTM OK" in out


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_site_renders_both_frequencies(tmp_path):
    out = run_node(FIX / "render-harness.mjs", REPO, tmp_path)
    res = json.loads(out)
    assert res["sections"] == 11, res
    assert res["qSections"] == 11, res
    assert res["errors"] == [], res["errors"]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_analysis_math_against_bundles():
    """Shares sum to 1 per window; edge-rule invariants; firm counts."""
    out = run_node(FIX / "analysis-check.mjs", REPO)
    d = json.loads(out)
    assert len(d) == 22  # 11 lines x 2 frequencies
    for key, v in d.items():
        assert v["maxShareDev"] < 1e-9, f"{key}: window shares do not sum to 1"
        assert v["edgeOk"], f"{key}: edge rule violated: {v['edgeErrs']}"
        assert v["nFirms"] >= 1
    # Spot-check known edges: trailing stubs are dropped, ramps detected.
    assert d["a/advisory"]["nDroppedTrailing"] == 1  # 2026: HL only
    assert d["a/advisory"]["stableStart"] == 2017
    assert d["q/advisory"]["nDroppedTrailing"] == 1  # 2026Q3: 1 firm
    assert d["a/ficc"]["stableStart"] == 2013


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_hhi_matches_hand_computation():
    """Independent python HHI for advisory 2020 vs the JS implementation."""
    panel = pl.read_csv(REPO / "data" / "revenue" / "panel_annual.csv")
    s = (panel.filter(
        (pl.col("taxonomy_line") == "advisory") & (pl.col("year") == 2020)
    ).select(["firm", "net_revenue_m"]).drop_nulls())
    vals = s["net_revenue_m"].to_list()
    tot = sum(vals)
    hhi_py = sum((v / tot) ** 2 for v in vals)

    out = run_node(FIX / "analysis-check.mjs", REPO)
    d = json.loads(out)
    hhi_js = dict(d["a/advisory"]["hhiSample"])[2020]
    assert abs(hhi_py - hhi_js) < 1e-9, (hhi_py, hhi_js)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_balanced_panel_identification():
    """GS quarterly FICC exits after 2019 (real reporting change: GS moved to
    3 segments in XBRL) — it must NOT appear in the balanced set, and the
    Sankey must carry it as an explicit exit, not a share loss."""
    out = run_node(FIX / "analysis-check.mjs", REPO)
    d = json.loads(out)
    bal = d["q/ficc"]["balanced"]
    assert "Goldman Sachs" not in bal
    assert set(bal) == {"JPMorgan", "Bank of America", "Morgan Stanley"}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_advisory_independents_never_folded():
    """The six independents stay individually labeled in advisory migration
    (never absorbed into "Other")."""
    keep = ["Evercore", "Lazard", "Moelis",
            "Houlihan Lokey", "PJT Partners", "Jefferies"]
    script = (
        "import{buildPeriods,migration,INDEPENDENTS}"
        f" from {json.dumps('file://' + str(REPO / 'docs/revenue/analysis.js'))};"
        "import{readFileSync}from'fs';"
        f"const b=JSON.parse(readFileSync({json.dumps(str(REPO / 'docs/data/revenue.json'))}));"
        "const line=b.lines.find(l=>l.line==='advisory');"
        "const P=buildPeriods(line,'a');"
        "const mig=migration(P,'a',{keepSet:new Set(INDEPENDENTS)});"
        "console.log(JSON.stringify(mig.order));"
    )
    r = subprocess.run([NODE, "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    order = json.loads(r.stdout)
    for f in keep:
        assert f in order, f"{f} folded into Other"
