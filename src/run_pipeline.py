#!/usr/bin/env python3
"""Run the heat-map index pipeline: data/panel_quarterly.csv -> components + aggregate
+ standardized indicators.

Pipeline (exactly the paper's documented construction):
  per indicator: sign orientation -> log | 10y-MA detrend | none
  -> standardize over the full available sample
  -> component = mean of its standardized indicators
  -> Gaussian-kernel CDF (Silverman bandwidth) to (0,1)
  aggregate = mean of the 14 components -> kernel CDF again.

The oriented standardized indicators are also written out
(data/indicators_standardized.csv): they are the actual component inputs
(higher = more vulnerable) and feed the site's hierarchical explorer.

Ragged-edge rule: input quarters beyond the last full calendar quarter are
dropped before the pipeline runs. Tail validation (from the end-of-sample
audit): any indicator whose standardized value jumps by more than 2.0 at the
final quarter is flagged on stderr before the index is written, so a bad
input at the edge can never silently become a headline artifact.

Usage:
    python src/run_pipeline.py [--panel data/panel_quarterly.csv]
                                [--components data/components.csv]
                                [--aggregate data/aggregate.csv]
                                [--mask-csv path/to/accessible.csv]

Requires: polars, numpy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from math import erf, sqrt
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent

# indicator -> (component, sign, transform)
# sign: +1 means higher raw value = more vulnerable; -1 flips.
# transform: 'log' | 'ma10' (subtract 10y moving average) | None.
# Keys are the accessible indicator names, verbatim (the panel's columns).
SPEC = {
    "Ratio of Total Assets Held by Top 5 Holding Companies to Total Bank Holding Company Assets": ("Size/concentration", +1, None),
    "Broker Dealer Leverage (Dealer financing)": ("Nonbank leverage", +1, None),
    "Short Term Wholesale Funding (Ratio of Gross Short-Term Wholesale Debt of the Financial Sector to GDP)": ("Short-term wholesale funding", +1, "ma10"),
    "Illiquidity Conventration Indicator": ("Size/concentration", +1, None),
    "Maturity Mismatch (Loans as a percent of deposits)": ("Maturity transformation", +1, None),
    "Weighted Average Maturity Gap at Commerical Banks": ("Maturity transformation", +1, None),
    "Broker Dealer Leverage (Broker dealer leverage ratio)": ("Nonbank leverage", -1, None),
    "Maturity Mismatch (Ratio of net short-term wholesale debt of the financial sector to GDP)": ("Maturity transformation", +1, "ma10"),
    "Ratio of U.S. Non Agency Securitization Issuance to GDP": ("Nonbank leverage", +1, None),
    "Hedge Fund Leverage (Gross assets to net assets, qualifying hedge funds)": ("Nonbank leverage", +1, None),
    "Hedge Fund Leverage (SCOOS net percent reporting increased use of leverage)": ("Nonbank leverage", +1, None),
    "Short-term Wholesale Funding (Ratio of total runnable money -like liabilities to GDP)": ("Short-term wholesale funding", +1, "ma10"),
    "Short-Term Wholesale Funding (Short-term money as a share of assets at BHCs)": ("Short-term wholesale funding", +1, None),
    "Bank Leverage (Total common equity to total assets)": ("Bank leverage", -1, None),
    "Bank Leverage (Tier 1 common equity ratio)": ("Bank leverage", -1, None),
    "Bank Leverage (Total Capital Ratio)": ("Bank leverage", -1, None),
    "Ratio of Total Financial Sector Liabilities to GDP": ("Size/concentration", +1, "ma10"),
    "Ratio of Nonfinancial Business Savings to GDP": ("Net saving", -1, None),
    "Comsumer Credit of Riskier Borrowers (Ratio of consumer credit debt owed by riskier borrowers to DPI)": ("Consumer credit", +1, None),
    "Consumer Credit (Consumer credit ratio to GDP)": ("Consumer credit", +1, "ma10"),
    "Consumer Credit (Consumer credit debt service ratio to DPI)": ("Consumer credit", +1, None),
    "Debt Burden of Nonfinancial Businesses (Debt-to-Income ratio)": ("Nonfinancial Business", +1, "ma10"),
    "Real Debt Growth of Nonfinancial Businesses": ("Nonfinancial Business", +1, None),
    "Ratio of Household Savings to GDP": ("Net saving", -1, None),
    "Debt Burden of Nonfinancial Business (Interest expenses)": ("Nonfinancial Business", +1, None),
    "Risky Debt of Nonfinancial Businesses (Deep junk Issuance Share)": ("Nonfinancial Business", +1, None),
    "Home Mortage Debt (Ratio of mortgage debt owed by riskier borrowers to DPI)": ("Home mortgages", +1, None),
    "Home Mortage Debt (Ratio of total mortgage debt to GDP)": ("Home mortgages", +1, "ma10"),
    "Home Mortgage Debt (Total mortgage debt service ratio to DPI)": ("Home mortgages", +1, None),
    "Risky Debt of Nonfinancial Businesses": ("Nonfinancial Business", +1, None),
    "Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back mortgages by riskier borrowers)": ("Home mortgages", +1, None),
    "Consumer Credit of Riskier Borrowers (Incidence of very rapid consumer borrowing by riskier borrowers)": ("Consumer credit", +1, None),
    "Mortgage Borrowing by Riskier Borrowers (Incidence of very rapid mortgage borrowing by riskier borrowers)": ("Home mortgages", +1, None),
    "Spread of Corporate Bonds (over Treasury Securities) (BBB)": ("Business credit", -1, "log"),
    "Indicators of Volitility (Credit Default Swap premium)": ("Price volatility", -1, "log"),
    "Equity Valuations (Ratro minus 10 Yr. Tres. Yeild)": ("Equity markets", -1, None),
    "Equity Valuations (Earnings/Price ratio )": ("Equity markets", -1, None),
    "Mortage Lending and FICO Scores (FICO scores)": ("Residential real estate", -1, None),
    "National House Prices Relative to Rents": ("Residential real estate", +1, "ma10"),
    "Spread of Corporate Bonds (over Treasury Securities) (High Yield)": ("Business credit", -1, "log"),
    "Investor Appetite for Risky Corporate Debt (Issuance of Junk Bonds and Leverage Loans)": ("Business credit", +1, "log"),
    "Prices for Commercial Real Estate (CRE)": ("Commercial real estate", +1, "ma10"),
    "Change in Lending Standards, CRE Loans": ("Commercial real estate", -1, None),
    "Mortage Lending and FICO Scores (Change in Lending Standards)": ("Residential real estate", -1, None),
    "Investor Appetite for Risky Corporate Debt (Change in Bank Lending Standards C&I Loans)": ("Business credit", -1, None),
    "Indicators of Volitility (VIX)": ("Price volatility", -1, "log"),
    "SRISK (Total capital shortfall relative to total assets)": ("Size/concentration", +1, None),
    "Granger-causality connections among large financial institutions": ("Size/concentration", +1, None),
}

COMPONENTS = ["Equity markets", "Price volatility", "Business credit",
              "Commercial real estate", "Residential real estate",
              "Nonfinancial Business", "Consumer credit", "Home mortgages",
              "Net saving", "Bank leverage", "Nonbank leverage",
              "Maturity transformation", "Short-term wholesale funding",
              "Size/concentration"]


def last_full_quarter(today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    cur = (today.month - 1) // 3 + 1
    if cur == 1:
        return f"{today.year - 1}Q4"
    return f"{today.year}Q{cur - 1}"


def rolling_mean_trailing(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """Trailing rolling mean over the last `window` observations, NaN-aware:
    the mean of the available values in each trailing window, requiring at
    least `min_periods` non-NaN values."""
    n = x.shape[0]
    out = np.full(n, np.nan)
    csum = np.cumsum(np.where(np.isnan(x), 0.0, x))
    cnt = np.cumsum((~np.isnan(x)).astype(float))
    csum_p = np.concatenate([[0.0], csum])
    cnt_p = np.concatenate([[0.0], cnt])
    for i in range(n):
        lo = max(0, i - window + 1)
        c = cnt_p[i + 1] - cnt_p[lo]
        if c >= min_periods:
            out[i] = (csum_p[i + 1] - csum_p[lo]) / c
    return out


def transform_column(vals: np.ndarray, sign: int, tf: str | None) -> np.ndarray:
    x = vals.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        if tf == "log":
            x = np.log(x)
        elif tf == "ma10":
            x = x - rolling_mean_trailing(x, 40, 20)
    return sign * x


def kernel_cdf(x: np.ndarray) -> np.ndarray:
    """Gaussian-kernel CDF estimate (Silverman bandwidth), evaluated at x."""
    x = np.asarray(x, dtype=float)
    xs = x[~np.isnan(x)]
    n = xs.size
    out = np.full_like(x, np.nan)
    if n < 2:
        return out
    h = 1.06 * xs.std(ddof=1) * n ** (-1 / 5)
    if h <= 0 or not np.isfinite(h):
        return out
    norm_cdf = np.vectorize(lambda t: 0.5 * (1 + erf(t / sqrt(2))))
    for i, xi in enumerate(x):
        if np.isnan(xi):
            continue
        out[i] = norm_cdf((xi - xs) / h).mean()
    return out


def standardize(mat: np.ndarray) -> np.ndarray:
    mu = np.nanmean(mat, axis=0)
    sd = np.nanstd(mat, axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (mat - mu) / sd


def run_pipeline(panel: pl.DataFrame,
                 mask: pl.DataFrame | None = None) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """panel: 'quarter' + indicator columns. Returns (components, aggregate, standardized indicators)."""
    quarters = panel["quarter"].to_list()
    cols = [c for c in SPEC if c in panel.columns]
    missing = [c for c in SPEC if c not in panel.columns]
    if missing:
        print(f"warning: {len(missing)} spec indicators absent from panel: "
              f"{missing[:3]}...", file=sys.stderr)

    mat = np.column_stack([
        panel[c].cast(pl.Float64, strict=False).to_numpy() for c in cols
    ])
    if mask is not None:
        mm = np.column_stack([
            mask[c].cast(pl.Float64, strict=False).to_numpy()
            if c in mask.columns else np.full(len(quarters), np.nan)
            for c in cols
        ])
        mat = np.where(np.isnan(mm), np.nan, mat)

    trans = np.column_stack([
        transform_column(mat[:, j], SPEC[c][1], SPEC[c][2])
        for j, c in enumerate(cols)
    ])
    std = standardize(trans)

    # tail validation: flag |Δ standardized| > 2 at the edge
    if std.shape[0] >= 2:
        jump = np.abs(std[-1] - std[-2])
        bad = [(cols[j], jump[j]) for j in range(len(cols))
               if np.isfinite(jump[j]) and jump[j] > 2.0]
        for name, d in bad:
            print(f"TAIL-FLAG {quarters[-1]}: {name[:60]} "
                  f"|Δ standardized| = {d:.2f} > 2.0", file=sys.stderr)

    comp = {}
    for cname in COMPONENTS:
        idx = [j for j, c in enumerate(cols) if SPEC[c][0] == cname]
        with np.errstate(invalid="ignore"):
            m = np.nanmean(std[:, idx], axis=1)
        m[np.all(np.isnan(std[:, idx]), axis=1)] = np.nan
        comp[cname] = kernel_cdf(m)

    comp_df = pl.DataFrame({"quarter": quarters, **comp})
    agg_raw = np.nanmean(np.column_stack([comp[c] for c in COMPONENTS]), axis=1)
    agg = kernel_cdf(agg_raw)
    agg_df = pl.DataFrame({"quarter": quarters, "Aggregate index": agg})
    # Oriented standardized indicators (the actual component inputs):
    # sign already applied, so higher z = more vulnerable. Saved for the
    # site's hierarchical explorer (indicator-level heat = empirical
    # quantile of z in its own history).
    std_df = pl.DataFrame({"quarter": quarters,
                           **{c: std[:, j] for j, c in enumerate(cols)}})
    return comp_df, agg_df, std_df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", default=str(HERE / "data" / "panel_quarterly.csv"))
    ap.add_argument("--components", default=str(HERE / "data" / "components.csv"))
    ap.add_argument("--aggregate", default=str(HERE / "data" / "aggregate.csv"))
    ap.add_argument("--mask-csv", default=None,
                    help="optional accessible raw panel: where it is NaN, the "
                         "proxy is also set to NaN (historical comparison)")
    ap.add_argument("--indicators", default=str(HERE / "data" / "indicators_standardized.csv"),
                    help="output CSV for oriented standardized indicators")
    args = ap.parse_args()

    panel = pl.read_csv(args.panel)
    edge = last_full_quarter()
    panel = panel.filter(pl.col("quarter") <= edge).sort("quarter")
    print(f"panel: {panel.height} quarters "
          f"({panel['quarter'][0]}..{panel['quarter'][-1]}), ragged edge {edge}")

    mask = pl.read_csv(args.mask_csv) if args.mask_csv else None
    comp_df, agg_df, std_df = run_pipeline(panel, mask)

    for path, df in ((args.components, comp_df), (args.aggregate, agg_df),
                     (args.indicators, std_df)):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.write_csv(p)
        print(f"wrote {p}: {df.height} rows")
    return 0


if __name__ == "__main__":
    main()
