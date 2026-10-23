"""Per-series completeness of data/panel_complete.csv with an explicit
documented-boundary allowlist.

Every indicator must be complete over 1990Q1-2026Q2 (146 quarters), OR its
null cells must form exactly the leading run allowed for it in
ALLOWED_BOUNDARIES, where every entry cites the source evidence for the
boundary. The slice that fills -- or finally documents -- a series updates
this allowlist in the same commit (work order: heat-completeness brief,
2026-10-21; all entries are final documented boundaries or filled series
as of 2026-10-22). Because the test fails on any hole not in
the allowlist, holes can never silently reappear, and because it fails on
any allowlist entry that no longer matches the data, entries can never rot.

Runnable with plain python3 (no pytest needed):
    python3 tests/test_completeness.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent.parent
PANEL = HERE / "data" / "panel_complete.csv"

ALL_QUARTERS = [f"{y}Q{q}" for y in range(1990, 2027)
                for q in range(1, 5) if not (y == 2026 and q > 2)]

# indicator -> (first_valid_quarter, evidence for the leading boundary).
# A series in this map may be null only BEFORE first_valid_quarter.
ALLOWED_BOUNDARIES: dict[str, tuple[str, str]] = {
    "Bank Leverage (Tier 1 common equity ratio)": (
        "2001Q1",
        "Documented regulatory-data boundary. The ratio's denominator, the "
        "Y-9C total risk-weighted assets item (BHCKB704, Schedule RC-R), "
        "first appears on the 2001Q1 report (FRB MDRM start date 3/31/2001; "
        "same for the Call Report RC-R items). Pre-2001 Y-9C RC-R collected "
        "only capital numerators (Tier 1 BHCK8274 from 1996Q1, Tier 2 "
        "BHCK8275 from 1998Q1, total qualifying BHCK3792) -- verified in "
        "the as-filed NIC microdata for 2000Q4 (BHCF20001231), which has no "
        "RWA item, and in the Chicago Fed BHC bulk extracts 1990-2000. Only "
        "other pre-2001 total-RWA item in the MDRM (8276) is FR 2886b "
        "(Edge corporations), not BHCs. Ratio not computable from primary "
        "public data pre-2001; no proxy denominator (no-silent-splicing "
        "rule)."),
    "Bank Leverage (Total Capital Ratio)": (
        "2001Q1",
        "Documented regulatory-data boundary, same denominator evidence as "
        "the Tier 1 common equity ratio: BHCKB704 (total risk-weighted "
        "assets) begins 2001Q1 (MDRM 3/31/2001); pre-2001 RC-R carried "
        "capital components only, so the ratio is not computable from "
        "primary public data before 2001Q1."),
    "Consumer Credit (Consumer credit debt service ratio to DPI)": (
        "2005Q1",
        "Documented source boundary. The panel series is the Fed's current "
        "credit-bureau-based household DSR release (FRED TDSP mirrors "
        "federalreserve.gov/releases/dsr/), whose history begins 2005:Q1 "
        "(release SDMX file FRB_dsr_xml.zip contains exactly 2005Q1-2026Q2; "
        "panel values match 1:1). Fed methodology note (2024-09-04, "
        "feds-notes/introducing-a-credit-bureau-based-measure-of-u-s-"
        "household-debt-service-20240904): full tradeline coverage in the "
        "underlying CCP begins 2005; the pre-2005 old-methodology DSR "
        "(housedebt archive, 1980Q1-2024Q1) is a different concept of debt "
        "service (2005Q1: 12.62 old vs 14.80 new) -- splicing it would "
        "inject an artificial level break (no-silent-splicing rule)."),
    "Home Mortgage Debt (Total mortgage debt service ratio to DPI)": (
        "2005Q1",
        "Documented source boundary, same evidence as the consumer DSR "
        "series: the Fed's credit-bureau-based mortgage DSR (FRED MDSP "
        "mirrors /releases/dsr/) begins 2005:Q1; the old-methodology "
        "archive (1980Q1-2024Q1) is a different concept (2005Q1: 6.27 old "
        "vs 7.50 new) and is not spliced."),
    "Mortage Lending and FICO Scores (FICO scores)": (
        "2003Q1",
        "Documented source boundary. The series is the UPB-weighted mean "
        "origination FICO of Fannie Mae single-family acquisitions, from "
        "Fannie Mae's credit-supplement acquisitions table; that "
        "published table's history begins with the 2003 acquisition year "
        "(the transcription, work/raw/fico_annual.csv, starts 2003; "
        "Fannie's earlier supplements profiled the outstanding book, not "
        "acquisitions by year). No same-series source reaches earlier: "
        "the FRBNY CCP/Equifax panel (1999+) measures a different "
        "population and statistic (borrower scores across all lenders "
        "in a 5% consumer sample), and FHFA's one-off 2001-2008 GSE "
        "study publishes credit-score band shares, not UPB-weighted "
        "means, so the series value cannot be recovered from it. "
        "Splicing either would change the measured concept "
        "(no-silent-splicing rule)."),
    "Mortage Lending and FICO Scores (Change in Lending Standards)": (
        "2007Q2",
        "Documented source boundary. Panel source FRED DRTSPM (net % of "
        "banks tightening standards for PRIME mortgages); first "
        "observation 2007-04-01 (FRED pull, 2026-10-22). Through 2007Q1 "
        "the SLOOS asked a single all-residential mortgage-standards "
        "question (its discontinued FRED series spans 1990Q3-2007Q1); "
        "the April 2007 survey split residential standards into prime / "
        "nontraditional / subprime and the aggregate was discontinued. "
        "The prime-only concept did not exist before 2007Q2; splicing "
        "the all-residential aggregate onto it would change the "
        "measured concept mid-series (no-silent-splicing rule; same "
        "disposition as the DSR boundaries)."),
    "SRISK (Total capital shortfall relative to total assets)": (
        "2000Q1",
        "Documented input-reliability boundary (truncation recorded in "
        "src/systemic/build_srisk.py). Firm market caps need shares "
        "outstanding: public anchors are EDGAR companyfacts (2009Q3+) "
        "and 10-K cover anchors for FY2004-FY2010 "
        "(src/systemic/fetch_10k_anchors.py); pre-2004 counts are "
        "split-adjusted backward carries from the 2004 anchor. Carries "
        "longer than ~4 years run through the late-1990s buyback wave "
        "and make market caps (hence SRISK levels) unreliable, so the "
        "aggregate is truncated at 2000Q1. V-Lab's own public SRISK "
        "history likewise begins ~2000. Book inputs (Y-9C) and prices "
        "reach 1990; the binding constraint is share counts, not a "
        "missing source series."),
    "Mortgage Borrowing by Riskier Borrowers (Incidence of piggy-back "
    "mortgages by riskier borrowers)": (
        "1990Q4",
        "Documented source boundary. Numerator FRED BOGZ1FL893065125Q "
        "(Z.1 FL893065125.Q, home equity loans): first observation "
        "1990-10-01 (FRED pull, 2026-10-22) -- the Z.1 home-equity-loan "
        "instrument line itself begins 1990Q4, so 1990Q1-Q3 do not "
        "exist in the source. Denominator (HHMSDODNS) reaches 1947Q4 "
        "and is not the constraint."),
    "Change in Lending Standards, CRE Loans": (
        "1990Q2",
        "SLOOS itself begins 1990Q2 (first Senior Loan Officer Opinion "
        "Survey); the CRE aggregate is transcribed from the SLOOS "
        "historical XML from its first observation (1990Q2). Documented "
        "survey-start boundary, not a defect."),
    "Investor Appetite for Risky Corporate Debt (Change in Bank Lending "
    "Standards C&I Loans)": (
        "1990Q2",
        "FRED DRTSCILM begins 1990Q2 -- SLOOS itself begins 1990Q2 "
        "(expected survey-start boundary, per data/proxy_catalog.csv)."),
}


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return False
    return True


def main() -> int:
    ok = True
    panel = pl.read_csv(PANEL)
    ok &= check(panel.height == 146 and panel.width == 47,
                f"panel_complete shape {panel.height}x{panel.width}, want 146x47")
    ok &= check(panel["quarter"].to_list() == ALL_QUARTERS,
                "panel_complete quarters must be exactly 1990Q1..2026Q2")

    cols = [c for c in panel.columns if c != "quarter"]
    allowed = set(ALLOWED_BOUNDARIES)
    unknown = [c for c in cols if c not in allowed
               and panel[c].null_count() > 0]
    ok &= check(not unknown,
                f"holes in series with NO allowlist entry: {unknown}")

    for c, (first_valid, evidence) in ALLOWED_BOUNDARIES.items():
        ok &= check(bool(evidence.strip()),
                    f"{c}: allowlist entry lacks evidence citation")
        v = panel.select(["quarter", c])
        nulls = v.filter(pl.col(c).is_null())["quarter"].to_list()
        expected = [q for q in ALL_QUARTERS if q < first_valid]
        ok &= check(nulls == expected,
                    f"{c}: nulls {nulls[:3]}..{nulls[-3:]} (n={len(nulls)}) "
                    f"!= allowed leading run ending at {first_valid} "
                    f"(n={len(expected)})")

    print("test_completeness: " + ("ALL PASS" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
