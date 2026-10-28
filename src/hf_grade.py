#!/usr/bin/env python3
"""Grade archived weekly-nowcast vintages against a realized OFR print.

Compares every row of the vintage log (data/hf_nowcast_vintages.csv,
written by src/hf_nowcast.py) that targets a given quarter against the
quarter's actual published GAV/NAV ratio, and reports: number of
vintages, RMSE of the nowcast, RMSE of the carry-forward benchmark,
mean error of the nowcast, and a per-vintage table.

The actual comes from --actuals (a CSV with quarter,value columns) or
--fetch-ofr (the OFR API, same series as the nowcast target).

Exit code is non-zero when the target quarter has no archived vintages
or no actual is available — a grading run that graded nothing must not
look like success.

Usage:
    python src/hf_grade.py --target-quarter 2026Q3 --fetch-ofr
    python src/hf_grade.py --target-quarter 2026Q3 \
        --actuals actuals.csv [--vintages data/hf_nowcast_vintages.csv]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hf_nowcast import fetch_target  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
DEFAULT_VINTAGES = HERE / "data" / "hf_nowcast_vintages.csv"


def grade(rows: list[dict], actual: float) -> dict:
    """rows: vintage-log rows for one target quarter. Pure function."""
    errs = np.array([r["nowcast_value"] - actual for r in rows],
                    dtype=float)
    cerrs = np.array([r["carry_forward"] - actual for r in rows],
                     dtype=float)
    return {
        "n_vintages": len(rows),
        "rmse_nowcast": float(np.sqrt(np.mean(errs ** 2))),
        "rmse_carry_forward": float(np.sqrt(np.mean(cerrs ** 2))),
        "mean_error_nowcast": float(np.mean(errs)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vintages", default=str(DEFAULT_VINTAGES))
    ap.add_argument("--target-quarter", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--actuals", default=None,
                     help="CSV with quarter,value columns")
    src.add_argument("--fetch-ofr", action="store_true",
                     help="fetch the actual from the OFR API")
    args = ap.parse_args()

    actual = None
    if args.fetch_ofr:
        actual = fetch_target().get(args.target_quarter)
    else:
        df = pl.read_csv(args.actuals)
        hit = df.filter(pl.col("quarter") == args.target_quarter)
        if hit.height:
            actual = float(hit["value"][0])
    if actual is None:
        print(f"ERROR: no actual available for {args.target_quarter}",
              file=sys.stderr)
        return 1

    if not Path(args.vintages).exists():
        print(f"ERROR: vintage log not found: {args.vintages}",
              file=sys.stderr)
        return 1
    log = pl.read_csv(args.vintages)
    rows = [r for r in log.iter_rows(named=True)
            if r["target_quarter"] == args.target_quarter]
    if not rows:
        print(f"ERROR: no vintages target {args.target_quarter}",
              file=sys.stderr)
        return 1

    g = grade(rows, actual)
    print(f"target {args.target_quarter}: actual {actual:.4f}")
    print(f"  vintages graded: {g['n_vintages']}")
    print(f"  RMSE nowcast:        {g['rmse_nowcast']:.4f}")
    print(f"  RMSE carry-forward:  {g['rmse_carry_forward']:.4f}")
    print(f"  mean error nowcast:  {g['mean_error_nowcast']:+.4f}")
    print("  per-vintage:")
    for r in sorted(rows, key=lambda r: r["vintage"]):
        print(f"    {r['vintage']}  nowcast {r['nowcast_value']:.4f}  "
              f"carry-forward {r['carry_forward']:.4f}  "
              f"error {r['nowcast_value'] - actual:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
