"""Tests for the weekly HF leverage nowcast (src/hf_nowcast.py).

- ridge fit/predict: near-exact recovery at tiny lambda, shrinkage
  toward the mean change at the shipped lambda=100
- quarter-to-date features on a hand-computed Friday-panel fixture
  (tests/fixtures/hf/friday_panel.csv: 13 Fridays of 2026Q3)
- vintage-log upsert replaces a rerun's rows, never duplicates
- SCOOS status: pending when the current quarter is past mid-quarter
  and its value has not posted; observed otherwise

Runnable with plain python3 (no pytest needed):
    python3 tests/test_hf_nowcast.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
import hf_nowcast as hf  # noqa: E402

FIXTURE = HERE / "tests" / "fixtures" / "hf" / "friday_panel.csv"


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return False
    return True


def main() -> int:
    ok = True

    # --- ridge: recovery at tiny lambda, shrinkage at lambda=100
    X = np.array([[1., 0.], [0., 1.], [1., 1.], [2., 0.], [0., 2.],
                  [2., 2.], [3., 1.], [1., 3.]])
    beta_true = np.array([2.0, -1.0])
    y = X @ beta_true
    x_new = np.array([1.5, 0.5])
    pred_tiny = hf.ridge_pred(hf.ridge_fit(X, y, 1e-6), x_new)
    ok &= check(abs(pred_tiny - float(x_new @ beta_true)) < 0.05,
                f"ridge tiny-lambda prediction {pred_tiny}, "
                f"want ~{float(x_new @ beta_true)}")
    pred_100 = hf.ridge_pred(hf.ridge_fit(X, y, 100.0), x_new)
    ok &= check(abs(pred_100 - y.mean()) < abs(float(x_new @ beta_true)
                                               - y.mean()),
                "ridge lambda=100 must shrink toward the mean change")

    # --- QTD features on the fixture (hand-computed expectations)
    wk = pl.read_csv(FIXTURE).with_columns(
        pl.col("friday").str.to_date())
    f = hf.qtd_features(wk, 2026, 3, upto=date(2026, 9, 25))
    ok &= check(f is not None, "qtd_features returned None on the fixture")
    if f:
        ok &= check(abs(f["spx_ret"] - np.log(1.1)) < 1e-9,
                    f"spx_ret {f['spx_ret']} != ln(1.1)")
        ok &= check(abs(f["vix"] - 20.0) < 1e-9, f"vix {f['vix']} != 20")
        # dgs10: 12 Fridays at 4.0, last at 4.25 -> mean 52.25/13
        ok &= check(abs(f["oas"] - (5.0 - 52.25 / 13)) < 1e-9,
                    f"oas {f['oas']} != 5 - 52.25/13")
        ok &= check(abs(f["d10"] - 0.25) < 1e-9, f"d10 {f['d10']} != 0.25")
        ok &= check(abs(f["d2"] - (-0.25)) < 1e-9, f"d2 {f['d2']} != -0.25")
        ok &= check(abs(f["usd"] - np.log(1.02)) < 1e-9,
                    f"usd {f['usd']} != ln(1.02)")

    # --- vintage upsert: rerun replaces, never duplicates
    bundle = {
        "vintage_friday": "2026-10-23",
        "last_published_quarter": "2026Q2",
        "last_published_value": 2.5155,
        "nowcasts": [{"target_quarter": "2026Q3", "value": 2.5273,
                      "carry_forward": 2.5155, "rmse_band": 0.0509}],
        "scoos": {"latest_quarter": "2026Q3", "latest_value": 0.0},
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "vintages.csv"
        hf.upsert_vintages(path, bundle)
        bundle["nowcasts"][0]["value"] = 2.5311
        hf.upsert_vintages(path, bundle)
        log = pl.read_csv(path)
        ok &= check(log.height == 1,
                    f"upsert left {log.height} rows, want 1")
        ok &= check(abs(log["nowcast_value"][0] - 2.5311) < 1e-9,
                    "upsert did not replace the rerun's value")

    # --- SCOOS status labeling
    ok &= check(hf.scoos_status("2026Q3", date(2026, 10, 23)) == "observed",
                "SCOOS current quarter must be observed")
    ok &= check(hf.scoos_status("2026Q2", date(2026, 8, 20)) == "pending",
                "SCOOS past mid-quarter with no print must be pending")
    ok &= check(hf.scoos_status("2026Q2", date(2026, 7, 20)) == "observed",
                "SCOOS early in the quarter is not yet due, not pending")

    print("test_hf_nowcast: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
