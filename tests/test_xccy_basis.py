"""Regression tests for the EUR/USD cross-currency basis guards.

The construction divides the spot/futures gap by tau, so a short residual
tenor or a stale spot leg manufactures basis moves of hundreds of bp
(November 2026 printed -171.97 bp, then +163.21 bp on recompute, from
identical code). These tests pin the two guards with synthetic windows:
prints need tau >= TAU_MIN and a spot observation at most one business
day old; a month left with no qualifying days is no-data.

Run: python3 tests/test_xccy_basis.py (no pytest needed).
"""

import datetime
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pulse import xccy_basis

D = datetime.date
R_EUR = 2.028  # the stale FRED Euribor level documented in the module
R_USD = 4.19


def _bdays(start: D, end: D) -> list[D]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def _frame(pairs) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [d for d, _ in pairs], "value": [v for _, v in pairs]}
    ).sort("date")


def _run(front: pl.DataFrame, spot: pl.DataFrame,
         tbill_days: list[D]) -> pl.DataFrame:
    series = {
        "DEXUSEU": spot,
        "IR3TIB01EZM156N": _frame([(D(2026, 1, 1), R_EUR)]),
        "DGS3MO": _frame([(d, R_USD) for d in tbill_days]),
    }
    orig_front = xccy_basis._front_contract_series
    orig_fred = xccy_basis._fred_daily
    xccy_basis._front_contract_series = lambda: front
    xccy_basis._fred_daily = lambda fred_id, timeout=60: series[fred_id]
    try:
        return xccy_basis.fetch_xccy_basis()
    finally:
        xccy_basis._front_contract_series = orig_front
        xccy_basis._fred_daily = orig_fred


def _front(expiry: D, f_by_date: dict) -> pl.DataFrame:
    dates = sorted(f_by_date)
    return pl.DataFrame({
        "date": dates,
        "F": [f_by_date[d] for d in dates],
        "expiry": [expiry] * len(dates),
    }).sort("date")


def _by_month(df: pl.DataFrame) -> dict:
    return {str(d): v for d, v in df.iter_rows()}


def test_business_days_after():
    fri, mon, tue = D(2026, 11, 6), D(2026, 11, 9), D(2026, 11, 10)
    assert xccy_basis._business_days_after(fri, fri, 9) == 0
    assert xccy_basis._business_days_after(fri, mon, 9) == 1
    assert xccy_basis._business_days_after(fri, tue, 9) == 2
    print("ok test_business_days_after")


def test_short_tenor_month_is_no_data():
    # The November 2026 window: front contract 6EZ26 (expiry 2026-12-16),
    # spot file ends Friday 2026-11-06 while futures fall into Nov 16.
    expiry = D(2026, 12, 16)
    f = {d: 1.1197 for d in _bdays(D(2026, 10, 1), D(2026, 10, 30))}
    nov = _bdays(D(2026, 11, 2), D(2026, 11, 16))
    for i, d in enumerate(nov):
        f[d] = 1.1182 - 0.0016 * i
    spot = _frame([(d, 1.1154) for d in _bdays(D(2026, 10, 1), D(2026, 11, 6))])
    out = _run(_front(expiry, f), spot, _bdays(D(2026, 10, 1), D(2026, 11, 16)))
    by_month = _by_month(out)
    assert "2026-11-01" not in by_month, by_month
    assert "2026-10-01" in by_month, by_month
    assert abs(by_month["2026-10-01"]) < 25.0, by_month
    assert all(abs(v) < 200.0 for v in by_month.values()), by_month
    print("ok test_short_tenor_month_is_no_data")


def test_stale_spot_days_dropped():
    # Long tenor (6EH27, expiry 2027-03-17), so only the spot-age guard can
    # bite: spot ends 2026-11-06, futures hold to Nov 9 then fall hard for
    # the rest of the month. Unguarded, the stale days are the majority of
    # the month and print up to roughly +1900 bp, dragging the monthly
    # median to several hundred bp; guarded, the print comes only from
    # the days whose spot is at most one business day old.
    expiry = D(2027, 3, 17)
    f = {d: 1.1245 for d in _bdays(D(2026, 11, 2), D(2026, 11, 9))}
    for i, d in enumerate(_bdays(D(2026, 11, 10), D(2026, 11, 30))):
        f[d] = 1.1182 - 0.004 * i
    spot = _frame([(d, 1.1154) for d in _bdays(D(2026, 11, 2), D(2026, 11, 6))])
    out = _run(_front(expiry, f), spot, _bdays(D(2026, 11, 2), D(2026, 11, 30)))
    by_month = _by_month(out)
    assert "2026-11-01" in by_month, by_month
    assert abs(by_month["2026-11-01"]) < 25.0, by_month
    print("ok test_stale_spot_days_dropped")


def test_consistent_month_unaffected():
    # A month built to satisfy CIP at exactly -10 bp, same-date spot and
    # tau well above the floor throughout: the guards must not disturb it.
    expiry = D(2027, 3, 17)
    x = -0.0010
    f = {}
    for d in _bdays(D(2026, 9, 1), D(2026, 9, 30)):
        tau = (expiry - d).days / 360.0
        f[d] = 1.1154 * (1 + (R_USD / 100.0) * tau) / (
            1 + (R_EUR / 100.0 + x) * tau
        )
    spot = _frame([(d, 1.1154) for d in f])
    out = _run(_front(expiry, f), spot, sorted(f))
    by_month = _by_month(out)
    assert "2026-09-01" in by_month, by_month
    assert abs(by_month["2026-09-01"] - (-10.0)) < 0.5, by_month
    print("ok test_consistent_month_unaffected")


if __name__ == "__main__":
    test_business_days_after()
    test_short_tenor_month_is_no_data()
    test_stale_spot_days_dropped()
    test_consistent_month_unaffected()
    print("all xccy_basis tests pass")
