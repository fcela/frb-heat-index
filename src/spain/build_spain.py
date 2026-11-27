"""Build the Spain heat index: fetch, archive, panel, percentiles, site.

Construction (docs/spain/methodology.md states it in full):
- Quarterly panel from 1999Q1. Each indicator is oriented so higher
  means more vulnerable, then percentile-transformed with the
  empirical CDF (midrank) against its own Spanish history.
- A component is the mean of its available indicators that quarter;
  the headline is the mean of the components that have at least one
  available indicator. Source-pending components never enter.
- Nothing is interpolated, filled or spliced. A quarter a source does
  not publish is a missing value in the panel.

Usage: python3 -m src.spain.build_spain [--no-fetch]
Without --no-fetch every route in the registry is exercised live and
the raw series are re-archived under data/spain/.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parent.parent.parent
sys.path.insert(0, str(REPO))

from src.spain import fetch  # noqa: E402
from src.spain.series import COMPONENTS, INDICATORS, PANEL_START  # noqa: E402

DATA_DIR = REPO / "data" / "spain"
DOCS_DATA = REPO / "docs" / "data"
PAGE_DIR = REPO / "docs" / "spain"


# ------------------------------------------------------------ quarters

def qidx(q: str) -> int:
    y, n = q.split("Q")
    return int(y) * 4 + int(n) - 1


def qlabel(i: int) -> str:
    return f"{i // 4}Q{i % 4 + 1}"


def qrange(start: str, end: str) -> list:
    return [qlabel(i) for i in range(qidx(start), qidx(end) + 1)]


def quarter_end(monthly: dict) -> dict:
    """Monthly {'YYYY-MM': v} -> quarterly, value of the 3rd month."""
    out = {}
    for p, v in monthly.items():
        y, m = p.split("-")
        if int(m) % 3 == 0:
            out[f"{y}Q{int(m) // 3}"] = v
    return out


def quarter_mean(periods: dict) -> dict:
    """Monthly or daily dict -> quarterly mean of available obs."""
    acc = {}
    for p, v in periods.items():
        if len(p) == 7:      # monthly
            y, m = p.split("-")
            q = f"{y}Q{(int(m) - 1) // 3 + 1}"
        else:                # daily
            q = f"{p[:4]}Q{(int(p[5:7]) - 1) // 3 + 1}"
        acc.setdefault(q, []).append(v)
    return {q: sum(vs) / len(vs) for q, vs in acc.items()}


def quarter_sum(monthly: dict) -> dict:
    """Monthly {'YYYY-MM': v} -> quarterly sums of available months.

    Used for new-business volume shares: numerator and denominator
    are published with the same lag, so a partial final quarter
    understates both and the share is unaffected.
    """
    acc = {}
    for p, v in monthly.items():
        y, m = p.split("-")
        q = f"{y}Q{(int(m) - 1) // 3 + 1}"
        acc[q] = acc.get(q, 0.0) + v
    return acc


def sum4(qseries: dict, q: str):
    """Trailing four-quarter sum ending at q; None if any is missing."""
    i = qidx(q)
    vals = [qseries.get(qlabel(i - k)) for k in range(4)]
    if any(v is None for v in vals):
        return None
    return sum(vals)


# ------------------------------------------------------------ fetching

def fetch_primary(route: dict) -> dict:
    t = route["type"]
    if t == "bis_sdmx":
        return fetch.fetch_bis_sdmx(route["url"])
    if t == "bis_tc":
        return fetch.fetch_bis_tc(route["url"])
    if t == "fred_yoy":
        return fetch.fetch_fred(route["url"])
    if t == "ecb_ciss":
        return fetch.fetch_ecb_ciss(route["url"])
    if t == "ecb_sdmx_q":
        return fetch.fetch_ecb_sdmx_q(route["url"])
    if t == "ine":
        return fetch.fetch_ine(route["url"])
    if t == "bde_column":
        from src.spain.series import BDE_TABLE_URL
        return fetch.fetch_bde_column(
            BDE_TABLE_URL.format(table=route["table"]), route["alias"])
    raise ValueError(f"unknown route type {t}")


def fetch_input(inp: dict) -> dict:
    if "table" in inp:
        from src.spain.series import BDE_TABLE_URL
        return fetch.fetch_bde_column(
            BDE_TABLE_URL.format(table=inp["table"]), inp["alias"])
    if "eurostat" in inp:
        from src.spain.series import EUROSTAT_GOV_URL
        return fetch.fetch_eurostat(
            EUROSTAT_GOV_URL.format(item=inp["eurostat"]))
    from src.spain.series import FRED_URL
    return fetch.fetch_fred(FRED_URL.format(sid=inp["fred"]))


# Unit handling is explicit per input: the CFEE tables (2.5, 2.7) are
# published in thousands of euros and carry "scale": 0.001 in the
# registry to normalise to millions; the ch. 7/8 balance tables and
# the national accounts are already in millions (scale 1, default).
def _scaled(inp: dict, series: dict) -> dict:
    s = inp.get("scale", 1.0)
    if s == 1.0:
        return series
    return {k: v * s for k, v in series.items()}


def build_indicator_series(ind: dict, archive_dir: Path, log: list) -> dict:
    """Return the indicator's quarterly raw series {'YYYYQn': value}."""
    route = ind["route"]
    if route["type"] != "derived":
        raw = fetch_primary(route)
        fetch.write_archive(archive_dir, route["archive"], raw)
        log.append(f"{ind['id']}: {len(raw)} obs "
                   f"({min(raw) if raw else '-'}..{max(raw) if raw else '-'})")
        if route["type"] == "fred_yoy":
            out = {}
            for q, v in raw.items():
                prev = raw.get(qlabel(qidx(q) - 4))
                if prev:
                    out[q] = 100.0 * (v / prev - 1.0)
            return out
        if route["type"] == "ecb_ciss":
            return quarter_mean(raw)
        return raw

    inputs = {}
    for inp in route["inputs"]:
        s = _scaled(inp, fetch_input(inp))
        fetch.write_archive(archive_dir, inp["archive"], s)
        inputs[inp["archive"]] = s
    return DERIVED[ind["id"]](inputs)


def _ratio_pct(num_q: dict, den_q: dict) -> dict:
    return {q: 100.0 * num_q[q] / den_q[q]
            for q in num_q.keys() & den_q.keys() if den_q[q]}


def _share_of_gdp(stock_m: dict, gdp_q: dict) -> dict:
    stock_q = quarter_end(stock_m)
    out = {}
    for q, v in stock_q.items():
        g = sum4(gdp_q, q)
        if g:
            out[q] = 100.0 * v / g
    return out


def _d_equity_ratio(inp):
    cap = quarter_end(inp["bde_be0832_capital.csv"])
    tot = quarter_end(inp["bde_be0832_total_liabilities.csv"])
    return _ratio_pct(cap, tot)


def _d_loans_deposits(inp):
    loans = quarter_end(inp["bde_be0833_loans_nonbank.csv"])
    deps = quarter_end(inp["bde_be0834_deposits_osr.csv"])
    return _ratio_pct(loans, deps)


def _d_fin_liab_gdp(inp):
    liab = inp["bde_be0205_fin_liabilities.csv"]
    gdp = inp["bde_be0201_gdp.csv"]
    return {q: 100.0 * liab[q] / sum4(gdp, q)
            for q in liab if sum4(gdp, q)}


def _d_mortgage_gdp(inp):
    return _share_of_gdp(inp["bde_be0819_mortgage_loans.csv"],
                         inp["bde_be0201_gdp.csv"])


def _d_consumer_gdp(inp):
    return _share_of_gdp(inp["bde_be0819_consumer_loans.csv"],
                         inp["bde_be0201_gdp.csv"])


def _d_wholesale_share(inp):
    sec = quarter_end(inp["bde_be0803_securities_issued.csv"])
    tot = quarter_end(inp["bde_be0803_total_liabilities.csv"])
    return _ratio_pct(sec, tot)


def _d_eurosystem_share(inp):
    lend = quarter_end(inp["bde_be0703_lending_to_mfis.csv"])
    tot = quarter_end(inp["bde_be0803_total_liabilities.csv"])
    return _ratio_pct(lend, tot)


def _d_bono_bund(inp):
    es, de = inp["fred_es_10y.csv"], inp["fred_de_10y.csv"]
    spread_m = {p: 100.0 * (es[p] - de[p]) for p in es.keys() & de.keys()}
    return quarter_mean(spread_m)


def _d_nexus_holdings(inp):
    gov = quarter_end(inp["bde_be0805_gov_securities.csv"])
    cap = quarter_end(inp["bde_be0803_capital_reserves.csv"])
    return _ratio_pct(gov, cap)


def _d_interest_burden(inp):
    interest = inp["eurostat_gov_interest.csv"]
    revenue = inp["eurostat_gov_revenue.csv"]
    out = {}
    for q in interest:
        si, sr = sum4(interest, q), sum4(revenue, q)
        if si is not None and sr:
            out[q] = 100.0 * si / sr
    return out


def _d_tourism_gdp(inp):
    # Both numerator and denominator are flows: trailing four
    # quarters of receipts over trailing four quarters of GDP.
    receipts = inp["bde_be1704_tourism_receipts.csv"]
    gdp = inp["bde_be0201_gdp.csv"]
    out = {}
    for q in receipts:
        sr, sg = sum4(receipts, q), sum4(gdp, q)
        if sr is not None and sg:
            out[q] = 100.0 * sr / sg
    return out


def _d_var_rate_share(inp):
    le1y = quarter_sum(inp["bde_be1912_mortgage_vol_le1y.csv"])
    total = quarter_sum(inp["bde_be1912_mortgage_vol_total.csv"])
    return _ratio_pct(le1y, total)


DERIVED = {
    "equity_ratio": _d_equity_ratio,
    "loans_deposits": _d_loans_deposits,
    "fin_liab_gdp": _d_fin_liab_gdp,
    "mortgage_gdp": _d_mortgage_gdp,
    "consumer_gdp": _d_consumer_gdp,
    "wholesale_share": _d_wholesale_share,
    "eurosystem_share": _d_eurosystem_share,
    "bono_bund_spread": _d_bono_bund,
    "nexus_holdings": _d_nexus_holdings,
    "interest_burden": _d_interest_burden,
    "tourism_gdp": _d_tourism_gdp,
    "var_rate_share": _d_var_rate_share,
}


# ------------------------------------------------------------ transform

def empirical_cdf(values: list) -> list:
    """Midrank empirical CDF of each non-null value against the
    series' own history; nulls stay null."""
    xs = sorted(v for v in values if v is not None)
    n = len(xs)
    out = []
    import bisect
    for v in values:
        if v is None or n == 0:
            out.append(None)
        else:
            lo = bisect.bisect_left(xs, v)
            hi = bisect.bisect_right(xs, v)
            out.append((lo + 0.5 * (hi - lo)) / n)
    return out


def mean_available(rows: list) -> list:
    out = []
    for vals in zip(*rows) if rows else []:
        live = [v for v in vals if v is not None]
        out.append(sum(live) / len(live) if live else None)
    return out


# ------------------------------------------------------------ svg

def svg_chart(series: list, quarters: list, w=330, h=110) -> str:
    """Small multiple on a shared 0-1 axis: area fill, straight line
    segments, square corners. Gaps break the path (never bridged)."""
    pad_l, pad_b, pad_t = 26, 14, 4
    iw, ih = w - pad_l - 4, h - pad_b - pad_t
    n = len(quarters)

    def x(i):
        return pad_l + (iw * i / (n - 1) if n > 1 else 0)

    def y(v):
        return pad_t + ih * (1 - v)

    runs, cur = [], []
    for i, v in enumerate(series):
        if v is None:
            if cur:
                runs.append(cur)
                cur = []
        else:
            cur.append(i)
    if cur:
        runs.append(cur)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
             f'aria-label="chart, values 0 to 1">']
    for gy in (0.0, 0.5, 1.0):
        parts.append(f'<line x1="{pad_l}" y1="{y(gy):.1f}" x2="{w - 4}" '
                     f'y2="{y(gy):.1f}" stroke="var(--grid,#ddd)" '
                     f'stroke-width="0.5"/>')
        parts.append(f'<text x="{pad_l - 3}" y="{y(gy) + 3:.1f}" '
                     f'font-size="8" text-anchor="end" '
                     f'fill="var(--muted,#777)">{gy:g}</text>')
    for run in runs:
        if len(run) < 2:
            continue
        pts = " ".join(f"{x(i):.1f},{y(series[i]):.2f}" for i in run)
        area = (f"{x(run[0]):.1f},{y(0):.1f} " + pts +
                f" {x(run[-1]):.1f},{y(0):.1f}")
        parts.append(f'<polygon points="{area}" fill="#b2182b" '
                     f'fill-opacity="0.18"/>')
        parts.append(f'<polyline points="{pts}" fill="none" '
                     f'stroke="#b2182b" stroke-width="1.2"/>')
    for i, q in enumerate(quarters):
        if q.endswith("Q1") and (i % 20 == 0 or i == 0):
            parts.append(f'<text x="{x(i):.1f}" y="{h - 2}" font-size="8" '
                         f'fill="var(--muted,#777)">{q[:4]}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ------------------------------------------------------------ page

PAGE_CSS = """
.sp-wrap { max-width: 1180px; margin: 0 auto; padding: 0 1.25rem; }
.sp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 1rem; }
.sp-card { border: 1px solid var(--border, #ddd); padding: 0.75rem 0.9rem; }
.sp-card h4 { margin: 0 0 0.25rem; font-size: 0.98rem; }
.sp-prov { font-size: 0.78rem; line-height: 1.45; color: var(--muted, #555); margin-top: 0.4rem; }
.sp-prov a { color: inherit; }
.sp-latest { font-size: 0.85rem; margin: 0.15rem 0 0.3rem; }
.sp-pending { font-size: 0.85rem; line-height: 1.5; }
"""


def render_page(bundle: dict, meta_indicators: list) -> str:
    quarters = bundle["quarters"]
    by_comp = {}
    for ind in meta_indicators:
        by_comp.setdefault(ind["component"], []).append(ind)
    pct = bundle["indicators"]
    peak_i = max((i for i, v in enumerate(bundle["headline"])
                  if v is not None),
                 key=lambda i: bundle["headline"][i])
    last_i = max(i for i, v in enumerate(bundle["headline"])
                 if v is not None)
    live_n = sum(1 for m in meta_indicators if m["status"] != "source-pending")
    pend_n = len(meta_indicators) - live_n
    comp_live = [c for c in COMPONENTS
                 if any(v is not None for v in bundle["components"][c])]

    out = []
    out.append(f"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spain Heat Index (experimental) — frb-heat-index</title>
<meta name="description" content="Experimental financial-system heat index for Spain: quarterly vulnerability percentiles by component, built from free public data (BIS, Banco de España, INE, ECB, Eurostat, FRED). Not linked from the site index; reachable by direct URL only.">
<link rel="stylesheet" href="../assets/style.css">
<link rel="stylesheet" href="../assets/pulse.css">
<style>{PAGE_CSS}</style>
<script>
  try {{
    var t = localStorage.getItem("frb-heat-theme");
    if (t === "dark" || (t === null && matchMedia("(prefers-color-scheme: dark)").matches))
      document.documentElement.dataset.theme = "dark";
  }} catch (e) {{}}
</script>
</head>
<body>
<div class="tape-band">
  <nav class="nav" aria-label="Sections">
    <div class="nav-inner">
      <span class="brand"><a href="../" style="color:inherit;text-decoration:none">frb-heat-index</a> <span class="dot">●</span></span>
      <div class="nav-links">
        <a href="../momentum/">Pulse of the Market</a>
        <a href="../pulse/">Early Warning</a>
        <a href="../frb/">FRB Heat Index</a>
        <a href="../predmarkets/">Prediction Markets</a>
      </div>
      <button class="theme-toggle" id="theme-toggle" aria-label="Toggle dark mode" title="Toggle dark mode">◐</button>
    </div>
  </nav>
  <header class="pulse-hero">
    <p class="kicker">Spain heat index · experimental section</p>
    <h1>Spain: where the financial system stands</h1>
    <p class="lede">An experimental second country section: the U.S. heat
      index construction applied to Spain, by analogy, component by
      component — including one component the U.S. index does not have
      (the sovereign–bank nexus) and several it cannot have filled the
      same way. This page is an experiment: it is not linked from the
      site index or the navigation and is reachable only by direct URL.
      Every indicator is percentile-transformed against its own Spanish
      history, so 0.9 means "higher than 90% of Spain's own past", not a
      comparison with the United States. Data through
      {quarters[last_i]}; headline {bundle['headline'][last_i]:.3f},
      against a sample peak of {bundle['headline'][peak_i]:.3f} in
      {quarters[peak_i]}. {live_n} indicators live, {pend_n}
      source-pending, {len(comp_live)} of {len(COMPONENTS)} components
      in the headline. Construction, the analogy and its limits:
      <a href="methodology.md">methodology</a>.</p>
  </header>
</div>
<main>
  <div class="sp-wrap">
    <section>
      <h3>Headline index</h3>
      <p class="blurb">Mean of the component percentiles below. Spain's
        sample contains a full systemic crisis (2010–13), so the scale
        is calibrated by a real event.</p>
      <div class="sp-card">
        {svg_chart(bundle['headline'], quarters, w=1060, h=220)}
        <p class="sp-prov">Source: computed from the component series
          below; per-indicator provenance is on each panel. Method:
          <a href="methodology.md">methodology</a>. Data:
          <code>../data/spain.json</code>; archived series under
          <code>data/spain/</code>.</p>
      </div>
    </section>
    <section>
      <h3>Components</h3>
      <div class="sp-grid">""")
    for comp in COMPONENTS:
        series = bundle["components"][comp]
        if all(v is None for v in series):
            out.append(f"""
        <div class="sp-card"><h4>{comp}</h4>
        <p class="sp-pending">Source-pending: every indicator in this
        component is listed below with the reason. The component does
        not enter the headline until at least one indicator is
        sourced.</p></div>""")
            continue
        li = max(i for i, v in enumerate(series) if v is not None)
        out.append(f"""
        <div class="sp-card"><h4>{comp}</h4>
        <p class="sp-latest">Latest: {series[li]:.3f} ({quarters[li]})</p>
        {svg_chart(series, quarters)}
        <p class="sp-prov">Mean of this component's available indicator
        percentiles (0–1, own history). Indicators below.</p></div>""")
    out.append("""
      </div>
    </section>""")

    for comp in COMPONENTS:
        inds = by_comp.get(comp, [])
        if not inds:
            continue
        out.append(f"""
    <section>
      <h3>{comp} — indicators</h3>
      <div class="sp-grid">""")
        for ind in inds:
            if ind["status"] == "source-pending":
                out.append(f"""
        <div class="sp-card"><h4>{ind['title']}</h4>
        <p class="sp-pending"><strong>Source-pending.</strong>
        {ind.get('pending_reason', '')}</p>
        <p class="sp-prov">Definition: {ind['definition']}<br>
        Frequency: {ind['frequency']} · Source (intended):
        {ind['source']}</p></div>""")
                continue
            p = pct[ind["id"]]["percentile"]
            li = max(i for i, v in enumerate(p) if v is not None)
            src_link = (f'<a href="{ind["source_url"]}">{ind["source"]}</a>'
                        if ind.get("source_url") else ind["source"])
            out.append(f"""
        <div class="sp-card"><h4>{ind['title']}</h4>
        <p class="sp-latest">Latest raw value:
        {ind['last_value']:.2f} ({ind['last_observation']}) ·
        percentile {p[li]:.3f}</p>
        {svg_chart(p, quarters)}
        <p class="sp-prov">Source: {src_link}<br>
        Series: {', '.join(ind['series_id'])}<br>
        Definition: {ind['definition']}<br>
        Frequency: {ind['frequency']} · Observations:
        {ind['first_observation']}–{ind['last_observation']}
        (n={ind['n_observations']}) · Chart: percentile against own
        history, 0–1.</p></div>""")
        out.append("""
      </div>
    </section>""")

    out.append(f"""
    <div class="pulse-foot">
      <p>Experimental section; not linked from the site index.
      Construction and the full analogy — what transfers from the U.S.
      index, what is Spain-specific, what is a labelled proxy and what
      does not transfer — are stated in the
      <a href="methodology.md">methodology</a>. Data:
      <code>../data/spain.json</code> and
      <code>../data/spain_meta.json</code>; archived series under
      <code>data/spain/</code>; build:
      <code>src/spain/build_spain.py</code>. Free public sources only:
      BIS, Banco de España, INE, ECB, Eurostat, FRED. Built
      {bundle['built_at_utc']}.</p>
    </div>
  </div>
</main>
</body>
</html>""")
    return "".join(out)


# ------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-fetch", action="store_true",
                    help="fail (archives are written by the fetch pass)")
    args = ap.parse_args()
    if args.no_fetch:
        print("--no-fetch is not supported: the build always exercises "
              "the live routes", file=sys.stderr)
        return 2

    built_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = []
    raw_by_id = {}
    for ind in INDICATORS:
        if ind["status"] == "source-pending":
            continue
        series = build_indicator_series(ind, DATA_DIR, log)
        raw_by_id[ind["id"]] = series
        if ind["route"]["type"] == "derived":
            fetch.write_archive(DATA_DIR, ind["route"]["archive"], series)
    for line in log:
        print(" ", line)

    # Panel end: the latest quarter in which at least 60% of the live
    # indicators have an observation. Faster sources (daily CISS,
    # monthly balance sheets) run ahead of the quarterly ones (BIS,
    # financial accounts); the rule keeps the tail from resting on a
    # minority of the panel, and drops partial quarters.
    live_ids = [i["id"] for i in INDICATORS if i["status"] != "source-pending"]
    cand = sorted({q for s in raw_by_id.values() for q in s}, reverse=True)
    last_q = next(q for q in cand
                  if sum(1 for i in live_ids if q in raw_by_id[i])
                  >= 0.6 * len(live_ids))
    quarters = qrange(PANEL_START, last_q)

    panel = {"quarter": quarters}
    pct_by_id = {}
    meta_indicators = []
    for ind in INDICATORS:
        entry = {k: ind[k] for k in
                 ("id", "title", "short", "component", "status", "source",
                  "source_url", "series_id", "definition", "frequency")}
        if ind["status"] == "source-pending":
            entry["pending_reason"] = ind["pending_reason"]
            entry.update({"first_observation": None,
                          "last_observation": None, "last_value": None,
                          "n_observations": 0})
            panel[ind["id"]] = [None] * len(quarters)
        else:
            s = raw_by_id[ind["id"]]
            vals = [s.get(q) for q in quarters]
            panel[ind["id"]] = vals
            oriented = [None if v is None else ind["sign"] * v
                        for v in vals]
            pct_by_id[ind["id"]] = empirical_cdf(oriented)
            obs = [(q, v) for q, v in zip(quarters, vals) if v is not None]
            entry.update({
                "first_observation": obs[0][0],
                "last_observation": obs[-1][0],
                "last_value": obs[-1][1],
                "n_observations": len(obs)})
        meta_indicators.append(entry)

    components = {}
    for comp in COMPONENTS:
        rows = [pct_by_id[ind["id"]] for ind in INDICATORS
                if ind["component"] == comp and ind["id"] in pct_by_id]
        components[comp] = mean_available(rows) if rows else \
            [None] * len(quarters)
    headline = mean_available([components[c] for c in COMPONENTS])

    bundle = {
        "generated_by": "python3 -m src.spain.build_spain",
        "built_at_utc": built_at,
        "panel_first_quarter": quarters[0],
        "panel_last_quarter": quarters[-1],
        "quarters": quarters,
        "headline": headline,
        "components": components,
        "indicators": {i: {"raw": panel[i],
                           "percentile": pct_by_id.get(i)}
                       for i in panel if i != "quarter"},
    }
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    (DOCS_DATA / "spain.json").write_text(
        json.dumps(bundle, indent=1), encoding="utf-8")
    meta = {
        "_comment": "GENERATED — DO NOT HAND-EDIT: python3 -m src.spain.build_spain",
        "generated_by": "python3 -m src.spain.build_spain",
        "generated_from": ["data/spain/ (archived series)"],
        "panel_first_quarter": quarters[0],
        "panel_last_quarter": quarters[-1],
        "indicators": meta_indicators,
    }
    (DOCS_DATA / "spain_meta.json").write_text(
        json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")

    # panel CSV in the repo data dir, beside the archives
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    ids = [ind["id"] for ind in INDICATORS]
    w.writerow(["quarter"] + ids)
    for r, q in enumerate(quarters):
        w.writerow([q] + ["" if panel[i][r] is None else panel[i][r]
                           for i in ids])
    (DATA_DIR / "panel_quarterly.csv").write_text(
        buf.getvalue(), encoding="utf-8")
    (DATA_DIR / "_meta.json").write_text(json.dumps({
        "section": "spain",
        "built_at_utc": built_at,
        "panel_first_quarter": quarters[0],
        "panel_last_quarter": quarters[-1],
        "fetch_log": log,
    }, indent=1), encoding="utf-8")

    PAGE_DIR.mkdir(parents=True, exist_ok=True)
    (PAGE_DIR / "index.html").write_text(
        render_page(bundle, meta_indicators), encoding="utf-8")

    peak_i = max((i for i, v in enumerate(headline) if v is not None),
                 key=lambda i: headline[i])
    print(f"panel {quarters[0]}..{quarters[-1]}; "
          f"headline peak {headline[peak_i]:.3f} at {quarters[peak_i]}")
    for probe in ("2007Q4", "2019Q4", quarters[-1]):
        if probe in quarters:
            print(f"  headline {probe}: "
                  f"{headline[quarters.index(probe)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
