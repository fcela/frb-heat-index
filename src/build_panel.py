#!/usr/bin/env python3
"""Build the quarterly proxy panel: work/raw/ -> data/panel_quarterly.csv.

Stage 2 of the quarterly pipeline (stage 1: src/fetch_series.py downloads the
raw files; stage 3: src/run_pipeline.py turns the panel into the index).

Each of the 44 heat-map indicators is built from public sources exactly as
documented in data/proxy_catalog.csv: REPLICATED where the paper's series is
public, a documented PROXY where it is not, and all-NaN where no workable
public proxy exists (the maturity gap at commercial banks).

Ragged-edge rule: the panel is truncated at the last full calendar quarter
(--end defaults to that; override explicitly for reproducible vintages).

Usage:
    python src/build_panel.py [--raw-dir work/raw] [--out data/panel_quarterly.csv]
                               [--start 1990Q1] [--end 2014Q4]

Requires: polars, numpy.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent
DEFAULT_RAW = HERE / "work" / "raw"
DEFAULT_OUT = HERE / "data" / "panel_quarterly.csv"

# ---------------------------------------------------------------------------
# Canonical panel columns: the 44 accessible indicator names, in the published
# table's column order (spellings kept verbatim so the panel joins cleanly
# with the paper's documented pipeline spec).
# ---------------------------------------------------------------------------
PANEL_COLUMNS = [
    'Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets',
    'Broker Dealer Leverage (Dealer financing)',
    'Short Term Wholesale Funding (Ratio of Gross Short-Term Wholesale Debt of the Financial Sector to GDP)',
    'Illiquidity Conventration Indicator',
    'Maturity Mismatch (Loans as a percent of deposits)',
    'Weighted Average Maturity Gap at Commerical Banks',
    'Broker Dealer Leverage (Broker dealer leverage ratio)',
    'Maturity Mismatch (Ratio of net short-term wholesale debt of the financial sector to GDP)',
    'Ratio of U.S. Non Agency Securitization Issuance to GDP',
    'Short-term Wholesale Funding (Ratio of total runnable money -like liabilities to GDP)',
    'Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)',
    'Bank Leverage (Total common equity to total assets)',
    'Bank Leverage (Tier 1 common equity ratio)',
    'Bank Leverage (Total Capital Ratio)',
    'Ratio of Total Financial Sector Liabilities to GDP',
    'Ratio of Nonfinancial Business Savings to GDP',
    'Comsumer Credit of Riskier Borrowers (Ratio of consumer credit debt owed by riskier borrowers to DPI)',
    'Consumer Credit (Consumer credit ratio to GDP)',
    'Consumer Credit (Consumer credit debt service ratio to DPI)',
    'Debt Burden of Nonfinancial Businesses (Debt-to-Income ratio)',
    'Real Debt Growth of Nonfinancial Businesses',
    'Ratio of Household Savings to GDP',
    'Debt Burden of Nonfinancial Business (Interest expenses)',
    'Risky Debt of Nonfinancial Businesses (Deep junk Issuance Share)',
    'Home Mortage Debt (Ratio of mortgage debt owed by riskier borrowers to DPI)',
    'Home Mortage Debt (Ratio of total mortgage debt to GDP)',
    'Home Mortgage Debt (Total mortgage debt service ratio to DPI)',
    'Risky Debt of Nonfinancial Businesses',
    'Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages by riskier borrowers)',
    'Consumer Credit of Riskier Borrowers (Incidence of very rapid consumer borrowing by riskier borrowers)',
    'Mortgage Borrowing by Riskier Borrowers (Incidence of very rapid mortgage borrowing by riskier borrowers)',
    'Spread of Corporate Bonds (over Treasury Securities) (BBB)',
    'Indicators of Volitility (Credit Default Swap premium)',
    'Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)',
    'Equity Valuations (Earnings/Price ratio )',
    'Mortage Lending and FICO Scores (FICO scores)',
    'National House Prices Relative to Rents',
    'Spread of Corporate Bonds (over Treasury Securities) (High Yield)',
    'Investor Appetite for Risky Corporate Debt (Issuance of Junk Bonds and Leverage Loans)',
    'Prices for Commercial Real Estate (CRE)',
    'Change in Lending Standards, CRE Loans',
    'Mortage Lending and FICO Scores (Change in Lending Standards)',
    'Investor Appetite for Risky Corporate Debt (Change in Bank Lending Standards C&I Loans)',
    'Indicators of Volitility (VIX)',
    'SRISK (Total capital shortfall relative to total assets)',
    'Granger-causality connections among large financial institutions',
    'Hedge Fund Leverage (Gross assets to net assets, qualifying hedge funds)',
    'Hedge Fund Leverage (SCOOS net percent reporting increased use of leverage)',
]


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def parse_q(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})Q([1-4])", s.strip())
    if not m:
        raise ValueError(f"bad quarter label: {s!r}")
    return int(m.group(1)), int(m.group(2))


def qlabel(year: int, quarter: int) -> str:
    return f"{year}Q{quarter}"


def quarter_range(start: str, end: str) -> list[str]:
    y0, q0 = parse_q(start)
    y1, q1 = parse_q(end)
    out, y, q = [], y0, q0
    while (y, q) <= (y1, q1):
        out.append(qlabel(y, q))
        q += 1
        if q == 5:
            q, y = 1, y + 1
    return out


def last_full_quarter(today: dt.date | None = None) -> str:
    """Ragged-edge rule: never publish a partial quarter."""
    today = today or dt.date.today()
    q = (today.month - 1) // 3  # quarter of the *previous* completed quarter...
    y = today.year
    # The quarter containing `today` is incomplete; step back one.
    cur = (today.month - 1) // 3 + 1
    if cur == 1:
        return qlabel(y - 1, 4)
    return qlabel(y, cur - 1)


def ensure_date(df: pl.DataFrame, col: str, out: str = "date") -> pl.DataFrame:
    """String -> Date, or pass through an already-parsed Date column
    (the CSV reader auto-parses ISO dates)."""
    if df.schema[col] == pl.String:
        return df.with_columns(pl.col(col).str.to_date().alias(out))
    return df.with_columns(pl.col(col).cast(pl.Date).alias(out))


def with_quarter(df: pl.DataFrame, date_col: str) -> pl.DataFrame:
    """Add a 'quarter' label column (YYYYQN) from a date column."""
    d = ensure_date(df, date_col, "_d")
    return d.with_columns(
        (pl.col("_d").dt.year().cast(pl.String) + "Q"
         + ((pl.col("_d").dt.month() - 1) // 3 + 1).cast(pl.String)
         ).alias("quarter")
    ).drop("_d")


# ---------------------------------------------------------------------------
# Raw readers
# ---------------------------------------------------------------------------

def read_fred_monthly(raw: Path, series_id: str) -> pl.DataFrame:
    """FRED graph CSV -> monthly (date, value), numeric, deduped."""
    p = raw / f"fred_{series_id}.csv"
    df = pl.read_csv(p, null_values=[".", ""])
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    val_col = series_id if series_id in df.columns else df.columns[1]
    df = df.select([
        pl.col(date_col),
        pl.col(val_col).cast(pl.Float64, strict=False).alias("value"),
    ])
    df = ensure_date(df, date_col, "date").select(["date", "value"])
    df = df.drop_nulls().unique("date").sort("date")
    return df


def quarterly_mean(df: pl.DataFrame) -> pl.DataFrame:
    return (with_quarter(df, "date")
            .group_by("quarter").agg(pl.col("value").mean().alias("value"))
            .sort("quarter"))


def fred_qmean(raw: Path, series_id: str) -> pl.DataFrame:
    return quarterly_mean(read_fred_monthly(raw, series_id))


def fred_qlast(raw: Path, series_id: str) -> pl.DataFrame:
    return (with_quarter(read_fred_monthly(raw, series_id), "date")
            .group_by("quarter").agg(pl.col("value").last().alias("value"))
            .sort("quarter"))


def align(a: pl.DataFrame, b: pl.DataFrame, how: str = "inner") -> pl.DataFrame:
    return a.join(b, on="quarter", how=how, suffix="_r").sort("quarter")


def series_frame(quarters: list[str], values, name: str) -> pl.DataFrame:
    return pl.DataFrame({"quarter": quarters, name: list(values)})


# ---------------------------------------------------------------------------
# Builders: each returns pl.DataFrame(quarter, value). Documented against
# data/proxy_catalog.csv (status: replicated | proxy).
# ---------------------------------------------------------------------------

def b_ep_spread(raw: Path):
    # Long-run E/P (university-hosted monthly file, converted to CSV once:
    # work/raw/ie_data_ep.csv with columns date,ep) minus 10y Treasury.
    # NOTE: the hosted source froze at 2023Q1; the E/P leg stops there.
    ep = quarterly_mean(
        ensure_date(
            pl.read_csv(raw / "ie_data_ep.csv")
            .select([pl.col("date"), pl.col("ep").cast(pl.Float64).alias("value")]),
            "date"))
    dgs10 = fred_qmean(raw, "DGS10")
    j = align(ep, dgs10)
    spread = j.with_columns(
        (pl.col("value") - pl.col("value_r")).alias("spread"))
    return {
        'Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)':
            spread.select(["quarter", pl.col("spread").alias("value")]),
        'Equity Valuations (Earnings/Price ratio )': ep,
    }


def b_vix(raw: Path):
    return {'Indicators of Volitility (VIX)': fred_qmean(raw, "VIXCLS")}


def _systemic_frame(raw: Path, fname: str) -> pl.DataFrame:
    """Read a prebuilt systemic-risk quarterly CSV (quarter, value).

    The series live in data/systemic/ (committed) and are rebuilt by
    src/systemic/build_srisk.py / build_granger.py when new quarters arrive.
    """
    f = raw.parent.parent / "data" / "systemic" / fname
    return pl.read_csv(f).select(["quarter", "value"])


def b_srisk(raw: Path):
    # Total SRISK relative to total assets (Acharya/Engle/Richardson 2012).
    # Built by src/systemic/build_srisk.py from Y-9C + Yahoo + EDGAR.
    return {'SRISK (Total capital shortfall relative to total assets)':
            _systemic_frame(raw, "srisk_quarterly.csv")}


def b_granger(raw: Path):
    # Significant Granger-causality connections among large financial
    # institutions (Billio/Getmansky/Lo/Pelizzon 2012).
    # Built by src/systemic/build_granger.py from Yahoo monthly returns.
    return {'Granger-causality connections among large financial institutions':
            _systemic_frame(raw, "granger_quarterly.csv")}


def b_hedge_fund_leverage(raw: Path):
    # Hedge fund leverage, two public series (added 2026-10; the FEDS
    # panel has no hedge-fund series because Form PF is confidential --
    # these are the published aggregates of the same filings):
    #  (1) OFR Hedge Fund Monitor, SEC Form PF aggregates for
    #      qualifying hedge funds: gross assets / net assets
    #      (FPF-ALLQHF_GAV_SUM / FPF-ALLQHF_NAV_SUM via the OFR public
    #      API). The ratio of the published aggregates equals the
    #      net-asset-weighted mean fund-level leverage ratio. The API
    #      CSV carries a label row under the header; keep parseable
    #      dates only. Series begins 2013Q1 (Form PF quarterly
    #      reporting start) -- a documented leading boundary.
    #  (2) SCOOS via FRED EXHE2C1Q8NP: net percent of dealers
    #      reporting increased use of leverage by hedge funds
    #      (quarterly survey, quarter-start dates; begins 2011Q3).
    out = {}
    p = raw / "ofr_hf_size.csv"
    df = pl.read_csv(p, schema={
        "date": pl.String,
        "FPF-ALLQHF_GAV_SUM": pl.String,
        "FPF-ALLQHF_NAV_SUM": pl.String,
    }).filter(pl.col("date").str.contains(r"^\d{4}-\d{2}-\d{2}$")).with_columns(
        pl.col("date").str.to_date().alias("date"),
        pl.col("FPF-ALLQHF_GAV_SUM").cast(pl.Float64).alias("gav"),
        pl.col("FPF-ALLQHF_NAV_SUM").cast(pl.Float64).alias("nav"),
    )
    lev = with_quarter(df, "date").with_columns(
        (pl.col("gav") / pl.col("nav")).alias("value")
    ).select(["quarter", "value"]).sort("quarter")
    out["Hedge Fund Leverage (Gross assets to net assets, qualifying hedge funds)"] = lev
    out["Hedge Fund Leverage (SCOOS net percent reporting increased use of leverage)"] = \
        fred_qlast(raw, "EXHE2C1Q8NP")
    return out


def b_baa_spread(raw: Path):
    # Monthly BAA yield minus 10y Treasury (quarterly means of each leg), pp.
    # CDS premium: the expected-default component of the Baa spread, i.e. the
    # spread minus the Fed's excess bond premium (Gilchrist-Zakrajsek
    # decomposition: spread = expected default + EBP). A CDS premium prices
    # protection against default, so the default component is the conceptually
    # correct public analog -- distinct from the full BBB spread by
    # construction (corr ~0.58) and strictly positive over 1990-2026
    # (min 1.05pp), so the pipeline log transform is safe.
    baa = fred_qmean(raw, "BAA").rename({"value": "baa"})
    dgs = fred_qmean(raw, "DGS10").rename({"value": "dgs"})
    ebp = ensure_date(
        pl.read_csv(raw / "ebp_raw.csv")
        .select([pl.col("date"), pl.col("ebp").cast(pl.Float64).alias("ebp")]),
        "date")
    ebp_q = quarterly_mean(ebp.rename({"ebp": "value"}))
    sp = align(align(baa, dgs), ebp_q).with_columns([
        (pl.col("baa") - pl.col("dgs")).alias("bbb"),
        (pl.col("baa") - pl.col("dgs") - pl.col("value")).alias("cds"),
    ])
    return {
        'Spread of Corporate Bonds (over Treasury Securities) (BBB)':
            sp.select(["quarter", pl.col("bbb").alias("value")]),
        'Indicators of Volitility (Credit Default Swap premium)':
            sp.select(["quarter", pl.col("cds").alias("value")]),
    }


def b_hy_spread(raw: Path):
    # Monthly Baa-Treasury spread + monthly excess bond premium -> quarterly.
    ebp = ensure_date(
        pl.read_csv(raw / "ebp_raw.csv")
        .select([pl.col("date"), pl.col("ebp").cast(pl.Float64).alias("ebp")]),
        "date")
    ebp_q = quarterly_mean(ebp.rename({"ebp": "value"})).rename({"value": "ebp"})
    baa = fred_qmean(raw, "BAA").rename({"value": "baa"})
    dgs = fred_qmean(raw, "DGS10").rename({"value": "dgs"})
    sp = align(align(baa, dgs), ebp_q, how="left")
    sp = sp.with_columns(
        ((pl.col("baa") - pl.col("dgs")).fill_null(strategy="forward")
         + pl.col("ebp").fill_null(strategy="forward")).alias("value"))
    return {'Spread of Corporate Bonds (over Treasury Securities) (High Yield)':
            sp.select(["quarter", "value"])}


def b_issuance_appetite(raw: Path):
    # Inverse average credit (GZ) spread: tight spreads <-> strong appetite.
    ebp = ensure_date(pl.read_csv(raw / "ebp_raw.csv"), "date")
    gz_q = quarterly_mean(
        ebp.select([pl.col("date"),
                    pl.col("gz_spread").cast(pl.Float64).alias("value")]))
    inv = gz_q.with_columns((1.0 / pl.col("value")).alias("value"))
    neg_ebp = quarterly_mean(
        ebp.select([pl.col("date"),
                    (-pl.col("ebp").cast(pl.Float64)).alias("value")]))
    return {
        'Investor Appetite for Risky Corporate Debt (Issuance of Junk Bonds and Leverage Loans)': inv,
        'Risky Debt of Nonfinancial Businesses (Deep junk Issuance Share)': neg_ebp,
    }


def b_sloos_cni(raw: Path):
    return {'Investor Appetite for Risky Corporate Debt (Change in Bank Lending Standards C&I Loans)':
            fred_qlast(raw, "DRTSCILM")}


def b_house_price_rent(raw: Path):
    hp = fred_qmean(raw, "CSUSHPISA").rename({"value": "hp"})
    rent = fred_qmean(raw, "CUSR0000SEHA").rename({"value": "rent"})
    j = align(hp, rent).with_columns((pl.col("hp") / pl.col("rent")).alias("value"))
    return {'National House Prices Relative to Rents': j.select(["quarter", "value"])}


def b_sloos_mortgage(raw: Path):
    return {'Mortage Lending and FICO Scores (Change in Lending Standards)':
            fred_qlast(raw, "DRTSPM")}


def natural_cubic_spline(x: np.ndarray, y: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """Natural cubic spline interpolation (pure numpy)."""
    n = len(x)
    h = np.diff(x)
    a = y.copy()
    alpha = np.zeros(n)
    alpha[1:-1] = (3 / h[1:] * (a[2:] - a[1:-1])
                   - 3 / h[:-1] * (a[1:-1] - a[:-2]))
    l = np.ones(n)
    mu = np.zeros(n)
    z = np.zeros(n)
    for i in range(1, n - 1):
        l[i] = 2 * (x[i + 1] - x[i - 1]) - h[i - 1] * mu[i - 1]
        mu[i] = h[i] / l[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i]
    c = np.zeros(n)
    b = np.zeros(n - 1)
    d = np.zeros(n - 1)
    for j in range(n - 2, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = (a[j + 1] - a[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3
        d[j] = (c[j + 1] - c[j]) / (3 * h[j])
    out = np.empty_like(xs, dtype=float)
    idx = np.clip(np.searchsorted(x, xs) - 1, 0, n - 2)
    dx = xs - x[idx]
    out = a[idx] + b[idx] * dx + c[idx] * dx ** 2 + d[idx] * dx ** 3
    return out


def b_fico(raw: Path):
    # Annual UPB-weighted mean origination FICO (GSE credit supplements,
    # transcribed to work/raw/fico_annual.csv: year,fico) -> quarterly via
    # natural cubic spline on mid-year points.
    ann = pl.read_csv(raw / "fico_annual.csv").sort("year")
    x = ann["year"].to_numpy().astype(float) + 0.5  # mid-year
    y = ann["fico"].to_numpy().astype(float)
    qs = [f"{yr}Q{q}" for yr in range(int(x[0]), int(x[-1]) + 1) for q in range(1, 5)]
    xs = np.array([int(q[:4]) + (int(q[5]) - 0.5) / 4 for q in qs])
    vals = natural_cubic_spline(x, y, xs)
    return {'Mortage Lending and FICO Scores (FICO scores)':
            series_frame(qs, vals, "value")}


def b_bizdebt(raw: Path):
    # All nonfinancial business debt = corporate (BCNSDODNS, $M SA) +
    # noncorporate (TCMILBSNNB, $M NSA), ffill-aligned.
    corp = fred_qlast(raw, "BCNSDODNS").rename({"value": "corp"})
    ncorp = fred_qlast(raw, "TCMILBSNNB").rename({"value": "ncorp"})
    gdp = fred_qlast(raw, "GDP").rename({"value": "gdp"})          # $B SAAR
    gdpdef = fred_qlast(raw, "GDPDEF").rename({"value": "gdpdef"})
    j = align(align(align(corp, ncorp, how="left"), gdp, how="left"), gdpdef, how="left")
    j = j.with_columns([
        (pl.col("corp") + pl.col("ncorp").fill_null(strategy="forward")).alias("debt"),
    ])
    j = j.with_columns([
        (100.0 * pl.col("debt") / (pl.col("gdp") * 1000.0)).alias("debt_income"),
        (pl.col("debt") / pl.col("gdpdef")).alias("real_debt"),
    ]).sort("quarter")
    growth = (j.with_columns(
        (100.0 * (pl.col("real_debt") / pl.col("real_debt").shift(4) - 1.0)
         ).alias("value"))
              .select(["quarter", "value"]))
    return {
        'Debt Burden of Nonfinancial Businesses (Debt-to-Income ratio)':
            j.select(["quarter", pl.col("debt_income").alias("value")]),
        'Real Debt Growth of Nonfinancial Businesses': growth,
    }


def b_risky_leverage(raw: Path):
    # 100 * NFCB debt securities & loans / NFCB total assets.
    debt = fred_qlast(raw, "BCNSDODNS").rename({"value": "debt"})
    ta = fred_qlast(raw, "BOGZ1FL104090103Q").rename({"value": "ta"})
    j = align(debt, ta).with_columns((100.0 * pl.col("debt") / pl.col("ta")).alias("value"))
    return {'Risky Debt of Nonfinancial Businesses': j.select(["quarter", "value"])}


def read_nipa(raw: Path) -> dict[str, pl.DataFrame]:
    """BEA NIPA bulk TXT -> {series_code: (quarter, value)}.

    Uses csv.reader (not naive split(',')) because data fields carry quoted
    thousands separators.
    """
    p = raw / "NipaDataQ.txt"
    series: dict[str, dict[str, float]] = {}
    with open(p, newline="") as f:
        rdr = csv.reader(f)
        next(rdr, None)  # header
        for row in rdr:
            if len(row) != 3:
                continue
            code, per, val = row
            try:
                v = float(val.replace(",", ""))
            except ValueError:
                continue
            m = re.fullmatch(r"(\d{4})Q([1-4])", per.strip())
            if not m:
                continue
            series.setdefault(code, {})[f"{m.group(1)}Q{m.group(2)}"] = v
    return {c: pl.DataFrame({"quarter": sorted(d), "value": [d[k] for k in sorted(d)]})
            for c, d in series.items()}


def b_nipa(raw: Path):
    nq = read_nipa(raw)
    gdp = nq["A191RC"].rename({"value": "gdp"})
    # Net lending of domestic business / GDP (saving net of capital formation).
    save = align(nq["W995RC"].rename({"value": "s"}), gdp)
    save = save.with_columns((100.0 * pl.col("s") / pl.col("gdp")).alias("value"))
    # Interest expenses / cash flow, nonfinancial corporate (Table 1.14).
    nint = nq["B471RC"].rename({"value": "nint"})
    undist = align(nq["A466RC"].rename({"value": "a"}),
                   nq["B467RC"].rename({"value": "b"}))
    undist = undist.with_columns((pl.col("a") - pl.col("b")).alias("undist"))
    cfc = align(nq["A455RC"].rename({"value": "a"}), nq["A457RC"].rename({"value": "b"}))
    cfc = cfc.with_columns((pl.col("a") - pl.col("b")).alias("cfc"))
    j = align(align(nint, undist[["quarter", "undist"]]),
              cfc[["quarter", "cfc"]])
    j = j.with_columns(
        (100.0 * pl.col("nint") / (pl.col("undist") + pl.col("cfc"))).alias("value"))
    return {
        'Ratio of Nonfinancial Business Savings to GDP': save.select(["quarter", "value"]),
        'Debt Burden of Nonfinancial Business (Interest expenses)': j.select(["quarter", "value"]),
    }


def b_consumer(raw: Path):
    totalsl = fred_qlast(raw, "TOTALSL").rename({"value": "sl"})   # $B SA
    gdp = fred_qlast(raw, "GDP").rename({"value": "gdp"})          # $B SAAR
    j = align(totalsl, gdp).with_columns((pl.col("sl") / pl.col("gdp")).alias("value"))
    return {
        'Consumer Credit (Consumer credit ratio to GDP)': j.select(["quarter", "value"]),
        'Consumer Credit (Consumer credit debt service ratio to DPI)':
            fred_qlast(raw, "TDSP"),
    }


def b_consumer_risky(raw: Path):
    # Revolving (credit-card) balances: the segment where riskier borrowers
    # concentrate. Stock/DPI and its y/y growth.
    rev = fred_qmean(raw, "REVOLSL").rename({"value": "rev"})     # $M SA
    dpi = fred_qmean(raw, "DPI").rename({"value": "dpi"})          # $B SAAR
    j = align(rev, dpi).with_columns([
        (100.0 * pl.col("rev") / (pl.col("dpi") * 1000.0)).alias("stock"),
    ]).sort("quarter")
    stock = j.select(["quarter", pl.col("stock").alias("value")])
    growth = (j.with_columns(
        (100.0 * (pl.col("stock") / pl.col("stock").shift(4) - 1.0)).alias("value"))
              .select(["quarter", "value"]))
    return {
        'Comsumer Credit of Riskier Borrowers (Ratio of consumer credit debt owed by riskier borrowers to DPI)': stock,
        'Consumer Credit of Riskier Borrowers (Incidence of very rapid consumer borrowing by riskier borrowers)': growth,
    }


def b_mortgage(raw: Path):
    # FRED HHMSDODNS is $M (verified 2026-10-15); convert to $B to match the
    # panel's long-standing $B convention for these ratios.
    md = (fred_qlast(raw, "HHMSDODNS").rename({"value": "md"})
          .with_columns((pl.col("md") / 1000.0).alias("md")))          # $M -> $B
    heq = fred_qlast(raw, "BOGZ1FL893065125Q").rename({"value": "heq"})  # $M NSA
    gdp = fred_qlast(raw, "GDP").rename({"value": "gdp"})
    dpi = fred_qmean(raw, "DPI").rename({"value": "dpi"})
    j = align(align(md, gdp), dpi, how="left").sort("quarter")
    # md ($B stock) / gdp ($B SAAR); 100 * md ($B) / dpi ($B SAAR) = % of DPI.
    j = j.with_columns([
        (pl.col("md") / pl.col("gdp")).alias("gdp_ratio"),
        (100.0 * pl.col("md") / pl.col("dpi")).alias("dpi_ratio"),
    ])
    growth = (j.with_columns(
        (100.0 * (pl.col("md") / pl.col("md").shift(4) - 1.0)).alias("value"))
              .select(["quarter", "value"]))
    # Piggy-back incidence: closed-end junior-lien (home equity loan) share of
    # household mortgage debt. Piggy-back seconds are closed-end subordinate
    # liens originated alongside first liens; their outstanding share tracks
    # the paper's CCP-based incidence series through the 2003-2007 boom and
    # post-crisis deleveraging, and is distinct from aggregate mortgage growth
    # (corr ~0.18). Z.1 all-sectors asset = household liability. Both legs $M.
    piggy = (align(
                fred_qlast(raw, "HHMSDODNS").rename({"value": "md"}),
                fred_qlast(raw, "BOGZ1FL893065125Q").rename({"value": "heq"}))
             .with_columns((100.0 * pl.col("heq") / pl.col("md")).alias("value"))
             .select(["quarter", "value"]))
    return {
        'Home Mortage Debt (Ratio of total mortgage debt to GDP)':
            j.select(["quarter", pl.col("gdp_ratio").alias("value")]),
        'Home Mortgage Debt (Total mortgage debt service ratio to DPI)':
            fred_qlast(raw, "MDSP"),
        'Home Mortage Debt (Ratio of mortgage debt owed by riskier borrowers to DPI)':
            j.select(["quarter", pl.col("dpi_ratio").alias("value")]),
        'Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages by riskier borrowers)': piggy,
        'Mortgage Borrowing by Riskier Borrowers (Incidence of very rapid mortgage borrowing by riskier borrowers)': growth,
    }


def b_saving_hh(raw: Path):
    psav = fred_qmean(raw, "PSAVERT").rename({"value": "ps"})     # % of DPI
    dspi = fred_qmean(raw, "DSPI").rename({"value": "dspi"})      # $B
    gdp = fred_qmean(raw, "GDP").rename({"value": "gdp"})
    j = align(align(psav, dspi), gdp)
    saving_gdp = (pl.col("ps") / 100.0) * pl.col("dspi") / pl.col("gdp")
    j = j.with_columns(saving_gdp.alias("value"))
    return {'Ratio of Household Savings to GDP': j.select(["quarter", "value"])}


# ---------------------------------------------------------------------------
# Y-9C (Chicago Fed bulk bhcf files)
# ---------------------------------------------------------------------------

Y9C_ITEMS = ["BHCK2170", "BHCK2122", "BHCK3217", "BHCK3163", "BHCK8274",
             "BHCK3283", "BHCK3000", "BHCKC502", "BHCKB704", "BHCK2800",
             "BHCKB995", "BHCKB996", "BHCK2332",
             "BHDM6631", "BHDM6636", "BHFN6631", "BHFN6636"]


def read_y9c(raw: Path, start: str, end: str) -> pl.DataFrame:
    """Aggregate Y-9C filer microdata to quarterly national sums (Polars)."""
    rows = []
    for q in quarter_range(start, end):
        y, qq = parse_q(q)
        fn = raw / "y9c" / f"bhcf{y % 100:02d}{qq * 3:02d}.csv"
        if not fn.exists():
            continue
        try:
            hdr = pl.read_csv(fn, n_rows=0, encoding="utf8-lossy").columns
        except Exception:
            continue
        cmap = {c.lower(): c for c in hdr}
        rid, rdt = cmap.get("rssd9001"), cmap.get("rssd9999")
        if rid is None or rdt is None:
            continue
        use = [cmap[i.lower()] for i in Y9C_ITEMS if i.lower() in cmap]
        try:
            d = pl.read_csv(fn, columns=[rid, rdt] + use, encoding="utf8-lossy",
                            infer_schema_length=10000)
        except Exception:
            continue
        d = d.filter(pl.col(rdt).cast(pl.String).str.contains(r"^\d{8}$"))
        rec: dict[str, float] = {"quarter": q, "n_filers": float(d.height)}
        for item, col in zip(Y9C_ITEMS, [cmap[i.lower()] for i in Y9C_ITEMS]):
            if col in d.columns:
                rec[item.lower()] = float(
                    d.select(pl.col(col).cast(pl.Float64, strict=False)
                             .fill_null(0).sum()).item())
            else:
                rec[item.lower()] = float("nan")
        # top-5 share inputs (BHCK2170, $ thousands)
        a = (d.select(pl.col(cmap["bhck2170"]).cast(pl.Float64, strict=False)
                      .fill_null(0).alias("a"))["a"] if "bhck2170" in cmap else None)
        if a is not None:
            rec["top5_assets"] = float(a.top_k(5).sum())
            rec["tot_assets"] = float(a.sum())
        rows.append(rec)
    if not rows:
        raise FileNotFoundError(f"no Y-9C files found under {raw / 'y9c'}")
    return pl.DataFrame(rows).sort("quarter")


def b_y9c(raw: Path, start: str, end: str):
    y = read_y9c(raw, start, end)
    y = y.with_columns([
        # tangible equity / tangible assets, %
        (100.0 * (pl.col("bhck3217") - pl.col("bhck3163"))
         / (pl.col("bhck2170") - pl.col("bhck3163"))).alias("tangible"),
        # loans / deposits, %
        (100.0 * pl.col("bhck2122")
         / (pl.col("bhdm6631") + pl.col("bhdm6636")
            + pl.col("bhfn6631") + pl.col("bhfn6636"))).alias("loans_dep"),
        # top-5 BHC asset share, %
        (100.0 * pl.col("top5_assets") / pl.col("tot_assets")).alias("top5"),
        # tier 1 common equity ratio, % (pre-2004 trust preferred treated as 0)
        (100.0 * (pl.col("bhck8274") - pl.col("bhck3283") - pl.col("bhck3000")
                  - pl.col("bhckc502").fill_nan(0).fill_null(0))
         / pl.col("bhckb704")).alias("t1c"),
        # short-term money at BHCs / assets, % (BHCK2800 pre-2002 splice)
        (100.0 * (pl.when(pl.col("bhckb995").is_not_nan())
                  .then(pl.col("bhckb995") + pl.col("bhckb996"))
                  .otherwise(pl.col("bhck2800"))
                  + pl.col("bhck2332")) / pl.col("bhck2170")).alias("st_money"),
    ])
    return {
        'Bank Leverage (Total common equity to total assets)':
            y.select(["quarter", pl.col("tangible").alias("value")]),
        'Maturity Mismatch (Loans as a percent of deposits)':
            y.select(["quarter", pl.col("loans_dep").alias("value")]),
        'Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets':
            y.select(["quarter", pl.col("top5").alias("value")]),
        'Bank Leverage (Tier 1 common equity ratio)':
            y.select(["quarter", pl.col("t1c").alias("value")]),
        'Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)':
            y.select(["quarter", pl.col("st_money").alias("value")]),
    }


def b_call_report(raw: Path):
    # Total capital ratio from Call Report aggregates. The original build read
    # sparse FFIEC CDR bulk .xpt files; here we read the pre-aggregated
    # work/raw/call_panel.csv (columns: quarter,tier1,tier2,rwa) produced by
    # that documented SAS step.
    p = raw / "call_panel.csv"
    if not p.exists():
        raise FileNotFoundError(
            f"{p} missing: aggregate the FFIEC CDR bulk files first (see README)")
    d = pl.read_csv(p)
    d = d.with_columns((100.0 * (pl.col("tier1") + pl.col("tier2"))
                        / pl.col("rwa")).alias("value"))
    return {'Bank Leverage (Total Capital Ratio)': d.select(["quarter", "value"])}


# ---------------------------------------------------------------------------
# Z.1 via FRED
# ---------------------------------------------------------------------------

def b_z1(raw: Path):
    gdp = fred_qlast(raw, "GDP").rename({"value": "gdp"})          # $B SAAR

    def q(sid: str) -> pl.DataFrame:
        return fred_qlast(raw, sid).rename({"value": sid})

    bd_a = q("BOGZ1FL664090005Q")
    bd_l = q("BOGZ1FL664190005Q")
    bd_ratio = align(bd_a, bd_l).with_columns(
        (100.0 * (pl.col("BOGZ1FL664090005Q") - pl.col("BOGZ1FL664190005Q"))
         / pl.col("BOGZ1FL664090005Q")).alias("value"))

    repo_a = q("BOGZ1FL662051003Q")
    repo_l = q("BOGZ1FL662151003Q")
    dealer = align(align(repo_a, repo_l), gdp, how="left").with_columns(
        (100.0 * (pl.col("BOGZ1FL662051003Q") + pl.col("BOGZ1FL662151003Q"))
         / pl.col("gdp")).alias("value"))

    s = q("BOGZ1FL794141005Q")      # ST loans liabilities
    o = q("BOGZ1FL793169175Q")     # open market paper liabilities
    ra = q("BOGZ1FL792050005Q")    # fed funds & repo assets
    oa = q("BOGZ1FL793069175Q")    # open market paper assets
    st = align(align(align(s, o), ra, how="left"), oa, how="left")
    st = align(st, gdp, how="left").with_columns([
        (100.0 * (pl.col("BOGZ1FL794141005Q") + pl.col("BOGZ1FL793169175Q")
                  - pl.col("BOGZ1FL792050005Q").fill_null(0)
                  - pl.col("BOGZ1FL793069175Q").fill_null(0))
         / pl.col("gdp")).alias("net"),
        (100.0 * (pl.col("BOGZ1FL794141005Q") + pl.col("BOGZ1FL793169175Q"))
         / pl.col("gdp")).alias("gross"),
    ])

    r = q("BOGZ1FL792150005Q")      # fed funds & repo liabilities
    mmf = q("BOGZ1FL634090005Q")   # money fund shares
    run = align(align(r, o, how="left"), mmf, how="left")
    run = align(run, gdp, how="left").with_columns(
        ((pl.col("BOGZ1FL792150005Q") + pl.col("BOGZ1FL793169175Q")
          + pl.col("BOGZ1FL634090005Q")) / pl.col("gdp")).alias("value"))

    cb = q("BOGZ1FL793063005Q")
    ln = q("BOGZ1FL794035005Q")
    ta = q("BOGZ1FL794090005Q")
    illiq = align(align(cb, ln), ta).with_columns(
        ((pl.col("BOGZ1FL793063005Q") + pl.col("BOGZ1FL794035005Q"))
         / pl.col("BOGZ1FL794090005Q")).alias("value"))

    finl = q("BOGZ1FL794190005Q")  # $M
    finl_gdp = align(finl, gdp).with_columns(
        ((pl.col("BOGZ1FL794190005Q") / 1000.0) / pl.col("gdp")).alias("value"))

    abs_txn = q("BOGZ1FA674190005Q")  # $M SAAR
    nonag = align(abs_txn, gdp).with_columns(
        ((pl.col("BOGZ1FA674190005Q") / 1000.0) / pl.col("gdp") * 100.0).alias("value"))

    return {
        'Broker Dealer Leverage (Broker dealer leverage ratio)':
            bd_ratio.select(["quarter", "value"]),
        'Broker Dealer Leverage (Dealer financing)':
            dealer.select(["quarter", "value"]),
        'Maturity Mismatch (Ratio of net short-term wholesale debt of the financial sector to GDP)':
            st.select(["quarter", pl.col("net").alias("value")]),
        'Short Term Wholesale Funding (Ratio of Gross Short-Term Wholesale Debt of the Financial Sector to GDP)':
            st.select(["quarter", pl.col("gross").alias("value")]),
        'Short-term Wholesale Funding (Ratio of total runnable money -like liabilities to GDP)':
            run.select(["quarter", "value"]),
        'Illiquidity Conventration Indicator':
            illiq.select(["quarter", "value"]),
        'Ratio of Total Financial Sector Liabilities to GDP':
            finl_gdp.select(["quarter", "value"]),
        'Ratio of U.S. Non Agency Securitization Issuance to GDP':
            nonag.select(["quarter", "value"]),
    }


# ---------------------------------------------------------------------------
# Manual / small sources
# ---------------------------------------------------------------------------

def b_bis_cre(raw: Path):
    # BIS commercial property price statistics, US, quarterly level (manual CSV).
    d = pl.read_csv(raw / "bis_cre_us.csv").select([
        pl.col("quarter").cast(pl.String),
        pl.col("value").cast(pl.Float64),
    ])
    return {'Prices for Commercial Real Estate (CRE)': d}


def b_sloos_cre(raw: Path):
    # CRE lending-standards splice (transcribed from the SLOOS historical XML):
    # discontinued aggregate 1990Q2-2013Q2 (re-dated back one quarter) spliced
    # to the mean of the three successor sub-questions from 2013Q3.
    d = pl.read_csv(raw / "sloos_cre.csv").select([
        pl.col("quarter").cast(pl.String),
        pl.col("value").cast(pl.Float64),
    ])
    return {'Change in Lending Standards, CRE Loans': d}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

BUILDERS = [
    b_ep_spread, b_vix, b_baa_spread, b_hy_spread, b_issuance_appetite,
    b_sloos_cni, b_house_price_rent, b_sloos_mortgage, b_fico, b_bizdebt,
    b_risky_leverage, b_nipa, b_consumer, b_consumer_risky, b_mortgage,
    b_saving_hh, b_z1, b_bis_cre, b_sloos_cre, b_call_report,
    b_srisk, b_granger, b_hedge_fund_leverage,
]


def build(raw: Path, start: str, end: str,
          skip_missing: bool = True) -> pl.DataFrame:
    quarters = quarter_range(start, end)
    panel = pl.DataFrame({"quarter": quarters})
    built: dict[str, pl.DataFrame] = {}
    # Y-9C builders need the quarter range, not the raw dir alone.
    try:
        built.update(b_y9c(raw, start, end))
    except Exception as e:  # noqa: BLE001 - missing bulk files skip the Y-9C leg
        if not skip_missing:
            raise
        print(f"  skip (b_y9c): {type(e).__name__}: {e}", file=sys.stderr)
    for fn in BUILDERS:
        try:
            built.update(fn(raw))
        except Exception as e:  # noqa: BLE001 - one bad input skips one builder
            if not skip_missing:
                raise
            print(f"  skip ({fn.__name__}): {type(e).__name__}: {e}",
                  file=sys.stderr)
    for col in PANEL_COLUMNS:
        s = built.get(col)
        if s is None:
            panel = panel.with_columns(pl.lit(None, dtype=pl.Float64).alias(col))
        else:
            panel = panel.join(s.rename({"value": col}), on="quarter", how="left")
    # maturity gap: no workable public proxy (documented in the catalog).
    return panel.select(["quarter"] + PANEL_COLUMNS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--start", default="1990Q1")
    ap.add_argument("--end", default=None,
                    help="default: last full quarter (ragged-edge rule)")
    ap.add_argument("--strict", action="store_true",
                    help="fail on any missing raw input instead of skipping")
    args = ap.parse_args()

    end = args.end or last_full_quarter()
    print(f"building panel {args.start}..{end}")
    panel = build(Path(args.raw_dir), args.start, end,
                  skip_missing=not args.strict)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.write_csv(out)
    filled = sum(panel[c].drop_nulls().len() > 0 for c in PANEL_COLUMNS)
    print(f"wrote {out}: {panel.height} quarters x {len(PANEL_COLUMNS)} "
          f"indicators ({filled} with data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
