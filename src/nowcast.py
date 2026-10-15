#!/usr/bin/env python3
"""Nowcast the missing tail quarters of the FRB quarterly panel.

The panel (data/panel_quarterly.csv) holds 46 indicators, 1990Q1-2026Q2.
32 series end at 2025Q4 (source-release lag); 14 reach 2026Q2. This script
fills 2026Q1 and 2026Q2 for the 32 incomplete series so the index pipeline
runs on complete inputs instead of silently averaging over missing data.

Methodology (per series, per horizon h in {1, 2}):
  Candidate specifications:
    - AR(p), p in {1,2,4}, direct-horizon OLS, p chosen by BIC
    - Ridge bridge: y_t on [own lags 1..4 as of origin] + [14 complete
      predictor series, contemporaneous at target + 3 lags], direct horizon,
      lambda chosen from {0.1, 1, 10, 100} by rolling-origin CV
    - Naive benchmarks: carry-forward, trailing-20Q mean
  Backtest: rolling-origin over the last 8 feasible origins, horizons 1 and 2.
    Bridge predictors use realized values through the target quarter, matching
    the operational setting (the 14 predictors are genuinely observed through
    2026Q2); the target series itself is never a predictor (no target leakage).
  Selection: lowest backtest RMSE per horizon.
  Decision rule: use the model nowcast iff
      RMSE_model < 0.9 * RMSE_carryforward  AND  R2 >= 0.30
    (R2 measured against the trailing-mean baseline). Otherwise fall back to
    a documented assumption:
      - lag-1 autocorrelation > 0.7  -> carry-forward
      - significant drift            -> linear trend from last 8 quarters
      - else                         -> trailing-20Q mean
  The maturity-gap series is NOT nowcast: it is extended by its committed
  OLS regression (src/build_maturity_gap.py) on observed 2026Q1/Q2 inputs.

Outputs:
  data/panel_complete.csv      panel with filled tails (146 quarters x 47)
  data/nowcast_provenance.csv  per (quarter, indicator) provenance record

data/panel_quarterly.csv is left untouched (observed values only).

Requires: polars, numpy.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
PANEL = HERE / "data" / "panel_quarterly.csv"
OUT_PANEL = HERE / "data" / "panel_complete.csv"
OUT_PROV = HERE / "data" / "nowcast_provenance.csv"

MATURITY_GAP_COL = "Weighted Average Maturity Gap at Commerical Banks"
MATURITY_GAP_EXT = HERE / "data" / "maturity_gap_extended.csv"

N_BACKTEST_ORIGINS = 8
R2_THRESHOLD = 0.30
RMSE_IMPROVEMENT = 0.90  # model must beat carry-forward by >=10%
AR_ORDERS = (1, 2, 4)
RIDGE_LAMBDAS = (0.1, 1.0, 10.0, 100.0)
N_LAGS_OWN = 4
N_LAGS_PRED = 3  # contemporaneous + 3 lags of complete predictors


def qkey(q: str) -> tuple[int, int]:
    y, qq = q.split("Q")
    return (int(y), int(qq))


# ----------------------------------------------------------------------------
# model fitting utilities (numpy only)
# ----------------------------------------------------------------------------

def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _add_intercept(X: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(X.shape[0]), X])


def fit_ar_direct(y: np.ndarray, p: int, h: int):
    """Direct h-step AR(p): y_t on y_{t-h}..y_{t-h-p+1}. Returns (beta, bic)."""
    n = len(y)
    # target index t runs from h+p-1 .. n-1  (0-based: y[t] predicted from y[t-h..t-h-p+1])
    rows = []
    for t in range(h + p - 1, n):
        if np.isnan(y[t]):
            continue
        lags = y[t - h - p + 1: t - h + 1][::-1]  # [y_{t-h}, ..., y_{t-h-p+1}]
        if np.isnan(lags).any():
            continue
        rows.append((t, lags))
    if len(rows) < p + 5:
        return None, np.inf
    T = np.array([t for t, _ in rows])
    X = _add_intercept(np.array([l for _, l in rows]))
    yy = y[T]
    beta = _ols(X, yy)
    resid = yy - X @ beta
    sse = float(resid @ resid)
    k = p + 1
    bic = len(yy) * np.log(sse / len(yy)) + k * np.log(len(yy))
    return beta, bic


def ar_predict(beta: np.ndarray, hist: np.ndarray, p: int, h: int) -> float:
    """hist: values up to origin (last = y_origin). Predict y_{origin+h}.

    Direct-horizon regressors for target t = origin+h are
    y_{t-h}..y_{t-h-p+1} = y_origin..y_{origin-p+1}."""
    lags = hist[-p:][::-1]
    if np.isnan(lags).any():
        return np.nan
    return float(beta[0] + beta[1:] @ lags)


def fit_ridge_direct(y: np.ndarray, P: np.ndarray, h: int, lam: float):
    """Direct h-step ridge: y_t on [own lags as of t-h] + [P_t..P_{t-3}].
    P: (n, n_pred) complete predictor matrix. Returns (beta, mu, sd) or None."""
    n, n_pred = P.shape
    rows = []
    for t in range(h + N_LAGS_OWN, n):
        if np.isnan(y[t]):
            continue
        own = y[t - h - N_LAGS_OWN + 1: t - h + 1][::-1]
        pred = P[t - N_LAGS_PRED: t + 1][::-1].reshape(-1)  # P_t, P_{t-1}, ...
        feat = np.concatenate([own, pred])
        if np.isnan(feat).any():
            continue
        rows.append((t, feat))
    if len(rows) < 40:
        return None
    T = np.array([t for t, _ in rows])
    X = np.array([f for _, f in rows])
    yy = y[T]
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Xs = (X - mu) / sd
    XtX = Xs.T @ Xs
    beta = np.linalg.solve(XtX + lam * np.eye(Xs.shape[1]), Xs.T @ yy)
    # model: y = b0 + ((x - mu)/sd) . beta  =>  b0 = mean(y)
    b0 = float(yy.mean())
    return beta, b0, mu, sd


def ridge_predict(fit, hist: np.ndarray, P_hist: np.ndarray,
                  P_target: np.ndarray) -> float:
    """hist: y up to origin; P_hist: predictors up to origin (rows);
    P_target: predictor rows for target-3..target (4 rows)."""
    beta, b0, mu, sd = fit
    own = hist[-N_LAGS_OWN:][::-1]
    pred = P_target[::-1].reshape(-1)
    feat = np.concatenate([own, pred])
    if np.isnan(feat).any():
        return np.nan
    return float(b0 + ((feat - mu) / sd) @ beta)


# ----------------------------------------------------------------------------
# backtest
# ----------------------------------------------------------------------------

def backtest(y: np.ndarray, P: np.ndarray, h: int):
    """Rolling-origin backtest over the last N_BACKTEST_ORIGINS feasible origins.
    Returns dict spec -> rmse, plus naive carry-forward rmse and mean-baseline mse."""
    n = len(y)
    # origin o: train on y[:o+1] (0-based inclusive), predict y[o+h]
    # need o+h <= n-1 and enough history: o >= 60
    origins = [o for o in range(n - h - N_BACKTEST_ORIGINS, n - h) if o >= 60]
    if len(origins) < 4:
        return None
    preds = {s: [] for s in ["carry", "mean"]}
    for p in AR_ORDERS:
        preds[f"ar{p}"] = []
    for lam in RIDGE_LAMBDAS:
        preds[f"ridge{lam}"] = []
    actuals = []
    for o in origins:
        ytr = y[: o + 1]
        Ptr = P[: o + 1]
        tgt = y[o + h]
        if np.isnan(tgt):
            continue
        actuals.append(tgt)
        # naive
        preds["carry"].append(ytr[-1])
        m20 = ytr[-20:]
        preds["mean"].append(float(np.nanmean(m20)))
        # AR direct
        for p in AR_ORDERS:
            beta, _ = fit_ar_direct(ytr, p, h)
            preds[f"ar{p}"].append(
                ar_predict(beta, ytr, p, h) if beta is not None else np.nan)
        # ridge direct
        for lam in RIDGE_LAMBDAS:
            fit = fit_ridge_direct(ytr, Ptr, h, lam)
            if fit is None:
                preds[f"ridge{lam}"].append(np.nan)
            else:
                Pt = P[o + h - N_LAGS_PRED: o + h + 1]
                preds[f"ridge{lam}"].append(
                    ridge_predict(fit, ytr, Ptr, Pt)
                    if Pt.shape[0] == N_LAGS_PRED + 1 and not np.isnan(Pt).any()
                    else np.nan)
    actuals = np.array(actuals)
    out = {}
    for s, pr in preds.items():
        pr = np.array(pr, dtype=float)
        mask = ~np.isnan(pr)
        out[s] = float(np.sqrt(np.mean((pr[mask] - actuals[mask]) ** 2))) \
            if mask.sum() >= 3 else np.inf
    # R2 vs trailing-mean baseline
    mse_mean = float(np.mean((actuals - np.mean(actuals)) ** 2))
    out["_mse_mean"] = mse_mean
    out["_n"] = len(actuals)
    return out


def choose_spec(bt: dict, h: int):
    """Return (spec_name, rmse, r2) of the best non-naive spec, or (None,...)."""
    cands = {k: v for k, v in bt.items()
             if not k.startswith("_") and k not in ("carry", "mean")}
    if not cands:
        return None, np.inf, -np.inf
    best = min(cands, key=cands.get)
    rmse = cands[best]
    r2 = 1 - rmse ** 2 / bt["_mse_mean"] if bt["_mse_mean"] > 0 else -np.inf
    return best, rmse, r2


# ----------------------------------------------------------------------------
# assumption fallback
# ----------------------------------------------------------------------------

def fallback_assumption(y: np.ndarray, h: int = 1):
    """Return (method, value, reason) when no model is predictive enough."""
    hist = y[~np.isnan(y)]
    if len(hist) < 8:
        return "assumption-mean", float(np.nanmean(hist)), "too short for model"
    ac1 = float(np.corrcoef(hist[:-1], hist[1:])[0, 1]) \
        if np.nanstd(hist) > 0 else 0.0
    if ac1 > 0.7:
        return ("assumption-carry-forward", float(hist[-1]),
                f"lag-1 autocorr {ac1:.2f} > 0.7; no model beat naive")
    d = np.diff(hist[-8:])
    drift, dsd = float(np.mean(d)), float(np.std(d))
    if dsd > 0 and abs(drift) > 0.5 * dsd:
        t = np.arange(8)
        slope, icept = np.polyfit(t, hist[-8:], 1)
        return ("assumption-trend", float(icept + slope * (7 + h)),
                f"drift {drift:.4f}/q vs noise {dsd:.4f}; trend extrapolation")
    return ("assumption-mean", float(np.mean(hist[-20:])),
            "no persistence, no drift; trailing-20Q mean")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main() -> int:
    df = pl.read_csv(PANEL)
    quarters = df["quarter"].to_list()

    # maturity gap: regression extension (committed builder), not a nowcast
    filled = {}
    prov_rows = []
    if MATURITY_GAP_EXT.exists():
        ext = pl.read_csv(MATURITY_GAP_EXT)
        vals = df[MATURITY_GAP_COL].to_list()
        for r in ext.iter_rows(named=True):
            q = r["quarter"]
            if q in quarters and vals[quarters.index(q)] is None:
                vals[quarters.index(q)] = float(r["value"])
                prov_rows.append({
                    "quarter": q, "indicator": MATURITY_GAP_COL,
                    "value_source": "regression-extension",
                    "value": float(r["value"]),
                    "selection_detail":
                        f"OLS gap=-1.87-3.40*y10-0.144*vix+0.423*loan_dep "
                        f"(R2 0.80, RMSE 1.8mo); inputs y10={r['y10']}, "
                        f"vix={r['vix']}, loan_dep={r['loan_dep']}",
                })
        df = df.with_columns(pl.Series(MATURITY_GAP_COL, vals, dtype=pl.Float64))
        print(f"maturity gap extended: {ext.height} quarters via regression")
    cols = [c for c in df.columns if c != "quarter"]

    # complete predictor series (no trailing NaN)
    pred_cols = []
    target_cols = []
    for c in cols:
        vals = df[c].to_numpy().astype(float)
        nn = np.nonzero(~np.isnan(vals))[0]
        tail = len(vals) - 1 - int(nn[-1]) if len(nn) else len(vals)
        (pred_cols if tail == 0 else target_cols).append(c)
    print(f"predictors (complete): {len(pred_cols)}, targets: {len(target_cols)}")

    P_full = np.column_stack([df[c].to_numpy().astype(float) for c in pred_cols])

    for c in target_cols:
        y = df[c].to_numpy().astype(float)
        nn = np.nonzero(~np.isnan(y))[0]
        last_obs = int(nn[-1])
        horizons = [h for h in (1, 2) if last_obs + h < len(y)]
        if not horizons:
            continue
        ytr = y[: last_obs + 1]
        Ptr = P_full[: last_obs + 1]
        # drop predictor columns NaN in training window (late starters)
        okp = ~np.isnan(Ptr).any(axis=0)
        Ptr_c = Ptr[:, okp]
        P_full_c = P_full[:, okp]
        col_provenance = {}
        for h in horizons:
            tgt_q = quarters[last_obs + h]
            bt = backtest(ytr, Ptr_c, h)
            method, value, detail = None, np.nan, ""
            spec, rmse, r2 = (None, np.inf, -np.inf)
            if bt is not None:
                spec, rmse, r2 = choose_spec(bt, h)
                carry_rmse = bt["carry"]
                if (spec is not None and np.isfinite(rmse)
                        and rmse < RMSE_IMPROVEMENT * carry_rmse
                        and r2 >= R2_THRESHOLD):
                    # refit on full history, predict
                    if spec.startswith("ar"):
                        p = int(spec[2:])
                        beta, _ = fit_ar_direct(ytr, p, h)
                        value = ar_predict(beta, ytr, p, h)
                    else:
                        lam = float(spec[5:])
                        fit = fit_ridge_direct(ytr, Ptr_c, h, lam)
                        Pt = P_full_c[last_obs + h - N_LAGS_PRED: last_obs + h + 1]
                        value = ridge_predict(fit, ytr, Ptr_c, Pt)
                    method = "nowcast-model"
                    detail = (f"{spec} h={h}; backtest RMSE {rmse:.4f} vs "
                              f"carry {carry_rmse:.4f}, R2 {r2:.2f} "
                              f"(n={bt['_n']})")
            if method is None:
                m, value, reason = fallback_assumption(ytr, h)
                method = m
                detail = (f"h={h}; best spec {spec} RMSE "
                          f"{rmse:.4f} R2 {r2:.2f} failed rule "
                          f"(need <{RMSE_IMPROVEMENT}*carry and R2>={R2_THRESHOLD}); "
                          f"{reason}")
                if bt is not None:
                    detail += f" [carry RMSE {bt['carry']:.4f}]"
            col_provenance[tgt_q] = (method, float(value), detail)
            prov_rows.append({
                "quarter": tgt_q, "indicator": c,
                "value_source": method, "value": float(value),
                "selection_detail": detail,
            })
            print(f"  {c[:48]:50s} {tgt_q} {method:26s} "
                  f"{float(value):12.4f}  {detail[:90]}")
        filled[c] = col_provenance

    # assemble completed panel
    out = df.clone()
    for c, qmap in filled.items():
        vals = out[c].to_list()
        for q, (method, value, _detail) in qmap.items():
            vals[quarters.index(q)] = value
        out = out.with_columns(pl.Series(c, vals, dtype=pl.Float64))
    out.write_csv(OUT_PANEL)
    print(f"wrote {OUT_PANEL}: {out.height} quarters x {out.width - 1} indicators")

    with open(OUT_PROV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["quarter", "indicator", "value_source",
                                         "value", "selection_detail"])
        w.writeheader()
        w.writerows(prov_rows)
    print(f"wrote {OUT_PROV}: {len(prov_rows)} provenance records")

    # sanity: no NaN in filled tails
    bad = []
    for c in target_cols:
        v = out[c].to_numpy()
        if np.isnan(v[-2:]).any():
            bad.append(c)
    if bad:
        print(f"ERROR: still-NaN tails: {bad}", file=sys.stderr)
        return 1

    # stress test: flag filled values outside [min, max] of history by >10% of range
    print("\nrange stress test (filled vs historical range):")
    for r in prov_rows:
        c, q = r["indicator"], r["quarter"]
        hist = df[c].to_numpy().astype(float)
        hist = hist[~np.isnan(hist)]
        lo, hi = float(hist.min()), float(hist.max())
        v = float(r["value"])
        rng = hi - lo
        over = max(lo - v, v - hi, 0.0)
        flag = "  <-- OUTSIDE by >10% of range" if rng > 0 and over > 0.1 * rng else ""
        if flag or over > 0:
            print(f"  {c[:45]:47s} {q} val={v:.4f} hist=[{lo:.4f},{hi:.4f}]{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
