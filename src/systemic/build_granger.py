#!/usr/bin/env python3
"""Build the Granger-causality connectedness indicator.

Method (Billio, Getmansky, Lo & Pelizzon 2012, adapted):
- Monthly log returns of 30 large listed US financial firms
  (banks, broker-dealers, insurers).
- 36-month rolling windows, stepped quarterly (window ends at quarter-end).
- Pairwise linear Granger-causality tests, 1 lag, 5% significance, with
  Newey-West (HAC) covariance ("adjusted for autocorrelation and
  heteroskedasticity", as in the paper; the paper additionally pre-filters
  with GARCH(1,1) -- omitted here as a documented simplification).
- Indicator = (# significant directed edges) / (n*(n-1)).

Adaptations vs Billio et al. (documented in the proxy catalog):
- 30 firms instead of 100 (25/sector incl. hedge funds): hedge funds have
  no public return data; delisted crisis-era firms (Lehman, Bear Stearns,
  Merrill, Wachovia, WaMu) have no public price history.
- HAC-robust Wald test instead of GARCH(1,1) pre-filtering + robust test.

Output: work/raw/systemic/granger_quarterly.csv
(quarter, value, n_firms, n_sig, n_possible).
"""
from __future__ import annotations

import datetime
import json
import sys
from math import erf, sqrt
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from systemic.universe import GRANGER_TICKERS  # noqa: E402

HERE = Path(__file__).resolve().parent.parent.parent
YRAW = HERE / "work" / "raw" / "yahoo"
OUT = HERE / "work" / "raw" / "systemic" / "granger_quarterly.csv"

WIN = 36
MIN_OBS = 30
ALPHA = 0.05


def load_returns(ticker: str) -> pl.DataFrame:
    d = json.loads((YRAW / f"{ticker}.json").read_bytes())
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
    if not adj or len(adj) != len(ts):
        adj = r["indicators"]["quote"][0]["close"]
    dates = [datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()
             for t in ts]
    df = pl.DataFrame({"date": dates, "px": adj}).with_columns(
        pl.col("date").cast(pl.Date), pl.col("px").cast(pl.Float64)
    ).drop_nulls().sort("date")
    return df.with_columns(
        (pl.col("px") / pl.col("px").shift(1)).log().alias("r")
    ).drop_nulls().select(["date", "r"])


def hac_pvalue(y: np.ndarray, X: np.ndarray, r: int = 2) -> float:
    """Two-sided HAC (Newey-West) p-value for H0: beta[r] = 0."""
    n, k = X.shape
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    e = y - X @ beta
    L = int(np.floor(4 * (n / 100) ** (2 / 9)))
    xe = X * e[:, None]
    S = xe.T @ xe
    for lag in range(1, L + 1):
        w = 1 - lag / (L + 1)
        G = xe[lag:].T @ xe[:-lag]
        S = S + w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    se = sqrt(max(V[r, r], 1e-18))
    t = beta[r] / se
    return 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))


def granger_ij(ri: np.ndarray, rj: np.ndarray) -> float:
    """P-value for i Granger-causing j (1 lag, HAC). NaN if infeasible."""
    m = ~(np.isnan(ri) | np.isnan(rj))
    ri, rj = ri[m], rj[m]
    if len(ri) < MIN_OBS:
        return np.nan
    y = rj[1:]
    X = np.column_stack([np.ones(len(y)), rj[:-1], ri[:-1]])
    if np.linalg.matrix_rank(X) < 3:
        return np.nan
    try:
        return hac_pvalue(y, X, r=2)
    except np.linalg.LinAlgError:
        return np.nan


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print("loading returns...")
    rets: dict[str, pl.DataFrame] = {}
    for t in GRANGER_TICKERS:
        try:
            rets[t] = load_returns(t)
        except Exception as e:  # noqa: BLE001
            print(f"  {t}: failed: {e}", file=sys.stderr)
    # month grid
    all_dates = sorted({d for df in rets.values() for d in df["date"].to_list()})
    # quarter-ends present in the data
    qends: dict[str, datetime.date] = {}
    for d in all_dates:
        q = (d.month - 1) // 3 + 1
        qends[f"{d.year}Q{q}"] = d  # last date of quarter wins (sorted)
    quarters = sorted(qends)
    # The Yahoo monthly histories fetched with period1=0 reach 1985-01 for
    # the core firm panel (RJF 1983-07, KEY 1987-11, SCHW 1987-09), so a
    # full 36-month window exists from 1987Q4 -- every panel quarter
    # 1990Q1 onward satisfies the 36m/30-obs requirements. Verified: the
    # previous 1993Q1 start was only this filter, not a data boundary.
    quarters = [q for q in quarters if q >= "1990Q1" and q <= "2026Q2"]

    rows = []
    for qi, q in enumerate(quarters):
        qend = qends[q]
        # 36-month window ending at qend
        start = datetime.date(qend.year - 3, qend.month, 1)
        win: dict[str, np.ndarray] = {}
        # build aligned panel: all month-ends in window
        months = sorted({d for d in all_dates if start <= d <= qend})
        if len(months) < MIN_OBS:
            continue
        for t, df in rets.items():
            s = df.filter(pl.col("date").is_in(months)).sort("date")
            if s.height >= MIN_OBS:
                # align to months grid
                m = {d: v for d, v in zip(s["date"].to_list(), s["r"].to_list())}
                win[t] = np.array([m.get(d, np.nan) for d in months])
        firms = sorted(win)
        n = len(firms)
        if n < 10:
            continue
        sig = 0
        poss = 0
        for i in firms:
            for j in firms:
                if i == j:
                    continue
                poss += 1
                p = granger_ij(win[i], win[j])
                if not np.isnan(p) and p < ALPHA:
                    sig += 1
        rows.append({"quarter": q, "value": sig / poss if poss else None,
                     "n_firms": n, "n_sig": sig, "n_possible": poss})
        if qi % 20 == 0 or q.endswith("Q4"):
            print(f"  {q}: {sig}/{poss} = {sig / poss:.3f} (n={n})",
                  flush=True)
    pl.DataFrame(rows).write_csv(OUT)
    print(f"wrote {OUT}: {len(rows)} quarters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
