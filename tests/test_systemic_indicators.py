"""Regression tests for the two journal-publication systemic indicators
(SRISK/total assets, Granger-causality connectedness; J. Banking & Finance
81, 2017) added beyond the FEDS Notes 44.

Guards against silent exclusion: the indicators must be present in the
pipeline SPEC, the panel, the site hierarchy, and must actually move the
components and the aggregate index. If any link in that chain drops them,
the site would show 44 indicators while the paper's published version has
46 -- with no error anywhere.

Runnable with plain python3 (no pytest needed):
    python3 tests/test_systemic_indicators.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
import make_site  # noqa: E402
import run_pipeline  # noqa: E402

SRISK = "SRISK (Total capital shortfall relative to total assets)"
GRANGER = "Granger-causality connections among large financial institutions"

EXPECTED = {
    SRISK: ("Bank leverage", +1),
    GRANGER: ("Size/concentration", +1),
}


def test_spec_has_both_with_correct_sign_and_component():
    for name, (component, sign) in EXPECTED.items():
        assert name in run_pipeline.SPEC, f"{name} missing from SPEC"
        got_component, got_sign, _ = run_pipeline.SPEC[name]
        assert got_component == component, f"{name}: component {got_component}"
        assert got_sign == sign, f"{name}: sign {got_sign} (higher must mean more vulnerable)"


def test_short_names_present_for_hierarchy():
    # hierarchy.json only lists indicators present in SHORT_NAMES; without
    # these the site explorer silently drops them.
    for name in EXPECTED:
        assert name in make_site.SHORT_NAMES, f"{name} missing from SHORT_NAMES"


def test_panel_columns_with_expected_coverage():
    panel = pl.read_csv(REPO / "data" / "panel_quarterly.csv")
    assert SRISK in panel.columns
    assert GRANGER in panel.columns
    srisk_n = panel[SRISK].drop_nulls().len()
    granger_n = panel[GRANGER].drop_nulls().len()
    assert srisk_n >= 100, f"SRISK coverage collapsed: {srisk_n}"
    assert granger_n >= 130, f"Granger coverage collapsed: {granger_n}"
    # both must reach the panel's last quarter
    last = panel["quarter"][-1]
    assert panel.filter(pl.col("quarter") == last)[SRISK][0] is not None
    assert panel.filter(pl.col("quarter") == last)[GRANGER][0] is not None


def test_site_jsons_include_both():
    ind = json.loads((REPO / "docs" / "data" / "indicators.json").read_text())
    assert SRISK in ind and GRANGER in ind
    assert any(p["z"] is not None for p in ind[SRISK])
    assert any(p["z"] is not None for p in ind[GRANGER])

    hier = json.loads((REPO / "docs" / "data" / "hierarchy.json").read_text())
    found = {}
    for cat in hier["categories"]:
        for comp in cat["components"]:
            for i in comp["indicators"]:
                if i["name"] in EXPECTED:
                    found[i["name"]] = comp["name"]
    assert found.get(SRISK) == "Bank leverage", f"SRISK hierarchy: {found}"
    assert found.get(GRANGER) == "Size/concentration", f"Granger hierarchy: {found}"


def test_both_move_components_and_aggregate():
    # The indicators must be real inputs, not decoration: removing them
    # must change the Bank leverage / Size-concentration components and
    # the aggregate index by a non-trivial amount.
    panel = pl.read_csv(REPO / "data" / "panel_quarterly.csv")
    comp_new, agg_new, _ = run_pipeline.run_pipeline(panel)
    comp_old, agg_old, _ = run_pipeline.run_pipeline(panel.drop([SRISK, GRANGER]))
    for cname in ("Bank leverage", "Size/concentration"):
        diff = (comp_new[cname].fill_null(0)
                - comp_old[cname].fill_null(0)).abs().max()
        assert diff > 0.01, f"{cname} unaffected by new indicators (Δ={diff})"
    adiff = (agg_new["Aggregate index"].fill_null(0)
             - agg_old["Aggregate index"].fill_null(0)).abs().max()
    assert adiff > 0.005, f"aggregate unaffected (Δ={adiff})"


if __name__ == "__main__":
    test_spec_has_both_with_correct_sign_and_component()
    test_short_names_present_for_hierarchy()
    test_panel_columns_with_expected_coverage()
    test_site_jsons_include_both()
    test_both_move_components_and_aggregate()
    print("all systemic-indicator regression tests passed")
