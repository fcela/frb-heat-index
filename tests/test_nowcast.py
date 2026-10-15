"""Regression tests for the nowcast-completed panel.

- panel_complete.csv has no trailing NaN for any of the 46 indicators
- every filled (quarter, indicator) cell has a provenance record
- provenance value_source is one of the known categories
- nowcast values are finite and within historical range
- maturity-gap extension reproduces the documented regression
- aggregate/components for 2026Q1/Q2 are computed from complete inputs

Runnable with plain python3 (no pytest needed):
    python3 tests/test_nowcast.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
PANEL = HERE / "data" / "panel_complete.csv"
RAW = HERE / "data" / "panel_quarterly.csv"
PROV = HERE / "data" / "nowcast_provenance.csv"
COMP = HERE / "data" / "components.csv"
AGG = HERE / "data" / "aggregate.csv"

SOURCES = {"nowcast-model", "regression-extension", "assumption-carry-forward",
           "assumption-trend", "assumption-mean"}


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return False
    return True


def main() -> int:
    ok = True
    panel = pl.read_csv(PANEL)
    prov = pl.read_csv(PROV)
    raw = pl.read_csv(RAW)
    cols = [c for c in panel.columns if c != "quarter"]

    ok &= check(panel.height == 146 and panel.width == 47,
                f"panel shape {panel.height}x{panel.width}, want 146x47")

    bad = [c for c in cols
           if np.isnan(panel[c].to_numpy().astype(float)[-2:]).any()]
    ok &= check(not bad, f"trailing NaN in: {bad}")

    expected = set()
    for c in cols:
        v = raw[c].to_numpy().astype(float)
        for i in (-2, -1):
            if np.isnan(v[i]):
                expected.add((panel["quarter"][i], c))
    got = set(zip(prov["quarter"].to_list(), prov["indicator"].to_list()))
    ok &= check(expected <= got, f"missing provenance for: {expected - got}")

    unknown = set(prov["value_source"].to_list()) - SOURCES
    ok &= check(not unknown, f"unknown value_source: {unknown}")

    for r in prov.iter_rows(named=True):
        q, c = r["quarter"], r["indicator"]
        pv = panel.filter(pl.col("quarter") == q)[c][0]
        if abs(pv - r["value"]) > 1e-9:
            ok &= check(False, f"{c} {q}: panel {pv} != prov {r['value']}")

    for r in prov.iter_rows(named=True):
        c = r["indicator"]
        if not np.isfinite(r["value"]):
            ok &= check(False, f"{c} {r['quarter']}: non-finite")
        hist = raw[c].to_numpy().astype(float)
        hist = hist[~np.isnan(hist)]
        lo, hi = float(hist.min()), float(hist.max())
        tol = 0.1 * (hi - lo)
        ok &= check(lo - tol <= r["value"] <= hi + tol,
                    f"{c} {r['quarter']}: {r['value']} far outside [{lo}, {hi}]")

    mg = prov.filter(pl.col("indicator").str.contains("Maturity Gap"))
    ok &= check(mg.height == 2, f"maturity gap provenance rows: {mg.height}")
    ok &= check(set(mg["value_source"].to_list()) == {"regression-extension"},
                "maturity gap source != regression-extension")
    vals = mg.sort("quarter")["value"].to_list()
    ok &= check(abs(vals[0] - 10.94) < 0.05 and abs(vals[1] - 10.88) < 0.05,
                f"maturity gap values {vals} != [10.94, 10.88]")

    comp = pl.read_csv(COMP)
    ccols = [c for c in comp.columns if c != "quarter"]
    for c in ccols:
        if np.isnan(comp.tail(2)[c].to_numpy()).any():
            ok &= check(False, f"component {c} NaN at 2026Q1/Q2")
    agg = pl.read_csv(AGG)
    ok &= check(not np.isnan(agg.tail(2)["Aggregate index"].to_numpy()).any(),
                "aggregate NaN at 2026Q1/Q2")

    print("test_nowcast: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
