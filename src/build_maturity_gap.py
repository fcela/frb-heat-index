#!/usr/bin/env python3
"""Build/extend the Weighted Average Maturity Gap proxy at commercial banks.

The direct Call Report RC-C/RC-E bucket construction was attempted and failed
(correlation -0.04 with the authors' accessible series), so the adopted proxy
is the documented OLS regression (see data/proxy_catalog.csv):

    gap = -1.87 - 3.40*y10 - 0.144*vix + 0.423*loan_dep

fitted on the accessible 1997Q2-2010Q4 series (N=55, R^2=0.799, RMSE=1.77 mo).

Regressors:
  y10      10-year Treasury yield, %. Historically implied as
           Shiller E/P minus the panel's E/P-minus-10Y series; the FRED DGS10
           quarterly mean reproduces the implied value exactly at 2025Q4
           (4.100 vs 4.100), so DGS10 extends it.
  vix      VIX level. FRED VIXCLS quarterly mean reproduces the panel's
           2025Q4 value (17.745 vs 17.75).
  loan_dep loans as a percent of deposits (panel's Maturity Mismatch series).

This script verifies the regression against the panel's latest observed value
and appends 2026Q1/2026Q2 (or any missing tail quarters) to
data/maturity_gap_extended.csv, which src/nowcast.py merges into the panel
with provenance 'regression-extension' (observed inputs, fitted model --
not a statistical nowcast).

Usage: python src/build_maturity_gap.py [--panel data/panel_quarterly.csv]

Requires: polars, numpy. Network access to FRED for DGS10/VIXCLS.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "data" / "maturity_gap_extended.csv"
COL = "Weighted Average Maturity Gap at Commerical Banks"
LOAN_DEP_COL = "Maturity Mismatch (Loans as a percent of deposits)"

B0, B_Y10, B_VIX, B_LOANDEP = -1.87, -3.40, -0.144, 0.423


def fred_quarterly_means(series_id: str, start: str = "2025-10-01") -> dict[str, float]:
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
           f"&cosd={start}")
    txt = urllib.request.urlopen(url, timeout=60).read().decode()
    q: dict[str, list[float]] = defaultdict(list)
    for r in csv.reader(io.StringIO(txt)):
        if len(r) != 2 or r[0] == "DATE":
            continue
        try:
            v = float(r[1])
        except ValueError:
            continue
        dt = date.fromisoformat(r[0])
        q[f"{dt.year}Q{(dt.month - 1) // 3 + 1}"].append(v)
    return {k: float(np.mean(v)) for k, v in q.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", default=str(HERE / "data" / "panel_quarterly.csv"))
    args = ap.parse_args()

    df = pl.read_csv(args.panel)
    quarters = df["quarter"].to_list()

    # sanity: regression must reproduce the panel's last observed value
    last_obs = quarters[int(np.nonzero(
        ~np.isnan(df[COL].to_numpy().astype(float)))[0][-1])]
    row = df.filter(pl.col("quarter") == last_obs).row(0, named=True)
    ep = row["Equity Valuations (Earnings/Price ratio )"]
    ep_m_10y = row["Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)"]
    y10_imp = ep - ep_m_10y
    pred = B0 + B_Y10 * y10_imp + B_VIX * row["Indicators of Volitility (VIX)"] \
        + B_LOANDEP * row[LOAN_DEP_COL]
    actual = row[COL]
    print(f"verify {last_obs}: implied y10={y10_imp:.3f}, "
          f"predicted={pred:.2f}, panel={actual:.2f}")
    if abs(pred - actual) > 0.05:
        print("ERROR: regression does not reproduce panel value", file=sys.stderr)
        return 1

    y10_q = fred_quarterly_means("DGS10")
    vix_q = fred_quarterly_means("VIXCLS")
    print(f"FRED: DGS10 {sorted(y10_q)[-3:]}, VIXCLS {sorted(vix_q)[-3:]}")

    missing = [q for q in quarters
               if q > last_obs and q in y10_q and q in vix_q]
    loan_dep = {r["quarter"]: r[LOAN_DEP_COL]
                for r in df.select(["quarter", LOAN_DEP_COL]).iter_rows(named=True)}
    ext = []
    for q in missing:
        if q not in loan_dep or loan_dep[q] is None or np.isnan(loan_dep[q]):
            print(f"  {q}: loan_dep missing, skipping", file=sys.stderr)
            continue
        gap = B0 + B_Y10 * y10_q[q] + B_VIX * vix_q[q] + B_LOANDEP * loan_dep[q]
        ext.append({"quarter": q, "value": round(float(gap), 2),
                    "y10": round(y10_q[q], 3), "vix": round(vix_q[q], 3),
                    "loan_dep": round(float(loan_dep[q]), 2),
                    "source": "regression-extension"})
        print(f"  {q}: y10={y10_q[q]:.3f} vix={vix_q[q]:.2f} "
              f"loan_dep={loan_dep[q]:.2f} -> gap={gap:.2f} months")
    if not ext:
        print("nothing to extend")
        return 0
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["quarter", "value", "y10", "vix",
                                         "loan_dep", "source"])
        w.writeheader()
        w.writerows(ext)
    print(f"wrote {OUT}: {len(ext)} quarters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
