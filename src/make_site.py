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
import json
import math
import sys
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
                    "Home mortgages", "Net saving"]},
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


def build(data: Path, out: Path) -> dict:
    """Build all site JSON files; return the meta dict that was written."""
    out.mkdir(parents=True, exist_ok=True)

    agg = pl.read_csv(data / "aggregate.csv")
    (out / "aggregate.json").write_text(
        dumps_strict(series_json(agg, "Aggregate index"), indent=1))

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
    (out / "meta.json").write_text(dumps_strict(meta, indent=1))

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
