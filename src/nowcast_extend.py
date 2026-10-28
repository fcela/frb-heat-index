#!/usr/bin/env python3
"""Extend the completed FRB quarterly panel by ONE quarter (default 2026Q3).

The observed panel (data/panel_quarterly.csv) ends at 2026Q2: 18 series are
observed through 2026Q2 and 30 end at 2025Q4. data/panel_complete.csv already
carries nowcast fills for 2026Q1-Q2 (src/nowcast.py). This script appends the
2026Q3 row to panel_complete.csv using the same decision rule as nowcast.py:

    ship the model fill iff backtest RMSE < 0.9 * carry-forward RMSE
    AND R^2 >= 0.30 (R^2 as in nowcast.choose_spec); otherwise the
    documented assumption fallback (nowcast.fallback_assumption).

Horizons are direct and use observed values only: h=1 for the 18 series
observed through 2026Q2, h=3 for the series ending 2025Q4. A filled value is
never used as an input to another fill (no chaining).

Two structural differences from nowcast.py, both forced by the target
quarter:

  * The ridge bridge cannot use contemporaneous predictors (nothing is
    observed for 2026Q3), so the predictor block for target quarter t is
    the 4 predictor rows t-1..t-4 (lag-1 relative to the target). The
    backtest mirrors this: every training row and every backtest origin
    uses a predictor block ending at that row's target-1.
  * TARGET-EXCLUSION RULE: the target series never appears in its own
    predictor block (nowcast.py's no-target-leakage principle, extended to
    the Q3 setting). For h=3 targets this is automatic (they are not among
    the 18 complete series); for h=1 targets the target's own column is
    dropped from the predictor block, otherwise its lagged values would
    merely duplicate the own-lag features.
  * Predictor columns with any NaN inside the training window are dropped,
    as in nowcast.py.

The maturity-gap series ("Weighted Average Maturity Gap at Commerical
Banks") is NOT nowcast and NOT extended here: its committed regression
extension (src/build_maturity_gap.py) needs contemporaneous 2026Q3 inputs,
and its loan/deposit input is observed only through 2026Q2. Its 2026Q3 cell
stays empty (NaN) in panel_complete.csv, is flagged "unextended" in
panel_complete_flags.csv, and has no 2026Q3 row in nowcast_provenance.csv
(the flags file and data/_meta.json carry the record).

Outputs (all append-only or new; existing content is never rewritten):
  data/panel_complete.csv        the 2026Q3 row appended (147 rows total);
                                 the existing 146 rows are byte-identical
                                 (verified by diff against the pre-run file)
  data/nowcast_provenance.csv    2026Q3 rows appended, existing format
  data/panel_complete_flags.csv  NEW: full flag matrix (quarter + one column
                                 per indicator): "observed" for observed
                                 cells, the provenance value_source for
                                 filled cells, "unextended" for the
                                 maturity-gap 2026Q3 cell, "missing" for
                                 cells that are neither (pre-sample periods
                                 and unfilled interior gaps)
  data/_meta.json                last_built_quarter stays "2026Q2" (the
                                 observed build); adds nowcast_through and
                                 an updated note

data/panel_quarterly.csv is read-only here (sha256 checked before/after).

Usage:
    python src/nowcast_extend.py [--target-quarter 2026Q3]

Requires: polars, numpy.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nowcast import (  # noqa: E402
    AR_ORDERS,
    MATURITY_GAP_COL,
    N_BACKTEST_ORIGINS,
    N_LAGS_OWN,
    R2_THRESHOLD,
    RIDGE_LAMBDAS,
    RMSE_IMPROVEMENT,
    ar_predict,
    choose_spec,
    fallback_assumption,
    fit_ar_direct,
    qkey,
)

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"
PANEL = DATA / "panel_quarterly.csv"
COMPLETE = DATA / "panel_complete.csv"
PROV = DATA / "nowcast_provenance.csv"
FLAGS = DATA / "panel_complete_flags.csv"
META = DATA / "_meta.json"

N_PRED_ROWS = 4  # predictor block rows t-1..t-4 (lag-1 rule)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def next_quarter(q: str) -> str:
    y, qq = qkey(q)
    return f"{y + 1}Q1" if qq == 4 else f"{y}Q{qq + 1}"


# ----------------------------------------------------------------------------
# lag-1 ridge bridge (nowcast.py's ridge is contemporaneous; see docstring)
# ----------------------------------------------------------------------------

def fit_ridge_lag1(y: np.ndarray, P: np.ndarray, h: int, lam: float):
    """Direct h-step ridge: y_t on [own lags 1..4 as of origin t-h] +
    [predictor rows t-1..t-4]. Returns (beta, b0, mu, sd) or None."""
    n = len(y)
    rows = []
    for t in range(h + N_LAGS_OWN, n):
        if np.isnan(y[t]):
            continue
        own = y[t - h - N_LAGS_OWN + 1: t - h + 1][::-1]
        pred = P[t - N_PRED_ROWS: t][::-1].reshape(-1)
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
    beta = np.linalg.solve(Xs.T @ Xs + lam * np.eye(Xs.shape[1]), Xs.T @ yy)
    b0 = float(yy.mean())
    return beta, b0, mu, sd


def ridge_predict_lag1(fit, hist: np.ndarray, P_full: np.ndarray,
                       t_pred: int) -> float:
    """Predict y at target row t_pred. hist ends at the origin
    (row t_pred - h). Predictor block: P_full rows t_pred-4..t_pred-1."""
    beta, b0, mu, sd = fit
    own = hist[-N_LAGS_OWN:][::-1]
    Pt = P_full[t_pred - N_PRED_ROWS: t_pred]
    pred = Pt[::-1].reshape(-1)
    feat = np.concatenate([own, pred])
    if Pt.shape[0] != N_PRED_ROWS or np.isnan(feat).any():
        return np.nan
    return float(b0 + ((feat - mu) / sd) @ beta)


def backtest_lag1(y: np.ndarray, P_win: np.ndarray, h: int):
    """Rolling-origin backtest over the last N_BACKTEST_ORIGINS feasible
    origins (o >= 60, target observed), direct horizon, observed data only.
    Predictor blocks lag the target by 1 quarter throughout."""
    n = len(y)
    origins = [o for o in range(n - h - N_BACKTEST_ORIGINS, n - h) if o >= 60]
    if len(origins) < 4:
        return None
    preds = {s: [] for s in ("carry", "mean")}
    for p in AR_ORDERS:
        preds[f"ar{p}"] = []
    for lam in RIDGE_LAMBDAS:
        preds[f"ridge{lam}"] = []
    actuals = []
    for o in origins:
        ytr = y[: o + 1]
        tgt = y[o + h]
        if np.isnan(tgt):
            continue
        actuals.append(tgt)
        preds["carry"].append(ytr[-1])
        preds["mean"].append(float(np.nanmean(ytr[-20:])))
        for p in AR_ORDERS:
            beta, _ = fit_ar_direct(ytr, p, h)
            preds[f"ar{p}"].append(
                ar_predict(beta, ytr, p, h) if beta is not None else np.nan)
        for lam in RIDGE_LAMBDAS:
            fit = fit_ridge_lag1(ytr, P_win[: o + 1], h, lam)
            preds[f"ridge{lam}"].append(
                ridge_predict_lag1(fit, ytr, P_win, o + h)
                if fit is not None else np.nan)
    actuals = np.array(actuals)
    out = {}
    for s, pr in preds.items():
        pr = np.array(pr, dtype=float)
        mask = ~np.isnan(pr)
        out[s] = (float(np.sqrt(np.mean((pr[mask] - actuals[mask]) ** 2)))
                  if mask.sum() >= 3 else np.inf)
    out["_mse_mean"] = float(np.mean((actuals - np.mean(actuals)) ** 2))
    out["_n"] = len(actuals)
    return out


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target-quarter", default="2026Q3")
    args = ap.parse_args()
    target_q = args.target_quarter
    target_key = qkey(target_q)

    panel_hash_before = sha256(PANEL)
    complete_before = COMPLETE.read_bytes()
    prov_before = PROV.read_bytes()

    df = pl.read_csv(PANEL)
    quarters = df["quarter"].to_list()
    n_rows = df.height
    cols = [c for c in df.columns if c != "quarter"]
    if next_quarter(quarters[-1]) != target_q:
        print(f"ERROR: target {target_q} is not the quarter after the "
              f"observed panel's last quarter {quarters[-1]}", file=sys.stderr)
        return 1
    target_row = n_rows  # 0-based index the new row will occupy

    comp = pl.read_csv(COMPLETE)
    if comp.height != n_rows or comp["quarter"].to_list() != quarters:
        print("ERROR: panel_complete.csv does not end at the observed "
              "panel's last quarter; refusing to append", file=sys.stderr)
        return 1

    ends = {}
    for c in cols:
        v = df[c].to_numpy().astype(float)
        nn = np.nonzero(~np.isnan(v))[0]
        ends[c] = int(nn[-1]) if len(nn) else -1
    complete_cols = [c for c in cols if ends[c] == n_rows - 1]
    print(f"panel: {n_rows} quarters x {len(cols)} indicators; "
          f"{len(complete_cols)} observed through {quarters[-1]}")

    P_full = np.column_stack(
        [df[c].to_numpy().astype(float) for c in complete_cols])

    prov_rows = []          # 2026Q3 provenance dicts
    fills = {}              # indicator -> value for the target row
    counts: dict[str, int] = {}
    stress = []

    for c in cols:
        y = df[c].to_numpy().astype(float)
        last_obs = ends[c]
        h = target_row - last_obs
        if c == MATURITY_GAP_COL:
            counts["unextended"] = counts.get("unextended", 0) + 1
            print(f"  {c[:48]:50s} {target_q} unextended "
                  f"(regression inputs not observed for {target_q})")
            continue
        if h not in (1, 3):
            print(f"ERROR: {c} ends {quarters[last_obs]} -> horizon {h} "
                  f"to {target_q} is outside this build's design (1 or 3)",
                  file=sys.stderr)
            return 1

        pred_idx = [i for i, pc in enumerate(complete_cols) if pc != c]
        Ptr = P_full[: last_obs + 1, pred_idx]
        okp = ~np.isnan(Ptr).any(axis=0)
        pred_idx_c = [i for i, keep in zip(pred_idx, okp) if keep]
        P_win_c = P_full[: last_obs + 1, pred_idx_c]
        P_all_c = P_full[:, pred_idx_c]

        ytr = y[: last_obs + 1]
        bt = backtest_lag1(ytr, P_win_c, h)
        spec, rmse, r2 = None, np.inf, -np.inf
        carry_rmse = None
        if bt is not None:
            spec, rmse, r2 = choose_spec(bt, h)
            carry_rmse = bt["carry"]

        method, value = None, np.nan
        if (bt is not None and spec is not None and np.isfinite(rmse)
                and rmse < RMSE_IMPROVEMENT * carry_rmse
                and r2 >= R2_THRESHOLD):
            if spec.startswith("ar"):
                p = int(spec[2:])
                beta, _ = fit_ar_direct(ytr, p, h)
                value = (ar_predict(beta, ytr, p, h)
                         if beta is not None else np.nan)
            else:
                lam = float(spec[5:])
                fit = fit_ridge_lag1(ytr, P_win_c, h, lam)
                value = (ridge_predict_lag1(fit, ytr, P_all_c, target_row)
                         if fit is not None else np.nan)
            if np.isfinite(value):
                method = "nowcast-model"
                detail = (f"{spec} h={h}; backtest RMSE {rmse:.4f} vs "
                          f"carry {carry_rmse:.4f}, R2 {r2:.2f} "
                          f"(n={bt['_n']})")
            else:
                value = np.nan  # fall through to the assumption fallback
        if method is None:
            method, value, reason = fallback_assumption(ytr, h)
            detail = (f"h={h}; best spec {spec} RMSE {rmse:.4f} "
                      f"R2 {r2:.2f} failed rule (need "
                      f"<{RMSE_IMPROVEMENT}*carry and R2>={R2_THRESHOLD}); "
                      f"{reason}")
            if bt is not None:
                detail += f" [carry RMSE {bt['carry']:.4f}]"

        fills[c] = float(value)
        prov_rows.append({
            "quarter": target_q, "indicator": c, "value_source": method,
            "value": float(value), "selection_detail": detail})
        counts[method] = counts.get(method, 0) + 1
        print(f"  {c[:48]:50s} {target_q} {method:26s} "
              f"{float(value):12.4f}  {detail[:80]}")

        hist = y[: last_obs + 1]
        hist = hist[~np.isnan(hist)]
        lo, hi = float(hist.min()), float(hist.max())
        rng = hi - lo
        over = max(lo - float(value), float(value) - hi, 0.0)
        if rng > 0 and over > 0.1 * rng:
            stress.append((c, float(value), lo, hi))

    # ---- append the target row to panel_complete.csv (text-level append:
    # the existing bytes are preserved exactly)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([target_q] + [
        "" if c == MATURITY_GAP_COL else repr(fills[c]) for c in cols])
    new_complete = complete_before
    if not new_complete.endswith(b"\n"):
        new_complete += b"\n"
    new_complete += buf.getvalue().encode()
    COMPLETE.write_bytes(new_complete)
    # verify: prefix unchanged, and the appended row parses back to the fills
    assert COMPLETE.read_bytes()[: len(complete_before)] == complete_before \
        or COMPLETE.read_bytes().startswith(complete_before.rstrip(b"\n"))
    check = pl.read_csv(COMPLETE)
    assert check.height == n_rows + 1
    for c in cols:
        got = check[c].to_numpy().astype(float)[-1]
        if c == MATURITY_GAP_COL:
            assert np.isnan(got)
        else:
            assert abs(got - fills[c]) < 1e-9, (c, got, fills[c])
    print(f"appended {target_q} row to {COMPLETE.name} "
          f"({n_rows} -> {n_rows + 1} rows; existing rows unchanged)")

    # ---- append provenance rows (existing format, existing bytes preserved)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["quarter", "indicator",
                                        "value_source", "value",
                                        "selection_detail"],
                       lineterminator="\n")
    w.writerows(prov_rows)
    new_prov = prov_before
    if not new_prov.endswith(b"\n"):
        new_prov += b"\n"
    PROV.write_bytes(new_prov + buf.getvalue().encode())
    print(f"appended {len(prov_rows)} provenance rows for {target_q}")

    # ---- flags matrix (new file): observed / provenance source / unextended
    prov_all = pl.read_csv(PROV)
    prov_map = {(r["quarter"], r["indicator"]): r["value_source"]
                for r in prov_all.iter_rows(named=True)}
    all_quarters = quarters + [target_q]
    flag_rows = []
    for i, q in enumerate(all_quarters):
        row = {"quarter": q}
        for c in cols:
            if i < n_rows and not np.isnan(
                    df[c].to_numpy().astype(float)[i]):
                row[c] = "observed"
            elif (q, c) in prov_map:
                row[c] = prov_map[(q, c)]
            elif q == target_q and c == MATURITY_GAP_COL:
                row[c] = "unextended"
            else:
                row[c] = "missing"
        flag_rows.append(row)
    pl.DataFrame(flag_rows).write_csv(FLAGS)
    print(f"wrote {FLAGS.name}: {len(flag_rows)} quarters x {len(cols)} "
          f"indicators")

    # ---- _meta.json: observed build quarter unchanged; record the nowcast
    meta = json.loads(META.read_text())
    meta["nowcast_through"] = target_q
    meta["note"] = (
        "Structural fixes: Tier 1 CET1 splice across Basel III 2014 break; "
        "total capital ratio rebuilt from Y-9C; SLOOS mortgage standards "
        "spliced to successor (SUBLPDHMSGLGNQ); 8 dead Z.1 FRED codes "
        "replaced via Z.1 bulk extraction. All fixable series through "
        "2026Q2. Journal-publication additions: SRISK/total assets and "
        "Granger-causality connectedness (J. Banking & Finance 81, 2017). "
        "Nowcast completion: in the observed panel 30 series end at 2025Q4 "
        "and 18 reach 2026Q2; the 2026Q1-Q2 fills for the 30 are tested "
        "nowcast models or documented assumptions (see "
        "data/nowcast_provenance.csv); maturity-gap proxy extended by OLS "
        "regression on observed inputs. 2023Q4 Y-9C ingestion hole "
        "repaired 2026-10-22 (NIC caret files carry no quoting convention; "
        "parse fix re-reads the FFIEC bulk file), 7 Y-9C-derived series "
        "completed at 2023Q4. 2026Q3 row: appended by "
        "src/nowcast_extend.py under the same decision rule (direct "
        "horizon 1 for the 18 series observed through 2026Q2, horizon 3 "
        "for 29 of the 30 ending 2025Q4; lag-1 predictor block, no filled "
        "value used as an input); the maturity-gap series is unextended "
        "for 2026Q3 because its regression inputs are not observed for "
        "that quarter. Per-cell flags: data/panel_complete_flags.csv.")
    META.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"updated _meta.json: nowcast_through={target_q}, "
          f"last_built_quarter stays {meta['last_built_quarter']}")

    if sha256(PANEL) != panel_hash_before:
        print("ERROR: panel_quarterly.csv changed during the run",
              file=sys.stderr)
        return 1

    print(f"\nQ3 summary: {counts}")
    print("range stress test (fills outside historical [min,max] by "
          ">10% of range):")
    if stress:
        for c, v, lo, hi in stress:
            print(f"  {c}: {v:.4f} vs [{lo:.4f}, {hi:.4f}]  <-- FLAGGED")
    else:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
