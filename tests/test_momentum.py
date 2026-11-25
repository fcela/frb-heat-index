"""Tests for the Pulse of the Market layer (src/momentum).

Run: python3 tests/test_momentum.py

Covers: registry completeness and the provenance standard; the
fingerprint state definitions and inflection signals on fixed fixture
windows with known firings (a vol-inversion onset, a rotation
crossing, the deliberately unclassified credit band, and both sides
of the credit bars); the Yahoo completed-session filter; and the
shape/provenance of the committed site bundle and meta.
"""

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.momentum import series as registry
from src.momentum import states, yahoo

START = dt.date(2026, 1, 5)


def mk(values, start=START):
    return ([start + dt.timedelta(days=i) for i in range(len(values))],
            [float(v) for v in values])


def ramp(n, f):
    return [f(i) for i in range(n)]


# ----------------------------------------------------------------------
# registry / provenance
# ----------------------------------------------------------------------


def test_registry_completeness():
    seen = set()
    for group, key, e in registry.all_series():
        assert group in registry.GROUPS
        assert key not in seen, f"duplicate key {key}"
        seen.add(key)
        for field in ("title", "why", "source", "frequency", "definition",
                      "unit", "status", "route"):
            assert e.get(field), f"{group}/{key}: missing {field}"
        assert e["status"] in (
            "live", "live-proxy", "live-experimental", "live-manual",
            "pending-sourcing")
        if e["status"] in registry.FETCHABLE_STATUSES:
            assert e.get("source_url"), \
                f"{group}/{key}: fetchable but no source_url"
        if e["route"] == "fred":
            assert e["source_url"] == \
                f"https://fred.stlouisfed.org/series/{e['fred_id']}", \
                f"{group}/{key}: FRED source_url not canonical"
            assert e["fred_id"] in e["series_id"]
        if e["route"] == "yahoo":
            assert e.get("symbol") and "Yahoo Finance" in e["series_id"]
    # Universe invariants the states rely on.
    sector_keys = {k for g, k, e in registry.all_series() if g == "sectors"}
    assert set(registry.GRID) <= sector_keys
    assert set(registry.QUADRANT_UNIVERSE) == \
        set(registry.GRID) | {"rsp", "qqq", "sphb", "splv"}
    assert set(registry.DEFENSIVE) & set(registry.CYCLICAL) == set()
    assert set(registry.DEFENSIVE + registry.CYCLICAL) <= sector_keys
    assert len(registry.GRID) == 15
    print(f"ok registry: {len(seen)} series, provenance standard holds")


# ----------------------------------------------------------------------
# fingerprint fixtures
# ----------------------------------------------------------------------


def test_vol_inversion_onset():
    vix = mk([20.0] * 30 + [23.0] * 10)
    vxv = mk([22.0] * 30 + [21.5] * 10)
    dates, spread = states.vix_spread_series(vix, vxv)
    e = states.eval_vol_inversion((dates, spread))
    assert e["state"] == "active"
    assert e["since"] == str(dates[30]), e["since"]
    sig = states.sig_vol_curve_flip((dates, spread))
    assert sig["last_fired"] == str(dates[30])
    assert sig["detail"]["direction"] == "inversion onset"
    print("ok vol inversion: onset dated at the crossing session")


def test_vol_shock_needs_calm_base():
    calm_then_spike = mk([18.0] * 40 + [24.0, 31.0, 33.0, 30.0, 28.0])
    e = states.eval_vol_shock(calm_then_spike)
    assert e["state"] == "active", e
    chronic = mk([32.0] * 50)
    e2 = states.eval_vol_shock(chronic)
    assert e2["state"] == "inactive", \
        "a chronic high is not a shock: VIX 21 sessions ago was >= 25"
    print("ok vol shock: fires off a calm base, not off a chronic high")


def _rotation_closes(def_rate, cyc_rate):
    closes = {}
    for k in registry.DEFENSIVE:
        closes[k] = mk(ramp(100, lambda i: 100 * (1 + def_rate) ** i))
    for k in registry.CYCLICAL:
        closes[k] = mk(ramp(100, lambda i: 100 * (1 + cyc_rate) ** i))
    return closes


def test_rotation_states():
    closes = _rotation_closes(0.003, -0.003)
    dates, spread = states.def_cyc_spread_series(closes)
    e = states.eval_rotation((dates, spread))
    assert e["state"] == "risk_off", e["measured"]
    assert e["measured"]["spread_pp"] > 3.0
    closes2 = _rotation_closes(-0.003, 0.003)
    dates2, spread2 = states.def_cyc_spread_series(closes2)
    e2 = states.eval_rotation((dates2, spread2))
    assert e2["state"] == "risk_on", e2["measured"]
    flat = _rotation_closes(0.0, 0.0)
    d3, s3 = states.def_cyc_spread_series(flat)
    assert states.eval_rotation((d3, s3))["state"] == "none"
    print("ok rotation: risk-off, risk-on mirror, and flat all classify")


def _credit_frames(hy_end):
    spy = mk([100.0] * 39 + [100.0 - 8.0 * i / 21 for i in range(1, 22)])
    hy = mk([3.0] * 39 + [3.0 + (hy_end - 3.0) * i / 21
                          for i in range(1, 22)])
    return {"spy": spy}, hy


def test_credit_band_is_unclassified():
    closes, hy = _credit_frames(3.35)  # +35 bp: inside the 25-50 band
    e = states.eval_credit(closes, hy)
    assert e["state"] == "none", e
    assert abs(e["measured"]["hy_c21_bp"] - 35.0) < 1.0
    assert e["measured"]["spy_r21_pct"] <= -5.0
    closes, hy = _credit_frames(3.55)  # +55 bp: confirms
    assert states.eval_credit(closes, hy)["state"] == "confirmed"
    closes, hy = _credit_frames(3.10)  # +10 bp: equity-only
    assert states.eval_credit(closes, hy)["state"] == "divergence"
    print("ok credit: +35 bp band unclassified, +55 confirms, +10 diverges")


def test_broadening_and_haven():
    closes = {
        "spy": mk([100.0] * 60),
        "rsp": mk([100.0] * 39 + ramp(21, lambda i: 100 * 1.002 ** (i + 1))),
        "gld": mk([100.0] * 39 + ramp(21, lambda i: 100 * 1.002 ** (i + 1))),
    }
    assert states.eval_broadening(closes)["state"] == "broadening"
    falling = {"spy": mk([100.0] * 39 +
                         [100.0 - 6.0 * i / 21 for i in range(1, 22)]),
               "gld": closes["gld"]}
    dollar = mk([120.0] * 39 + [120.0 + i / 21 for i in range(1, 22)])
    assert states.eval_haven_bid(falling, dollar)["state"] == "active"
    print("ok broadening and haven bid classify on fixtures")


def test_rates_energy_snapback_conditions():
    dgs10 = mk([4.0] * 39 + [4.0 + 0.45 * i / 21 for i in range(1, 22)])
    e = states.eval_rates_shock(dgs10)
    assert e["state"] == "active" and e["measured"]["direction"] == "up"
    wti = mk([80.0] * 39 + [80.0 * (1 - 0.20 * i / 21)
                            for i in range(1, 22)])
    e = states.eval_energy_shock(wti)
    assert e["state"] == "active" and e["measured"]["direction"] == "down"
    nfci = mk([-0.50] * 17 + [-0.50 + 0.15 * i / 13 for i in range(1, 14)])
    assert states.eval_conditions_tightening(nfci)["state"] == "active"
    nfci_flat = mk([-0.50] * 17 + [-0.50 + 0.05 * i / 13
                                   for i in range(1, 14)])
    assert states.eval_conditions_tightening(nfci_flat)["state"] == "inactive"
    spy_vals = []
    v = 100.0
    for _ in range(35):
        v *= 0.994
        spy_vals.append(v)
    for _ in range(5):
        v *= 1.008
        spy_vals.append(v)
    pad = [spy_vals[0]] * (60 - len(spy_vals)) + spy_vals
    e = states.eval_snapback({"spy": mk(pad)})
    assert e["state"] == "active", e["measured"]
    print("ok rates/energy shocks, conditions tightening, snapback")


# ----------------------------------------------------------------------
# inflection fixtures
# ----------------------------------------------------------------------


def test_spread_peak():
    vals = [3.0 + 0.01 * i for i in range(80)]            # ramp to 3.79
    vals += [3.79 - 0.02 * i for i in range(1, 16)]       # tighten 0.30
    hy = mk([round(v, 10) for v in vals])
    sig = states.sig_spread_peak(hy)
    assert sig["last_fired"] is not None
    assert sig["detail"]["direction"] == "peak — credit turned"
    assert abs(sig["detail"]["extreme_level"] - 3.79) < 1e-6
    ramp_only = mk([3.0 + 0.01 * i for i in range(95)])
    assert states.sig_spread_peak(ramp_only)["last_fired"] is None
    print("ok spread peak: fires after a 63-session high tightens 0.15+")


def test_zero_cross_and_resteepening():
    vals = []
    v = 100.0
    for _ in range(40):
        v *= 1.003
        vals.append(v)
    for _ in range(30):
        v *= 0.995
        vals.append(v)
    sig = states.sig_momentum_zero_cross({"spy": mk(vals)})
    assert sig["last_fired"] is not None
    assert sig["detail"]["direction"] == "turned negative"
    curve = mk([-0.20] * 40 + [-0.20 + 0.30 * i / 21
                               for i in range(1, 22)] + [0.10] * 20)
    sig2 = states.sig_curve_resteepening(curve)
    assert sig2["last_fired"] is not None
    print("ok momentum zero-cross and curve re-steepening fire")


def test_breadth_thrust():
    closes = {}
    for k in registry.GRID:
        vals = []
        v = 100.0
        for _ in range(40):
            v *= 0.995
            vals.append(v)
        for _ in range(25):
            v *= 1.01
            vals.append(v)
        closes[k] = mk(vals)
    sig = states.sig_breadth_thrust(closes)
    assert sig["last_fired"] is not None
    assert sig["detail"]["direction"] == "thrust up"
    assert sig["detail"]["current_share"] == 1.0
    print("ok breadth thrust: grid-wide flip from <=30% to >=70% fires")


# ----------------------------------------------------------------------
# fetch filter
# ----------------------------------------------------------------------


def test_yahoo_drops_unfinished_sessions():
    today_ny = dt.datetime.now(ZoneInfo("America/New_York")).date()
    days = [today_ny - dt.timedelta(days=2), today_ny - dt.timedelta(days=1),
            today_ny, today_ny + dt.timedelta(days=1)]
    payload = {"chart": {"result": [{
        "timestamp": [int(dt.datetime(d.year, d.month, d.day,
                                     tzinfo=dt.timezone.utc).timestamp())
                      for d in days],
        "indicators": {"quote": [{"close": [10.0, 11.0, 12.0, 13.0]}]},
    }], "error": None}}

    def transport(url, timeout=60):
        return json.dumps(payload)

    df = yahoo.fetch_daily_closes("SPY", transport=transport)
    got = [str(d) for d in df["date"].to_list()]
    assert got == [str(days[0]), str(days[1])], got
    print("ok yahoo: today's partial bar and future bars are dropped")


# ----------------------------------------------------------------------
# committed bundle / meta shape
# ----------------------------------------------------------------------


def test_site_bundle_shape():
    bundle_path = REPO / "docs" / "data" / "momentum.json"
    assert bundle_path.exists(), "site bundle missing — run the build"
    data = json.loads(bundle_path.read_text())
    assert data["meta"]["cadence"] == "Weekly (Mondays)"
    assert data["meta"]["as_of"]
    assert len(data["board"]) == 10, \
        f"board has {len(data['board'])} states, expected 10"
    for e in data["board"]:
        for f in ("key", "title", "definition", "state", "active",
                  "since", "measured", "history"):
            assert f in e, f"board entry {e.get('key')}: missing {f}"
    assert len(data["inflections"]) == 6
    assert len(data["grid"]) == 15
    for g in data["grid"]:
        assert len(g["chart"]) == 64
        assert g["series_id"] and g["source_url"] and g["definition"]
    assert data["rotation"]["quadrants"] and data["rotation"]["patterns"]
    built = data["meta"]["built_at_utc"][:10]
    for seg in data["segments"]:
        for ind in seg["indicators"]:
            assert ind["source"] and ind["frequency"]
            if ind["series"]:
                assert ind["series_id"] and ind["definition"]
                assert ind["last_observation"] <= built, \
                    f"{ind['key']}: observation after build date"
    print("ok site bundle: shape, provenance, no future observations")


def test_meta_matches_registry():
    meta_path = REPO / "data" / "momentum" / "_meta.json"
    assert meta_path.exists(), "_meta.json missing — run the build"
    meta = json.loads(meta_path.read_text())
    want = {f"{g}/{k}" for g, k, _ in registry.all_series()}
    assert set(meta["indicators"]) == want
    assert meta["n_indicators"] == len(want)
    for k, v in meta["indicators"].items():
        assert v["source"] and v["frequency"] and "status" in v
    print("ok meta: one entry per registered series")


def test_page_static_rules():
    app = (REPO / "docs" / "momentum" / "app.js").read_text()
    assert "momentum.json" in app
    assert "monotone" not in app, \
        "chart rules: straight lines only, no monotone/spline curves"
    page = (REPO / "docs" / "momentum" / "index.html").read_text()
    assert "weekly" in page.lower() and "Mondays" in page
    assert "methodology.md" in page
    print("ok page: straight-line charts, weekly cadence stated")


if __name__ == "__main__":
    test_registry_completeness()
    test_vol_inversion_onset()
    test_vol_shock_needs_calm_base()
    test_rotation_states()
    test_credit_band_is_unclassified()
    test_broadening_and_haven()
    test_rates_energy_snapback_conditions()
    test_spread_peak()
    test_zero_cross_and_resteepening()
    test_breadth_thrust()
    test_yahoo_drops_unfinished_sessions()
    test_site_bundle_shape()
    test_meta_matches_registry()
    test_page_static_rules()
    print("all momentum tests passed")
