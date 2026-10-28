"""Tests for the HF nowcast grading script (src/hf_grade.py).

- grading math on synthetic vintage rows (known RMSE / mean error)
- CLI exits non-zero when the target quarter has no vintages or no
  actual, and zero with a graded report when both exist

Runnable with plain python3 (no pytest needed):
    python3 tests/test_hf_grade.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))
from hf_grade import grade  # noqa: E402


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return False
    return True


def main() -> int:
    ok = True

    rows = [
        {"vintage": "2026-10-02", "nowcast_value": 2.6,
         "carry_forward": 2.8},
        {"vintage": "2026-10-09", "nowcast_value": 2.3,
         "carry_forward": 2.8},
        {"vintage": "2026-10-16", "nowcast_value": 2.6,
         "carry_forward": 2.8},
    ]
    g = grade(rows, actual=2.5)
    ok &= check(g["n_vintages"] == 3, "grade: n_vintages")
    ok &= check(abs(g["rmse_nowcast"] - np.sqrt(0.02)) < 1e-9,
                f"grade: rmse_nowcast {g['rmse_nowcast']} != sqrt(0.02)")
    ok &= check(abs(g["rmse_carry_forward"] - 0.3) < 1e-9,
                "grade: rmse_carry_forward != 0.3")
    ok &= check(abs(g["mean_error_nowcast"]) < 1e-9,
                "grade: mean_error_nowcast != 0")

    with tempfile.TemporaryDirectory() as td:
        vint = Path(td) / "vintages.csv"
        pl.DataFrame([{
            "vintage": "2026-10-23", "target_quarter": "2026Q3",
            "nowcast_value": 2.5273, "carry_forward": 2.5155,
            "last_published_quarter": "2026Q2",
            "last_published_value": 2.5155, "rmse_band": 0.0509,
            "scoos_latest_quarter": "2026Q3", "scoos_latest_value": 0.0,
        }]).write_csv(vint)
        act = Path(td) / "actuals.csv"
        pl.DataFrame([{"quarter": "2026Q3", "value": 2.55}]).write_csv(act)

        def run(target):
            return subprocess.run(
                [sys.executable, str(HERE / "src" / "hf_grade.py"),
                 "--vintages", str(vint), "--target-quarter", target,
                 "--actuals", str(act)],
                capture_output=True, text=True)

        r = run("2026Q3")
        ok &= check(r.returncode == 0 and "RMSE nowcast" in r.stdout,
                    f"grade CLI on a graded quarter: rc={r.returncode}, "
                    f"out={r.stdout!r} err={r.stderr!r}")
        r = run("2026Q4")
        ok &= check(r.returncode != 0,
                    "grade CLI must fail when no vintages target the "
                    "quarter")
        act2 = Path(td) / "actuals2.csv"
        pl.DataFrame([{"quarter": "2026Q4", "value": 2.6}]).write_csv(act2)
        r = subprocess.run(
            [sys.executable, str(HERE / "src" / "hf_grade.py"),
             "--vintages", str(vint), "--target-quarter", "2026Q3",
             "--actuals", str(act2)],
            capture_output=True, text=True)
        ok &= check(r.returncode != 0,
                    "grade CLI must fail when the actual is missing")

    print("test_hf_grade: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
