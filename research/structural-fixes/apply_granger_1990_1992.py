#!/usr/bin/env python3
"""Extend the Granger connectedness series back to 1990Q1 (work-order slice 4).

The series starts at 1993Q1 in the committed data only because
build_granger.py hard-filtered its quarter list to q >= "1993Q1" under the
assumption that the Yahoo monthly return inputs start in 1990 (so a full
36-month window would begin in 1993). That assumption is stale: the
pipeline's own fetcher (src/systemic/fetch_market.py, period1=0) returns
monthly bars from 1985-01 for the core firm panel (17 of 30 tickers; RJF
1983-07, SCHW 1987-09, KEY 1987-11), so a full 36-month window exists from
1987Q4 and EVERY panel quarter 1990Q1..1992Q4 satisfies the methodology
(36-month window, >=30 monthly obs per firm, >=10 firms). Verified before
this apply: rerunning the unmodified builder reproduces the committed
1993Q1..2026Q2 series exactly (all four columns), so the extension adds
history and revises none.

Guards (fail loudly BEFORE writing anything):
  - the rebuilt series must reproduce the committed 1993Q1..2026Q2 values
    exactly (no silent history revision);
  - the only new quarters may be 1990Q1..1992Q4;
  - in data/panel_quarterly.csv the Granger cells at 1990Q1..1992Q4 must be
    null before the run and non-null after; every other Granger cell must
    be unchanged and must equal the systemic series exactly.

Requires: polars. Run: python3 research/structural-fixes/apply_granger_1990_1992.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BUILD = REPO / "work" / "raw" / "systemic" / "granger_quarterly.csv"
PUBLISHED = REPO / "data" / "systemic" / "granger_quarterly.csv"
PANEL = REPO / "data" / "panel_quarterly.csv"
COL = "Granger-causality connections among large financial institutions"
NEW_QUARTERS = [f"{y}Q{q}" for y in (1990, 1991, 1992) for q in range(1, 5)]


def main() -> int:
    if not BUILD.exists():
        print(f"ABORT: builder output {BUILD} missing -- run "
              "src/systemic/build_granger.py first", file=sys.stderr)
        return 1
    new = pl.read_csv(BUILD).sort("quarter")
    old = pl.read_csv(PUBLISHED).sort("quarter")

    # guard 1: committed history reproduced exactly
    j = old.join(new, on="quarter", how="inner", suffix="_new")
    if j.height != old.height:
        print(f"ABORT: rebuilt series lost committed quarters "
              f"({j.height} of {old.height})", file=sys.stderr)
        return 1
    for c in ["value", "n_firms", "n_sig", "n_possible"]:
        bad = j.filter(pl.col(c) != pl.col(c + "_new"))
        if bad.height:
            print(f"ABORT: rebuilt series revises {c} at "
                  f"{bad['quarter'].head(3).to_list()}", file=sys.stderr)
            return 1

    # guard 2: only 1990Q1..1992Q4 added, nothing else
    added = sorted(set(new["quarter"].to_list()) - set(old["quarter"].to_list()))
    if added != NEW_QUARTERS:
        print(f"ABORT: new quarters {added[:5]}.. != {NEW_QUARTERS}",
              file=sys.stderr)
        return 1
    back = new.filter(pl.col("quarter").is_in(NEW_QUARTERS))
    if back["value"].null_count() or back["n_firms"].min() < 10:
        print("ABORT: extension quarters missing values or <10 firms",
              file=sys.stderr)
        return 1

    # guard 3: panel Granger cells before the run
    panel = pl.read_csv(PANEL)
    if COL not in panel.columns:
        print(f"ABORT: panel lacks column {COL!r}", file=sys.stderr)
        return 1
    pv = panel.select(["quarter", COL])
    pre_null = pv.filter(pl.col(COL).is_null())["quarter"].to_list()
    if sorted(pre_null) != NEW_QUARTERS:
        print(f"ABORT: panel Granger nulls before run are {pre_null[:5]}.. "
              f"(n={len(pre_null)}), expected exactly 1990Q1..1992Q4",
              file=sys.stderr)
        return 1
    m = dict(zip(new["quarter"].to_list(), new["value"].to_list()))
    for q, v in zip(pv["quarter"].to_list(), pv[COL].to_list()):
        if q in NEW_QUARTERS or v is None:
            continue
        if float(v) != float(m[q]):
            print(f"ABORT: panel {q} = {v} != systemic {m[q]}", file=sys.stderr)
            return 1

    # publish the rebuilt systemic series, then fill the 12 leading cells
    shutil.copy2(BUILD, PUBLISHED)
    vals = panel[COL].to_list()
    filled = 0
    for i, q in enumerate(panel["quarter"].to_list()):
        if q in NEW_QUARTERS:
            vals[i] = float(m[q])
            filled += 1
    out = panel.with_columns(pl.Series(COL, vals, dtype=pl.Float64))
    out.write_csv(PANEL)

    print(f"published {PUBLISHED} ({new.height} quarters)")
    print(f"panel_quarterly.csv: filled {filled} leading Granger cells "
          f"(1990Q1..1992Q4):")
    for q in NEW_QUARTERS:
        r = new.filter(pl.col("quarter") == q)
        print(f"  {q}: value={r['value'][0]:.6f} n_firms={r['n_firms'][0]} "
              f"n_sig={r['n_sig'][0]}/{r['n_possible'][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
