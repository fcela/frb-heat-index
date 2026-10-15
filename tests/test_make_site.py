"""Regression tests for src/make_site.py.

Covers the 2026-10-14 outage: the build wrote bare NaN literals into
docs/data/components.json (invalid JSON), killing every chart on the site.
The build must emit strict JSON (no NaN/Inf) even when the panel has
missing values, and meta.json must carry the fields renderMeta() reads.

Runnable with plain python3 (no pytest needed):
    python3 tests/test_make_site.py
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import make_site  # noqa: E402


def _reject(constant: str):
    raise ValueError(f"non-strict JSON literal in output: {constant}")


def _strict_loads(text: str):
    return json.loads(text, parse_constant=_reject)


def _fixture(data_dir: Path) -> None:
    """Write a small panel with missing values (empty, NaN, Inf)."""
    quarters = ["2020Q1", "2020Q2", "2020Q3"]
    comp = pl.DataFrame({
        "quarter": quarters,
        "Alpha": [0.1, float("nan"), 0.3],          # NaN in the middle
        "Beta": [None, 0.5, float("inf")],          # null + +Inf
        "Gamma": [float("-inf"), None, 0.9],       # -Inf + null
    })
    comp.write_csv(data_dir / "components.csv")
    agg = pl.DataFrame({
        "quarter": quarters,
        "Aggregate index": [0.2, float("nan"), 0.8],
    })
    agg.write_csv(data_dir / "aggregate.csv")
    (data_dir / "_meta.json").write_text(json.dumps({
        "last_built_quarter": "2020Q3",
        "built_at_utc": "2020-01-01T00:00:00Z",
        "n_indicators": 3,
        "n_components": 3,
        "note": "test fixture",
    }))


def test_sanitize_json_replaces_nonfinite():
    dirty = {"a": float("nan"), "b": [1.0, float("inf")],
             "c": {"d": float("-inf")}, "e": "ok", "f": None}
    clean = make_site.sanitize_json(dirty)
    assert clean == {"a": None, "b": [1.0, None],
                     "c": {"d": None}, "e": "ok", "f": None}


def test_dumps_strict_fails_loudly_on_missed_nan():
    # belt-and-braces: if sanitization ever misses, the build must fail,
    # not ship invalid JSON.
    try:
        json.dumps({"v": float("nan")}, allow_nan=False)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError from allow_nan=False")
    # our dumps_strict must never emit such literals
    out = make_site.dumps_strict({"v": float("nan")})
    assert "NaN" not in out and "Infinity" not in out
    assert _strict_loads(out) == {"v": None}


def test_build_emits_strict_json_with_nulls_for_missing():
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        out = Path(td) / "out"
        data.mkdir()
        _fixture(data)

        meta = make_site.build(data, out)

        for name in ("aggregate.json", "components.json", "meta.json"):
            text = (out / name).read_text()
            _strict_loads(text)  # raises on any NaN/Inf literal

        comp = json.loads((out / "components.json").read_text())
        assert comp["Alpha"] == [
            {"q": "2020Q1", "v": 0.1},
            {"q": "2020Q2", "v": None},   # NaN -> null
            {"q": "2020Q3", "v": 0.3},
        ]
        assert comp["Beta"][0]["v"] is None      # null stays null
        assert comp["Beta"][2]["v"] is None      # +Inf -> null
        assert comp["Gamma"][0]["v"] is None     # -Inf -> null

        agg = json.loads((out / "aggregate.json").read_text())
        assert agg[1] == {"q": "2020Q2", "v": None}

        # site contract: fields docs/assets/app.js renderMeta() reads
        assert meta["components"] == ["Alpha", "Beta", "Gamma"]
        assert meta["last_updated_quarter"] == "2020Q3"
        assert meta["start_quarter"] == "2020Q1"
        assert meta["n_quarters"] == 3
        assert meta["stale_sources"] == []
        # build fields kept
        assert meta["last_built_quarter"] == "2020Q3"
        assert meta["n_components"] == 3


def test_build_preserves_pipeline_staleness_flags():
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        out = Path(td) / "out"
        data.mkdir()
        _fixture(data)
        m = json.loads((data / "_meta.json").read_text())
        m["stale_sources"] = ["FRED"]
        (data / "_meta.json").write_text(json.dumps(m))

        meta = make_site.build(data, out)
        assert meta["stale_sources"] == ["FRED"]


if __name__ == "__main__":
    test_sanitize_json_replaces_nonfinite()
    test_dumps_strict_fails_loudly_on_missed_nan()
    test_build_emits_strict_json_with_nulls_for_missing()
    test_build_preserves_pipeline_staleness_flags()
    print("all make_site regression tests passed")
