"""Tests for src/indicators_meta.py.

Verifies:
  - 48/48 coverage (emitter covers exactly panel_complete columns)
  - Independent recomputation of all computed fields from CSVs
  - last_value_status cross-check against data/nowcast_provenance.csv
  - Schema compliance (required fields, https URLs, list series_ids)
  - Site bundle check (docs/data/indicators_meta.json exists, strict, matches emitter)
  - Grep-level smoke test of docs/assets/app.js provenance rendering strings

Runnable with plain python3 (no pytest needed):
    python3 tests/test_indicators_meta.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "src"))

from indicators_meta import SOURCES, build_indicators_meta  # noqa: E402
from make_site import dumps_strict  # noqa: E402


def _reject(constant: str):
    raise ValueError(f"non-strict JSON literal in output: {constant}")


def _strict_loads(text: str):
    return json.loads(text, parse_constant=_reject)


REQUIRED_FIELDS = {
    "title",
    "short",
    "component",
    "status",
    "source",
    "source_url",
    "series_id",
    "definition",
    "frequency",
    "note",
    "first_observation",
    "last_observation",
    "last_value",
    "last_value_status",
    "nowcast_method",
    "last_observed_quarter",
    "last_observed_value",
    "n_observations",
    "n_observed",
}


def test_coverage_48_indicators():
    data_dir = HERE / "data"
    p_comp = pl.read_csv(data_dir / "panel_complete.csv")
    cols = [c for c in p_comp.columns if c != "quarter"]
    assert len(cols) == 48, f"expected 48 columns in panel_complete, got {len(cols)}"

    assert len(SOURCES) == 48, f"expected 48 entries in SOURCES, got {len(SOURCES)}"
    assert set(SOURCES.keys()) == set(cols), "SOURCES keys do not match panel_complete columns exactly"

    meta = build_indicators_meta(data_dir)
    meta_indicators = meta["indicators"]
    assert len(meta_indicators) == 48, f"expected 48 indicators in meta, got {len(meta_indicators)}"
    meta_titles = [m["title"] for m in meta_indicators]
    assert meta_titles == cols, "indicators list order does not match panel_complete column order"


def test_independent_recomputation():
    data_dir = HERE / "data"
    p_comp = pl.read_csv(data_dir / "panel_complete.csv")
    p_q = pl.read_csv(data_dir / "panel_quarterly.csv")
    comp_qs = p_comp["quarter"].to_list()
    q_qs = p_q["quarter"].to_list()

    meta = build_indicators_meta(data_dir)
    by_title = {m["title"]: m for m in meta["indicators"]}

    for col in [c for c in p_comp.columns if c != "quarter"]:
        c_obs = [(q, v) for q, v in zip(comp_qs, p_comp[col].to_list()) if v is not None]
        assert c_obs, f"no non-null observations for {col} in panel_complete"
        expected_first = c_obs[0][0]
        expected_last_obs, expected_last_val = c_obs[-1]
        expected_n_obs = len(c_obs)

        q_obs = [(q, v) for q, v in zip(q_qs, p_q[col].to_list()) if v is not None]
        assert q_obs, f"no non-null observations for {col} in panel_quarterly"
        expected_last_q, expected_last_q_val = q_obs[-1]
        expected_n_observed = len(q_obs)

        m = by_title[col]
        assert m["first_observation"] == expected_first, f"{col} first_observation mismatch"
        assert m["last_observation"] == expected_last_obs, f"{col} last_observation mismatch"
        assert math.isclose(m["last_value"], expected_last_val, abs_tol=1e-12), f"{col} last_value mismatch"
        assert m["n_observations"] == expected_n_obs, f"{col} n_observations mismatch"

        assert m["last_observed_quarter"] == expected_last_q, f"{col} last_observed_quarter mismatch"
        assert math.isclose(m["last_observed_value"], expected_last_q_val, abs_tol=1e-12), f"{col} last_observed_value mismatch"
        assert m["n_observed"] == expected_n_observed, f"{col} n_observed mismatch"


def test_last_value_status_cross_check():
    data_dir = HERE / "data"
    p_now = pl.read_csv(data_dir / "nowcast_provenance.csv")
    nowcast_map = {}
    for row in p_now.iter_rows(named=True):
        nowcast_map[(row["indicator"], row["quarter"])] = row["value_source"]

    meta = build_indicators_meta(data_dir)
    assert meta["panel_last_quarter"] == "2026Q2"

    nowcast_count = 0
    observed_count = 0

    for m in meta["indicators"]:
        col = m["title"]
        last_obs = m["last_observation"]
        is_nowcast = (col, last_obs) in nowcast_map

        if is_nowcast:
            nowcast_count += 1
            assert m["last_value_status"] == "nowcast", f"{col} should be nowcast"
            assert m["nowcast_method"] == nowcast_map[(col, last_obs)], f"{col} nowcast_method mismatch"
            assert m["last_observed_quarter"] != last_obs, f"{col} nowcast series should have observed < last_observation"
            assert m["n_observed"] < m["n_observations"], f"{col} nowcast series should have n_observed < n_observations"
        else:
            observed_count += 1
            assert m["last_value_status"] == "observed", f"{col} should be observed"
            assert m["nowcast_method"] is None, f"{col} observed series must have null nowcast_method"
            assert m["last_observed_quarter"] == last_obs, f"{col} observed series should have last_observed_quarter == last_observation"
            assert math.isclose(m["last_value"], m["last_observed_value"], abs_tol=1e-12), f"{col} last_value should equal last_observed_value"
            assert m["n_observed"] == m["n_observations"], f"{col} observed series should have n_observed == n_observations"

    assert nowcast_count == 30, f"expected 30 nowcast indicators, got {nowcast_count}"
    assert observed_count == 18, f"expected 18 observed indicators, got {observed_count}"


def test_schema_bar():
    data_dir = HERE / "data"
    meta = build_indicators_meta(data_dir)

    assert "panel_last_quarter" in meta
    assert "generated_by" in meta
    assert "indicators" in meta
    assert isinstance(meta["indicators"], list)

    http_flagged = []
    for m in meta["indicators"]:
        assert set(m.keys()) == REQUIRED_FIELDS, f"field set mismatch for {m['title']}"

        assert isinstance(m["title"], str) and m["title"]
        assert isinstance(m["short"], str) and m["short"]
        assert isinstance(m["component"], str) and m["component"]
        assert m["status"] in {"proxy", "replicated"}, f"unexpected status for {m['title']}: {m['status']}"
        assert isinstance(m["source"], str) and m["source"]

        url = m["source_url"]
        if url is not None:
            assert isinstance(url, str)
            if url.startswith("https://"):
                pass
            elif url.startswith("http://"):
                # Shiller ie_data is the only known http URL (flagged per spec)
                http_flagged.append((m["title"], url))
            else:
                raise AssertionError(f"Invalid URL scheme for {m['title']}: {url}")

        assert isinstance(m["series_id"], list)
        for s in m["series_id"]:
            assert isinstance(s, str)

        assert isinstance(m["definition"], str) and m["definition"]
        assert isinstance(m["frequency"], str) and m["frequency"]
        assert isinstance(m["note"], str)

        assert isinstance(m["last_value"], float) and math.isfinite(m["last_value"])
        assert isinstance(m["last_observed_value"], float) and math.isfinite(m["last_observed_value"])
        assert isinstance(m["n_observations"], int) and m["n_observations"] > 0
        assert isinstance(m["n_observed"], int) and m["n_observed"] > 0

    assert len(http_flagged) == 2, f"expected 2 flagged http URLs (Shiller E/P and E/P spread), got {len(http_flagged)}"


def test_site_bundle_check():
    bundle_path = HERE / "docs" / "data" / "indicators_meta.json"
    assert bundle_path.exists(), f"missing site bundle: {bundle_path}"

    text = bundle_path.read_text()
    bundle = _strict_loads(text)  # raises on any NaN/Inf literal

    assert len(bundle["indicators"]) == 48
    assert bundle["panel_last_quarter"] == "2026Q2"

    # Must match fresh emitter run byte-for-byte
    fresh = dumps_strict(build_indicators_meta(HERE / "data"), indent=1) + "\n"
    assert text == fresh, "docs/data/indicators_meta.json does not match fresh emitter run byte-for-byte"

    data_meta_path = HERE / "data" / "indicators_meta.json"
    assert data_meta_path.exists(), f"missing data/indicators_meta.json: {data_meta_path}"
    data_text = data_meta_path.read_text()
    _strict_loads(data_text)
    assert data_text == fresh, "data/indicators_meta.json does not match fresh emitter run byte-for-byte"


def test_app_js_provenance_rendering_grep():
    """Grep-level smoke test that app.js contains provenance rendering strings.

    Labeled as such: no DOM harness exists for the frb page; this asserts
    the source link rendering and last-observation line are present.
    """
    app_js = (HERE / "docs" / "assets" / "app.js").read_text()

    # Provenance source link rendering
    assert 'href="${esc(m.source_url)}"' in app_js, "app.js missing source link rendering"

    # Series-end line rendering:
    # "Last observation: <last_observation> — <last_value> (<observed|nowcast [+ method]>); last observed <last_observed_quarter>"
    assert "Last observation:" in app_js, "app.js missing 'Last observation:' prefix"
    assert "; last observed " in app_js, "app.js missing '; last observed ' delimiter"


if __name__ == "__main__":
    test_coverage_48_indicators()
    test_independent_recomputation()
    test_last_value_status_cross_check()
    test_schema_bar()
    test_site_bundle_check()
    test_app_js_provenance_rendering_grep()
    print("all test_indicators_meta tests passed (6/6)")
