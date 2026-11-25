"""Build the Pulse of the Market dataset.

Fetches every live series (Yahoo Finance v8 for prices, FRED via
fredgraph for macro/vol, the FEDS Notes file for the excess bond
premium — all free, no key), archives the daily history under
data/momentum/, computes the fingerprint states, inflection signals,
rotation quadrants/patterns, grid statistics and movers from the
archived series only, and writes:

  data/momentum/<group>/<series>.csv   (date,value)
  data/momentum/_meta.json             (per-series provenance, pulse format)
  docs/data/momentum.json              (site bundle)

Failure policy (the pulse/predmarkets policy): a source that fails
marks its entry "error" in the meta and keeps its last archived data —
the run never fails and never fabricates. States are computed from the
archive, so a failed fetch leaves the board reading the last good
history, with the error visible in the meta and on the page.

Refresh cadence is weekly (Mondays, .github/workflows/momentum.yml);
the archived series are daily, and every state window is counted in
sessions, so the dated inflections (e.g. the vol-curve flip) are exact
despite the weekly refresh.

Run: python3 -m src.momentum.build_momentum [--incremental]
"""

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.momentum import series as registry
from src.momentum import states, yahoo
from src.pulse import fred
from src.pulse.cds_bond_basis import fetch_ebp

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "data" / "momentum"
SITE_JSON = REPO / "docs" / "data" / "momentum.json"

CHART_WINDOW = 252  # observations shipped per charted series (~1 year daily)


# ----------------------------------------------------------------------
# helpers (the pulse builder's contract, kept local so this layer is
# self-contained like predmarkets)
# ----------------------------------------------------------------------


def sanitize_records(records):
    out = []
    for r in records:
        v = r.get("value")
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            r = dict(r, value=None)
        out.append(r)
    return out


def strict_json(obj) -> str:
    return json.dumps(obj, allow_nan=False)


def write_series_csv(group: str, key: str, df: pl.DataFrame) -> Path:
    d = DATA_DIR / group
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.csv"
    df.select(["date", "value"]).write_csv(p)
    return p


def load_existing(group: str, key: str) -> pl.DataFrame | None:
    p = DATA_DIR / group / f"{key}.csv"
    if not p.exists():
        return None
    df = pl.read_csv(p)
    return df.with_columns(pl.col("date").str.to_date()).sort("date")


def merge_incremental(old, new):
    if old is None or old.height == 0:
        return new
    return pl.concat([old, new]).unique("date", keep="last").sort("date")


def detect_history_rewrites(old, new, tol_rel=0.02, tol_abs=0.05):
    """Same contract as the pulse builder: flag stored dates a fresh
    fetch would change by more than max(tol_rel*|old|, tol_abs)."""
    if old is None or old.height == 0 or new.height == 0:
        return []
    j = old.join(new, on="date", suffix="_new")
    out = []
    for r in j.iter_rows(named=True):
        o, n = r["value"], r["value_new"]
        if o is None or n is None:
            continue
        if abs(n - o) > max(tol_rel * abs(o), tol_abs):
            out.append({"date": str(r["date"]), "old": o, "new": n})
    return out


def validate_frame(df, key):
    if "date" not in df.columns or "value" not in df.columns:
        raise RuntimeError(f"{key}: fetcher must return date/value columns")
    if df.height == 0:
        raise RuntimeError(f"{key}: fetcher returned no rows")
    bad = df.filter(~pl.col("value").is_finite())
    if bad.height:
        raise RuntimeError(f"{key}: {bad.height} non-finite values")


def fetch_series(entry, transports):
    route = entry["route"]
    if route == "yahoo":
        return yahoo.fetch_daily_closes(
            entry["symbol"], transport=transports.get("yahoo"))
    if route == "fred":
        return fred.fetch_fred_csv(
            entry["fred_id"], transport=transports.get("fred"))
    if route == "ebp":
        return fetch_ebp()
    raise RuntimeError(f"no fetcher for route {route}")


def frame_lists(df):
    return df["date"].to_list(), df["value"].to_list()


def _round(obj, nd=4):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def _long_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ----------------------------------------------------------------------
# the board's one-paragraph read: templated factual sentences from the
# computed states, in the make_site now-block style. No claim appears
# here that is not a measured value or a state computed above.
# ----------------------------------------------------------------------


def build_read(board, patterns, frames, as_of):
    b = {e["key"]: e for e in board}
    active = [e for e in board if e["active"]]
    s = []
    names = []
    for e in active:
        since = f" (since {_long_date(e['since'])})" if e.get("since") else ""
        names.append(f"{e['title'].lower()}{since}")
    s.append(
        f"As of {_long_date(as_of)}, {len(active)} of {len(board)} "
        f"fingerprint states are active"
        + (f": {'; '.join(names)}." if names else "."))
    credit = b["credit"]
    spy_r21 = (credit["measured"] or {}).get("spy_r21_pct")
    hy_bp = (credit["measured"] or {}).get("hy_c21_bp")
    if spy_r21 is not None and spy_r21 <= -5.0 and hy_bp is not None:
        if credit["state"] == "confirmed":
            s.append(
                f"Equities are down {abs(spy_r21):.1f}% over 21 sessions "
                f"and credit confirms: HY OAS has widened {hy_bp:.0f} bp "
                "over the same window, through the +50 bp bar.")
        elif credit["state"] == "divergence":
            s.append(
                f"Equities are down {abs(spy_r21):.1f}% over 21 sessions "
                f"but credit does not confirm: HY OAS has widened only "
                f"{hy_bp:.0f} bp, under the +25 bp divergence bar — an "
                "equity-only move on these definitions.")
        else:
            s.append(
                f"Equities are down {abs(spy_r21):.1f}% over 21 sessions "
                f"while HY OAS has widened {hy_bp:.0f} bp — inside the "
                "deliberately unclassified 25–50 bp band: credit is "
                "leaning, not confirming.")
    nfci = (b["conditions_tightening"]["measured"] or {}).get("nfci")
    if nfci is not None and nfci < 0:
        s.append(
            f"Financial conditions still print loose: the NFCI is "
            f"{nfci:.2f}, below the zero line that marks average "
            "tightness.")
    haven = b["haven_bid"]
    gld = (haven["measured"] or {}).get("gld_r21_pct")
    if spy_r21 is not None and spy_r21 < 0 and gld is not None and gld < 0:
        s.append(
            f"Gold is falling with equities ({gld:.1f}% over 21 "
            "sessions), so the haven bid is not in gold on these "
            "definitions.")
    pats = [p["title"] for p in patterns if p["active"]]
    if pats:
        s.append(f"Rotation patterns active: {', '.join(pats)}.")
    else:
        s.append("No named rotation pattern is active.")
    return s


# ----------------------------------------------------------------------
# build
# ----------------------------------------------------------------------


def build(incremental: bool = False, transports=None) -> dict:
    transports = transports or {}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    meta_indicators = {}
    frames = {}          # key -> (dates, values), merged archive state
    site_groups = {}     # group -> [indicator entries with chart series]
    entries_by_key = {}  # key -> meta entry (for grid/movers joins)

    primaries = [(g, k, e) for g, k, e in registry.all_series()
                 if e["route"] != "derived"]
    derived = [(g, k, e) for g, k, e in registry.all_series()
               if e["route"] == "derived"]

    def process(group, key, entry, df_new, error=None):
        """Merge, archive, and record one series. df_new None = fetch
        failed; fall back to the archive if one exists."""
        meta_entry = {
            "key": key,
            "title": entry["title"],
            "why": entry["why"],
            "source": entry["source"],
            "source_url": entry.get("source_url", ""),
            "series_id": entry.get("series_id", ""),
            "definition": entry.get("definition", ""),
            "frequency": entry["frequency"],
            "status": entry["status"],
            "note": entry.get("note", ""),
            "unit": entry.get("unit", ""),
        }
        old = load_existing(group, key)
        df = df_new
        if error is not None:
            meta_entry["status"] = "error"
            meta_entry["error"] = error[:200]
            df = old  # keep last good data; never fabricate
        elif df is not None:
            validate_frame(df, key)
            if incremental:
                rewrites = detect_history_rewrites(old, df)
                if rewrites:
                    meta_entry["history_rewrites"] = rewrites
                df = merge_incremental(old, df)
                validate_frame(df, key)
            write_series_csv(group, key, df)
        if df is None or df.height == 0:
            meta_entry.update({"last_observation": None, "n_observations": 0})
            site_entry = {**meta_entry, "series": []}
        else:
            dates, values = frame_lists(df)
            frames[key] = (dates, values)
            meta_entry.update({
                "last_observation": str(dates[-1]),
                "last_value": values[-1],
                "n_observations": len(dates),
                "first_observation": str(dates[0]),
            })
            if error is None:
                meta_entry["fetched_at_utc"] = now
            chart = df.tail(CHART_WINDOW)
            records = sanitize_records(
                [{"date": str(d), "value": v}
                 for d, v in zip(*frame_lists(chart))])
            strict_json(records)
            site_entry = {**meta_entry, "series": records}
        meta_indicators[f"{group}/{key}"] = meta_entry
        entries_by_key[key] = meta_entry
        site_groups.setdefault(group, []).append(site_entry)
        return meta_entry

    for group, key, entry in primaries:
        if entry["status"] not in registry.FETCHABLE_STATUSES:
            process(group, key, entry, None)
            continue
        try:
            df_new = fetch_series(entry, transports)
            process(group, key, entry, df_new)
        except Exception as e:  # failure policy: mark, keep archive, go on
            process(group, key, entry, None, error=str(e))

    # Derived series, computed from the merged archive frames.
    closes = {k: frames[k] for k in
              [s for _, s, e in registry.all_series() if e["route"] == "yahoo"]
              if k in frames}
    for group, key, entry in derived:
        try:
            if key == "vix_spread":
                dates, values = states.vix_spread_series(
                    frames["vix"], frames["vxv"])
            elif key == "def_cyc_spread":
                dates, values = states.def_cyc_spread_series(closes)
            else:
                raise RuntimeError(f"no derivation for {key}")
            df = pl.DataFrame({"date": dates, "value": values},
                              schema={"date": pl.Date, "value": pl.Float64})
            process(group, key, entry, df)
        except Exception as e:
            process(group, key, entry, None, error=str(e))

    # ---------------- states, signals, rotation ----------------
    required = (["vix", "vxv", "hy_oas", "dollar", "dgs10", "wti",
                 "nfci", "t10y2y"]
                + [s for _, s, e in registry.all_series()
                   if e["route"] == "yahoo"])
    missing = [k for k in required if k not in frames]
    if missing:
        # No archive to compute from (e.g. a first build during a
        # source outage). The run still succeeds; the board is empty
        # and the gap is stated, never papered over.
        site = {
            "meta": {"built_at_utc": now, "cadence": "Weekly (Mondays)",
                     "as_of": None},
            "read": [f"State computation skipped: no archived data for "
                     f"{', '.join(missing)}. The series are marked "
                     "error in the meta; the next weekly run retries."],
            "board": [], "inflections": [],
            "rotation": {"quadrants": [], "patterns": [],
                         "spread_title": ""},
            "grid": [], "movers": [], "vol_panel": {},
            "segments": [
                {"key": gk, "title": g["title"], "subtitle": g["subtitle"],
                 "indicators": site_groups.get(gk, [])}
                for gk, g in registry.GROUPS.items()],
        }
        meta = {
            "built_at_utc": now, "incremental": incremental,
            "n_indicators": len(meta_indicators),
            "n_live": sum(1 for v in meta_indicators.values()
                          if v["status"] in registry.FETCHABLE_STATUSES),
            "n_pending": sum(1 for v in meta_indicators.values()
                             if v["status"] == "pending-sourcing"),
            "n_error": sum(1 for v in meta_indicators.values()
                           if v["status"] == "error"),
            "indicators": meta_indicators,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "_meta.json").write_text(strict_json(_round(meta)) + "\n")
        SITE_JSON.write_text(strict_json(_round(site)) + "\n")
        json.loads(SITE_JSON.read_text())
        return meta
    board = []
    board.append(states.eval_vol_shock(frames["vix"]))
    spread = states.vix_spread_series(frames["vix"], frames["vxv"])
    board.append(states.eval_vol_inversion(spread))
    dc = states.def_cyc_spread_series(closes)
    rot_entry = states.eval_rotation(dc)
    fac = states.factor_spread_60(closes)
    if fac is not None and rot_entry["measured"] is not None:
        rot_entry["measured"]["factor_spread_60_pp"] = fac
        rot_entry["measured"]["factor_agrees"] = bool(
            (rot_entry["measured"]["spread_pp"] > 0) == (fac < 0))
    board.append(rot_entry)
    board.append(states.eval_broadening(closes))
    board.append(states.eval_credit(closes, frames["hy_oas"]))
    board.append(states.eval_haven_bid(closes, frames["dollar"]))
    board.append(states.eval_rates_shock(frames["dgs10"]))
    board.append(states.eval_energy_shock(frames["wti"]))
    board.append(states.eval_conditions_tightening(frames["nfci"]))
    board.append(states.eval_snapback(closes))
    board_by_key = {e["key"]: e for e in board}

    inflections = [
        states.sig_vol_curve_flip(spread),
        states.sig_spread_peak(frames["hy_oas"]),
        states.sig_momentum_zero_cross(closes),
        states.sig_breadth_thrust(closes),
        states.sig_curve_resteepening(frames["t10y2y"]),
        states.sig_dollar_turn(frames["dollar"]),
    ]

    quads = states.quadrants(closes)
    patterns = states.rotation_patterns(board_by_key, quads, closes)
    grid = states.grid_stats(closes)
    for row in grid:
        e = entries_by_key[row["key"]]
        row.update({
            "title": registry.DISPLAY_NAMES[row["key"]],
            "source": e["source"], "source_url": e["source_url"],
            "series_id": e["series_id"], "definition": e["definition"],
            "frequency": e["frequency"],
        })
        # Rebased chart: 100 at 63 sessions ago, trailing 64 sessions.
        dates, values = closes[row["key"]]
        base = values[-64] if len(values) > 63 else values[0]
        row["chart"] = [{"date": str(d), "value": v / base * 100.0}
                        for d, v in zip(dates[-64:], values[-64:])]
    movers = states.movers(frames)
    for row in movers:
        e = entries_by_key[row["key"]]
        row.update({"title": e["title"], "unit": e["unit"]})

    vol_panel = {}
    for k in ("vix", "vxv"):
        dates, values = frames[k]
        vol_panel[k] = [{"date": str(d), "value": v}
                        for d, v in zip(dates[-CHART_WINDOW:],
                                        values[-CHART_WINDOW:])]
    sd, sv = spread
    vol_panel["spread"] = [{"date": str(d), "value": v}
                           for d, v in zip(sd[-CHART_WINDOW:],
                                           sv[-CHART_WINDOW:])]

    as_of = str(closes["spy"][0][-1])
    read = build_read(board, patterns, frames, as_of)

    meta = {
        "built_at_utc": now,
        "incremental": incremental,
        "n_indicators": len(meta_indicators),
        "n_live": sum(1 for v in meta_indicators.values()
                      if v["status"] in registry.FETCHABLE_STATUSES),
        "n_pending": sum(1 for v in meta_indicators.values()
                         if v["status"] == "pending-sourcing"),
        "n_error": sum(1 for v in meta_indicators.values()
                       if v["status"] == "error"),
        "indicators": meta_indicators,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "_meta.json").write_text(strict_json(_round(meta)) + "\n")

    segments_out = []
    for gkey, group in registry.GROUPS.items():
        segments_out.append({
            "key": gkey,
            "title": group["title"],
            "subtitle": group["subtitle"],
            "indicators": site_groups.get(gkey, []),
        })
    site = {
        "meta": {
            "built_at_utc": now,
            "cadence": "Weekly (Mondays)",
            "as_of": as_of,
        },
        "read": read,
        "board": _round(board),
        "inflections": _round(inflections),
        "rotation": {
            "quadrants": _round(quads),
            "patterns": patterns,
            "spread_title": entries_by_key["def_cyc_spread"]["title"],
        },
        "grid": _round(grid),
        "movers": _round(movers),
        "vol_panel": vol_panel,
        "segments": segments_out,
    }
    SITE_JSON.write_text(strict_json(_round(site)) + "\n")
    json.loads(SITE_JSON.read_text())  # strict parse of what the site serves
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--incremental", action="store_true")
    args = ap.parse_args()
    meta = build(incremental=args.incremental)
    print(f"live: {meta['n_live']}/{meta['n_indicators']}  "
          f"pending: {meta['n_pending']}  errors: {meta['n_error']}")
    for k, v in meta["indicators"].items():
        if v["status"] in registry.FETCHABLE_STATUSES:
            print(f"  {k}: {v['n_observations']} obs, last {v['last_observation']}")
        else:
            print(f"  {k}: {v['status']}")
