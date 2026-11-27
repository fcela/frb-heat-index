"""Spain heat index: panel integrity, transform correctness, anchors.

Reads the committed build outputs (docs/data/spain.json and
data/spain/panel_quarterly.csv); the percentile fixture exercises the
build's own transform function directly.
"""
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.spain.build_spain import empirical_cdf, qidx  # noqa: E402

BUNDLE = json.loads((REPO / "docs/data/spain.json").read_text())


def _panel():
    rows = list(csv.DictReader(
        (REPO / "data/spain/panel_quarterly.csv").open()))
    return rows


def test_percentile_fixture():
    # midrank ECDF: ties share the middle of their rank block
    out = empirical_cdf([1.0, 2.0, 2.0, 4.0, None])
    assert out[0] == 0.125
    assert out[1] == 0.5 and out[2] == 0.5
    assert out[3] == 0.875
    assert out[4] is None
    # monotone: a rising series gets rising percentiles
    out = empirical_cdf([float(i) for i in range(10)])
    assert all(b > a for a, b in zip(out, out[1:]))
    assert out[-1] == 0.95


def test_quarters_contiguous_from_1999q1():
    qs = BUNDLE["quarters"]
    assert qs[0] == "1999Q1"
    idx = [qidx(q) for q in qs]
    assert idx == list(range(idx[0], idx[0] + len(idx)))


def test_headline_is_mean_of_components():
    comp = BUNDLE["components"]
    for i, v in enumerate(BUNDLE["headline"]):
        live = [c[i] for c in comp.values() if c[i] is not None]
        expect = sum(live) / len(live) if live else None
        if expect is None:
            assert v is None
        else:
            assert abs(v - expect) < 1e-9


def test_components_are_mean_of_indicator_percentiles():
    meta = json.loads((REPO / "docs/data/spain_meta.json").read_text())
    by_comp = {}
    for ind in meta["indicators"]:
        by_comp.setdefault(ind["component"], []).append(ind["id"])
    for comp, ids in by_comp.items():
        rows = [BUNDLE["indicators"][i]["percentile"] for i in ids
                if BUNDLE["indicators"][i]["percentile"] is not None]
        if not rows:
            continue
        for i, v in enumerate(BUNDLE["components"][comp]):
            live = [r[i] for r in rows if r[i] is not None]
            expect = sum(live) / len(live) if live else None
            if expect is None:
                assert v is None
            else:
                assert abs(v - expect) < 1e-9


def test_no_filled_gaps():
    rows = _panel()
    by_q = {r["quarter"]: r for r in rows}
    # INE IPV starts 2007Q1: every earlier quarter must be missing,
    # never back-filled, and 2007Q1 itself must be present.
    for q, r in by_q.items():
        if q < "2007Q1":
            assert r["ipv_yoy"] == "", f"ipv_yoy filled at {q}"
    assert by_q["2007Q1"]["ipv_yoy"] != ""
    # BIS DSR is published from 1999Q1: present at the panel start.
    assert by_q["1999Q1"]["dsr"] != ""


def test_anchor_headline_peaks_at_boom_top():
    # The headline is a vulnerability gauge, like the U.S. index it
    # copies (which also peaks before its crisis, in 2006-07): Spain's
    # credit, leverage, funding-gap and saving gauges all crest at the
    # 2006-08 boom top. The 2010-13 window is when that vulnerability
    # realised, through the sovereign loop — the next test pins that
    # the crisis-channel components peak there instead.
    qs = BUNDLE["quarters"]
    head = BUNDLE["headline"]
    peak_q = qs[max((i for i, v in enumerate(head) if v is not None),
                    key=lambda i: head[i])]
    assert "2006Q1" <= peak_q <= "2009Q4", peak_q


def test_anchor_crisis_components_peak_2010_2013():
    qs = BUNDLE["quarters"]
    for comp in ("Sovereign–bank nexus", "Short-term wholesale funding"):
        s = BUNDLE["components"][comp]
        peak_q = qs[max((i for i, v in enumerate(s) if v is not None),
                        key=lambda i: s[i])]
        assert "2010Q1" <= peak_q <= "2013Q4", (comp, peak_q)


def test_anchor_bono_bund_spread_2012():
    rows = _panel()
    vals = {r["quarter"]: float(r["bono_bund_spread"])
            for r in rows if r["bono_bund_spread"] != ""}
    peak_q = max(vals, key=vals.get)
    assert peak_q.startswith("2012"), peak_q
    # Quarterly mean of monthly-average yields: the July 2012 monthly
    # print is 555 bp; daily closes ran higher (~600+). The band is
    # set for the series as sourced, not for the daily figure.
    assert 450.0 <= vals[peak_q] <= 650.0, vals[peak_q]


def test_anchor_loans_deposits_peak():
    rows = _panel()
    vals = {r["quarter"]: float(r["loans_deposits"])
            for r in rows if r["loans_deposits"] != ""}
    peak_q = max(vals, key=vals.get)
    assert "2006Q1" <= peak_q <= "2008Q4", peak_q
    assert vals[peak_q] > 130.0, vals[peak_q]
