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

HERE = Path(__file__).resolve().parent.parent


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


def test_build_writes_clustered_bundle():
    """When indicators_standardized.csv exists, the build must also ship
    docs/data/clustered.json: strict JSON, every indicator present exactly
    once in the row order, and the merge tree sized n-1."""
    import math as _math
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        out = Path(td) / "out"
        data.mkdir()
        _fixture(data)
        T = 40  # enough quarters for the pairwise-complete overlap guard
        quarters = [f"2020Q{i + 1}" for i in range(T)]
        pl.DataFrame({
            "quarter": quarters,
            "Alpha": [_math.sin(0.2 * i) for i in range(T)],
            "Beta": [_math.sin(0.2 * i) + 0.1 * _math.cos(i) for i in range(T)],
            "Gamma": [_math.cos(0.25 * i) for i in range(T)],
        }).write_csv(data / "indicators_standardized.csv")

        make_site.build(data, out)

        text = (out / "clustered.json").read_text()
        _strict_loads(text)  # raises on any NaN/Inf literal
        b = json.loads(text)
        assert set(b["leaves"]) == {"Alpha", "Beta", "Gamma"}
        assert sorted(b["order"]) == [0, 1, 2], "order must be a permutation"
        assert len(b["merges"]) == 2  # n - 1 merges
        assert sorted(l for g in b["cut"] for l in g) == [0, 1, 2]
        assert isinstance(b["method"], str) and b["method"]


def test_build_computes_freshness_from_raw_panel():
    """meta.freshness = the least-recent OBSERVED series, from
    panel_quarterly.csv (never from the nowcast-extended panel)."""
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        out = Path(td) / "out"
        data.mkdir()
        _fixture(data)
        pl.DataFrame({
            "quarter": ["2020Q1", "2020Q2", "2020Q3"],
            "Alpha": [1.0, 2.0, None],     # observed through 2020Q2
            "Beta": [1.0, None, None],     # observed through 2020Q1
            "Gamma": [1.0, 2.0, 3.0],      # observed through 2020Q3
            "Delta": [None, 5.0, None],    # observed through 2020Q2
        }).write_csv(data / "panel_quarterly.csv")
        meta = make_site.build(data, out)
        f = meta["freshness"]
        assert f["observed_through"] == "2020Q1"
        assert f["least_series"] == "Beta"
        assert f["least_count"] == 1
        assert f["n_series"] == 4


def test_shipped_meta_freshness_matches_raw_panel():
    """The committed docs/data/meta.json freshness block must equal a
    recomputation from the committed data/panel_quarterly.csv."""
    raw_p = HERE / "data" / "panel_quarterly.csv"
    meta_p = HERE / "docs" / "data" / "meta.json"
    if not raw_p.exists() or not meta_p.exists():
        print("skip test_shipped_meta_freshness: data files absent")
        return
    raw = pl.read_csv(raw_p)
    qs = raw["quarter"].to_list()
    ends = {}
    for c in raw.columns:
        if c == "quarter":
            continue
        vals = raw[c].to_list()
        obs = [i for i, v in enumerate(vals) if v is not None]
        if obs:
            ends[c] = qs[max(obs)]
    least_q = min(ends.values())
    least = [c for c, q in ends.items() if q == least_q]
    f = json.loads(meta_p.read_text())["freshness"]
    assert f["observed_through"] == least_q
    assert f["least_series"] == least[0]
    assert f["least_count"] == len(least)
    assert f["n_series"] == len(ends)


def _landing_fixture_heat(**overrides):
    meta = {
        "last_built_quarter": "2026Q2",
        "nowcast_through": "2026Q3",
        "n_indicators": 48,
        "n_components": 14,
    }
    meta.update(overrides)
    return meta


def _landing_fixture_pulse(**overrides):
    meta = {
        "n_indicators": 5,
        "n_live": 4,
        "indicators": {
            "ib/a": {}, "ib/b": {}, "ficc/c": {},
            "awm/d": {}, "awm/e": {},
        },
    }
    meta.update(overrides)
    return meta


def test_landing_lines_from_metas():
    """The three spec lines derive from the metas: counts verbatim, the
    coverage end at the nowcast quarter with its qualifier only when the
    panel is extended past the last built quarter, and the segment count
    from the distinct pulse key prefixes."""
    lines = make_site.landing_spec_lines(
        _landing_fixture_heat(), _landing_fixture_pulse(), "1990Q1")
    assert lines["heat_indicators"] == \
        "48 public-proxy indicators · 14 components"
    assert lines["heat_coverage"] == "1990Q1 – 2026Q3 (2026Q3 nowcast)"
    assert lines["pulse_indicators"] == \
        "5 across 3 segments · 4 live from public sources"

    # No extension past the built quarter: no nowcast qualifier.
    lines = make_site.landing_spec_lines(
        _landing_fixture_heat(nowcast_through="2026Q2"),
        _landing_fixture_pulse(), "1990Q1")
    assert lines["heat_coverage"] == "1990Q1 – 2026Q2"
    heat = _landing_fixture_heat()
    del heat["nowcast_through"]
    lines = make_site.landing_spec_lines(
        heat, _landing_fixture_pulse(), "1990Q1")
    assert lines["heat_coverage"] == "1990Q1 – 2026Q2"


_LANDING_FIXTURE_HTML = """<main>
    <section class="card product-card">
      <h2>FRB Heat Index</h2>
      <dl class="specs">
        <dt>Indicators</dt><dd>STALE heat counts</dd>
        <dt>Frequency</dt><dd>Quarterly, refreshed by the quarterly pipeline</dd>
        <dt>Coverage</dt><dd>STALE coverage</dd>
      </dl>
    </section>
    <section class="card product-card">
      <h2>Early Warning</h2>
      <dl class="specs">
        <dt>Indicators</dt><dd>STALE pulse counts</dd>
        <dt>Coverage</dt><dd>5+ year backfills where sources allow</dd>
      </dl>
    </section>
    <section class="card product-card">
      <h2>Prediction Markets</h2>
      <dl class="specs">
        <dt>Indicators</dt><dd>8 series from Kalshi's public API</dd>
      </dl>
    </section>
</main>
"""


def test_update_landing_page_rewrites_only_spec_lines():
    """The rewrite replaces exactly the three generated spec lines, is
    byte-identical everywhere else (including the hand-written pulse
    Coverage line and the untouched third card), is idempotent, and
    fails loudly when a card or label is missing."""
    lines = {
        "heat_indicators": "48 public-proxy indicators · 14 components",
        "heat_coverage": "1990Q1 – 2026Q3 (2026Q3 nowcast)",
        "pulse_indicators": "42 across 10 segments · 41 live from public sources",
    }
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "index.html"
        page.write_text(_LANDING_FIXTURE_HTML)
        assert make_site.update_landing_page(page, lines) is True
        out = page.read_text()
        expected = (_LANDING_FIXTURE_HTML
                    .replace("STALE heat counts", lines["heat_indicators"])
                    .replace("STALE coverage", lines["heat_coverage"])
                    .replace("STALE pulse counts", lines["pulse_indicators"]))
        assert out == expected
        assert make_site.update_landing_page(page, lines) is False

        page.write_text(_LANDING_FIXTURE_HTML.replace(
            "<dt>Coverage</dt><dd>STALE coverage</dd>", ""))
        try:
            make_site.update_landing_page(page, lines)
        except ValueError:
            pass
        else:
            raise AssertionError("missing Coverage label must raise")


def test_shipped_landing_page_matches_metas():
    """The committed landing page's three spec lines must equal the lines
    derived from the committed metas -- the guard against the drift this
    generator exists to remove."""
    heat_p = HERE / "data" / "_meta.json"
    pulse_p = HERE / "data" / "pulse" / "_meta.json"
    page_p = HERE / "docs" / "index.html"
    comp_p = HERE / "data" / "components.csv"
    if not all(p.exists() for p in (heat_p, pulse_p, page_p, comp_p)):
        print("skip test_shipped_landing_page: data files absent")
        return
    lines = make_site.landing_spec_lines(
        json.loads(heat_p.read_text()), json.loads(pulse_p.read_text()),
        pl.read_csv(comp_p)["quarter"][0])
    text = page_p.read_text()
    for key, line in lines.items():
        assert line in text, f"{key}: landing page lacks {line!r}"


def _now_fixture_pm(data_dir: Path) -> None:
    """A minimal predmarkets layer: a front FOMC event whose modal
    bucket flips between the two fixture dates (so the block must read
    the last date), and a two-year recession ladder."""
    pm = data_dir / "predmarkets"
    pm.mkdir(parents=True, exist_ok=True)
    (pm / "_meta.json").write_text(json.dumps({"indicators": {
        "fomc_decision": {"horizons": [{
            "label": "Fed decision in Dec 2026?", "date": "2026-12-09",
            "event_ticker": "KXFEDDECISION-26DEC"}]},
        "recession": {"horizons": [
            {"label": "2026", "date": "2027-01-31",
             "event_ticker": "KXRECSSNBER-26"},
            {"label": "2027", "date": "2028-01-31",
             "event_ticker": "KXRECSSNBER-27"}]},
    }}))
    pl.DataFrame({
        "date": ["2026-11-19", "2026-11-19", "2026-11-20", "2026-11-20"],
        "value": [0.5, 0.5, 0.09, 0.90],
        "event": ["KXFEDDECISION-26DEC"] * 4,
        "ticker": ["KXFEDDECISION-26DEC-H0", "KXFEDDECISION-26DEC-H25"] * 2,
        "strike": ["Hike 0bps", "Hike 25bps"] * 2,
        "volume": [1.0] * 4,
    }).write_csv(pm / "fomc_decision.csv")
    pl.DataFrame({
        "date": ["2026-11-20", "2026-11-20"],
        "value": [0.02, 0.26],
        "event": ["KXRECSSNBER-26", "KXRECSSNBER-27"],
        "ticker": ["KXRECSSNBER-26", "KXRECSSNBER-27"],
        "strike": ["", "Yes"],
        "volume": [1.0, 1.0],
    }).write_csv(pm / "recession.csv")


def _now_fixture_meta(**overrides):
    meta = {
        "built_at_utc": "2026-10-22T00:00:00Z",
        "last_built_quarter": "2026Q2",
        "nowcast_through": "2026Q3",
        "n_indicators": 48,
        "freshness": {"observed_through": "2025Q4", "least_series": "X",
                      "least_count": 30, "n_series": 48},
        "nowcast": {
            "quarters": ["2026Q1", "2026Q2", "2026Q3"],
            "counts_by_quarter": {"2026Q3": {"nowcast-model": 47}},
            "unextended": [{"quarter": "2026Q3", "indicator": "Gap"}],
        },
    }
    meta.update(overrides)
    return meta


def test_now_block_from_data():
    """The 'What is happening now' block derives every number from the
    data: headline level and change, hottest/coldest components, the
    modal FOMC bucket at the front event's last close, the recession
    ladder's last year, the panel build date, and the entirely-nowcast
    caveat when fills + unextended cells cover the whole panel."""
    agg = [{"q": "2026Q2", "v": 0.369}, {"q": "2026Q3", "v": 0.3652}]
    comp = {
        "Equity markets": [{"q": "2026Q3", "v": 0.874}],
        "Business credit": [{"q": "2026Q3", "v": 0.813}],
        "Bank leverage": [{"q": "2026Q3", "v": 0.400}],
        "Consumer credit": [{"q": "2026Q3", "v": 0.050}],
        "Nonfinancial Business": [{"q": "2026Q3", "v": 0.022}],
    }
    with tempfile.TemporaryDirectory() as td:
        data = Path(td)
        _now_fixture_pm(data)
        block = make_site.now_block_html(_now_fixture_meta(), agg, comp, data)
    assert "as of November 20, 2026" in block
    assert ("The heat index stands at <strong>0.37</strong> in 2026Q3, "
            "little changed from 0.37 in 2026Q2.") in block
    assert ("Hottest components: Equity markets (0.87) and "
            "Business credit (0.81).") in block
    assert ("Coldest: Nonfinancial Business (0.02) and "
            "Consumer credit (0.05).") in block
    assert ("give the highest probability, 90%, to a 25 bp hike at the "
            "December 9 FOMC meeting.") in block
    assert "Recession odds for 2027 stand at 26%." in block
    assert "Quarterly panel built October 22, 2026." in block
    assert ("The 2026Q3 column is entirely nowcast — no indicator has "
            "an observed 2026Q3 value; observations run through "
            "2026Q2.") in block
    assert "30 of 48 series end at 2025Q4." in block

    # Partial coverage: the caveat counts the filled series instead.
    meta = _now_fixture_meta(nowcast={
        "quarters": ["2026Q3"],
        "counts_by_quarter": {"2026Q3": {"nowcast-model": 30}},
        "unextended": []})
    with tempfile.TemporaryDirectory() as td:
        data = Path(td)
        _now_fixture_pm(data)
        block = make_site.now_block_html(meta, agg, comp, data)
    assert ("The 2026Q3 column carries nowcast fills for 30 of 48 "
            "series") in block
    assert "entirely nowcast" not in block


def test_update_now_block():
    """The marker rewrite replaces exactly the block between the
    markers, is idempotent, and fails loudly when markers are gone."""
    html = ("<main>\n"
            "  <!-- NOW-BLOCK:BEGIN — generated; do not edit. -->\n"
            "  STALE\n"
            "<!-- NOW-BLOCK:END -->\n"
            "</main>\n")
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "index.html"
        page.write_text(html)
        assert make_site.update_now_block(page, "  <section>NEW</section>") is True
        out = page.read_text()
        assert out == ("<main>\n"
                       "  <!-- NOW-BLOCK:BEGIN — generated; do not edit. -->\n"
                       "  <section>NEW</section>\n"
                       "<!-- NOW-BLOCK:END -->\n"
                       "</main>\n")
        assert make_site.update_now_block(page, "  <section>NEW</section>") is False
        page.write_text("<main>no markers</main>")
        try:
            make_site.update_now_block(page, "x")
        except ValueError:
            pass
        else:
            raise AssertionError("missing NOW-BLOCK markers must raise")


def test_shipped_now_block_matches_data():
    """The committed landing page's now block must equal the block
    derived from the committed bundles and predmarkets layer."""
    meta_p = HERE / "docs" / "data" / "meta.json"
    agg_p = HERE / "docs" / "data" / "aggregate.json"
    comp_p = HERE / "docs" / "data" / "components.json"
    page_p = HERE / "docs" / "index.html"
    pm_p = HERE / "data" / "predmarkets" / "_meta.json"
    if not all(p.exists() for p in (meta_p, agg_p, comp_p, page_p, pm_p)):
        print("skip test_shipped_now_block: data files absent")
        return
    block = make_site.now_block_html(
        json.loads(meta_p.read_text()), json.loads(agg_p.read_text()),
        json.loads(comp_p.read_text()), HERE / "data")
    assert block in page_p.read_text(), "landing page now block is stale"


if __name__ == "__main__":
    test_sanitize_json_replaces_nonfinite()
    test_dumps_strict_fails_loudly_on_missed_nan()
    test_build_emits_strict_json_with_nulls_for_missing()
    test_build_preserves_pipeline_staleness_flags()
    test_build_writes_clustered_bundle()
    test_build_computes_freshness_from_raw_panel()
    test_shipped_meta_freshness_matches_raw_panel()
    test_landing_lines_from_metas()
    test_update_landing_page_rewrites_only_spec_lines()
    test_shipped_landing_page_matches_metas()
    test_now_block_from_data()
    test_update_now_block()
    test_shipped_now_block_matches_data()
    print("all make_site regression tests passed")
