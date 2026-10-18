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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.predmarkets import kalshi
from src.predmarkets.build_predmarkets import (
    build,
    chart_events,
    event_horizon,
    front_gap_note,
    merge_incremental,
    open_events,
    sequential_colors,
    strike_label,
    strike_rank,
    validate_frame,
)
from src.predmarkets.indicators import INDICATORS

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
    assert strike_label(market_of(nov_ev, "KXCPIYOY-26NOV-T3.8"), nov_ev,
                        reg) == "≥3.8%"
    u3 = load_fixture("events_KXU3.json")["events"]
    oct_ev = next(e for e in u3 if e["event_ticker"] == "KXU3-26OCT")
    reg = INDICATORS["unemployment"]
    assert strike_label(market_of(oct_ev, "KXU3-26OCT-T4.1"), oct_ev,
                        reg) == "≥4.1%"
    # recession: empty subtitle -> year from the event ticker
    rec = load_fixture("events_KXRECSSNBER.json")["events"]
    rec26 = next(e for e in rec if e["event_ticker"] == "KXRECSSNBER-26")
    reg = INDICATORS["recession"]
    assert strike_label(rec26["markets"][0], rec26, reg) == "2026"
    # GDP's "Above 0.0%" subtitle normalizes into the threshold family
    gdp_market = {"subtitle": "Above 0.0%", "strike_type": "greater",
                  "ticker": "KXGDP-26OCT30-T0.0"}
    assert strike_label(gdp_market, {"event_ticker": "KXGDP-26OCT30"},
                        INDICATORS["gdp_quarterly"]) == "≥0.0%"
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
    assert event_horizon(rec27, reg) == dt.date(2027, 12, 31)
    fed = load_fixture("events_KXFEDDECISION.json")["events"]
    oct_ev = next(e for e in fed if e["event_ticker"] == "KXFEDDECISION-26OCT")
    assert event_horizon(oct_ev, INDICATORS["fomc_decision"]) == dt.date(2026, 10, 28)
    print("ok test_event_horizons")


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
        assert meta["n_live"] == 4, meta["n_live"]
        # KXFED / KXGDP have no open fixture events -> honest status, no series
        assert meta["indicators"]["fed_funds"]["status"] == "no-open-market"
        assert meta["indicators"]["gdp_quarterly"]["status"] == "no-open-market"
        assert "No open markets" in meta["indicators"]["fed_funds"]["note"]

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
        assert "≥3.0%" in cpi["strikes"] and "≥5.0%" in cpi["strikes"]

        # recession ladder: both open years charted, year-end horizons
        rec = by_key["recession"]
        assert rec["strikes"] == ["2026", "2027"]
        assert [h["date"] for h in rec["horizons"]] == ["2026-12-31", "2027-12-31"]
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
    test_build_end_to_end()
    print("all predmarkets tests pass")
