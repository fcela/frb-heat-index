#!/usr/bin/env python3
"""Add the two hedge-fund leverage indicators to the committed panels.

The FEDS panel has no hedge-fund series (Form PF filings are
confidential); these are the published aggregates of the same
filings, added 2026-10 as site extensions in the Nonbank leverage
component:

  1. "Hedge Fund Leverage (Gross assets to net assets, qualifying
     hedge funds)" -- OFR Hedge Fund Monitor public API (no key),
     series FPF-ALLQHF_GAV_SUM / FPF-ALLQHF_NAV_SUM (SEC Form PF
     aggregates for qualifying hedge funds). The ratio of the two
     published aggregates equals the net-asset-weighted mean
     fund-level leverage ratio. Coverage 2013Q1 onward (the start of
     Form PF quarterly reporting) -- a documented leading boundary,
     registered in tests/test_completeness.py.
  2. "Hedge Fund Leverage (SCOOS net percent reporting increased use
     of leverage)" -- FRED EXHE2C1Q8NP (Senior Credit Officer Opinion
     Survey, quarterly). Coverage 2011Q3 onward (the survey's start).

Both series are observed through the panels' last quarter (2026Q2),
so panel_quarterly.csv and panel_complete.csv receive identical
values (no nowcast extension needed). The full rebuild path is also
wired: src/fetch_series.py downloads both sources and
src/build_panel.py builds the same columns from the raw files.

Idempotent: existing columns are dropped and rebuilt.
Usage: python3 research/structural-fixes/apply_hf_leverage.py
"""
from __future__ import annotations

import io
import json
import sys
import urllib.request
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent.parent.parent
OFR_URL = ("https://data.financialresearch.gov/hf/v1/series/multifull"
           "?mnemonics=FPF-ALLQHF_GAV_SUM,FPF-ALLQHF_NAV_SUM&output=csv")
SCOOS_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=EXHE2C1Q8NP"
OFR_COL = ("Hedge Fund Leverage (Gross assets to net assets, "
           "qualifying hedge funds)")
SCOOS_COL = ("Hedge Fund Leverage (SCOOS net percent reporting "
             "increased use of leverage)")


def fetch(url: str) -> bytes:
    # curl rather than urllib: the sandbox's urllib path to FRED times
    # out on the response read while curl completes in seconds.
    import subprocess
    r = subprocess.run(["curl", "-sS", "--max-time", "120", url],
                       capture_output=True, check=True)
    return r.stdout


def qlabel(y: int, m: int) -> str:
    return f"{y}Q{(m - 1) // 3 + 1}"


def ofr_series() -> dict[str, float]:
    raw = fetch(OFR_URL).decode()
    lines = [ln for ln in raw.splitlines()
             if len(ln) > 10 and ln[4] == "-" and ln[:4].isdigit()]
    out = {}
    for ln in lines:
        date, gav, nav = ln.split(",")[:3]
        y, m, _ = (int(p) for p in date.split("-"))
        out[qlabel(y, m)] = float(gav) / float(nav)
    return out


def scoos_series() -> dict[str, float]:
    raw = fetch(SCOOS_URL).decode()
    out = {}
    for row in raw.splitlines()[1:]:
        if not row.strip():
            continue
        date, val = row.split(",")[:2]
        if val in (".", ""):
            continue
        y, m, _ = (int(p) for p in date.split("-"))
        out[qlabel(y, m)] = float(val)
    return out


def main() -> int:
    ofr = ofr_series()
    scoos = scoos_series()
    for name, panel_path in (("panel_quarterly", HERE / "data" / "panel_quarterly.csv"),
                             ("panel_complete", HERE / "data" / "panel_complete.csv")):
        panel = pl.read_csv(panel_path)
        last_q = panel["quarter"][-1]
        for col in (OFR_COL, SCOOS_COL):
            if col in panel.columns:
                panel = panel.drop(col)
        panel = panel.with_columns(
            pl.Series(OFR_COL, [ofr.get(q) if q <= last_q else None
                                for q in panel["quarter"]]),
            pl.Series(SCOOS_COL, [scoos.get(q) if q <= last_q else None
                                  for q in panel["quarter"]]),
        )
        panel.write_csv(panel_path)
        for col, src in ((OFR_COL, ofr), (SCOOS_COL, scoos)):
            vals = panel[col].drop_nulls()
            print(f"{name}: {col[:44]}... {len(vals)} obs "
                  f"{panel.filter(pl.col(col).is_not_null())['quarter'][0]}.."
                  f"{panel.filter(pl.col(col).is_not_null())['quarter'][-1]}, "
                  f"last={vals[-1]:.4g}")
    meta_p = HERE / "data" / "_meta.json"
    meta = json.loads(meta_p.read_text())
    meta["n_indicators"] = 48
    meta_p.write_text(json.dumps(meta, indent=2) + "\n")
    print("data/_meta.json: n_indicators -> 48")
    return 0


if __name__ == "__main__":
    sys.exit(main())
