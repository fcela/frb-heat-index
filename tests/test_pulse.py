"""Regression tests for the market-pulse build.

Run: python3 tests/test_pulse.py (no pytest needed).
"""

import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.pulse.build_pulse import sanitize_records, strict_json
from src.pulse.segments import SEGMENTS, FETCHABLE_STATUSES, all_indicators

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "pulse"


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


def test_provenance_standard():
    # PR #36 standard on every card: canonical source_url (working link),
    # series identifier where one exists, one-line definition. Empty only
    # where honestly none exists — never fabricated.
    n = 0
    for seg_key, ind_key, ind in all_indicators():
        for f in ("source_url", "series_id", "definition"):
            assert f in ind, f"{seg_key}/{ind_key} missing {f}"
        assert ind["definition"], f"{seg_key}/{ind_key}: no definition"
        url = ind["source_url"]
        if url:
            assert url.startswith(("http://", "https://")), \
                f"{seg_key}/{ind_key}: malformed source_url: {url}"
        # Every fetchable card links somewhere; only pending-sourcing may
        # stay URL-less (and is flagged in its note + PR).
        if ind["status"] in FETCHABLE_STATUSES:
            assert url, f"{seg_key}/{ind_key}: fetchable but no source_url"
        else:
            assert ind["status"] == "pending-sourcing"
        # FRED-backed series: canonical landing page + the ID itself.
        if "fred_id" in ind:
            fid = ind["fred_id"]
            assert ind["source_url"] == f"https://fred.stlouisfed.org/series/{fid}", \
                f"{seg_key}/{ind_key}: source_url not the canonical FRED page"
            assert fid in ind["series_id"], \
                f"{seg_key}/{ind_key}: series_id must carry the FRED ID"
        elif "fred.stlouisfed.org" in ind["source"]:
            assert ind["source_url"].startswith("https://fred.stlouisfed.org/series/"), \
                f"{seg_key}/{ind_key}: FRED-derived series should link its FRED leg"
        n += 1
    assert n == 42, n

    # Feed contract: the site bundle carries the same provenance fields.
    site = json.loads((REPO / "docs" / "data" / "pulse.json").read_text())
    served = {i["key"]: i for s in site["segments"] for i in s["indicators"]}
    assert len(served) == 42
    for seg_key, ind_key, ind in all_indicators():
        got = served[ind_key]
        assert got["source_url"] == ind["source_url"], ind_key
        assert got["series_id"] == ind["series_id"], ind_key
        assert got["definition"] == ind["definition"], ind_key
    print(f"ok test_provenance_standard ({n} cards)")


def test_app_renders_provenance(tmp_path=None):
    """Render smoke test: docs/pulse/app.js boots against the committed
    bundle under stubbed DOM + Plot (node harness, zero network) and every
    card renders the provenance block — source link, series identifier,
    definition, frequency, last observation; URL-less cards fall back to
    plain text. Skips when node is unavailable."""
    node = shutil.which("node")
    if node is None:
        print("skip test_app_renders_provenance: node not installed")
        return
    tmp = Path(tmp_path) if tmp_path is not None else Path(tempfile.mkdtemp())
    r = subprocess.run(
        [node, str(FIXTURES / "render-harness.mjs"), str(REPO), str(tmp)],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"render harness failed:\n{r.stdout}\n{r.stderr}"
    res = json.loads(r.stdout)
    assert res["errors"] == [], res["errors"]
    assert res["cards"] == 42, res
    assert res["sections"] == 10, res
    assert res["provenanceCards"] == 42, res  # every card, incl. pending-sourcing
    assert res["figures"] == 41, res          # one chart per fetchable card
    assert res["tapeItems"] == 41, res
    assert res["tocAnchors"] == 10, res
    print("ok test_app_renders_provenance")


if __name__ == "__main__":
    test_sanitize_nan_inf()
    test_strict_json_rejects_nan()
    test_segments_complete()
    test_site_json_parses_strictly()
    test_provenance_standard()
    test_app_renders_provenance()
    print("all pulse tests pass")
