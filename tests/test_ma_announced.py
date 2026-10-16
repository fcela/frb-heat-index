"""Regression tests for the PitchBook announced-M&A series.

Run: python3 tests/test_ma_announced.py (no pytest needed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pulse import ma_announced


def test_ma_announced_values():
    df = ma_announced.fetch_ma_announced()
    assert df.height == 12, f"expected 12 annual obs, got {df.height}"
    assert list(df.columns) == ["date", "value"]
    # Spot-check anchored values from the PDFs
    by_year = {str(d)[:4]: v for d, v in df.iter_rows()}
    assert by_year["2014"] == 3187.1, by_year["2014"]
    assert by_year["2021"] == 4857.0, by_year["2021"]
    assert by_year["2025"] == 4938.5, by_year["2025"]
    # All values finite and in sane $2-6T range
    assert df["value"].is_finite().all()
    assert (df["value"] >= 2000.0).all() and (df["value"] <= 6000.0).all()
    # Sorted ascending, annual Dec-31 dates
    assert df["date"].is_sorted()
    print("ok test_ma_announced_values")


def test_ma_announced_rejects_insane():
    orig = list(ma_announced.ANNUAL_VALUES)
    try:
        ma_announced.ANNUAL_VALUES = [(2026, 99999.9, "x")]
        try:
            ma_announced.fetch_ma_announced()
            raise AssertionError("should have raised on insane value")
        except RuntimeError:
            pass
    finally:
        ma_announced.ANNUAL_VALUES = orig
    print("ok test_ma_announced_rejects_insane")


if __name__ == "__main__":
    test_ma_announced_values()
    test_ma_announced_rejects_insane()
    print("all ma_announced tests passed")
