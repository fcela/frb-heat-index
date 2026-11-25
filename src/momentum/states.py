"""Fingerprint states, inflection signals, and rotation — pure
computations over the section's archived series.

Every state is a Boolean (or labelled) function of stored data with the
definition stated here and on the methodology page, evaluated at every
build over the full archived history, so the board can say "active
since <date>" and two runs always agree. Nothing in this module
fetches anything.

Conventions: a "series" is a (dates, values) pair of parallel lists,
dates ascending datetime.date, values float. R_n(X) is the trailing
n-observation return in percent, C_n(X) the trailing n-observation
change in the series' own units — each computed on the series' own
observation rows (for cross-series states, the second series is read
as of the primary series' dates, using its own trailing window).
"""

import bisect
import math

from src.momentum.series import CYCLICAL, DEFENSIVE, GRID, QUADRANT_UNIVERSE

# ----------------------------------------------------------------------
# primitives
# ----------------------------------------------------------------------


def ret(values, i, n):
    """Trailing n-observation return in percent at index i (None if short)."""
    if i < n or values[i - n] == 0:
        return None
    return (values[i] / values[i - n] - 1.0) * 100.0


def chg(values, i, n):
    """Trailing n-observation change in own units at index i."""
    if i < n:
        return None
    return values[i] - values[i - n]


def on_or_before(dates, d):
    """Index of the last observation dated <= d, or None."""
    j = bisect.bisect_right(dates, d) - 1
    return j if j >= 0 else None


def common_dates(series_list):
    """Intersection of the date lists of several (dates, values) series."""
    sets = [set(d) for d, _ in series_list]
    common = set.intersection(*sets) if sets else set()
    return sorted(common)


def align(series_list):
    """(dates, [values_per_series]) on the common dates of the inputs."""
    dates = common_dates(series_list)
    cols = []
    for d, v in series_list:
        lut = dict(zip(d, v))
        cols.append([lut[x] for x in dates])
    return dates, cols


def summarize(records):
    """Current label, the date the current run began, and the full
    transition history of a per-date record list."""
    if not records:
        return {"state": None, "since": None, "transitions": []}
    transitions = []
    for r in records:
        if not transitions or transitions[-1]["state"] != r["state"]:
            transitions.append({"date": str(r["date"]), "state": r["state"]})
    cur = records[-1]["state"]
    since = None
    for r in reversed(records):
        if r["state"] != cur:
            break
        since = r["date"]
    return {"state": cur, "since": str(since) if since else None,
            "transitions": transitions}


def _board_entry(key, title, definition, records, active_states, measured):
    s = summarize(records)
    return {
        "key": key,
        "title": title,
        "definition": definition,
        "state": s["state"],
        "active": s["state"] in active_states,
        "since": s["since"],
        "measured": measured,
        "history": s["transitions"],
    }


# ----------------------------------------------------------------------
# derived spread series (also archived by the builder)
# ----------------------------------------------------------------------


def vix_spread_series(vix, vxv):
    """(dates, values) of VIX − VXV on their common dates."""
    dates, (a, b) = align([vix, vxv])
    return dates, [x - y for x, y in zip(a, b)]


def def_cyc_spread_series(closes):
    """(dates, values) of the defensive-minus-cyclical 21-session
    momentum spread, in percentage points, on the six tickers' common
    trading days. closes: {key: (dates, values)}."""
    keys = DEFENSIVE + CYCLICAL
    dates, cols = align([closes[k] for k in keys])
    vals = {k: cols[i] for i, k in enumerate(keys)}
    out_d, out_v = [], []
    for i in range(len(dates)):
        if i < 21:
            continue
        dm = sum(ret(vals[k], i, 21) for k in DEFENSIVE) / len(DEFENSIVE)
        cm = sum(ret(vals[k], i, 21) for k in CYCLICAL) / len(CYCLICAL)
        out_d.append(dates[i])
        out_v.append(dm - cm)
    return out_d, out_v


def factor_spread_60(closes):
    """SPHB − SPLV trailing 60-session return spread (pp), computed
    from this section's own archived closes as the rotation
    cross-check."""
    dates, (hb, lv) = align([closes["sphb"], closes["splv"]])
    if len(dates) <= 60:
        return None
    return ret(hb, len(dates) - 1, 60) - ret(lv, len(dates) - 1, 60)


# ----------------------------------------------------------------------
# fingerprints (design Section 3, plus snapback from Section 4.7)
# ----------------------------------------------------------------------


def eval_vol_shock(vix):
    dates, v = vix
    recs = []
    for i in range(21, len(dates)):
        m5 = max(v[i - 4:i + 1])
        active = m5 >= 30.0 and v[i - 21] < 25.0
        recs.append({"date": dates[i],
                     "state": "active" if active else "inactive"})
    last = recs[-1] if recs else None
    measured = None
    if last:
        i = len(dates) - 1
        measured = {"vix": v[i], "vix_max_5s": max(v[i - 4:i + 1]),
                    "vix_21s_ago": v[i - 21]}
    return _board_entry(
        "vol_shock", "Vol shock",
        "max(VIX close, trailing 5 sessions) ≥ 30 AND VIX 21 sessions "
        "ago < 25 — a spike out of a calm regime, not a chronic high.",
        recs, {"active"}, measured)


def eval_vol_inversion(spread):
    dates, v = spread
    recs = [{"date": d, "state": "active" if x > 0 else "inactive"}
            for d, x in zip(dates, v)]
    measured = {"vix_minus_vxv": v[-1]} if v else None
    return _board_entry(
        "vol_inversion", "Vol inversion",
        "VIXCLS − VXVCLS > 0: spot vol above 3-month vol "
        "(backwardation).",
        recs, {"active"}, measured)


def eval_rotation(spread):
    """Risk-off / risk-on rotation from the defensive-minus-cyclical
    spread series (design fingerprint 3)."""
    dates, v = spread
    recs = []
    for d, x in zip(dates, v):
        label = "risk_off" if x >= 3.0 else "risk_on" if x <= -3.0 else "none"
        recs.append({"date": d, "state": label})
    measured = {"spread_pp": v[-1]} if v else None
    return _board_entry(
        "rotation", "Risk-off / risk-on rotation",
        "Mean 21-session return of XLP, XLU, XLV minus mean of XLY, "
        "XLI, XLK ≥ +3.0 pp → risk-off rotation; ≤ −3.0 pp → risk-on "
        "rotation (mirror).",
        recs, {"risk_off", "risk_on"}, measured)


def eval_broadening(closes):
    dates, (rsp, spy) = align([closes["rsp"], closes["spy"]])
    recs = []
    for i in range(21, len(dates)):
        s = ret(rsp, i, 21) - ret(spy, i, 21)
        label = "broadening" if s >= 1.5 else "narrowing" if s <= -1.5 else "none"
        recs.append({"date": dates[i], "state": label})
    measured = None
    if recs:
        i = len(dates) - 1
        measured = {"rsp_minus_spy_pp": ret(rsp, i, 21) - ret(spy, i, 21),
                    "rsp_r21_pct": ret(rsp, i, 21),
                    "spy_r21_pct": ret(spy, i, 21)}
    return _board_entry(
        "broadening", "Broadening / narrowing",
        "R21(RSP) − R21(SPY) ≥ +1.5 pp → broadening; ≤ −1.5 pp → "
        "narrowing. RSP is the equal-weight index — the average stock — "
        "so the spread is the breadth proxy (labelled a proxy: true "
        "constituent breadth has no free source).",
        recs, {"broadening", "narrowing"}, measured)


def eval_credit(closes, hy):
    spy_d, spy_v = closes["spy"]
    hy_d, hy_v = hy
    recs = []
    for i in range(21, len(spy_d)):
        j = on_or_before(hy_d, spy_d[i])
        if j is None or j < 21:
            continue
        r = ret(spy_v, i, 21)
        c = hy_v[j] - hy_v[j - 21]
        if r <= -5.0 and c >= 0.50:
            label = "confirmed"
        elif r <= -5.0 and c < 0.25:
            label = "divergence"
        else:
            label = "none"
        recs.append({"date": spy_d[i], "state": label})
    measured = None
    if recs:
        i = len(spy_d) - 1
        j = on_or_before(hy_d, spy_d[i])
        measured = {"spy_r21_pct": ret(spy_v, i, 21),
                    "hy_c21_bp": (hy_v[j] - hy_v[j - 21]) * 100.0}
    return _board_entry(
        "credit", "Credit-confirmed stress / equity-only divergence",
        "R21(SPY) ≤ −5% AND HY OAS 21-session widening ≥ +0.50 → "
        "credit-confirmed stress. The same equity leg with widening "
        "< +0.25 → equity-only divergence. The band between is "
        "deliberately unclassified; the measured widening is shown "
        "either way.",
        recs, {"confirmed", "divergence"}, measured)


def eval_haven_bid(closes, dollar):
    spy_d, spy_v = closes["spy"]
    gld_d, gld_v = closes["gld"]
    dl_d, dl_v = dollar
    recs = []
    for i in range(21, len(spy_d)):
        j = on_or_before(gld_d, spy_d[i])
        k = on_or_before(dl_d, spy_d[i])
        if j is None or j < 21 or k is None or k < 21:
            continue
        rg = ret(gld_v, j, 21)
        rd = dl_v[k] - dl_v[k - 21]
        active = rg > 0 and ret(spy_v, i, 21) < 0 and rd > 0
        recs.append({"date": spy_d[i],
                     "state": "active" if active else "inactive"})
    measured = None
    if recs:
        i = len(spy_d) - 1
        j = on_or_before(gld_d, spy_d[i])
        k = on_or_before(dl_d, spy_d[i])
        measured = {"gld_r21_pct": ret(gld_v, j, 21),
                    "spy_r21_pct": ret(spy_v, i, 21),
                    "dollar_c21": dl_v[k] - dl_v[k - 21]}
    return _board_entry(
        "haven_bid", "Haven bid",
        "R21(GLD) > 0 AND R21(SPY) < 0 AND C21(broad dollar) > 0. "
        "Its failure during an equity fall is informative and is "
        "reported as such.",
        recs, {"active"}, measured)


def eval_rates_shock(dgs10):
    dates, v = dgs10
    recs = []
    for i in range(21, len(dates)):
        c = v[i] - v[i - 21]
        recs.append({"date": dates[i],
                     "state": "active" if abs(c) >= 0.40 else "inactive"})
    measured = None
    if recs:
        c = v[-1] - v[-22]
        measured = {"dgs10_c21_bp": c * 100.0,
                    "direction": "up" if c > 0 else "down"}
    return _board_entry(
        "rates_shock", "Rates shock",
        "|C21(10-year Treasury yield)| ≥ 0.40 (40 bp in 21 sessions), "
        "direction labelled.",
        recs, {"active"}, measured)


def eval_energy_shock(wti):
    dates, v = wti
    recs = []
    for i in range(21, len(dates)):
        r = ret(v, i, 21)
        recs.append({"date": dates[i],
                     "state": "active" if abs(r) >= 15.0 else "inactive"})
    measured = None
    if recs:
        r = ret(v, len(v) - 1, 21)
        measured = {"wti_r21_pct": r,
                    "direction": "up" if r > 0 else "down"}
    return _board_entry(
        "energy_shock", "Energy shock",
        "|R21(WTI crude)| ≥ 15%, direction labelled. (WTI is a FRED "
        "series with a publication lag of about a week; the window is "
        "21 of its own observations.)",
        recs, {"active"}, measured)


def eval_conditions_tightening(nfci):
    dates, v = nfci
    recs = []
    for i in range(13, len(dates)):
        c = v[i] - v[i - 13]
        active = c >= 0.10 and v[i] < 0
        recs.append({"date": dates[i],
                     "state": "active" if active else "inactive"})
    measured = None
    if recs:
        measured = {"nfci": v[-1], "nfci_c13": v[-1] - v[-14]}
    return _board_entry(
        "conditions_tightening", "Conditions tightening",
        "NFCI 13-week change ≥ +0.10 while the NFCI level is still "
        "below 0 — conditions moving the wrong way from a loose base, "
        "weeks before any stress index prints positive. Evaluated on "
        "weekly closes.",
        recs, {"active"}, measured)


def eval_snapback(closes):
    spy_d, spy_v = closes["spy"]
    recs = []
    for i in range(21, len(spy_d)):
        active = ret(spy_v, i, 21) <= -5.0 and ret(spy_v, i, 5) >= 3.0
        recs.append({"date": spy_d[i],
                     "state": "active" if active else "inactive"})
    measured = None
    if recs:
        i = len(spy_d) - 1
        measured = {"spy_r21_pct": ret(spy_v, i, 21),
                    "spy_r5_pct": ret(spy_v, i, 5)}
    return _board_entry(
        "snapback", "Snapback (inflection candidate)",
        "R21(SPY) ≤ −5% AND R5(SPY) ≥ +3%: a violent reversal off a "
        "drawdown. Labelled the weakest signal of the set — most "
        "snapbacks fail; the state exists so the board can say one is "
        "being attempted.",
        recs, {"active"}, measured)


# ----------------------------------------------------------------------
# inflection signals (design Section 4)
# ----------------------------------------------------------------------


def sig_vol_curve_flip(spread):
    dates, v = spread
    firings = []
    for i in range(1, len(v)):
        if v[i - 1] <= 0 < v[i]:
            firings.append({"date": str(dates[i]), "direction": "inversion onset"})
        elif v[i] <= 0 < v[i - 1]:
            firings.append({"date": str(dates[i]), "direction": "normalisation"})
    last = firings[-1] if firings else None
    return {
        "key": "vol_curve_flip", "title": "Vol-curve flip",
        "definition": "VIXCLS − VXVCLS crosses zero. Up-cross = "
                      "inversion onset (stress arriving); down-cross = "
                      "normalisation (the earliest standard sign a "
                      "dislocation is ending).",
        "last_fired": last["date"] if last else None,
        "detail": {"direction": last["direction"] if last else None,
                   "current_spread": v[-1] if v else None},
    }


def sig_spread_peak(hy):
    dates, v = hy
    firings = []
    for p in range(62, len(v)):
        window = v[p - 62:p + 1]
        is_peak = v[p] == max(window)
        is_trough = v[p] == min(window)
        if not (is_peak or is_trough):
            continue
        for j in range(p + 1, min(p + 21, len(v) - 1) + 1):
            if is_peak and v[p] - v[j] >= 0.15:
                firings.append({"date": str(dates[j]),
                                "direction": "peak — credit turned",
                                "extreme_date": str(dates[p]),
                                "extreme_level": v[p]})
                break
            if is_trough and v[j] - v[p] >= 0.15:
                firings.append({"date": str(dates[j]),
                                "direction": "deterioration off the low",
                                "extreme_date": str(dates[p]),
                                "extreme_level": v[p]})
                break
    last = firings[-1] if firings else None
    return {
        "key": "spread_peak", "title": "Spread peak / deterioration turn",
        "definition": "HY OAS prints a 63-session high, then tightens "
                      "≥ 0.15 from that high within 21 sessions → "
                      "credit has turned. Mirror: a 63-session low "
                      "followed by ≥ 0.15 of widening within 21 "
                      "sessions is the deterioration inflection.",
        "last_fired": last["date"] if last else None,
        "detail": last,
    }


def sig_momentum_zero_cross(closes):
    spy_d, spy_v = closes["spy"]
    firings = []
    prev = None
    for i in range(21, len(spy_d)):
        r = ret(spy_v, i, 21)
        s = 1 if r > 0 else -1 if r < 0 else 0
        if s != 0:
            if prev is not None and prev != 0 and s != prev:
                firings.append({"date": str(spy_d[i]),
                                "direction": "turned positive" if s > 0
                                else "turned negative"})
            if s != 0:
                prev = s
    last = firings[-1] if firings else None
    i = len(spy_d) - 1
    return {
        "key": "momentum_zero_cross", "title": "Momentum zero-cross",
        "definition": "The S&P 500's trailing 21-session return "
                      "(SPY) crosses zero in either direction; the "
                      "board dates the current momentum regime.",
        "last_fired": last["date"] if last else None,
        "detail": {"direction": last["direction"] if last else None,
                   "current_r21_pct": ret(spy_v, i, 21) if i >= 21 else None,
                   "regime_since": last["date"] if last else None},
    }


def breadth_share_series(closes):
    """(dates, shares): fraction of grid panels with R21 > 0, on the
    grid's common trading days."""
    dates, cols = align([closes[k] for k in GRID])
    vals = {k: cols[i] for i, k in enumerate(GRID)}
    out_d, out_s = [], []
    for i in range(21, len(dates)):
        pos = sum(1 for k in GRID if ret(vals[k], i, 21) > 0)
        out_d.append(dates[i])
        out_s.append(pos / len(GRID))
    return out_d, out_s


def sig_breadth_thrust(closes):
    dates, shares = breadth_share_series(closes)
    firings = []
    zone = None
    for d, s in zip(dates, shares):
        z = "low" if s <= 0.30 else "high" if s >= 0.70 else zone
        if zone == "low" and z == "high":
            firings.append({"date": str(d), "direction": "thrust up"})
        elif zone == "high" and z == "low":
            firings.append({"date": str(d), "direction": "thrust down (breadth collapse)"})
        if z is not None:
            zone = z
    last = firings[-1] if firings else None
    return {
        "key": "breadth_thrust", "title": "Sector breadth thrust",
        "definition": "The share of the 15 grid panels in positive "
                      "21-session momentum moves from ≤ 30% to ≥ 70% "
                      "within the band (thrust up), or the reverse "
                      "(thrust down). Computed entirely from the "
                      "section's own grid — no external breadth data.",
        "last_fired": last["date"] if last else None,
        "detail": {"direction": last["direction"] if last else None,
                   "current_share": shares[-1] if shares else None,
                   "panels_positive": int(round(shares[-1] * len(GRID)))
                   if shares else None,
                   "panels_total": len(GRID)},
    }


def sig_curve_resteepening(t10y2y):
    dates, v = t10y2y
    firings = []
    prev = False
    for i in range(62, len(dates)):
        c = (v[i] - v[i - 21]) >= 0.15 and min(v[i - 62:i + 1]) <= 0
        if c and not prev:
            firings.append({"date": str(dates[i])})
        prev = c
    last = firings[-1] if firings else None
    return {
        "key": "curve_resteepening", "title": "Curve re-steepening",
        "definition": "C21(T10Y2Y) ≥ +0.15 after a trailing 63-session "
                      "minimum ≤ 0 — the post-inversion turn that "
                      "historically sits much closer to recessions "
                      "than the inversion itself.",
        "last_fired": last["date"] if last else None,
        "detail": {"current_c21": (v[-1] - v[-22]) if len(v) > 21 else None,
                   "current_level": v[-1] if v else None},
    }


def sig_dollar_turn(dollar):
    dates, v = dollar
    c21 = [None] * len(v)
    for i in range(21, len(v)):
        c21[i] = v[i] - v[i - 21]
    firings = []
    for i in range(21 + 42, len(v)):
        s = c21[i]
        if s is None or s == 0:
            continue
        sign = 1 if s > 0 else -1
        prev_signs = set()
        ok = True
        for j in range(i - 42, i):
            if c21[j] is None or c21[j] == 0:
                ok = False
                break
            prev_signs.add(1 if c21[j] > 0 else -1)
        if ok and prev_signs == {-sign}:
            firings.append({"date": str(dates[i]),
                            "direction": "dollar momentum turned up"
                            if sign > 0 else "dollar momentum turned down"})
    last = firings[-1] if firings else None
    return {
        "key": "dollar_turn", "title": "Dollar turn",
        "definition": "Sign flip of C21(broad dollar) after at least "
                      "42 sessions of the opposite sign — a dollar "
                      "momentum regime change.",
        "last_fired": last["date"] if last else None,
        "detail": {"direction": last["direction"] if last else None,
                   "current_c21": c21[-1] if c21 else None},
    }


# ----------------------------------------------------------------------
# rotation (design Section 5)
# ----------------------------------------------------------------------


def quadrants(closes):
    """RRG-style quadrant assignment for QUADRANT_UNIVERSE vs SPY."""
    keys = QUADRANT_UNIVERSE
    dates, cols = align([closes[k] for k in keys + ["spy"]])
    vals = {k: cols[i] for i, k in enumerate(keys + ["spy"])}
    i = len(dates) - 1
    out = []
    for k in keys:
        rt = ret(vals[k], i, 63) - ret(vals["spy"], i, 63)
        rm = ret(vals[k], i, 21) - ret(vals["spy"], i, 21)
        q = ("Leading" if rt >= 0 and rm >= 0 else
             "Weakening" if rt >= 0 else
             "Lagging" if rm < 0 else "Improving")
        out.append({"key": k, "rs_trend_pp": rt, "rs_momentum_pp": rm,
                    "quadrant": q})
    return out


def rotation_patterns(board, quads, closes):
    """Named rotation patterns on top of the fingerprint states and
    the quadrants. board: {state_key: board entry}."""
    q = {x["key"]: x["quadrant"] for x in quads}
    rot = board["rotation"]["state"]
    defs_in_leading = sum(1 for k in DEFENSIVE if q.get(k) == "Leading")
    cyc_in_leading = sum(1 for k in CYCLICAL if q.get(k) == "Leading")
    defs_in_lagging = sum(1 for k in DEFENSIVE if q.get(k) == "Lagging")
    patterns = []
    defensive = (rot == "risk_off" and defs_in_leading >= 2
                 and q.get("xlk") == "Lagging")
    patterns.append({
        "key": "defensive_rotation", "title": "Defensive rotation",
        "definition": "Risk-off rotation active AND at least two of "
                      "XLP/XLU/XLV in Leading while XLK is in Lagging.",
        "active": defensive})
    cyclical = (rot == "risk_on" and cyc_in_leading >= 2
                and q.get("xlp") == "Lagging")
    patterns.append({
        "key": "cyclical_rotation", "title": "Cyclical rotation (risk-on)",
        "definition": "Risk-on rotation active AND at least two of "
                      "XLY/XLI/XLK in Leading while XLP is in Lagging "
                      "(the mirror of defensive rotation).",
        "active": cyclical})
    unwind = (board["broadening"]["state"] == "broadening"
              and (q.get("qqq") == "Lagging" or q.get("xlk") == "Lagging")
              and q.get("rsp") in ("Leading", "Improving"))
    patterns.append({
        "key": "concentration_unwind", "title": "Concentration unwind",
        "definition": "Broadening active AND QQQ or XLK in Lagging "
                      "AND RSP in Leading or Improving: leadership "
                      "leaving the mega-caps without leaving equities.",
        "active": unwind})
    dates, cols = align([closes[k] for k in GRID])
    vals = {k: cols[i] for i, k in enumerate(GRID)}
    i = len(dates) - 1
    neg = sum(1 for k in GRID if ret(vals[k], i, 21) < 0)
    liquidation = neg >= 12 and board["credit"]["state"] == "confirmed"
    patterns.append({
        "key": "broad_liquidation", "title": "Broad liquidation",
        "definition": "At least 12 of the 15 grid panels with "
                      "negative 21-session momentum AND "
                      "credit-confirmed stress active: everything "
                      "falling together, credit included.",
        "active": liquidation,
        "measured": {"panels_negative": neg, "panels_total": len(GRID)}})
    return patterns


# ----------------------------------------------------------------------
# grid stats and movers
# ----------------------------------------------------------------------


def grid_stats(closes):
    """Per-panel 5/21/63-session returns, distance from the 52-week
    high, and 21-session realized volatility (annualised %)."""
    out = []
    for k in GRID:
        dates, v = closes[k]
        i = len(v) - 1
        hi = max(v[max(0, i - 251):i + 1])
        vol = None
        if i >= 21:
            rets = [math.log(v[j] / v[j - 1]) for j in range(i - 20, i + 1)]
            m = sum(rets) / len(rets)
            var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
            vol = math.sqrt(var) * math.sqrt(252) * 100.0
        out.append({
            "key": k,
            "r5_pct": ret(v, i, 5), "r21_pct": ret(v, i, 21),
            "r63_pct": ret(v, i, 63),
            "dist_high_pct": (v[i] / hi - 1.0) * 100.0 if hi else None,
            "vol21_pct": vol,
            "last": v[i], "last_date": str(dates[i]),
        })
    return out


# Movers table configuration: (series key, kind). "pct" = trailing
# returns in percent; "bp" = trailing changes in basis points.
MOVERS = [
    ("sp500", "pct"), ("nasdaq", "pct"), ("dgs2", "bp"), ("dgs10", "bp"),
    ("t10y2y", "bp"), ("t10y3m", "bp"), ("dollar", "pct"), ("euro", "pct"),
    ("wti", "pct"), ("brent", "pct"), ("gld", "pct"), ("bitcoin", "pct"),
    ("tlt", "pct"), ("hy_oas", "bp"), ("ig_oas", "bp"),
]


def movers(frames):
    """frames: {key: (dates, values)} across all archived series."""
    out = []
    for key, kind in MOVERS:
        if key not in frames:
            continue
        dates, v = frames[key]
        i = len(v) - 1
        row = {"key": key, "kind": kind, "last": v[i],
               "last_date": str(dates[i])}
        for n in (5, 21, 63):
            if kind == "pct":
                row[f"c{n}"] = ret(v, i, n)
            else:
                c = chg(v, i, n)
                row[f"c{n}"] = c * 100.0 if c is not None else None
        out.append(row)
    return out
