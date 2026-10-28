"""Regression tests for the nowcast-completed panel.

- panel_complete.csv: 147 quarters x 48 indicators (through 2026Q3);
  the only trailing NaN is the maturity-gap 2026Q3 cell, unextended
  by design (its regression inputs are not observed for 2026Q3)
- every filled (quarter, indicator) cell has a provenance record,
  except the maturity-gap 2026Q3 cell, which has none by design and
  is flagged "unextended" in data/panel_complete_flags.csv
- every 2026Q3 cell's flag matches provenance / observed / unextended
- provenance value_source is one of the known categories
- every nowcast-model row's selection_detail satisfies the decision
  rule (RMSE < 0.9 * carry, R2 >= 0.30), parsed from the detail text
- nowcast values are finite and within historical range
- maturity-gap Q1/Q2 extension reproduces the documented regression
- aggregate for 2026Q3 is finite (components use nanmean, so the
  unextended maturity gap does not propagate as a hard NaN)
- no-overwrite: data/panel_quarterly.csv and the pre-Q3 rows of
  panel_complete.csv are byte-identical to the pre-build copies in
  ../base_backup/ when that directory is present (the build layout);
  skipped with a note in a plain repo checkout

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
FLAGS = HERE / "data" / "panel_complete_flags.csv"
COMP = HERE / "data" / "components.csv"
AGG = HERE / "data" / "aggregate.csv"
BACKUP = HERE.parent / "base_backup"
MATURITY_GAP = "Weighted Average Maturity Gap at Commerical Banks"

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

    ok &= check(panel.height == 147 and panel.width == 49,
                f"panel shape {panel.height}x{panel.width}, want 147x49")

    # trailing NaN: only the maturity-gap 2026Q3 cell may be empty,
    # and only in the last row
    bad = []
    for c in cols:
        v = panel[c].to_numpy().astype(float)
        if np.isnan(v[-3:-1]).any():
            bad.append((c, "NaN at 2026Q1/Q2"))
        if np.isnan(v[-1]) and c != MATURITY_GAP:
            bad.append((c, "NaN at 2026Q3"))
    ok &= check(not bad, f"unexpected trailing NaN: {bad}")
    mg_last = panel[MATURITY_GAP].to_numpy().astype(float)[-1]
    ok &= check(np.isnan(mg_last), "maturity gap 2026Q3 must be unextended (NaN)")

    expected = set()
    for c in cols:
        v = raw[c].to_numpy().astype(float)
        for i in (-2, -1):
            if np.isnan(v[i]):
                expected.add((raw["quarter"][i], c))
    for c in cols:
        if c != MATURITY_GAP:
            expected.add(("2026Q3", c))
    got = set(zip(prov["quarter"].to_list(), prov["indicator"].to_list()))
    ok &= check(expected <= got, f"missing provenance for: {expected - got}")
    ok &= check(("2026Q3", MATURITY_GAP) not in got,
                "maturity gap must have NO 2026Q3 provenance row")

    # h=3 path: a series observed through 2025Q4 carries h=3 in its
    # 2026Q3 selection_detail
    fico = prov.filter((pl.col("quarter") == "2026Q3")
                       & pl.col("indicator").str.contains("FICO scores"))
    ok &= check(fico.height == 1 and "h=3" in fico["selection_detail"][0],
                "FICO 2026Q3 provenance must record h=3")

    # decision-rule spot check, parsed from selection_detail: every
    # nowcast-model row must show RMSE < 0.9*carry and R2 >= 0.30
    import re
    pat = re.compile(r"RMSE ([\d.]+) vs carry ([\d.]+), R2 (-?[\d.]+)")
    for r in prov.iter_rows(named=True):
        if r["value_source"] != "nowcast-model":
            continue
        m = pat.search(r["selection_detail"])
        ok &= check(m is not None,
                    f"{r['indicator']} {r['quarter']}: unparseable detail")
        if m:
            rmse, carry, r2 = (float(m.group(1)), float(m.group(2)),
                               float(m.group(3)))
            ok &= check(rmse < 0.9 * carry and r2 >= 0.30,
                        f"{r['indicator']} {r['quarter']}: model row "
                        f"violates the rule (RMSE {rmse} vs carry {carry}, "
                        f"R2 {r2})")

    # flags matrix consistency at 2026Q3 (+ two anchor cells)
    flags = pl.read_csv(FLAGS)
    ok &= check(flags.height == 147 and flags.width == 49,
                f"flags shape {flags.height}x{flags.width}, want 147x49")
    f3 = flags.filter(pl.col("quarter") == "2026Q3").to_dicts()[0]
    prov3 = {r["indicator"]: r["value_source"]
             for r in prov.iter_rows(named=True)
             if r["quarter"] == "2026Q3"}
    for c in cols:
        want = "unextended" if c == MATURITY_GAP else prov3[c]
        ok &= check(f3[c] == want,
                    f"flag 2026Q3 {c}: {f3[c]} != {want}")
    f2 = flags.filter(pl.col("quarter") == "2026Q2").to_dicts()[0]
    ok &= check(f2[MATURITY_GAP] == "regression-extension",
                "flag 2026Q2 maturity gap must be regression-extension")
    f0 = flags.filter(pl.col("quarter") == "1990Q1").to_dicts()[0]
    ok &= check(f0["Bank Leverage (Total Capital Ratio)"] in
                ("observed", "missing"), "1990Q1 anchor flag")

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
    ok &= check(comp.height == 147, f"components rows {comp.height}, want 147")
    ccols = [c for c in comp.columns if c != "quarter"]
    for c in ccols:
        if np.isnan(comp.tail(3).head(2)[c].to_numpy()).any():
            ok &= check(False, f"component {c} NaN at 2026Q1/Q2")
    agg = pl.read_csv(AGG)
    ok &= check(agg.height == 147, f"aggregate rows {agg.height}, want 147")
    ok &= check(not np.isnan(agg.tail(3)["Aggregate index"].to_numpy()).any(),
                "aggregate NaN at 2026Q1/Q2/Q3 (Q3 must be finite)")

    # no-overwrite, against the pre-build copies when present
    if BACKUP.exists():
        import hashlib
        def sha(p):
            return hashlib.sha256(Path(p).read_bytes()).hexdigest()
        ok &= check(sha(RAW) == sha(BACKUP / "panel_quarterly.csv"),
                    "panel_quarterly.csv differs from the pre-build copy")
        base_lines = (BACKUP / "panel_complete.csv").read_bytes().splitlines()
        new_lines = PANEL.read_bytes().splitlines()
        ok &= check(new_lines[:len(base_lines)] == base_lines,
                    "panel_complete pre-Q3 rows differ from the pre-build copy")
    else:
        print("note: ../base_backup not present; byte-identity checks skipped")

    print("test_nowcast: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
