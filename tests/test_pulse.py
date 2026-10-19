"""Regression tests for the market-pulse build.

Run: python3 tests/test_pulse.py (no pytest needed).
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.pulse.build_pulse import sanitize_records, strict_json
from src.pulse.segments import SEGMENTS, FETCHABLE_STATUSES, all_indicators


def test_sanitize_nan_inf():
    recs = [
        {"date": "2026-01-01", "value": 1.5},
        {"date": "2026-01-02", "value": float("nan")},
        {"date": "2026-01-03", "value": float("inf")},
        {"date": "2026-01-04", "value": float("-inf")},
        {"date": "2026-01-05", "value": None},
    ]
    out = sanitize_records(recs)
    assert out[0]["value"] == 1.5
    for r in out[1:]:
        assert r["value"] is None, r
    # Strict serialization must not raise and must not contain NaN literals.
    s = strict_json(out)
    assert "NaN" not in s and "Infinity" not in s
    parsed = json.loads(s)
    assert parsed[1]["value"] is None
    print("ok test_sanitize_nan_inf")


def test_strict_json_rejects_nan():
    try:
        strict_json([{"v": float("nan")}])
    except ValueError:
        print("ok test_strict_json_rejects_nan")
        return
    raise AssertionError("strict_json did not reject NaN")


def test_segments_complete():
    # Every indicator has the fields the site needs; none silently dropped.
    n = 0
    for seg_key, ind_key, ind in all_indicators():
        for f in ("title", "why", "source", "frequency", "status"):
            assert f in ind, f"{seg_key}/{ind_key} missing {f}"
        assert ind["status"] in FETCHABLE_STATUSES + ("pending-sourcing",), ind_key
        n += 1
    assert n == 42, f"expected 42 indicators, got {n}"
    assert len(SEGMENTS) == 10
    fetchable = sum(1 for _, _, i in all_indicators() if i["status"] in FETCHABLE_STATUSES)
    assert fetchable == 41, f"expected 41 fetchable, got {fetchable}"
    print(f"ok test_segments_complete ({n} indicators, {fetchable} fetchable)")


def test_site_json_parses_strictly():
    p = Path(__file__).resolve().parents[1] / "docs" / "data" / "pulse.json"
    assert p.exists(), "docs/data/pulse.json missing — run build_pulse first"
    raw = p.read_text()
    assert "NaN" not in raw, "bare NaN literal in site JSON"
    d = json.loads(raw)
    assert "segments" in d and len(d["segments"]) == 10
    total = sum(len(s["indicators"]) for s in d["segments"])
    assert total == 42, total
    # Multi-series indicators carry their group column and subseries labels.
    for s in d["segments"]:
        for ind in s["indicators"]:
            if ind.get("subseries"):
                gcol = ind["group_by"]
                assert ind["series"], f"{ind['key']} has subseries but no data"
                assert all(gcol in r for r in ind["series"]), ind["key"]
    print("ok test_site_json_parses_strictly")


if __name__ == "__main__":
    test_sanitize_nan_inf()
    test_strict_json_rejects_nan()
    test_segments_complete()
    test_site_json_parses_strictly()
    print("all pulse tests pass")
