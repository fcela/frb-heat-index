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
    s = BUNDLE["components"]["Short-term wholesale funding"]
    peak_q = qs[max((i for i, v in enumerate(s) if v is not None),
                    key=lambda i: s[i])]
    assert "2010Q1" <= peak_q <= "2013Q4", peak_q


def test_anchor_nexus_component_peaks_with_the_debt_stock():
    # The nexus component gained stock and burden legs in the second
    # build (public debt/GDP, regional debt/GDP, interest burden,
    # holdings over capital) and five more legs in the fourth build
    # (non-resident holdings share, Banco de España holdings share,
    # residual-maturity share, NIIP/GDP, FLA share of regional debt).
    # Its peak is still not the 2012 spread event: it crests in
    # 2016Q1, where the debt stocks and the burden jointly peak. The
    # 2012-13 window reads lower than in the second build (0.52-0.55
    # against 0.60-0.67) because two of the new legs — the BdE
    # holdings share and the FLA share — are near zero before the
    # official-sector rescues they measure, so they read cold in
    # 2012 by construction and dilute the quarter. The spread leg
    # itself still peaks in 2012 (test_anchor_bono_bund_spread_2012).
    qs = BUNDLE["quarters"]
    s = dict(zip(qs, BUNDLE["components"]["Sovereign–bank nexus"]))
    peak_q = max((q for q, v in s.items() if v is not None),
                 key=lambda q: s[q])
    assert "2014Q1" <= peak_q <= "2017Q4", peak_q
    assert s["2012Q3"] > 0.50, s["2012Q3"]
    assert s["2013Q3"] > s["2012Q3"]


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


def _vals(rows, col):
    return {r["quarter"]: float(r[col]) for r in rows if r[col] != ""}


def test_anchor_public_debt_gdp():
    vals = _vals(_panel(), "gov_debt_gdp")
    # Crosses 100% of GDP in 2014 and peaks with the COVID borrowing
    # of 2020-21, above the 2012-13 crisis levels.
    assert vals["2014Q4"] > 100.0, vals["2014Q4"]
    peak_q = max(vals, key=vals.get)
    assert "2020Q1" <= peak_q <= "2021Q4", peak_q
    assert vals[peak_q] > 115.0, vals[peak_q]


def test_anchor_regional_debt_gdp():
    vals = _vals(_panel(), "regional_debt_gdp")
    # The regional stock crested in the mid-20s percent of GDP in
    # 2015-17; the ratio's global peak is 2021Q1, where the GDP
    # denominator collapsed and the regions borrowed through COVID.
    mid = max(v for q, v in vals.items() if "2015Q1" <= q <= "2017Q4")
    assert 23.0 <= mid <= 27.0, mid
    assert 25.0 <= vals[max(vals, key=vals.get)] <= 30.0


def test_anchor_interest_burden():
    vals = _vals(_panel(), "interest_burden")
    # Crisis-window peak in 2014Q1 at about 9.2% of revenue. The
    # panel's first year reads higher still (about 10%): Spain
    # entered the euro with pre-convergence interest rates, and the
    # burden fell for a decade before the crisis raised it again.
    local = max((v, q) for q, v in vals.items()
                if "2012Q1" <= q <= "2015Q4")
    assert local[1] == "2014Q1", local
    assert 8.5 <= local[0] <= 10.0, local
    assert vals["1999Q1"] > local[0]
    assert vals["2019Q4"] < 6.5, vals["2019Q4"]


def test_anchor_tourism_covid_collapse_and_record():
    vals = _vals(_panel(), "tourism_gdp")
    # Trailing-four-quarter receipts: about 5.7% of GDP in 2019,
    # a collapse to about a quarter of that through the COVID
    # quarters, then record shares in 2023-25.
    assert 5.0 <= vals["2019Q4"] <= 6.5, vals["2019Q4"]
    assert vals["2020Q4"] < 0.4 * vals["2019Q4"], vals["2020Q4"]
    trough_q = min(vals, key=vals.get)
    assert "2020Q1" <= trough_q <= "2021Q4", trough_q
    assert vals["2024Q4"] > vals["2019Q4"], vals["2024Q4"]


def test_anchor_var_rate_share():
    vals = _vals(_panel(), "var_rate_share")
    # Near-total variable-rate share of new mortgages at the boom
    # (94% at 2006Q1); by the panel's end the fixed-rate share of
    # new lending dominates and the variable share is under 20%.
    assert vals["2006Q1"] > 85.0, vals["2006Q1"]
    last_q = max(vals)
    assert vals[last_q] < 20.0, vals[last_q]


def test_anchor_bls_standards_peak():
    vals = _vals(_panel(), "bls_standards")
    peak_q = max(vals, key=vals.get)
    assert "2008Q1" <= peak_q <= "2009Q4", peak_q
    assert vals[peak_q] >= 60.0, vals[peak_q]


def test_anchor_nexus_holdings_over_capital():
    vals = _vals(_panel(), "nexus_holdings")
    # Holdings over capital: about 145% at the panel start (pre-euro
    # sovereign-heavy portfolios against thin capital), 37% at the
    # boom top in 2007Q4, back above 75% by 2014Q4.
    assert vals["1999Q1"] > vals["2014Q4"] > 75.0
    assert vals["2007Q4"] < 50.0, vals["2007Q4"]


# ------------------------------------------------ fourth build anchors

def test_anchor_permits_proxy_peaks_at_boom_and_collapses():
    vals = _vals(_panel(), "construction_credit")
    # The activity proxy for construction credit: the permits floor-
    # area index peaks at the boom top and loses over 95% to 2013.
    peak_q = max(vals, key=vals.get)
    assert "2006Q1" <= peak_q <= "2007Q4", peak_q
    assert vals["2013Q4"] < 0.06 * vals[peak_q], vals["2013Q4"]


def test_anchor_cet1_proxy_range():
    vals = _vals(_panel(), "cet1")
    # Significant-institution CET1: a narrow 11.5-13.7 band over its
    # published history (from 2015Q2), latest above 13.
    assert min(vals) == "2015Q2"
    assert all(11.0 <= v <= 14.5 for v in vals.values())
    assert vals[max(vals)] > 13.0, vals[max(vals)]


def test_anchor_cre_prices_covid_trough():
    vals = _vals(_panel(), "cre_prices")
    # BIS commercial property prices, y/y: published index from
    # 2014Q1, so the change enters in 2015Q1; the COVID trough is
    # deeply negative and the post-2023 recovery peaks in 2025.
    assert min(vals) == "2015Q1"
    assert vals["2020Q3"] < -8.0, vals["2020Q3"]
    peak_q = max(vals, key=vals.get)
    assert peak_q.startswith("2025"), peak_q


def test_anchor_nonres_share_flight_and_return():
    vals = _vals(_panel(), "nonres_share")
    # Non-resident share of government debt: about 51% at the 2007
    # peak, a flight trough around 2012 at roughly a third, then a
    # recovery above 45% by 2019.
    assert vals["2007Q1"] > 50.0, vals["2007Q1"]
    trough = min(v for q, v in vals.items() if "2010Q1" <= q <= "2013Q4")
    assert trough < 38.0, trough
    assert vals["2019Q4"] > 45.0, vals["2019Q4"]


def test_anchor_bde_share_qe_era():
    vals = _vals(_panel(), "bde_share")
    # Banco de España holdings share: under 5% before the purchase
    # programmes, above 25% at the 2022 peak, lower at the panel end
    # as the portfolio runs off.
    assert vals["2014Q2"] < 5.0, vals["2014Q2"]
    peak_q = max(vals, key=vals.get)
    assert "2021Q1" <= peak_q <= "2023Q4", peak_q
    assert vals[peak_q] > 25.0, vals[peak_q]
    assert vals[max(vals)] < vals[peak_q]


def test_anchor_refi_short_share():
    vals = _vals(_panel(), "refi_short_share")
    # Debt with residual maturity up to one year: a fifth to a
    # quarter of the stock at the panel start, trending down to
    # the low teens — the Tesoro's maturity-lengthening, read from
    # the residual-maturity table.
    assert all(10.0 <= v <= 30.0 for v in vals.values())
    early = max(v for q, v in vals.items() if q <= "2000Q4")
    assert early > 24.0, early
    assert vals[max(vals)] < 16.0, vals[max(vals)]


def test_anchor_niip_gdp():
    vals = _vals(_panel(), "niip_gdp")
    # NIIP: -85% of GDP at the boom top, a trough below -90% around
    # the crisis (end-2009 on the fallen denominator), improving to
    # about -40% at the panel end.
    assert vals["2007Q4"] < -75.0, vals["2007Q4"]
    trough_q = min(vals, key=vals.get)
    assert "2009Q1" <= trough_q <= "2010Q4", trough_q
    assert vals[trough_q] < -90.0, vals[trough_q]
    assert vals[max(vals)] > -50.0, vals[max(vals)]


def test_anchor_fla_share():
    vals = _vals(_panel(), "fla_share")
    # State-fund share of regional debt: published as zero before
    # the funds' creation in 2012, above half by 2016, peaking
    # above 60% in 2024.
    assert vals["2011Q4"] == 0.0, vals["2011Q4"]
    assert vals["2016Q4"] > 50.0, vals["2016Q4"]
    assert max(vals.values()) > 60.0, max(vals.values())


def test_anchor_temp_share():
    vals = _vals(_panel(), "temp_share")
    # Temporary employment share: near a third of employees at the
    # boom, about a quarter in 2019, and a step down after the 2021
    # labour reform to the mid-teens at the panel end.
    assert vals["2007Q4"] > 28.0, vals["2007Q4"]
    assert 24.0 <= vals["2019Q4"] <= 28.0, vals["2019Q4"]
    assert vals[max(vals)] < 17.0, vals[max(vals)]


def test_anchor_tourism_emp_share():
    vals = _vals(_panel(), "tourism_emp_share")
    # Hospitality employment share: 6-10% throughout, with the
    # COVID dip in 2020 against 2019.
    assert all(6.0 <= v <= 10.0 for v in vals.values())
    assert vals["2020Q2"] < vals["2019Q4"]


def test_anchor_tedr_spread():
    vals = _vals(_panel(), "tedr_spread")
    # TEDR new-lending margin over the interbank rate: the TEDR
    # statistics begin in June 2010, the margin peaks with the
    # crisis repricing in 2013-14 above 300 bp, and compresses to
    # about 110 bp by 2024.
    assert min(vals) == "2010Q2"
    peak_q = max(vals, key=vals.get)
    assert "2013Q1" <= peak_q <= "2014Q4", peak_q
    assert vals[peak_q] > 300.0, vals[peak_q]
    assert vals["2024Q1"] < 150.0, vals["2024Q1"]


def test_anchor_price_income():
    vals = _vals(_panel(), "price_income")
    # Constructed price-to-income: highest at its first observation
    # (2008Q1, the boom's tail), a trough below 10 in 2013, and a
    # renewed rise after 2021 past the 2019 reading.
    peak_q = max(vals, key=vals.get)
    assert peak_q == "2008Q1", peak_q
    assert min(vals.values()) < 10.0, min(vals.values())
    assert vals[max(vals)] > vals["2019Q4"]
