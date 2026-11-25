#!/usr/bin/env python3
"""Build the website's data files: data/*.csv -> docs/data/*.json.

Outputs (under --out, default <repo>/docs/data):
  aggregate.json   [{"q": "1990Q1", "v": 0.34}, ...]
  components.json  {"Equity markets": [{"q": ..., "v": ...}, ...], ...}
  indicators.json  {"<full indicator name>": [{"q": ..., "z": ...}, ...], ...}
                   (z = oriented standardized value; higher = more vulnerable)
  hierarchy.json   categories -> components -> indicators (+ short names)
  clustered.json   row order + dendrogram structure for the clustered
                   heat-map view (src/cluster_indicators.py computes it)
  meta.json        data/_meta.json + site-contract fields

Also rewrites, in place, the data-derived spec lines on the landing
page's product cards (docs/index.html, when it sits beside --out): the
heat-index indicator/component counts and coverage from data/_meta.json,
the pulse indicator/segment/live counts from data/pulse/_meta.json, and
the pulse-of-the-market series/group/live counts from
data/momentum/_meta.json (when that layer has been built).
Those lines are generated, not maintained by hand; the rest of the page
is hand-written and is left byte-identical.

The same landing rewrite fills the page's "What is happening now" block
(between the NOW-BLOCK markers): the headline index level and change,
the hottest and coldest components, the prediction markets' modal FOMC
outcome and recession odds (data/predmarkets/_meta.json + the fomc and
recession CSVs), and the panel's build date with its nowcast caveat.
Every number in the block is computed from those files at build time.

Missing or non-finite values are emitted as null. JSON is serialized with
allow_nan=False so a missed NaN/Inf fails the build loudly instead of
shipping invalid JSON to the site (2026-10-14 outage: bare NaN literals
killed every chart).

meta.json contract (what docs/assets/app.js renderMeta() reads):
  components, last_updated_quarter, start_quarter, n_quarters, stale_sources
plus the build fields already in data/_meta.json (kept verbatim).

Usage:
    python src/make_site.py [--data data] [--out docs/data]

Requires: polars.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from html import escape as html_escape
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster_indicators import compute_bundle  # noqa: E402
from run_pipeline import SPEC  # noqa: E402  (indicator -> (component, sign, transform))

HERE = Path(__file__).resolve().parent.parent

# The three broad vulnerability categories from the paper, in component order.
CATEGORIES = [
    {"name": "Valuation pressures",
     "blurb": "Asset prices and credit-market pricing. Stretched valuations "
              "and complacent pricing read as vulnerability.",
     "components": ["Equity markets", "Price volatility", "Business credit",
                    "Commercial real estate", "Residential real estate"]},
    {"name": "Nonfinancial-sector borrowing",
     "blurb": "Debt and saving of households and nonfinancial businesses.",
     "components": ["Nonfinancial Business", "Consumer credit",
                    "Home mortgages", "Net saving", "Credit cycle"]},
    {"name": "Financial-sector health",
     "blurb": "Leverage, funding, and concentration inside the financial system.",
     "components": ["Bank leverage", "Nonbank leverage",
                    "Maturity transformation", "Short-term wholesale funding",
                    "Size/concentration"]},
]

# Short display names for the indicators (full names are the SPEC keys in
# run_pipeline.py). Drawn from the paper's indicator catalog, plus the two
# added in the journal publication (J. Banking & Finance 81, 2017).
SHORT_NAMES = {
    "Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)": "E/P minus 10-yr Treasury yield",
    "Equity Valuations (Earnings/Price ratio )": "E/P ratio",
    "Indicators of Volitility (VIX)": "VIX",
    "Indicators of Volitility (Credit Default Swap premium)": "CDS premium",
    "Spread of Corporate Bonds (over Treasury Securities) (BBB)": "BBB spread",
    "Spread of Corporate Bonds (over Treasury Securities) (High Yield)": "High-yield spread",
    "Investor Appetite for Risky Corporate Debt (Issuance of Junk Bonds and Leverage Loans)": "Junk + leveraged-loan issuance",
    "Investor Appetite for Risky Corporate Debt (Change in Bank Lending Standards C&I Loans)": "C&I lending standards",
    "Prices for Commercial Real Estate (CRE)": "CRE prices",
    "Change in Lending Standards, CRE Loans": "CRE lending standards",
    "Mortage Lending and FICO Scores (FICO scores)": "FICO scores",
    "National House Prices Relative to Rents": "House prices / rents",
    "Mortage Lending and FICO Scores (Change in Lending Standards)": "Mortgage lending standards",
    "Real Debt Growth of Nonfinancial Businesses": "Real debt growth",
    "Risky Debt of Nonfinancial Businesses": "Net leverage of risky firms",
    "Risky Debt of Nonfinancial Businesses (Deep junk Issuance Share)": "Deep-junk issuance share",
    "Debt Burden of Nonfinancial Businesses (Debt-to-Income ratio)": "Debt-to-income ratio",
    "Debt Burden of Nonfinancial Business (Interest expenses)": "Interest expenses",
    "Consumer Credit (Consumer credit ratio to GDP)": "Consumer credit / GDP",
    "Consumer Credit (Consumer credit debt service ratio to DPI)": "Consumer debt service ratio",
    "Comsumer Credit of Riskier Borrowers (Ratio of consumer credit debt owed by riskier borrowers to DPI)": "Riskier-borrower debt / DPI",
    "Consumer Credit of Riskier Borrowers (Incidence of very rapid consumer borrowing by riskier borrowers)": "Rapid-borrowing incidence",
    "Home Mortage Debt (Ratio of total mortgage debt to GDP)": "Mortgage debt / GDP",
    "Home Mortgage Debt (Total mortgage debt service ratio to DPI)": "Mortgage debt service ratio",
    "Home Mortage Debt (Ratio of mortgage debt owed by riskier borrowers to DPI)": "Riskier-borrower mortgage debt / DPI",
    "Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages by riskier borrowers)": "Piggy-back incidence",
    "Mortgage Borrowing by Riskier Borrowers (Incidence of very rapid mortgage borrowing by riskier borrowers)": "Rapid-borrowing incidence",
    "Ratio of Household Savings to GDP": "Household saving / GDP",
    "Ratio of Nonfinancial Business Savings to GDP": "Business saving / GDP",
    "Bank Leverage (Total common equity to total assets)": "Tangible common equity / assets",
    "Bank Leverage (Tier 1 common equity ratio)": "Tier 1 common equity ratio",
    "Bank Leverage (Total Capital Ratio)": "Total capital ratio",
    "Broker Dealer Leverage (Dealer financing)": "Broker-dealer financing",
    "Broker Dealer Leverage (Broker dealer leverage ratio)": "Broker-dealer leverage ratio",
    "Hedge Fund Leverage (Gross assets to net assets, qualifying hedge funds)": "Hedge fund leverage",
    "Hedge Fund Leverage (SCOOS net percent reporting increased use of leverage)": "Hedge fund leverage (SCOOS)",
    "Ratio of U.S. Non Agency Securitization Issuance to GDP": "Non-agency securitization / GDP",
    "Maturity Mismatch (Loans as a percent of deposits)": "Loans / deposits",
    "Weighted Average Maturity Gap at Commerical Banks": "Weighted-average maturity gap",
    "Maturity Mismatch (Ratio of net short-term wholesale debt of the financial sector to GDP)": "Net short-term wholesale debt / GDP",
    "Short Term Wholesale Funding (Ratio of Gross Short-Term Wholesale Debt of the Financial Sector to GDP)": "Gross short-term wholesale debt / GDP",
    "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)": "Short-term money / BHC assets",
    "Short-term Wholesale Funding (Ratio of total runnable money -like liabilities to GDP)": "Runnable money-like liabilities / GDP",
    "Ratio of Total Financial Sector Liabilities to GDP": "Financial-sector liabilities / GDP",
    "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets": "Top-5 BHC asset share",
    "Illiquidity Conventration Indicator": "Illiquidity concentration",
    "SRISK (Total capital shortfall relative to total assets)": "SRISK / total assets",
    "Granger-causality connections among large financial institutions": "Granger-causality connections",
    "Private Credit Loans (Share of nonfinancial business debt)": "Private credit share of business debt",
    "Private Credit Intermediary Leverage (Total liabilities to total financial assets)": "Private credit intermediary leverage",
    "Credit-to-GDP Gap (BIS, private non-financial sector)": "Credit-to-GDP gap (BIS)",
    "Debt Service Ratio (BIS, private non-financial sector)": "Private-sector debt service ratio (BIS)",
}


def json_value(v):
    """Map one scalar to a JSON-safe value: None/NaN/Inf -> None."""
    if v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return float(v)


def sanitize_json(obj):
    """Recursively replace NaN/Inf floats with None (strict-JSON safe)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_json(x) for k, x in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_json(x) for x in obj]
    return obj


def dumps_strict(obj, **kwargs) -> str:
    """Serialize to JSON, failing loudly on any NaN/Inf that slipped through."""
    return json.dumps(sanitize_json(obj), allow_nan=False, **kwargs)


def series_json(df: pl.DataFrame, value_col: str) -> list[dict]:
    out = []
    for q, v in df.select(["quarter", value_col]).iter_rows():
        out.append({"q": q, "v": json_value(v)})
    return out


def quarter_key(q: str) -> tuple[int, int]:
    """Order key for a 'YYYYQn' quarter string."""
    year, _, qtr = q.partition("Q")
    return int(year), int(qtr)


def landing_spec_lines(heat_meta: dict, pulse_meta: dict,
                       start_quarter: str,
                       momentum_meta: dict | None = None) -> dict[str, str]:
    """The landing page's data-derived spec lines.

    Every number and date comes from the build metas and the panel's
    first quarter; nothing is copied from the page's current text, so the
    lines cannot drift from the data the site serves. The coverage line
    ends at the nowcast quarter, flagged as a nowcast, when the panel has
    been extended past the last built (observed) quarter; otherwise it
    ends at the last built quarter with no qualifier. The pulse segment
    count is the number of distinct key prefixes in the pulse meta's
    indicator map. The momentum line is included only when the caller
    passes the momentum layer's meta (that layer is built by its own
    weekly pipeline, after this one historically ran).
    """
    lines = {
        "heat_indicators": (
            f"{heat_meta['n_indicators']} public-proxy indicators "
            f"· {heat_meta['n_components']} components"
        ),
    }
    last_built = heat_meta["last_built_quarter"]
    nowcast = heat_meta.get("nowcast_through")
    if nowcast and quarter_key(nowcast) > quarter_key(last_built):
        end, qualifier = nowcast, f" ({nowcast} nowcast)"
    else:
        end, qualifier = last_built, ""
    lines["heat_coverage"] = f"{start_quarter} – {end}{qualifier}"
    segments = {key.split("/")[0] for key in pulse_meta["indicators"]}
    lines["pulse_indicators"] = (
        f"{pulse_meta['n_indicators']} across {len(segments)} segments "
        f"· {pulse_meta['n_live']} live from public sources"
    )
    if momentum_meta is not None:
        groups = {key.split("/")[0] for key in momentum_meta["indicators"]}
        lines["momentum_indicators"] = (
            f"{momentum_meta['n_indicators']} archived series across "
            f"{len(groups)} groups "
            f"· {momentum_meta['n_live']} live from free public sources"
        )
    return lines


# (card heading, {spec label: line key}) for the landing-page cards whose
# spec lines are generated. Cards and labels not listed here are
# hand-written and are never touched.
LANDING_SECTIONS = (
    ("FRB Heat Index", {"Indicators": "heat_indicators",
                        "Coverage": "heat_coverage"}),
    ("Early Warning", {"Indicators": "pulse_indicators"}),
    ("Pulse of the Market", {"Indicators": "momentum_indicators"}),
)


def update_landing_page(path: Path, lines: dict[str, str]) -> bool:
    """Rewrite the generated spec lines in the landing page, in place.

    Only the <dd> text of the listed spec labels, inside the listed card
    sections, is replaced; everything else stays byte-identical. A missing
    card or label raises ValueError -- a page whose structure has moved
    away from the generator must fail the build loudly, not drift.
    Returns True when the file changed.
    """
    text = path.read_text()
    new = text
    for heading, specs in LANDING_SECTIONS:
        if not any(key in lines for key in specs.values()):
            # No generated lines for this card in this build (e.g. the
            # momentum layer has not been built): leave the card as
            # hand-written. When lines ARE supplied, a missing card or
            # label below still fails loudly.
            continue
        anchor = f"<h2>{heading}</h2>"
        start = new.find(anchor)
        if start < 0:
            raise ValueError(f"{path}: landing card {heading!r} not found")
        end = new.find("</section>", start)
        if end < 0:
            raise ValueError(f"{path}: landing card {heading!r} not closed")
        section = new[start:end]
        for label, key in specs.items():
            pattern = re.compile(r"(<dt>" + label + r"</dt><dd>).*?(</dd>)")
            section, n = pattern.subn(
                lambda m: m.group(1) + lines[key] + m.group(2),
                section, count=1)
            if n != 1:
                raise ValueError(
                    f"{path}: spec {label!r} not found in card {heading!r}")
        new = new[:start] + section + new[end:]
    if new == text:
        return False
    path.write_text(new)
    return True


# ---------------------------------------------------------------------
# Landing page: the "What is happening now" block.
#
# Every number below is computed from the committed data files at build
# time -- the aggregate and component series this module has just built,
# the panel meta (build date, nowcast provenance, observed freshness),
# and the prediction-markets layer (data/predmarkets/_meta.json for the
# front FOMC event and the recession ladder, the fomc_decision and
# recession CSVs for the latest closes). Nothing is copied from the
# page's current text, so the block cannot drift from the data.
# ---------------------------------------------------------------------

def _long_date(iso: str) -> str:
    """'2026-11-20' -> 'November 20, 2026'."""
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _month_day(iso: str) -> str:
    """'2026-12-09' -> 'December 9'."""
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.strftime('%B')} {d.day}"


def _pct(v: float) -> str:
    return f"{round(v * 100):.0f}%"


def _fomc_phrase(strike: str) -> str:
    """A Kalshi decision bucket label as prose: 'Hike 25bps' -> 'a 25 bp
    hike', 'Hike 0bps' -> 'no change to the target range'."""
    m = re.fullmatch(r"(Hike|Cut) (>)?(\d+)bps", strike or "")
    if not m:
        return strike
    side, gt, n = m.group(1), m.group(2), int(m.group(3))
    if n == 0:
        return "no change to the target range"
    if gt:
        return f"a {side.lower()} of more than {n} bp"
    return f"a {n} bp {side.lower()}"


def _predmarket_readings(data: Path) -> tuple[str, str]:
    """(markets sentence, as-of ISO date) from the predmarkets layer.

    The front FOMC event and the recession ladder come from the layer's
    own meta (the pipeline's discovery, not a hardcoded calendar); the
    probabilities are the latest closes in the per-indicator CSVs.
    """
    pm_dir = data / "predmarkets"
    meta = json.loads((pm_dir / "_meta.json").read_text())

    fomc = meta["indicators"]["fomc_decision"]
    front = fomc["horizons"][0]
    fdf = pl.read_csv(pm_dir / "fomc_decision.csv")
    frows = fdf.filter(pl.col("event") == front["event_ticker"])
    flast = frows["date"].max()
    fday = frows.filter(pl.col("date") == flast)
    modal = max(fday.iter_rows(named=True), key=lambda r: r["value"])

    rec = meta["indicators"]["recession"]
    ladder = rec["horizons"][-1]
    rdf = pl.read_csv(pm_dir / "recession.csv")
    rrows = rdf.filter(pl.col("event") == ladder["event_ticker"])
    rlast = rrows["date"].max()
    rday = rrows.filter(pl.col("date") == rlast)
    yes = rday.filter(pl.col("strike") == "Yes")
    rrow = (yes if yes.height else rday).row(0, named=True)

    asof = max(flast, rlast)
    sentence = (
        f"Prediction markets (Kalshi closes through {_long_date(asof)}) "
        f"give the highest probability, {_pct(modal['value'])}, to "
        f"{_fomc_phrase(modal['strike'])} at the "
        f"{_month_day(front['date'])} FOMC meeting. Recession odds for "
        f"{html_escape(str(ladder['label']))} stand at {_pct(rrow['value'])}."
    )
    return sentence, asof


def now_block_html(meta: dict, agg_rows: list[dict],
                   comp_json: dict[str, list[dict]], data: Path) -> str:
    """The landing page's 'What is happening now' section, as HTML.

    meta is the built site meta (freshness + nowcast blocks included);
    agg_rows / comp_json are the aggregate and per-component series this
    build has just produced; data is the data directory, for the
    prediction-markets layer.
    """
    pts = [r for r in agg_rows if r["v"] is not None]
    last, prev = pts[-1], pts[-2]
    delta = last["v"] - prev["v"]
    if delta <= -0.005:
        change = f"down from {prev['v']:.2f} in {prev['q']}"
    elif delta >= 0.005:
        change = f"up from {prev['v']:.2f} in {prev['q']}"
    else:
        change = f"little changed from {prev['v']:.2f} in {prev['q']}"
    headline = (f"The heat index stands at <strong>{last['v']:.2f}</strong> "
                f"in {last['q']}, {change}.")

    at_last = {}
    for name, rows in comp_json.items():
        vals = [r["v"] for r in rows if r["q"] == last["q"]]
        if vals and vals[0] is not None:
            at_last[name] = vals[0]
    ranked = sorted(at_last.items(), key=lambda kv: kv[1])
    hot = [f"{html_escape(n)} ({v:.2f})" for n, v in ranked[-2:]][::-1]
    cold = [f"{html_escape(n)} ({v:.2f})" for n, v in ranked[:2]]
    extremes = (f"Hottest components: {hot[0]} and {hot[1]}. "
                f"Coldest: {cold[0]} and {cold[1]}.")

    markets, asof = _predmarket_readings(data)

    caveat = [f"Quarterly panel built {_long_date(meta['built_at_utc'])}."]
    last_built = meta["last_built_quarter"]
    nowcast = meta.get("nowcast_through")
    if nowcast and quarter_key(nowcast) > quarter_key(last_built):
        nc = meta.get("nowcast") or {}
        counts = (nc.get("counts_by_quarter") or {}).get(nowcast) or {}
        fills = sum(counts.values())
        unext = sum(1 for u in nc.get("unextended", [])
                    if u["quarter"] == nowcast)
        if fills + unext >= meta["n_indicators"]:
            caveat.append(
                f"The {nowcast} column is entirely nowcast — no indicator "
                f"has an observed {nowcast} value; observations run "
                f"through {last_built}.")
        elif fills:
            caveat.append(
                f"The {nowcast} column carries nowcast fills for {fills} "
                f"of {meta['n_indicators']} series; observations run "
                f"through {last_built}.")
        fresh = meta.get("freshness")
        if fresh:
            caveat.append(
                f"{fresh['least_count']} of {fresh['n_series']} series "
                f"end at {fresh['observed_through']}.")

    return "\n".join([
        '  <section class="card now-card" aria-label="What is happening now">',
        f'    <p class="kicker">What is happening now · as of '
        f'{_long_date(asof)}</p>',
        f'    <p class="now-lede">{headline} {extremes}</p>',
        f'    <p>{markets}</p>',
        f'    <p class="now-note">{" ".join(caveat)}</p>',
        '  </section>',
    ])


def update_now_block(path: Path, block: str) -> bool:
    """Replace the landing page's NOW-BLOCK section with `block`.

    The block lives between the <!-- NOW-BLOCK:BEGIN ... --> and
    <!-- NOW-BLOCK:END --> markers; missing markers raise ValueError, so
    a page whose structure has moved away from the generator fails the
    build loudly rather than drifting. Returns True when the file
    changed.
    """
    text = path.read_text()
    begin = text.find("<!-- NOW-BLOCK:BEGIN")
    end = text.find("<!-- NOW-BLOCK:END -->")
    if begin < 0 or end < 0 or end < begin:
        raise ValueError(f"{path}: NOW-BLOCK markers not found")
    line_end = text.find("\n", begin)
    if line_end < 0:
        raise ValueError(f"{path}: NOW-BLOCK begin marker not terminated")
    new = text[:line_end + 1] + block + "\n" + text[end:]
    if new == text:
        return False
    path.write_text(new)
    return True


def build(data: Path, out: Path) -> dict:
    """Build all site JSON files; return the meta dict that was written."""
    out.mkdir(parents=True, exist_ok=True)

    agg = pl.read_csv(data / "aggregate.csv")
    agg_json = series_json(agg, "Aggregate index")
    (out / "aggregate.json").write_text(dumps_strict(agg_json, indent=1))

    comp = pl.read_csv(data / "components.csv")
    comp_cols = [c for c in comp.columns if c != "quarter"]
    comp_json = {c: series_json(comp, c) for c in comp_cols}
    (out / "components.json").write_text(dumps_strict(comp_json, indent=1))

    # Oriented standardized indicators (the actual component inputs).
    ind_path = data / "indicators_standardized.csv"
    ind_json: dict[str, list[dict]] = {}
    if ind_path.exists():
        ind = pl.read_csv(ind_path)
        for c in ind.columns:
            if c == "quarter":
                continue
            ind_json[c] = [{"q": q, "z": json_value(v)}
                           for q, v in ind.select(["quarter", c]).iter_rows()]
        (out / "indicators.json").write_text(dumps_strict(ind_json, indent=1))
        print(f"Wrote {out / 'indicators.json'} ({len(ind_json)} indicators)")

        # Clustered-view bundle: row order + dendrogram structure computed
        # by the pipeline (never in the browser). Computed from the same
        # standardized frame the site ships; loud failure by design.
        clustered = compute_bundle(ind)
        (out / "clustered.json").write_text(dumps_strict(clustered, indent=1) + "\n")
        print(f"Wrote {out / 'clustered.json'} "
              f"({len(clustered['leaves'])} indicators, "
              f"{len(clustered['cut'])} top-level groups)")

    # Hierarchy: categories -> components -> indicators (+ short names).
    # Category assignment mirrors the paper's three broad vulnerability
    # categories, in the paper's component order.
    comp_of = {name: SPEC[name][0] for name in SPEC if name in SHORT_NAMES}
    hierarchy = {"categories": []}
    for cat in CATEGORIES:
        cat_entry = {"name": cat["name"], "blurb": cat["blurb"],
                     "components": []}
        for cname in cat["components"]:
            inds = sorted(
                (name for name, comp in comp_of.items() if comp == cname),
                key=list(SPEC).index,
            )
            cat_entry["components"].append({
                "name": cname,
                "indicators": [{"name": n, "short": SHORT_NAMES[n]}
                               for n in inds],
            })
        hierarchy["categories"].append(cat_entry)
    (out / "hierarchy.json").write_text(dumps_strict(hierarchy, indent=1))
    print(f"Wrote {out / 'hierarchy.json'}")

    quarters = comp["quarter"].to_list()
    meta = json.loads((data / "_meta.json").read_text())
    # Site contract (docs/assets/app.js renderMeta): data-derived fields are
    # authoritative from the CSVs actually shipped; staleness flags stay with
    # the pipeline that authored them in data/_meta.json.
    meta["components"] = comp_cols
    meta["last_updated_quarter"] = quarters[-1] if quarters else None
    meta["start_quarter"] = quarters[0] if quarters else None
    meta["n_quarters"] = len(quarters)
    meta.setdefault("stale_sources", [])
    # Freshness for the page banner: the least-recent OBSERVED series.
    # Computed from the raw quarterly panel -- the standardized panel
    # carries nowcast extensions to the panel end for most series, which
    # would mask real staleness (observed ends are what a reader means
    # by "data through").
    meta["freshness"] = None
    raw_p = data / "panel_quarterly.csv"
    if raw_p.exists():
        raw = pl.read_csv(raw_p)
        raw_qs = raw["quarter"].to_list()
        ends = {}
        for c in raw.columns:
            if c == "quarter":
                continue
            vals = raw[c].to_list()
            obs = [i for i, v in enumerate(vals) if v is not None]
            if obs:
                ends[c] = raw_qs[max(obs)]
        if ends:
            least_q = min(ends.values())
            least = [c for c, q in ends.items() if q == least_q]
            meta["freshness"] = {
                "observed_through": least_q,
                "least_series": least[0],
                "least_count": len(least),
                "n_series": len(ends),
            }
    # Nowcast flagging for the bundle: which quarters of panel_complete.csv
    # are fills rather than observations, per data/nowcast_provenance.csv,
    # and which cells remain unextended, per data/panel_complete_flags.csv.
    # The freshness block above stays observed-only; this block is what lets
    # a reader (or a chart) tell a nowcast quarter from an observed one.
    meta["nowcast"] = None
    prov_p, flags_p = data / "nowcast_provenance.csv", \
        data / "panel_complete_flags.csv"
    if prov_p.exists():
        prov = pl.read_csv(prov_p)
        by_quarter: dict[str, dict[str, int]] = {}
        for r in prov.iter_rows(named=True):
            counts = by_quarter.setdefault(r["quarter"], {})
            counts[r["value_source"]] = counts.get(r["value_source"], 0) + 1
        unextended = []
        if flags_p.exists():
            flags = pl.read_csv(flags_p)
            fcols = [c for c in flags.columns if c != "quarter"]
            for r in flags.iter_rows(named=True):
                for c in fcols:
                    if r[c] == "unextended":
                        unextended.append(
                            {"quarter": r["quarter"], "indicator": c})
        meta["nowcast"] = {
            "quarters": sorted(by_quarter),
            "counts_by_quarter": {q: by_quarter[q]
                                  for q in sorted(by_quarter)},
            "unextended": unextended,
        }
    (out / "meta.json").write_text(dumps_strict(meta, indent=1))

    # Indicator provenance metadata (Pulse-parity bundle)
    if (data / "panel_complete.csv").exists():
        from indicators_meta import build_indicators_meta
        ind_meta = build_indicators_meta(data)
        (out / "indicators_meta.json").write_text(dumps_strict(ind_meta, indent=1) + "\n")
        print(f"Wrote {out / 'indicators_meta.json'} ({len(ind_meta['indicators'])} indicators)")

    # Landing page: regenerate the product cards' data-derived spec lines
    # from the two metas, so the page cannot drift from the data at the
    # next expansion. Only runs when the page sits beside the output dir
    # (the real site build); fixture builds into scratch dirs skip it.
    landing = out.parent / "index.html"
    if landing.exists():
        pulse_meta = json.loads((data / "pulse" / "_meta.json").read_text())
        momentum_meta_path = data / "momentum" / "_meta.json"
        momentum_meta = (json.loads(momentum_meta_path.read_text())
                         if momentum_meta_path.exists() else None)
        spec_lines = landing_spec_lines(meta, pulse_meta, quarters[0],
                                        momentum_meta)
        if update_landing_page(landing, spec_lines):
            print(f"Wrote {landing} (spec lines from data/_meta.json, "
                  f"data/pulse/_meta.json and data/momentum/_meta.json)")
        block = now_block_html(meta, agg_json, comp_json, data)
        if update_now_block(landing, block):
            print(f"Wrote {landing} (now block from the panel and "
                  f"data/predmarkets/)")

    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(HERE / "data"))
    ap.add_argument("--out", default=str(HERE / "docs" / "data"))
    args = ap.parse_args()

    meta = build(Path(args.data), Path(args.out))
    out = Path(args.out)
    print(f"wrote {out / 'aggregate.json'}")
    print(f"wrote {out / 'components.json'} ({len(meta['components'])} components)")
    print(f"wrote {out / 'meta.json'} "
          f"({meta['start_quarter']}..{meta['last_updated_quarter']}, "
          f"{meta['n_quarters']} quarters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
