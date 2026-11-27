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
import html as _html
import json
import math
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
    if t == "bis_cpp":
        return fetch.fetch_bis_cpp(route["url"])
    if t == "fred_yoy":
        return fetch.fetch_fred(route["url"])
    if t == "eurostat_q":
        return fetch.fetch_eurostat(route["url"])
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
    if "eurostat_url" in inp:
        return fetch.fetch_eurostat(inp["eurostat_url"])
    if "eurostat_m_url" in inp:
        return fetch.fetch_eurostat_m(inp["eurostat_m_url"])
    if "ine" in inp:
        from src.spain.series import INE_SERIES_URL
        return fetch.fetch_ine(INE_SERIES_URL.format(code=inp["ine"]))
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
        if route["type"] in ("fred_yoy", "bis_cpp"):
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


def _d_construction_permits(inp):
    # Monthly permits index -> quarterly mean (the panel's monthly
    # flow/price convention).
    return quarter_mean(inp["eurostat_permits_residential.csv"])


def _d_price_income(inp):
    ipv = inp["ine_ipv_level.csv"]
    b6g = inp["eurostat_hh_disposable_income.csv"]
    emp = inp["eurostat_employment_total.csv"]
    out = {}
    for q in ipv.keys() & b6g.keys() & emp.keys():
        if emp[q]:
            # B6G is in millions of euro, employment in thousands of
            # persons: their ratio is thousands of euro of quarterly
            # disposable income per employed person.
            out[q] = ipv[q] / (b6g[q] / emp[q])
    return out


def _d_tedr_spread(inp):
    tedr = inp["bde_be1903_tedr_nfc.csv"]
    bench = inp["fred_ea_interbank_3m.csv"]
    spread_m = {p: 100.0 * (tedr[p] - bench[p])
                for p in tedr.keys() & bench.keys()}
    return quarter_mean(spread_m)


def _d_nonres_share(inp):
    return _ratio_pct(inp["bde_be1113_gov_debt_nonres.csv"],
                      inp["bde_be1113_gov_debt_total.csv"])


def _d_bde_share(inp):
    return _ratio_pct(inp["bde_be1113_gov_debt_bde.csv"],
                      inp["bde_be1113_gov_debt_total.csv"])


def _d_refi_short_share(inp):
    sec = inp["bde_be1113_gov_debt_sec_1y.csv"]
    loans = inp["bde_be1113_gov_debt_loans_1y.csv"]
    total = inp["bde_be1113_gov_debt_total.csv"]
    due = {q: sec[q] + loans[q] for q in sec.keys() & loans.keys()}
    return _ratio_pct(due, total)


def _d_niip_gdp(inp):
    niip = inp["bde_be1721_niip_net.csv"]
    gdp = inp["bde_be0201_gdp.csv"]
    return {q: 100.0 * niip[q] / sum4(gdp, q)
            for q in niip if sum4(gdp, q)}


def _d_fla_share(inp):
    return _ratio_pct(inp["bde_be1307_ccaa_fund_loans.csv"],
                      inp["bde_be1307_ccaa_debt_total.csv"])


def _d_tourism_emp_share(inp):
    return _ratio_pct(inp["eurostat_employment_hospitality.csv"],
                      inp["eurostat_employment_total.csv"])


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
    "construction_credit": _d_construction_permits,
    "price_income": _d_price_income,
    "tedr_spread": _d_tedr_spread,
    "nonres_share": _d_nonres_share,
    "bde_share": _d_bde_share,
    "refi_short_share": _d_refi_short_share,
    "niip_gdp": _d_niip_gdp,
    "fla_share": _d_fla_share,
    "tourism_emp_share": _d_tourism_emp_share,
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

def svg_chart(series: list, quarters: list, w=330, h=110,
              shade=None) -> str:
    """Small multiple on a shared 0-1 axis: area fill, straight line
    segments, square corners. Gaps break the path (never bridged).
    shade=(first_i, last_i, label) shades a quarter span behind the
    line (the headline chart's crisis window)."""
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
    if shade:
        i1, i2, label = shade
        step = iw / (n - 1) if n > 1 else 0
        sx, sw = x(i1) - step / 2, step * (i2 - i1 + 1)
        parts.append(f'<rect x="{sx:.1f}" y="{pad_t}" width="{sw:.1f}" '
                     f'height="{ih}" fill="#b2182b" fill-opacity="0.06"/>')
        parts.append(f'<text x="{sx + sw / 2:.1f}" y="{pad_t + 10}" '
                     f'font-size="8" text-anchor="middle" '
                     f'fill="var(--muted,#777)">{label}</text>')
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


# ------------------------------------------------------------ heat maps

# The U.S. page's heat scale (docs/assets/app.js, HEAT): the paper's
# blue -> red stops, one colour story in both themes. heat_color is a
# line-for-line port of app.js heatColor (linear interpolation across
# the nine stops), so the Spain grids read identically to the U.S. map.
HEAT = ["#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7",
        "#fddbc7", "#f4a582", "#d6604d", "#b2182b"]


def _hex_rgb(h: str) -> tuple:
    n = int(h[1:], 16)
    return (n >> 16 & 255, n >> 8 & 255, n & 255)


def heat_color(v: float) -> str:
    t = max(0.0, min(1.0, v)) * (len(HEAT) - 1)
    i = min(int(math.floor(t)), len(HEAT) - 2)
    f = t - i
    a, b = _hex_rgb(HEAT[i]), _hex_rgb(HEAT[i + 1])
    return "#" + "".join(
        f"{int(math.floor(x + (y - x) * f + 0.5)):02x}"
        for x, y in zip(a, b))


def heat_fg(v: float) -> str:
    """Readable text colour on a heat_color background (app.js rule)."""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (c / 255 for c in _hex_rgb(heat_color(v)))
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#1b1a16" if lum > 0.5 else "#ffffff"


def _esc(s) -> str:
    return _html.escape(str(s), quote=True)


def svg_heatmap(rows: list, quarters: list, row_h=23) -> str:
    """Rows x quarters heat grid. Row dicts: {"label", "key",
    "values"} for data rows (values aligned to quarters; None renders
    as a no-observation gray cell), {"label", "key", "nodata"} for
    all-pending rows (one wide gray cell stating the reason — the row
    is shown, never dropped), {"group"} for a component header row
    (indicator map only). Data cells carry data-q / data-v attributes
    so tests can verify the grid against the bundle cell by cell."""
    W, label_w, pad_r, top = 1060, 180, 6, 18
    n = len(quarters)
    cw = (W - label_w - pad_r) / n
    total = top + 4 + sum(17 if "group" in r else row_h for r in rows)
    parts = [f'<svg viewBox="0 0 {W} {total}" width="{W}" '
             f'height="{total}" role="img" aria-label="heat map">']
    for i, q in enumerate(quarters):
        if q.endswith("Q1") and i % 20 == 0:
            parts.append(
                f'<text x="{label_w + cw * i:.1f}" y="11" font-size="8.5" '
                f'fill="var(--muted,#777)">{q[:4]}</text>')
    y = top
    for r in rows:
        if "group" in r:
            hh = 17
            parts.append(
                f'<g class="hm-row" data-kind="group" '
                f'data-row="{_esc(r["group"])}">'
                f'<text x="{label_w - 8}" y="{y + hh - 5}" font-size="10" '
                f'font-weight="700" text-anchor="end" '
                f'fill="var(--ink,#222)">{_esc(r["group"])}</text>'
                f'<line x1="0" y1="{y + hh - 1}" x2="{W}" '
                f'y2="{y + hh - 1}" stroke="var(--line,#e9e4d6)" '
                f'stroke-width="0.7"/></g>')
            y += hh
            continue
        kind = "nodata" if "nodata" in r else "data"
        fs = "10.5" if row_h >= 20 else "9.5"
        parts.append(f'<g class="hm-row" data-kind="{kind}" '
                     f'data-row="{_esc(r["key"])}">')
        parts.append(
            f'<text x="{label_w - 8}" y="{y + row_h / 2 + 3.5:.1f}" '
            f'font-size="{fs}" text-anchor="end" '
            f'fill="var(--ink,#222)">{_esc(r["label"])}</text>')
        if kind == "nodata":
            parts.append(
                f'<rect x="{label_w}" y="{y + 1}" width="{cw * n:.1f}" '
                f'height="{row_h - 2}" style="fill:var(--missing,#ece7d9)"/>')
            parts.append(
                f'<text x="{label_w + cw * n / 2:.1f}" '
                f'y="{y + row_h / 2 + 3.5:.1f}" font-size="9.5" '
                f'text-anchor="middle" fill="var(--muted,#777)">'
                f'{_esc(r["nodata"])}</text>')
        else:
            for i, v in enumerate(r["values"]):
                cx = label_w + cw * i
                if v is None:
                    parts.append(
                        f'<rect class="miss" data-q="{quarters[i]}" '
                        f'x="{cx:.1f}" y="{y + 1}" width="{cw - 0.6:.2f}" '
                        f'height="{row_h - 2}" '
                        f'style="fill:var(--missing,#ece7d9)"/>')
                else:
                    parts.append(
                        f'<rect data-q="{quarters[i]}" data-v="{v:.6f}" '
                        f'x="{cx:.1f}" y="{y + 1}" width="{cw - 0.6:.2f}" '
                        f'height="{row_h - 2}" fill="{heat_color(v)}">'
                        f'<title>{_esc(r["label"])} · {quarters[i]} · '
                        f'{v:.3f} — hotter than {round(v * 100)}% of its '
                        f'own history</title></rect>')
        parts.append("</g>")
        y += row_h
    parts.append("</svg>")
    return "".join(parts)


def svg_heat_legend() -> str:
    stops = "".join(
        f'<stop offset="{i / (len(HEAT) - 1):.3f}" stop-color="{c}"/>'
        for i, c in enumerate(HEAT))
    ticks = "".join(
        f'<text x="{180 + 300 * t:.0f}" y="30" font-size="8.5" '
        f'text-anchor="middle" fill="var(--muted,#777)">{t:.2f}</text>'
        for t in (0, 0.25, 0.5, 0.75, 1))
    return (
        f'<svg viewBox="0 0 1060 34" width="100%" role="img" '
        f'aria-label="heat scale legend">'
        f'<defs><linearGradient id="sp-heat-grad" x1="0" y1="0" x2="1" '
        f'y2="0">{stops}</linearGradient></defs>'
        f'<text x="172" y="13" font-size="9.5" text-anchor="end" '
        f'fill="var(--muted,#777)">Quantile in own history</text>'
        f'<rect x="180" y="3" width="300" height="10" '
        f'fill="url(#sp-heat-grad)"/>{ticks}'
        f'<rect x="520" y="3" width="14" height="10" '
        f'style="fill:var(--missing,#ece7d9)"/>'
        f'<text x="540" y="13" font-size="9.5" '
        f'fill="var(--muted,#777)">no observation</text></svg>')


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
.sp-hm { margin: 0; }
.sp-hm svg { display: block; }
.sp-chip { display: inline-block; min-width: 2.6rem; text-align: center; padding: 0.08rem 0.3rem; font-weight: 600; font-size: 0.82rem; }
.sp-glance td, .sp-glance th { white-space: nowrap; }
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
    <ul class="stats">
      <li><span class="stat-num">{len(quarters)}</span><span class="stat-label">quarters</span></li>
      <li><span class="stat-num">{live_n}</span><span class="stat-label">indicators live (of {len(meta_indicators)})</span></li>
      <li><span class="stat-num">{len(comp_live)}</span><span class="stat-label">components in the headline (of {len(COMPONENTS)})</span></li>
    </ul>
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
        {svg_chart(bundle['headline'], quarters, w=1060, h=220,
                   shade=(quarters.index("2010Q1"),
                          quarters.index("2013Q4"), "2010–13 crisis"))}
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

    # ---- heat maps (the U.S. page's signature visual, Spain data) ----
    comp_rows = []
    for comp in COMPONENTS:
        s = bundle["components"][comp]
        if all(v is None for v in s):
            comp_rows.append({"label": comp, "key": comp,
                              "nodata": "no live indicator — "
                                        "source-pending (see below)"})
        else:
            comp_rows.append({"label": comp, "key": comp, "values": s})
    ind_rows = []
    for comp in COMPONENTS:
        ind_rows.append({"group": comp})
        for ind in by_comp.get(comp, []):
            if ind["status"] == "source-pending":
                ind_rows.append({"label": ind["short"], "key": ind["id"],
                                 "nodata": "source-pending"})
            else:
                ind_rows.append({"label": ind["short"], "key": ind["id"],
                                 "values": pct[ind["id"]]["percentile"]})
    out.append(f"""
    <section id="sp-heatmap">
      <h3>The heat map</h3>
      <p class="blurb">Sixteen components × quarters. Colours are
        percentiles of each component's own historical distribution —
        a red cell means "hotter than most of this component's
        history," not an absolute level. Hover any cell for the exact
        value. Components whose indicators are all source-pending are
        shown as no-data rows: they exist in the framework, they are
        listed below with the reasons, and they do not enter the
        headline.</p>
      <div class="heatmap-scroll"><figure class="sp-hm" id="sp-heatmap-components">{svg_heatmap(comp_rows, quarters, row_h=23)}</figure></div>
      {svg_heat_legend()}
      <p class="legend-note"><strong>Reading the scale:</strong> 0.00
        is the lowest vulnerability reading on record for that
        component (deep blue); 1.00 is the highest (deep red). Low
        values are information, not errors — gray cells are the ones
        with no observation.</p>
    </section>
    <section id="sp-indmap">
      <h3>The heat map, by indicator</h3>
      <p class="blurb">The same heat, resolved to all
        {len(meta_indicators)} indicators in component order. Each
        cell is the indicator's percentile in its own history — the
        same scores the indicator panels below show. Gray cells are
        quarters before a series begins; source-pending indicators
        are no-data rows. Hover any cell for the exact value.</p>
      <div class="heatmap-scroll"><figure class="sp-hm" id="sp-heatmap-indicators">{svg_heatmap(ind_rows, quarters, row_h=16)}</figure></div>
      <p class="legend-note"><strong>Method:</strong> rows are in
        framework order and are never re-ordered; the values are the
        build's empirical-CDF percentiles, identical to the panel
        charts below and to <code>panel_percentiles.csv</code> in the
        data downloads.</p>
    </section>""")

    # ---- components at a glance ----
    glance = []
    for comp in COMPONENTS:
        s = bundle["components"][comp]
        inds = by_comp.get(comp, [])
        n_live = sum(1 for i in inds if i["status"] != "source-pending")
        if all(v is None for v in s):
            glance.append(
                f'<tr><td>{comp}</td><td colspan="4">source-pending — '
                f'no live indicator</td><td>0 of {len(inds)}</td></tr>')
            continue
        li = max(i for i, v in enumerate(s) if v is not None)
        pi = max((i for i, v in enumerate(s) if v is not None),
                 key=lambda i: s[i])
        glance.append(
            f'<tr><td>{comp}</td>'
            f'<td><span class="sp-chip" style="background:'
            f'{heat_color(s[li])};color:{heat_fg(s[li])}">'
            f'{s[li]:.2f}</span></td>'
            f'<td>{quarters[li]}</td><td>{s[pi]:.3f}</td>'
            f'<td>{quarters[pi]}</td><td>{n_live} of {len(inds)}</td></tr>')
    out.append(f"""
    <section id="sp-glance">
      <h3>Components at a glance</h3>
      <p class="blurb">Latest reading and sample peak for every
        component, from the same series as the heat map above.</p>
      <table class="pulse-table sp-glance">
        <thead><tr><th>Component</th><th>Latest</th><th>Quarter</th>
        <th>Peak</th><th>Peak quarter</th><th>Indicators live</th></tr>
        </thead>
        <tbody>{''.join(glance)}</tbody>
      </table>
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

    # ---- how it is built (compact on-page method; full text in
    #      methodology.md) ----
    nodata_comps = [c for c in COMPONENTS
                    if all(v is None for v in bundle["components"][c])]
    out.append(f"""
    <section id="sp-method">
      <h3>How this index is built</h3>
      <p class="blurb">The U.S. index's construction applied to
        Spanish sources. The complete methodology — the analogy
        component by component, what does not transfer and why, and
        every source route — is in the
        <a href="methodology.md">methodology</a>.</p>
      <ol class="steps">
        <li><div class="step-body"><strong>Sign orientation.</strong>
          <p>Each series is flipped as needed so that higher values
            always mean greater vulnerability: a thinner capital
            share, a wider Bono–Bund spread, and tighter lending
            standards all read as hotter.</p></div></li>
        <li><div class="step-body"><strong>Percentile transform.</strong>
          <p>Each indicator is mapped through the empirical CDF
            (midrank) of its own Spanish history, so every value is a
            0–1 percentile: 0.9 means "higher than 90% of Spain's own
            past."</p></div></li>
        <li><div class="step-body"><strong>Component index.</strong>
          <p>The mean of a component's available indicator
            percentiles that quarter. Components whose indicators are
            all source-pending ({", ".join(nodata_comps)}) never
            enter.</p></div></li>
        <li><div class="step-body"><strong>Headline index.</strong>
          <p>The mean of the components that have data — currently
            {len(comp_live)} of {len(COMPONENTS)}.</p></div></li>
      </ol>
      <div class="caveats">
        <h3>Honest caveats</h3>
        <ul>
          <li><strong>Observed only — no nowcasts.</strong> A quarter
            a source has not published is a missing value, never
            filled, interpolated or spliced. The panel ends at the
            latest quarter in which at least 60% of live indicators
            are observed.</li>
          <li><strong>Percentiles are Spain-relative.</strong> The
            scale is each series' own Spanish history, so readings
            are not level-comparable with the U.S. index — and there
            is no official Spanish counterpart series to validate
            against.</li>
          <li><strong>Coverage: {live_n} of {len(meta_indicators)}
            indicators live.</strong> {pend_n} are source-pending
            and are shown as explicit no-data rows and cards, with
            the reason, rather than being silently dropped or
            proxied.</li>
          <li><strong>The sample starts in 1999.</strong> Nothing is
            spliced across the peseta/euro changeover; Spain's sample
            contains a full systemic crisis (2010–13), so the scale
            is calibrated by a real event.</li>
        </ul>
      </div>
    </section>""")

    # ---- data downloads ----
    lagging = [i for i in meta_indicators
               if i["status"] != "source-pending"
               and i["last_observation"] < quarters[-1]]
    lag_note = ""
    if lagging:
        lag_note = (" Indicators ending before the panel end: " +
                    ", ".join(f'{i["short"]} (through '
                              f'{i["last_observation"]})'
                              for i in lagging) + ".")
    raw_base = ("https://raw.githubusercontent.com/fcela/"
                "frb-heat-index/main/data/spain")
    out.append(f"""
    <section id="sp-data">
      <h3>Data downloads</h3>
      <p class="blurb">The full quarterly panel behind this page.
        Raw values are the source series as published; percentiles
        are the build's empirical-CDF transform of the oriented
        series. Every chart and cell on this page is drawn from
        these files — no values are hardcoded.</p>
      <ul class="dl-list">
        <li><a href="{raw_base}/panel_quarterly.csv">
          <span class="fname">panel_quarterly.csv</span>
          <span class="fdesc">quarter × {len(meta_indicators)} indicators, raw values</span></a></li>
        <li><a href="{raw_base}/panel_percentiles.csv">
          <span class="fname">panel_percentiles.csv</span>
          <span class="fdesc">quarter × {len(meta_indicators)} indicators, percentiles (0–1)</span></a></li>
        <li><a href="{raw_base}/components.csv">
          <span class="fname">components.csv</span>
          <span class="fdesc">quarter × {len(COMPONENTS)} component percentiles</span></a></li>
        <li><a href="{raw_base}/headline.csv">
          <span class="fname">headline.csv</span>
          <span class="fdesc">quarter × headline index (0–1)</span></a></li>
        <li><a href="../data/spain.json">
          <span class="fname">spain.json</span>
          <span class="fdesc">the site data bundle this page is rendered from</span></a></li>
        <li><a href="../data/spain_meta.json">
          <span class="fname">spain_meta.json</span>
          <span class="fdesc">per-indicator source, series, definition and status — the catalog</span></a></li>
      </ul>
      <p class="stamp">Panel: {quarters[0]}–{quarters[-1]} · Built
        {bundle['built_at_utc']}.{lag_note}</p>
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


# ------------------------------------------------------------ downloads

def write_extra_downloads(data_dir: Path, quarters: list, ids: list,
                          pct_by_id: dict, components: dict,
                          headline: list) -> None:
    """The download CSVs beside panel_quarterly.csv: component
    percentiles, the headline, and the indicator percentile panel —
    the Spain analogues of the U.S. components / aggregate /
    standardized downloads. Values are written exactly as computed;
    missing quarters are empty cells, never filled."""
    import csv as _csv
    import io as _io

    def dump(header, rows):
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()

    def cell(series, r):
        return "" if series is None or series[r] is None else series[r]

    comp_names = list(components)
    (data_dir / "components.csv").write_text(dump(
        ["quarter"] + comp_names,
        [[q] + [cell(components[c], r) for c in comp_names]
         for r, q in enumerate(quarters)]), encoding="utf-8")
    (data_dir / "headline.csv").write_text(dump(
        ["quarter", "headline"],
        [[q, cell(headline, r)] for r, q in enumerate(quarters)]),
        encoding="utf-8")
    (data_dir / "panel_percentiles.csv").write_text(dump(
        ["quarter"] + ids,
        [[q] + [cell(pct_by_id.get(i), r) for i in ids]
         for r, q in enumerate(quarters)]), encoding="utf-8")


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
    write_extra_downloads(DATA_DIR, quarters, ids, pct_by_id,
                          components, headline)
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
