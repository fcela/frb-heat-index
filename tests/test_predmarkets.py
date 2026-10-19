"""Regression tests for the prediction-markets build.

Run: python3 tests/test_predmarkets.py (no pytest needed).

All network paths are exercised through the fixture transport backed by
tests/fixtures/predmarkets/ — real trimmed Kalshi API payloads — so the
tests make zero live calls and stay deterministic.
"""

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.predmarkets import kalshi
from src.predmarkets import distribution as distmod
from src.predmarkets import fred as fredmod
from src.predmarkets import realized as realized_mod
from src.predmarkets.build_predmarkets import (
    build,
    build_distribution_track_record,
    build_distribution_views,
    build_fomc_fan,
    build_fomc_track_record,
    build_ipo_expected,
    build_tail_risks,
    chart_events,
    event_close_time,
    event_horizon,
    front_gap_note,
    merge_incremental,
    open_events,
    sequential_colors,
    strike_label,
    strike_rank,
    validate_frame,
)
from src.predmarkets.indicators import (
    FOMC_BUCKET_DELTAS,
    INDICATORS,
    PAPER_DOI,
    SPEC_VERIFIED,
    kalshi_event_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "predmarkets"


def fixture_transport(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if parsed.path.endswith("/events"):
        series = qs["series_ticker"][0]
        return (FIXTURES / f"events_{series}.json").read_text()
    if parsed.path.endswith("/markets/candlesticks"):
        tickers = qs["market_tickers"][0].split(",")
        bank = json.loads((FIXTURES / "candles_by_ticker.json").read_text())
        payload = {"markets": [{"market_ticker": t, "candlesticks": bank.get(t, [])}
                               for t in tickers]}
        return json.dumps(payload)
    if parsed.path == "/graph/fredgraph.csv":
        sid = qs["id"][0]
        return (FIXTURES / "fred" / f"{sid}.csv").read_text()
    raise AssertionError(f"unexpected URL in fixture transport: {url}")


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def market_of(event, ticker):
    return next(m for m in event["markets"] if m["ticker"] == ticker)


# --------------------------------------------------------------- unit tests

def test_candle_close_extraction():
    # current API shape
    assert kalshi.candle_close({"price": {"close_dollars": "0.4200"}}) == 0.42
    # no-trade day: exchange carries the previous close only
    assert kalshi.candle_close({"price": {"previous_dollars": "0.4200"}}) == 0.42
    # old API shape (pre-_dollars keys)
    assert kalshi.candle_close({"price": {"close": "0.50"}}) == 0.50
    # priority: close beats previous; nothing -> None
    assert kalshi.candle_close(
        {"price": {"close_dollars": "0.1000", "previous_dollars": "0.9000"}}) == 0.10
    assert kalshi.candle_close({"price": {}}) is None
    assert kalshi.candle_close({}) is None
    assert kalshi.candle_volume({"volume_fp": "11.00"}) == 11.0
    assert kalshi.candle_volume({}) == 0.0
    print("ok test_candle_close_extraction")


def test_candle_trading_date():
    # daily candles close at midnight ET; the covered day is the one before
    ts = int(dt.datetime(2025, 7, 15, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    assert kalshi.candle_trading_date(ts) == dt.date(2025, 7, 14)
    # winter (EST): close at 05:00 UTC
    ts = int(dt.datetime(2026, 12, 2, 5, 0, tzinfo=dt.timezone.utc).timestamp())
    assert kalshi.candle_trading_date(ts) == dt.date(2026, 12, 1)
    print("ok test_candle_trading_date")


def test_open_and_front_events():
    events = load_fixture("events_KXRECSSNBER.json")["events"]
    evs = open_events(events)
    tickers = [e["event_ticker"] for e in evs]
    assert tickers == ["KXRECSSNBER-26", "KXRECSSNBER-27"], tickers  # settled 25 drops
    # front pick = nearest future close (computed independently here)
    now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
    reg = INDICATORS["recession"]
    chart, closed = chart_events(reg, evs, now)
    assert [e["event_ticker"] for e in chart] == tickers  # ladder charts all open
    assert not closed

    fed = load_fixture("events_KXFEDDECISION.json")["events"]
    fed_evs = open_events(fed)
    reg = INDICATORS["fomc_decision"]
    chart, closed = chart_events(reg, fed_evs, now)
    expected = min((kalshi.parse_iso_z(m["close_time"])
                    for e in fed_evs for m in e["markets"]
                    if kalshi.parse_iso_z(m["close_time"]) > now))
    assert chart[0]["event_ticker"] == "KXFEDDECISION-26OCT"
    assert not closed
    # after the Oct meeting, the front rolls to December — no hardcoding
    later = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    chart, closed = chart_events(reg, fed_evs, later)
    assert chart[0]["event_ticker"] == "KXFEDDECISION-26DEC"
    assert not closed
    print("ok test_open_and_front_events")


def test_strike_labels():
    fomd = load_fixture("events_KXFEDDECISION.json")["events"]
    oct_ev = next(e for e in fomd if e["event_ticker"] == "KXFEDDECISION-26OCT")
    reg = INDICATORS["fomc_decision"]
    labels = [strike_label(market_of(oct_ev, t), oct_ev, reg) for t in [
        "KXFEDDECISION-26OCT-C26", "KXFEDDECISION-26OCT-C25",
        "KXFEDDECISION-26OCT-H0", "KXFEDDECISION-26OCT-H25",
        "KXFEDDECISION-26OCT-H26"]]
    assert labels == ["Cut >25bps", "Cut 25bps", "Hike 0bps", "Hike 25bps",
                      "Hike >25bps"], labels
    # stance ordering comes from the registry map, not the API sort
    ranks = [strike_rank(market_of(oct_ev, t), oct_ev, reg)[0] for t in [
        "KXFEDDECISION-26OCT-H25", "KXFEDDECISION-26OCT-C26",
        "KXFEDDECISION-26OCT-H0"]]
    assert ranks == [3, 0, 2], ranks

    cpi = load_fixture("events_KXCPIYOY.json")["events"]
    nov_ev = next(e for e in cpi if e["event_ticker"] == "KXCPIYOY-26NOV")
    reg = INDICATORS["cpi_yoy"]
    # ">" (strict): the listed rules settle on "increases by more than X%",
    # so an exact X.0 print resolves the strike to No.
    assert strike_label(market_of(nov_ev, "KXCPIYOY-26NOV-T3.8"), nov_ev,
                        reg) == ">3.8%"
    u3 = load_fixture("events_KXU3.json")["events"]
    oct_ev = next(e for e in u3 if e["event_ticker"] == "KXU3-26OCT")
    reg = INDICATORS["unemployment"]
    assert strike_label(market_of(oct_ev, "KXU3-26OCT-T4.1"), oct_ev,
                        reg) == ">4.1%"
    # recession: empty subtitle -> year from the event ticker
    rec = load_fixture("events_KXRECSSNBER.json")["events"]
    rec26 = next(e for e in rec if e["event_ticker"] == "KXRECSSNBER-26")
    reg = INDICATORS["recession"]
    assert strike_label(rec26["markets"][0], rec26, reg) == "2026"
    # GDP's "Above 0.0%" subtitle normalizes into the threshold family
    gdp_market = {"subtitle": "Above 0.0%", "strike_type": "greater",
                  "ticker": "KXGDP-26OCT30-T0.0"}
    assert strike_label(gdp_market, {"event_ticker": "KXGDP-26OCT30"},
                        INDICATORS["gdp_quarterly"]) == ">0.0%"
    print("ok test_strike_labels")


def test_gap_notes():
    cpi = load_fixture("events_KXCPIYOY.json")["events"]
    nov_ev = next(e for e in cpi if e["event_ticker"] == "KXCPIYOY-26NOV")
    reg = INDICATORS["cpi_yoy"]
    now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
    note = front_gap_note(reg, nov_ev, False, now)
    assert note and "October 2026" in note and "not interpolated" in note, note
    # once the Nov print's market is near, no gap is claimed
    soon = dt.datetime(2026, 11, 25, tzinfo=dt.timezone.utc)
    assert front_gap_note(reg, nov_ev, False, soon) is None
    # monthly series with a near front (U3) has no note today
    u3 = load_fixture("events_KXU3.json")["events"]
    oct_ev = next(e for e in u3 if e["event_ticker"] == "KXU3-26OCT")
    assert front_gap_note(INDICATORS["unemployment"], oct_ev, False, now) is None
    # no open markets -> honest note, empty chart
    assert "No open markets" in front_gap_note(reg, None, False, now)
    # front just closed and nothing is listed yet
    closed_note = front_gap_note(reg, nov_ev, True, dt.datetime(
        2026, 12, 11, tzinfo=dt.timezone.utc))
    assert closed_note and "closed" in closed_note, closed_note
    print("ok test_gap_notes")


def test_event_horizons():
    rec = load_fixture("events_KXRECSSNBER.json")["events"]
    rec27 = next(e for e in rec if e["event_ticker"] == "KXRECSSNBER-27")
    reg = INDICATORS["recession"]
    # close_time mode: the annual recession markets trade into late January
    # of the following year (per the listed rules), so the marker is the
    # market's last trading day, not the covered calendar year-end.
    assert event_horizon(rec27, reg) == dt.date(2028, 1, 31)
    fed = load_fixture("events_KXFEDDECISION.json")["events"]
    oct_ev = next(e for e in fed if e["event_ticker"] == "KXFEDDECISION-26OCT")
    assert event_horizon(oct_ev, INDICATORS["fomc_decision"]) == dt.date(2026, 10, 28)
    print("ok test_event_horizons")


def test_event_close_time_ignores_settled_early_closers():
    # Regression (live bug, 2026-10-18): the production KXIPO-26 card said
    # "the most recent listed event closed 2026-08-24; no further market was
    # listed" because event_close_time took the MIN over nested markets —
    # one confirmed-IPO company had settled in August — while 27 company
    # markets still traded to Jan 1, 2027. The event trades until its LAST
    # market closes.
    ev = {"event_ticker": "KXIPO-26", "markets": [
        {"ticker": "KXIPO-26-SHEIN", "close_time": "2026-08-24T15:00:15Z"},
        {"ticker": "KXIPO-26-STRIPE", "close_time": "2027-01-01T04:59:00Z"},
    ]}
    assert event_close_time(ev) == dt.datetime(
        2027, 1, 1, 4, 59, tzinfo=dt.timezone.utc)
    now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
    chart, closed = chart_events(INDICATORS["ipo_pipeline"], [ev], now)
    assert not closed and chart[0]["event_ticker"] == "KXIPO-26"
    assert front_gap_note(INDICATORS["ipo_pipeline"], chart[0], closed,
                          now) is None
    print("ok test_event_close_time_ignores_settled_early_closers")


def test_merge_incremental():
    import polars as pl
    old = pl.DataFrame({
        "date": ["2026-09-20", "2026-09-20", "2026-09-21"],
        "value": [0.5, 0.9, 0.6],
        "event": ["E", "E", "E"],
        "ticker": ["A", "B", "A"],
        "strike": ["x", "y", "x"],
        "volume": [1.0, 1.0, 1.0],
    })
    new = pl.DataFrame({
        "date": ["2026-09-21", "2026-09-22"],
        "value": [0.7, 0.8],          # 2026-09-21/A revised; C is new
        "event": ["E", "E"],
        "ticker": ["A", "C"],
        "strike": ["x", "z"],
        "volume": [2.0, 5.0],
    })
    out = merge_incremental(old, new)
    assert out.height == 4, out.height
    a21 = out.filter((pl.col("date") == "2026-09-21") & (pl.col("ticker") == "A"))
    assert a21["value"].to_list() == [0.7]  # fresh row wins
    assert out["date"].to_list() == sorted(out["date"].to_list())
    assert merge_incremental(None, new).height == 2
    assert merge_incremental(old, new.clear()).height == 3
    print("ok test_merge_incremental")


def test_validate_bounds():
    import polars as pl
    schema = {"date": pl.Utf8, "value": pl.Float64, "event": pl.Utf8,
              "ticker": pl.Utf8, "strike": pl.Utf8, "volume": pl.Float64}
    ok = pl.DataFrame({"date": ["2026-09-20"], "value": [0.0], "event": ["E"],
                       "ticker": ["A"], "strike": [""], "volume": [1.0]},
                      schema=schema)
    validate_frame(ok, "x")
    for bad in (1.5, -0.01, float("nan")):
        df = pl.DataFrame({"date": ["2026-09-20"], "value": [bad], "event": ["E"],
                           "ticker": ["A"], "strike": [""], "volume": [1.0]},
                          schema=schema)
        try:
            validate_frame(df, "x")
        except RuntimeError:
            continue
        raise AssertionError(f"validate_frame accepted {bad}")
    print("ok test_validate_bounds")


def test_sequential_colors():
    one = sequential_colors(1)
    assert one == ["#1d4ed8"]
    ramp = sequential_colors(21)
    assert len(set(ramp)) == 21  # distinct -> lines stay distinguishable
    assert all(c.startswith("#") and len(c) == 7 for c in ramp)
    assert ramp[0] == "#1d4ed8" and ramp[-1] == "#b2182b"
    print("ok test_sequential_colors")


# ------------------------------------------------------- end-to-end (fixtures)

def test_build_end_to_end():
    now = dt.datetime.now(dt.timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        site_json = Path(tmp) / "site" / "predmarkets.json"
        meta = build(transport=fixture_transport, data_dir=data_dir,
                     site_json=site_json)
        assert meta["n_error"] == 0, meta["indicators"]
        # 4 original live + ipo_pipeline + tail_risks (derived)
        assert meta["n_live"] == 6, meta["n_live"]
        # KXFED / KXGDP have no open fixture events -> honest status, no series
        assert meta["indicators"]["fed_funds"]["status"] == "no-open-market"
        assert meta["indicators"]["gdp_quarterly"]["status"] == "no-open-market"
        assert "No open markets" in meta["indicators"]["fed_funds"]["note"]
        # Derived indicators built from fixtures.
        assert meta["indicators"]["tail_risks"]["status"] == "live"
        assert meta["indicators"]["ipo_pipeline"]["status"] == "live"
        assert "expected IPOs" in meta["indicators"]["ipo_pipeline"]["strikes"]

        site = json.loads(site_json.read_text())
        raw = site_json.read_text()
        assert "NaN" not in raw and "Infinity" not in raw
        by_key = {i["key"]: i for i in site["indicators"]}

        # FOMC: five stance-ordered strikes, series only from the front event
        fomc = by_key["fomc_decision"]
        assert fomc["event_ticker"] == "KXFEDDECISION-26OCT"
        assert fomc["strikes"] == ["Cut >25bps", "Cut 25bps", "Hike 0bps",
                                   "Hike 25bps", "Hike >25bps"]
        assert fomc["horizons"] == [{"label": "Fed decision in Oct 2026?",
                                     "date": "2026-10-28",
                                     "event_ticker": "KXFEDDECISION-26OCT"}]
        series_tickers = {r["strike"] for r in fomc["series"]}
        assert series_tickers == set(fomc["strikes"])
        assert all(0 <= r["value"] <= 1 for r in fomc["series"])
        # archive: the 26DEC (non-chart) markets stay in the CSV
        csv_text = (data_dir / "fomc_decision.csv").read_text()
        assert "KXFEDDECISION-26DEC-H0" in csv_text
        # carried/legacy candles survived: 5 candles for H0 incl. volume-0 day
        assert csv_text.count("KXFEDDECISION-26OCT-H0,") == 5

        # CPI front = nearest future close among fixtures (independent oracle)
        cpi = by_key["cpi_yoy"]
        cpi_evs = open_events(load_fixture("events_KXCPIYOY.json")["events"])
        closes = [min(kalshi.parse_iso_z(m["close_time"]) for m in e["markets"])
                  for e in cpi_evs]
        future = [e for e, c in zip(cpi_evs, closes) if c > now]
        expected_front = future[0] if future else cpi_evs[-1]
        assert cpi["event_ticker"] == expected_front["event_ticker"]
        # ">X%" strict-inequality labels match the listed settlement rules
        assert ">3.0%" in cpi["strikes"] and ">5.0%" in cpi["strikes"]

        # recession ladder: both open years charted; horizons are each
        # market's actual last trading day (late January of the following
        # year, per the listed rules — not the covered calendar year)
        rec = by_key["recession"]
        assert rec["strikes"] == ["2026", "2027"]
        rec_evs = [e for e in load_fixture("events_KXRECSSNBER.json")["events"]
                   if e.get("event_ticker") in ("KXRECSSNBER-26", "KXRECSSNBER-27")]
        exp_dates = [str(min(kalshi.parse_iso_z(m["close_time"])
                              for m in e["markets"]).date()) for e in rec_evs]
        assert [h["date"] for h in rec["horizons"]] == exp_dates
        assert set(rec["series"][0]) == {"date", "strike", "value"}

        # incremental rerun is idempotent: same rows, no duplicates
        heights = {p.name: p.read_text().splitlines() for p in
                   sorted(data_dir.glob("*.csv"))}
        build(incremental=True, transport=fixture_transport, data_dir=data_dir,
              site_json=site_json)
        for name, lines in heights.items():
            again = (data_dir / name).read_text().splitlines()
            assert len(again) == len(lines), (name, len(lines), len(again))
        print("ok test_build_end_to_end")


def test_distribution_recovery():
    # Clean ladder: bins by differencing, sums to 1.
    bins = distmod.recover_threshold_bins(
        [3.5, 3.75, 4.0, 4.25], [0.95, 0.80, 0.30, 0.05])
    assert abs(sum(b["mass"] for b in bins) - 1.0) < 1e-9
    by = {(b["low"], b["high"]): b["mass"] for b in bins}
    assert abs(by[(3.75, 4.0)] - 0.50) < 1e-9
    assert abs(by[(4.25, None)] - 0.05) < 1e-9
    assert abs(by[(None, 3.5)] - 0.05) < 1e-9
    # Messy ladder (non-monotone from a stale tail quote): no negative mass,
    # still sums to 1, mode-outward monotonicity holds.
    bins2 = distmod.recover_threshold_bins([3.5, 3.75, 4.0], [0.90, 0.95, 0.30])
    assert all(b["mass"] >= 0 for b in bins2)
    assert abs(sum(b["mass"] for b in bins2) - 1.0) < 1e-9
    masses = [b["mass"] for b in bins2]
    mode = masses.index(max(masses))
    for i in range(mode - 1, -1, -1):
        assert masses[i] <= masses[i + 1] + 1e-9
    for i in range(mode + 1, len(masses)):
        assert masses[i] <= masses[i - 1] + 1e-9
    # Quantiles land inside the ladder; moments are finite.
    qs = distmod.distribution_quantiles(bins, [0.05, 0.5, 0.95])
    assert 3.5 <= qs[0.05] <= qs[0.5] <= qs[0.95] <= 4.25
    mo = distmod.distribution_moments(bins)
    assert mo["mean"] is not None and mo["variance"] >= 0
    # Mutually exclusive buckets normalize.
    assert abs(sum(distmod.recover_exclusive_buckets([0.6, 0.3, 0.2])) - 1.0) < 1e-9
    # Grid allocation conserves mass.
    grid = [3.0 + 0.25 * i for i in range(7)]
    cells = distmod.allocate_to_grid(bins, grid)
    assert abs(sum(cells) - 1.0) < 1e-6
    print("ok test_distribution_recovery")


def test_tail_risks_and_track_record():
    # Synthetic frames: one date, one event each.
    cpi = pl.DataFrame({
        "date": ["2026-10-01"] * 4, "value": [0.99, 0.90, 0.40, 0.05],
        "event": ["KXCPIYOY-26NOV"] * 4, "ticker": ["t"] * 4,
        "strike": ["2.9", "3.1", "4.1", "5.1"], "volume": [1.0] * 4,
    })
    gdp = pl.DataFrame({
        "date": ["2026-10-01"] * 3, "value": [0.95, 0.60, 0.10],
        "event": ["KXGDP-26OCT30"] * 3, "ticker": ["t"] * 3,
        "strike": ["0.5%", "1.5%", "3.0%"], "volume": [1.0] * 3,
    })
    tail = build_tail_risks({"cpi_yoy": cpi, "gdp_quarterly": gdp}, {})
    got = {r["strike"]: r["value"] for r in tail.iter_rows(named=True)}
    # P(CPI>3) = mass at/above 3.1 ≈ 0.90-ish; P(GDP<1.5) ≈ 1-0.60 = 0.40-ish.
    assert got["CPI > 3%"] > 0.8, got
    assert got["CPI > 4%"] < 0.5, got
    assert abs(got["GDP < 1.5%"] - 0.4) < 0.15, got
    # Track record on a synthetic meeting.
    fomc = pl.DataFrame({
        "date": ["2026-09-15", "2026-09-15"],
        "value": [0.87, 0.10],
        "event": ["KXFEDDECISION-26SEP"] * 2,
        "ticker": ["a", "b"],
        "strike": ["Hike 25bps", "Hike 0bps"],
        "volume": [1.0, 1.0],
    })
    rec = build_fomc_track_record(fomc)
    assert len(rec) == 1 and rec[0]["hit"] is True
    assert rec[0]["mode_bucket"] == "Hike 25bps"
    # IPO expected count sums company probabilities.
    ipo = pl.DataFrame({
        "date": ["2026-10-01"] * 3, "value": [0.7, 0.2, 0.05],
        "event": ["KXIPO-26"] * 3,
        "ticker": ["KXIPO-26-A", "KXIPO-26-B", "KXIPO-26-C"],
        "strike": ["", "", ""], "volume": [1.0] * 3,
    })
    exp = build_ipo_expected(ipo, {})
    assert abs(exp.row(0, named=True)["value"] - 0.95) < 1e-9
    print("ok test_tail_risks_and_track_record")


def test_paper_citations_present():
    for key in ("fomc_decision", "fed_funds", "cpi_yoy", "gdp_quarterly",
                "unemployment", "recession"):
        assert INDICATORS[key].get("paper"), key
    assert PAPER_DOI.startswith("https://doi.org/")
    print("ok test_paper_citations_present")


# ------------------------------------------- definitions, links, realized

def test_measures_and_kalshi_urls():
    # Every indicator states exactly what it measures: quantity, reference
    # period, source release, resolution semantics.
    for key, reg in INDICATORS.items():
        m = reg.get("measures")
        assert m, key
        for field in ("quantity", "reference", "source_release", "resolution"):
            assert m.get(field), (key, field)
    # The spec research carries its verification date.
    dt.date.fromisoformat(SPEC_VERIFIED)
    # URL pattern (verified 2026-10-19 via archived captures — see
    # indicators.py): event pages are lowercase event tickers under /markets/.
    assert kalshi_event_url("KXCPIYOY-26NOV") == \
        "https://kalshi.com/markets/kxcpiyoy-26nov"
    assert kalshi_event_url("KXRECSSNBER-26") == \
        "https://kalshi.com/markets/kxrecssnber-26"
    # Decision buckets map to target-range upper-bound changes; ">25bps"
    # buckets take the smallest move in their family (documented choice).
    assert set(FOMC_BUCKET_DELTAS) == {"Cut >25bps", "Cut 25bps", "Hike 0bps",
                                       "Hike 25bps", "Hike >25bps"}
    assert FOMC_BUCKET_DELTAS["Hike 25bps"] == 0.25
    assert FOMC_BUCKET_DELTAS["Cut >25bps"] == -0.50
    print("ok test_measures_and_kalshi_urls")


def test_parse_level_strict_labels():
    # Regression: legacy "≥X%" archive strikes and new ">X%" strikes both
    # parse; stance labels still do not.
    assert distmod.parse_level(">4.25%") == 4.25
    assert distmod.parse_level("≥4.25%") == 4.25
    assert distmod.parse_level("4.25%") == 4.25
    assert distmod.parse_level("3.1") == 3.1
    assert distmod.parse_level("Hike 25bps") is None
    print("ok test_parse_level_strict_labels")


def test_fred_series_math():
    # CPI YoY from a 13-month synthetic index: 300.0 -> 309.6 is +3.2%,
    # one decimal, matching the rate the contracts settle on.
    idx = pl.DataFrame({
        "date": [dt.date(2025, m, 1).isoformat() for m in range(1, 13)]
                + ["2026-01-01"],
        "value": [300.0] * 12 + [309.6],
    })
    yoy = fredmod.cpi_yoy_from_index(idx)
    assert yoy.height == 1
    assert yoy.row(0, named=True) == {"date": "2026-01", "value": 3.2}
    # Fixture FRED feeds parse; YoY matches the series' own math.
    cpi = fredmod.parse_fred_csv(
        (FIXTURES / "fred" / "CPIAUCSL.csv").read_text(), "CPIAUCSL")
    fyoy = fredmod.cpi_yoy_from_index(cpi)
    assert fyoy.height == 9 and all(3.15 < v < 3.25
                                    for v in fyoy["value"].to_list())
    try:
        fredmod.parse_fred_csv("garbage,columns\n", "CPIAUCSL")
        raise AssertionError("malformed FRED body accepted")
    except RuntimeError:
        pass
    # GDP release day -> priced quarter for the four verified KXGDP
    # release days (28-31 days after quarter end).
    assert fredmod.quarter_of_release(dt.date(2026, 10, 30)) == "2026Q3"
    assert fredmod.quarter_of_release(dt.date(2027, 1, 28)) == "2026Q4"
    assert fredmod.quarter_of_release(dt.date(2027, 4, 29)) == "2027Q1"
    assert fredmod.quarter_of_release(dt.date(2027, 7, 29)) == "2027Q2"
    assert fredmod.quarter_of_release(dt.date(2026, 6, 15)) is None
    # Fed funds around a meeting: hike, hold, future, missing feed.
    ffr = pl.DataFrame({"date": ["2026-09-14", "2026-09-15", "2026-09-16",
                                "2026-09-17"],
                       "value": [4.50, 4.50, 4.75, 4.75]})
    assert fredmod.fed_funds_around(ffr, dt.date(2026, 9, 16)) == (4.50, 4.75)
    hold = pl.DataFrame({"date": ["2026-09-14", "2026-09-15"],
                         "value": [4.50, 4.50]})
    assert fredmod.fed_funds_around(hold, dt.date(2026, 9, 15)) == (4.50, 4.50)
    assert fredmod.fed_funds_around(hold, dt.date(2026, 9, 16)) == (4.50, None)
    assert fredmod.fed_funds_around(pl.DataFrame(),
                                    dt.date(2026, 9, 16)) == (None, None)
    # Exchange recession rule: two consecutive negative quarters, adjacency
    # enforced by date so a data gap never fakes a pair.
    pair = pl.DataFrame({"date": ["2026-01-01", "2026-04-01"],
                         "value": [-0.4, -0.6]})
    rec = fredmod.technical_recession_quarters(pair)
    assert [r["date"] for r in rec.iter_rows(named=True)
            if r["rec_flag"]] == ["2026Q2"]
    gappy = pl.DataFrame({"date": ["2026-01-01", "2026-07-01"],
                           "value": [-0.4, -0.6]})
    assert fredmod.technical_recession_quarters(gappy).height == 0
    print("ok test_fred_series_math")


def test_realized_row_builders():
    # CPI: event tickers map to periods; unpublished prints stay null.
    yoy = pl.DataFrame({"date": ["2026-05", "2026-09"],
                        "value": [3.1, 3.2]})
    cpi_frame = pl.DataFrame({"event": ["KXCPIYOY-26MAY", "KXCPIYOY-26NOV"]})
    rows = {r["event"]: r for r in realized_mod.cpi_realized_rows(cpi_frame, yoy)}
    assert rows["KXCPIYOY-26MAY"]["value"] == 3.1
    assert rows["KXCPIYOY-26MAY"]["period"] == "2026-05"
    assert rows["KXCPIYOY-26MAY"]["kind"] == "print"
    assert rows["KXCPIYOY-26NOV"]["value"] is None
    # Unemployment: same shape from the UNRATE print.
    unrate = pl.DataFrame({"date": ["2026-05-01", "2026-09-01"],
                           "value": [4.2, 4.3]})
    u3_frame = pl.DataFrame({"event": ["KXU3-26MAY", "KXU3-26NOV"]})
    rows = {r["event"]: r for r in realized_mod.u3_realized_rows(u3_frame, unrate)}
    assert rows["KXU3-26MAY"]["value"] == 4.2
    assert rows["KXU3-26NOV"]["value"] is None
    # GDP: release day in the ticker prices the prior quarter; a non-release
    # ticker shape yields no row (withheld, not guessed).
    gdp_q = pl.DataFrame({"date": ["2026Q3", "2026Q4"],
                           "value": [2.6, 2.4]})
    gdp_frame = pl.DataFrame({"event": ["KXGDP-26OCT30", "KXGDP-27JAN28",
                                        "KXGDP-26JUN15"]})
    rows = {r["event"]: r for r in realized_mod.gdp_realized_rows(gdp_frame, gdp_q)}
    assert rows["KXGDP-26OCT30"]["value"] == 2.6
    assert rows["KXGDP-26OCT30"]["period"] == "2026Q3"
    assert rows["KXGDP-27JAN28"]["value"] == 2.4
    assert "KXGDP-26JUN15" not in rows
    assert "ADVANCE" in rows["KXGDP-26OCT30"]["note"]
    # FOMC: resolved meeting -> post-meeting upper bound; future meeting ->
    # null with the prevailing base recorded.
    ffr = pl.DataFrame({"date": ["2026-09-14", "2026-09-15", "2026-09-16",
                                "2026-10-01"],
                        "value": [4.50, 4.50, 4.75, 4.75]})
    frame = pl.DataFrame({"event": ["KXFEDDECISION-26SEP",
                                    "KXFEDDECISION-26OCT"]})
    ct = {"KXFEDDECISION-26SEP": dt.datetime(2026, 9, 16, 17, 59,
                                             tzinfo=dt.timezone.utc),
          "KXFEDDECISION-26OCT": dt.datetime(2026, 10, 28, 17, 59,
                                             tzinfo=dt.timezone.utc)}
    rows = {r["event"]: r for r in realized_mod.fomc_realized_rows(
        frame, ct, ffr, "fomc_decision")}
    assert rows["KXFEDDECISION-26SEP"]["value"] == 4.75
    assert "pre-meeting base 4.50%" in rows["KXFEDDECISION-26SEP"]["note"]
    assert rows["KXFEDDECISION-26OCT"]["value"] is None
    assert "not yet held" in rows["KXFEDDECISION-26OCT"]["note"]
    # Recession: exchange rule status per open market; undocumented windows
    # are withheld.
    rec_q = pl.DataFrame({"date": ["2026Q1", "2026Q2"],
                          "value": [-0.4, -0.6],
                          "rec_flag": [False, True]})
    rec_frame = pl.DataFrame({"event": ["KXRECSSNBER-26", "KXRECSSNBER-28"]})
    rows = {r["event"]: r for r in realized_mod.recession_realized_rows(
        rec_frame, rec_q)}
    assert rows["KXRECSSNBER-26"]["kind"] == "status"
    assert rows["KXRECSSNBER-26"]["value"] == 1.0
    assert "Occurred" in rows["KXRECSSNBER-26"]["note"]
    assert rows["KXRECSSNBER-26"]["period"] == "2026"
    assert rows["KXRECSSNBER-28"]["value"] is None
    # IPO: the exchange's own settlement counts confirmed companies.
    ipo_event = {"event_ticker": "KXIPO-26", "markets": [
        {"ticker": "KXIPO-26-BEAS", "result": None, "rules_primary": None},
        {"ticker": "KXIPO-26-CHIME", "result": "yes",
         "rules_primary": "If Chime confirms an IPO before January 1st, "
                         "2027, 12:00:00 AM ET, the market resolves Yes."},
    ]}
    assert realized_mod.company_name_of(ipo_event["markets"][1]) == "Chime"
    rows = realized_mod.ipo_realized_rows(ipo_event)
    assert len(rows) == 1 and rows[0]["value"] == 1.0
    assert rows[0]["kind"] == "status" and rows[0]["period"] == "2026"
    assert "Chime" in rows[0]["note"] and "2 named companies" in rows[0]["note"]
    print("ok test_realized_row_builders")


def test_merge_realized_freeze():
    def row(ind, period, event, kind, value, note="n"):
        return {"indicator": ind, "period": period, "event": event,
                "kind": kind, "value": value, "source": "s", "note": note}

    d1 = [row("cpi_yoy", "2026-05", "KXCPIYOY-26MAY", "print", 3.1),
          row("fomc_decision", "2026-10-28", "KXFEDDECISION-26OCT", "print",
              None),
          row("ipo_pipeline", "2026", "KXIPO-26", "status", 1.0)]
    df1 = realized_mod.merge_realized(
        None, d1, dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc))
    assert df1.height == 3
    # Later run: a GDP-style revision arrives (3.3), the meeting resolves
    # (4.75), the IPO count grows (2.0).
    now = dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc)
    d2 = [row("cpi_yoy", "2026-05", "KXCPIYOY-26MAY", "print", 3.3, "revised"),
          row("fomc_decision", "2026-10-28", "KXFEDDECISION-26OCT", "print",
              4.75),
          row("ipo_pipeline", "2026", "KXIPO-26", "status", 2.0)]
    df2 = realized_mod.merge_realized(df1, d2, now)
    look = {(r["indicator"], r["event"]): r for r in df2.iter_rows(named=True)}
    # Prints are FROZEN at first observation — the revision is not spliced.
    assert look[("cpi_yoy", "KXCPIYOY-26MAY")]["value"] == 3.1
    # A null placeholder never freezes; the first observed print wins.
    assert look[("fomc_decision", "KXFEDDECISION-26OCT")]["value"] == 4.75
    # Statuses always refresh.
    assert look[("ipo_pipeline", "KXIPO-26")]["value"] == 2.0
    # Provenance: first_seen survives, last_checked refreshes.
    assert look[("cpi_yoy", "KXCPIYOY-26MAY")]["first_seen"] == "2026-10-20"
    assert look[("cpi_yoy", "KXCPIYOY-26MAY")]["last_checked"] == "2026-10-29"
    # Rows absent from a later run are retained (archived events keep prints).
    df3 = realized_mod.merge_realized(df2, [], now)
    assert df3.height == 3
    print("ok test_merge_realized_freeze")


def test_fomc_fan_and_distribution_track():
    now = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)
    # Resolved Sep meeting (hike 25bps) + open Oct meeting, day-before and
    # current ladders over a 4.50->4.75 target-range path.
    fomc = pl.DataFrame({
        "date": ["2026-09-15", "2026-09-15", "2026-09-15",
                 "2026-10-16", "2026-10-16", "2026-10-16"],
        "value": [0.05, 0.90, 0.05, 0.10, 0.85, 0.05],
        "event": ["KXFEDDECISION-26SEP"] * 3 + ["KXFEDDECISION-26OCT"] * 3,
        "ticker": ["a", "b", "c", "d", "e", "f"],
        "strike": ["Cut 25bps", "Hike 25bps", "Hike 0bps",
                   "Cut 25bps", "Hike 0bps", "Hike 25bps"],
        "volume": [1.0] * 6,
    })
    ct = {"KXFEDDECISION-26SEP": dt.datetime(2026, 9, 16, 17, 59,
                                             tzinfo=dt.timezone.utc),
          "KXFEDDECISION-26OCT": dt.datetime(2026, 10, 28, 17, 59,
                                             tzinfo=dt.timezone.utc)}
    ffr = pl.DataFrame({"date": ["2026-09-14", "2026-09-15", "2026-09-16",
                                "2026-10-16"],
                        "value": [4.50, 4.50, 4.75, 4.75]})
    fan = build_fomc_fan(fomc, "KXFEDDECISION-26OCT", ct, ffr, now)
    # Front meeting: per-meeting base from the daily series (4.75 after the
    # September hike), never a single hardcoded base.
    assert fan["base_rate"] == 4.75
    assert fan["meeting"] == "2026-10-28"
    assert "UPPER BOUND" in fan["level_note"]
    oct_row = next(r for r in fan["rows"] if r["date"] == "2026-10-16")
    assert oct_row["q50"] == 4.75   # H0 at 85% -> median stays at the base
    assert oct_row["q05"] == 4.50 and oct_row["q95"] == 5.00
    # Resolved meeting: realized post-meeting mark with hit flags.
    sep = next(r for r in fan["realized"] if r["event"] == "KXFEDDECISION-26SEP")
    assert sep["post_meeting_upper"] == 4.75 and sep["base_rate"] == 4.50
    assert sep["hit_50"] is True and sep["hit_90"] is True
    assert sep["as_of"] == "2026-09-15"
    # With no funds-rate feed the fan is withheld, never guessed.
    none_fan = build_fomc_fan(fomc, "KXFEDDECISION-26OCT", ct,
                              pl.DataFrame(), now)
    assert none_fan["rows"] == [] and none_fan["base_rate"] is None
    assert "withheld" in none_fan["note"]

    # Distributional track record: resolved CPI event vs its FRED print.
    cpi = pl.DataFrame({
        "date": ["2026-10-14", "2026-10-14", "2026-10-14",
                 "2026-11-20", "2026-11-20"],
        "value": [0.95, 0.60, 0.10, 0.90, 0.30],
        "event": ["KXCPIYOY-26SEP"] * 3 + ["KXCPIYOY-26NOV"] * 2,
        "ticker": ["a", "b", "c", "d", "e"],
        "strike": [">3.0%", ">3.2%", ">3.4%", ">3.0%", ">3.2%"],
        "volume": [1.0] * 5,
    })
    cpi_ct = {"KXCPIYOY-26SEP": dt.datetime(2026, 10, 15, 13, 29,
                                            tzinfo=dt.timezone.utc),
              "KXCPIYOY-26NOV": dt.datetime(2026, 12, 10, 13, 29,
                                            tzinfo=dt.timezone.utc)}
    rz = realized_mod.merge_realized(None, [{
        "indicator": "cpi_yoy", "period": "2026-09",
        "event": "KXCPIYOY-26SEP", "kind": "print", "value": 3.2,
        "source": "s", "note": "n"}], now)
    tr = build_distribution_track_record("cpi_yoy", cpi, cpi_ct, rz, now)
    assert len(tr) == 1   # the November event is unresolved -> excluded
    got = tr[0]
    assert got["event"] == "KXCPIYOY-26SEP" and got["realized"] == 3.2
    assert got["period"] == "2026-09" and got["as_of"] == "2026-10-14"
    assert got["hit_50"] is True and got["hit_90"] is True
    # Missing realized print -> honest null, no hit claimed.
    tr2 = build_distribution_track_record("cpi_yoy", cpi, cpi_ct,
                                           pl.DataFrame(), now)
    assert tr2[0]["realized"] is None and tr2[0]["hit_50"] is False
    assert "not yet available" in tr2[0]["note"]
    print("ok test_fomc_fan_and_distribution_track")


if __name__ == "__main__":
    test_candle_close_extraction()
    test_candle_trading_date()
    test_open_and_front_events()
    test_strike_labels()
    test_gap_notes()
    test_event_horizons()
    test_merge_incremental()
    test_validate_bounds()
    test_sequential_colors()
    test_distribution_recovery()
    test_tail_risks_and_track_record()
    test_paper_citations_present()
    test_build_end_to_end()
    print("all predmarkets tests pass")
