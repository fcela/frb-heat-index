"""Fixture tests for src/cluster_indicators.py (the clustered-view bundle).

The brief's contract, tested:
  - the shipped row order equals a recomputation on fixture data, and the
    rebuild is byte-identical (deterministic, fixed tie-breaking);
  - the order is a permutation of all 46 indicators (on the real committed
    panel, docs/data/clustered.json must be reproducible from
    data/indicators_standardized.csv);
  - correlations use pairwise-complete observations only; insufficient
    overlap or zero variance fails loudly instead of imputing.

Runnable with plain python3 (no pytest needed):
    python3 tests/test_cluster_indicators.py
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np
import polars as pl

import sys
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
import cluster_indicators as ci  # noqa: E402


def _frame(cols: dict[str, list]) -> pl.DataFrame:
    """Build an indicators_standardized-shaped frame: 'quarter' + columns."""
    n = len(next(iter(cols.values())))
    return pl.DataFrame({"quarter": [f"2020Q{i+1}" for i in range(n)], **cols})


def test_heat_scores_matches_site_semantics():
    # fraction of the indicator's own observed values <= the value (the
    # app.js equantile / searchsorted-right convention), NaN passes through.
    f = _frame({"a": [None, 1.0, 2.0, 3.0], "b": [1.0, 1.0, 2.0, None]})
    mat = np.column_stack([
        f[c].cast(pl.Float64, strict=False).to_numpy() for c in ("a", "b")])
    s = ci.heat_scores(mat)
    assert np.allclose(s[:, 0], [np.nan, 1/3, 2/3, 1.0], equal_nan=True)
    assert np.allclose(s[:, 1], [2/3, 2/3, 1.0, np.nan], equal_nan=True)


def test_correlation_distance_hand_checked():
    # x and y perfectly correlated on their overlap -> distance 0;
    # x and z anti-correlated -> distance 2. Pairwise-complete: the shared
    # quarters decide, not the full series.
    T = 48
    x = np.sin(np.arange(T) * 0.3)
    y = 2.0 * np.sin(np.arange(T) * 0.3) + 5.0   # affine of x on all quarters
    z = -np.sin(np.arange(T) * 0.3)              # exact negative
    w = np.cos(np.arange(T) * 0.3)
    w[30:] = np.nan                              # overlaps x on 30 quarters
    scores = np.column_stack([x, y, z, w])
    D = ci.correlation_distance(scores)
    assert D[0, 1] == 0.0, "perfect correlation -> distance 0"
    # r(x, z) = -1 -> d = 2 (clamped from any 1e-16 overshoot)
    assert D[0, 2] <= 2.0 and D[0, 2] >= 2.0 - 1e-9
    # known value: r(x, w) on the 20 shared quarters
    m = np.isfinite(w)
    a, b = x[m], w[m]
    a = a - a.mean(); b = b - b.mean()
    r = (a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum())
    assert abs(D[0, 3] - round(1.0 - r, 12)) < 1e-12
    assert np.allclose(np.diag(D), 0.0) and np.allclose(D, D.T)


def test_insufficient_overlap_raises():
    # x observed on quarters 0-39, y on quarters 20-59: overlap = 20 < 24
    x = np.concatenate([np.sin(np.arange(40.0)), np.full(20, np.nan)])
    y = np.concatenate([np.full(20, np.nan), np.sin(np.arange(40.0))])
    try:
        ci.correlation_distance(np.column_stack([x, y]), min_overlap=24)
    except ValueError as e:
        assert "insufficient overlap" in str(e)
    else:
        raise AssertionError("expected ValueError on insufficient overlap")


def test_zero_variance_raises():
    T = 40
    x = np.sin(np.arange(T) * 0.2)
    flat = np.zeros(T)                  # zero variance on the common sample
    try:
        ci.correlation_distance(np.column_stack([x, flat]))
    except ValueError as e:
        assert "zero variance" in str(e)
    else:
        raise AssertionError("expected ValueError on zero-variance series")


def test_average_linkage_known_answer_and_tie_break():
    # A and B are identical (d=0), C and D are identical (d=0): an exact
    # distance tie. Documented tie-break: the pair with the smallest leaf
    # indices merges first -> (A, B) before (C, D).
    T = 30
    a = np.sin(np.arange(T) * 0.5)
    b = a.copy()
    c = np.cos(np.arange(T) * 0.5)
    d = c.copy()
    D = ci.correlation_distance(np.column_stack([a, b, c, d]))
    merges = ci.average_linkage(D)
    assert merges[0] == (0, 1, 0.0), f"tie must merge (0,1) first, got {merges[0]}"
    assert merges[1] == (2, 3, 0.0)
    # third merge joins the two 2-clusters at the mean cross distance
    cross = D[np.ix_([0, 1], [2, 3])].mean()
    assert abs(merges[2][2] - cross) < 1e-9
    assert merges[2][0] == 4 and merges[2][1] == 5  # internal node ids n + step


def test_leaf_order_and_cut_groups():
    T = 30
    a = np.sin(np.arange(T) * 0.5)
    b = a + 0.01 * np.cos(np.arange(T))
    c = np.cos(np.arange(T) * 0.5)
    d = c + 0.01 * np.sin(np.arange(T))
    e = np.sin(np.arange(T) * 0.5 + 2.0)
    f = e + 0.02 * np.cos(np.arange(T))
    D = ci.correlation_distance(np.column_stack([a, b, c, d, e, f]))
    merges = ci.average_linkage(D)
    order = ci.leaf_order(merges, 6)
    assert sorted(order) == list(range(6))
    # {a,b}, {c,d}, {e,f} subtrees must each be contiguous in the order
    for pair in ([0, 1], [2, 3], [4, 5]):
        p = [order.index(x) for x in pair]
        assert abs(p[0] - p[1]) == 1, f"{pair} not adjacent: {order}"
    # cutting at k=3 recovers exactly those pairs, ordered by row position
    groups = ci.cut_groups(merges, 6, 3)
    assert sorted(map(sorted, groups)) == [[0, 1], [2, 3], [4, 5]]
    # and each group is one contiguous block of the row order
    pos = {leaf: p for p, leaf in enumerate(order)}
    spans = sorted((min(pos[l] for l in g), max(pos[l] for l in g)) for g in groups)
    assert all(spans[i][1] + 1 == spans[i + 1][0] for i in range(len(spans) - 1))


def test_bundle_deterministic_and_permutation():
    # rebuild twice from the same fixture -> byte-identical bundle; order is
    # a permutation of every indicator, names preserved in 'leaves'.
    T = 50
    rng_vals = np.sin(np.arange(T) * np.linspace(0.1, 1.3, T)) * 3.0
    cols = {}
    for k in range(7):
        v = rng_vals + 0.1 * k * np.cos(np.arange(T) + k)
        if k == 2:                     # leading gap only: the complete
            v[:13] = np.nan            # window is quarters 13..49
        cols[f"ind{k}"] = [None if not np.isfinite(x) else float(x) for x in v]
    std = _frame(cols)
    b1 = ci.compute_bundle(std)
    b2 = ci.compute_bundle(std)
    assert json.dumps(b1) == json.dumps(b2), "rebuild is not deterministic"
    n = len(b1["leaves"])
    assert sorted(b1["order"]) == list(range(n))
    assert set(b1["leaves"]) == set(cols)
    assert len(b1["merges"]) == n - 1
    # heights are monotone for UPGMA (cuts at the largest heights are valid)
    hs = [h for _, _, h in b1["merges"]]
    assert all(hs[i] <= hs[i + 1] + 1e-12 for i in range(len(hs) - 1))
    # every cut group contiguous in row order; partition covers all leaves
    pos = {leaf: p for p, leaf in enumerate(b1["order"])}
    assert sorted(l for g in b1["cut"] for l in g) == list(range(n))
    spans = sorted((min(pos[l] for l in g), max(pos[l] for l in g)) for g in b1["cut"])
    assert all(spans[i][1] + 1 == spans[i + 1][0] for i in range(len(spans) - 1))


def test_complete_window_is_the_all_observed_suffix():
    # a starts at quarter 2, b at quarter 5, c is full: the window must
    # start at quarter 5 (the last series to begin) and run to the end.
    T = 40
    base = np.sin(np.arange(T) * 0.3)
    a = base.copy(); a[:2] = np.nan
    b = base * 1.7 + 1.0; b[:5] = np.nan
    cols = {"a": [None if not np.isfinite(x) else float(x) for x in a],
            "b": [None if not np.isfinite(x) else float(x) for x in b],
            "c": [float(x) for x in base + 2.0]}
    std = _frame(cols)
    start, qs = ci.complete_window(std, ["a", "b", "c"])
    assert start == 5 and len(qs) == T - 5
    bundle = ci.compute_bundle(std)
    assert bundle["window"]["quarters"] == qs
    assert bundle["window"]["start"] == qs[0] and bundle["window"]["end"] == qs[-1]
    # the window slice of the source frame contains no nulls at all
    win = std.slice(start, len(qs))
    assert all(win[c].null_count() == 0 for c in ("a", "b", "c"))


def test_trailing_hole_leaves_no_window_and_raises():
    # a series still missing in the final quarter has no complete suffix;
    # clustering must fail loudly rather than fall back to a ragged sample.
    T = 40
    base = np.sin(np.arange(T) * 0.3)
    a = base.copy(); a[-1] = np.nan
    cols = {"a": [None if not np.isfinite(x) else float(x) for x in a],
            "b": [float(x) for x in base + 1.0]}
    std = _frame(cols)
    try:
        ci.compute_bundle(std)
    except ValueError as e:
        assert "complete window" in str(e)
    else:
        raise AssertionError("expected ValueError when no complete window exists")


def test_window_on_real_panel():
    """On the committed panel the complete window is set by the
    prime-mortgage lending-standards start: 2007Q2 through the last
    quarter, and the shipped bundle's window slice has zero nulls."""
    std_csv = HERE / "data" / "indicators_standardized.csv"
    shipped_p = HERE / "docs" / "data" / "clustered.json"
    if not std_csv.exists() or not shipped_p.exists():
        print("skip test_window_on_real_panel: data files absent")
        return
    std = pl.read_csv(std_csv)
    shipped = json.loads(shipped_p.read_text())
    w = shipped["window"]
    assert w["start"] == "2007Q2", f"window start moved: {w['start']}"
    assert w["end"] == std["quarter"][-1]
    assert w["quarters"] == std["quarter"].to_list()[-len(w["quarters"]):]
    win = std.tail(len(w["quarters"]))
    cols = [c for c in std.columns if c != "quarter"]
    assert all(win[c].null_count() == 0 for c in cols), \
        "the clustered window still contains empty cells"


def test_shipped_bundle_equals_recomputation():
    """The brief's core test: the committed docs/data/clustered.json must be
    reproducible from the committed data/indicators_standardized.csv, and
    its order must be a permutation of all 46 indicators."""
    std_csv = HERE / "data" / "indicators_standardized.csv"
    shipped_p = HERE / "docs" / "data" / "clustered.json"
    if not std_csv.exists() or not shipped_p.exists():
        print("skip test_shipped_bundle_equals_recomputation: data files absent")
        return
    shipped = json.loads(shipped_p.read_text())
    rebuilt = ci.compute_bundle(pl.read_csv(std_csv))
    assert json.dumps(shipped, sort_keys=True) == json.dumps(rebuilt, sort_keys=True), \
        "shipped clustered.json does not match a recomputation from the panel"
    assert len(rebuilt["leaves"]) == 46
    assert sorted(rebuilt["order"]) == list(range(46))
    assert set(rebuilt["leaves"]) == set(
        c for c in pl.read_csv(std_csv).columns if c != "quarter")


def test_csv_roundtrip_recomputation():
    # fixture -> CSV -> re-read -> bundle equals the in-memory bundle
    # (the site build path goes through a CSV, so parse order must not matter)
    T = 40
    cols = {f"ind{k}": list(np.sin(np.arange(T) * (0.2 + 0.05 * k)) + k)
            for k in range(5)}
    std = _frame(cols)
    b1 = ci.compute_bundle(std)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "std.csv"
        std.write_csv(p)
        b2 = ci.compute_bundle(pl.read_csv(p))
    assert json.dumps(b1) == json.dumps(b2)


def test_small_panel_degrades_gracefully():
    # a 2-indicator panel cannot support 3 cut groups; the bundle clamps
    T = 30
    std = _frame({"a": list(np.sin(np.arange(T))), "b": list(np.cos(np.arange(T)))})
    b = ci.compute_bundle(std)
    assert b["order"] in ([0, 1], [1, 0])
    assert sorted(map(len, b["cut"])) == [1, 1]


if __name__ == "__main__":
    test_heat_scores_matches_site_semantics()
    test_correlation_distance_hand_checked()
    test_insufficient_overlap_raises()
    test_zero_variance_raises()
    test_average_linkage_known_answer_and_tie_break()
    test_leaf_order_and_cut_groups()
    test_bundle_deterministic_and_permutation()
    test_shipped_bundle_equals_recomputation()
    test_csv_roundtrip_recomputation()
    test_small_panel_degrades_gracefully()
    print("all cluster_indicators tests passed")
