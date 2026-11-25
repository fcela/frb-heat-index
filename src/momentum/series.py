"""Pulse of the Market series definitions.

Every series the section archives, grouped for the page. Two free
routes, both already used by this repo; no API key, no paid feed:

  route "yahoo" — Yahoo Finance v8 chart API (unadjusted daily closes),
                  the feed src/pulse/factor_rotation.py uses.
  route "fred"  — FRED via fredgraph CSV (src/pulse/fred.py).
  route "ebp"   — the Federal Reserve's FEDS Notes EBP file, read by
                  the existing fetcher src/pulse/cds_bond_basis.py.
  route "derived" — computed by the build from other archived series;
                  the construction is stated in the definition.

status: "live" (fetchable from a verified public source) or
"live-proxy" (public stand-in for an unlicensed ideal measure).
Anything that cannot be sourced free is not silently substituted; it
would be listed here as "pending-sourcing" and charted as a gap.

Commentary standard (as in pulse): what the series measures, why it is
on the page — for the leading block, the lead relationship it is there
to capture — its frequency and lag, and known drawbacks. Full text:
docs/momentum/methodology.md.
"""

FETCHABLE_STATUSES = ("live", "live-proxy", "live-experimental", "live-manual")

_FRED = "https://fred.stlouisfed.org/series/{}"


def _fred(key, fid, title, why, definition, frequency, unit, note=""):
    return key, {
        "title": title,
        "why": why,
        "source": f"FRED: {fid} — fred.stlouisfed.org/series/{fid}",
        "source_url": _FRED.format(fid),
        "series_id": fid,
        "definition": definition,
        "frequency": frequency,
        "status": "live",
        "unit": unit,
        "note": note,
        "route": "fred",
        "fred_id": fid,
    }


def _yahoo(key, sym, title, why, definition, unit="USD (close)", note=""):
    return key, {
        "title": title,
        "why": why,
        "source": f"Yahoo Finance chart API: {sym} — finance.yahoo.com/quote/{sym}",
        "source_url": f"https://finance.yahoo.com/quote/{sym}",
        "series_id": f"Yahoo Finance chart API: {sym} (unadjusted close)",
        "definition": definition,
        "frequency": "Daily",
        "status": "live",
        "unit": unit,
        "note": note,
        "route": "yahoo",
        "symbol": sym,
    }


def _sector(sym, name):
    return _yahoo(
        sym.lower(), sym, f"{name} ({sym})",
        f"Daily close of the {name} sector SPDR. One panel of the "
        "sector momentum grid: the grid is the evidence underneath the "
        "rotation states — leadership is read from these panels' "
        "relative returns, not asserted.",
        f"Daily unadjusted close of the {name} sector SPDR ({sym}), "
        "in USD; trading days only.",
    )


GROUPS = {
    "leading": {
        "title": "Leading indicators",
        "subtitle": "The series that historically turn before broad "
                    "risk — credit, financial conditions, the volatility "
                    "term structure, the curve, and the fastest hard "
                    "data. Each panel is captioned with the lead it is "
                    "there to capture.",
        "indicators": dict([
            _fred(
                "hy_oas", "BAMLH0A0HYM2", "High-yield credit spread (OAS)",
                "Credit spreads typically widen before equity drawdowns "
                "deepen and tighten before durable equity bottoms; this "
                "is the confirmation channel for any equity stress. The "
                "same series is carried by the Market Pulse layer; it is "
                "archived here as well because the fingerprint states "
                "are computed only from this section's own archive.",
                "ICE BofA US High Yield Index option-adjusted spread "
                "(FRED BAMLH0A0HYM2); daily, in percent.",
                "Daily", "percent",
                "Widely used as the credit-confirmation gauge: the "
                "fingerprint bar is +0.50 of widening over 21 sessions."),
            _fred(
                "ig_oas", "BAMLC0A0CM", "Investment-grade credit spread (OAS)",
                "Slower, higher-quality credit. IG widening after "
                "HY-only widening marks stress graduating up the quality "
                "ladder; IG staying calm while HY widens marks a "
                "contained, low-quality event.",
                "ICE BofA US Corporate Index option-adjusted spread "
                "(FRED BAMLC0A0CM); daily, in percent.",
                "Daily", "percent"),
            ("ebp", {
                "title": "Excess bond premium",
                "why": "The Gilchrist–Zakrajšek credit-sentiment "
                       "component of corporate spreads: the part of "
                       "spreads not explained by expected default. It "
                       "rises ahead of recessions and equity bear "
                       "markets in the Fed's own work, and led the "
                       "2007–08 widening. Read by the existing pulse "
                       "fetcher from the same FEDS Notes file.",
                "source": "Federal Reserve FEDS Notes EBP series — federalreserve.gov",
                "source_url": "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv",
                "series_id": "EBP (Gilchrist–Zakrajšek)",
                "definition": "Excess Bond Premium (Gilchrist–Zakrajšek "
                              "methodology, FEDS Notes series) — the "
                              "non-default component of corporate bond "
                              "spreads; monthly, in percentage points.",
                "frequency": "Monthly",
                "status": "live-proxy",
                "unit": "percentage points",
                "note": "Monthly and revised with the FEDS vintage; a "
                        "sentiment gauge, not a traded price.",
                "route": "ebp",
            }),
            _fred(
                "nfci", "NFCI", "Chicago Fed NFCI",
                "Composite financial conditions lead real activity and "
                "risk-asset stress by construction of the Chicago Fed's "
                "factor model (105 weekly series). Positive = tighter "
                "than average. The level is the section's conditions "
                "anchor: markets can fall a long way while this still "
                "prints loose, and that gap is a classification signal.",
                "Chicago Fed National Financial Conditions Index "
                "(FRED NFCI); weekly (week ended Wednesday, published "
                "Friday), index (0 = average conditions).",
                "Weekly", "index",
                "Restated by the Chicago Fed as source data revise; the "
                "archive keeps the latest vintage."),
            _fred(
                "nfci_credit", "NFCICREDIT", "NFCI — credit subindex",
                "The credit component of the NFCI: credit reprices fast "
                "*during* events, so this subindex is the coincident-to-"
                "early read on whether a market move is becoming a "
                "funding problem.",
                "Chicago Fed NFCI credit subindex (FRED NFCICREDIT); "
                "weekly, index (0 = average).",
                "Weekly", "index"),
            _fred(
                "nfci_leverage", "NFCILEVERAGE", "NFCI — leverage subindex",
                "The leverage component of the NFCI: leverage builds "
                "slowly *before* events, so a rising leverage subindex "
                "from a low base is the quiet vulnerability signal — "
                "the mirror image of the credit subindex's fast "
                "repricing.",
                "Chicago Fed NFCI leverage subindex (FRED "
                "NFCILEVERAGE); weekly, index (0 = average).",
                "Weekly", "index"),
            _fred(
                "nfci_risk", "NFCIRISK", "NFCI — risk subindex",
                "The risk component of the NFCI (volatility and "
                "risk-appetite measures): the fastest-moving of the "
                "three subindexes, and the one that should move first "
                "if a vol event is becoming a conditions event.",
                "Chicago Fed NFCI risk subindex (FRED NFCIRISK); "
                "weekly, index (0 = average).",
                "Weekly", "index"),
            _fred(
                "anfci", "ANFCI", "Chicago Fed ANFCI (adjusted)",
                "The NFCI adjusted to remove the part of conditions "
                "explained by the state of the economy itself — the "
                "purer financial impulse. Agreement between NFCI and "
                "ANFCI is a robustness check on the conditions read.",
                "Chicago Fed Adjusted NFCI (FRED ANFCI); weekly, "
                "index (0 = average conditions).",
                "Weekly", "index"),
            _fred(
                "stlfsi4", "STLFSI4", "St. Louis Fed Financial Stress Index",
                "A second conditions composite, built differently "
                "(18 weekly rates, spreads and vol series, principal "
                "components). Agreement between STLFSI4 and the NFCI "
                "family is the robustness check; divergence is itself "
                "information about which construction is driving the "
                "read.",
                "St. Louis Fed Financial Stress Index, version 4 "
                "(FRED STLFSI4); weekly, index (0 = average stress).",
                "Weekly", "index"),
            _fred(
                "vix", "VIXCLS", "VIX (spot)",
                "The 30-day implied volatility of the S&P 500. Spot vol "
                "is coincident, but its level against the 3-month VIX "
                "(the derived spread below) is one of the cleanest "
                "stress fingerprints in free data.",
                "CBOE Volatility Index, daily close (FRED VIXCLS); "
                "daily, in vol points.",
                "Daily", "vol points"),
            _fred(
                "vxv", "VXVCLS", "VIX 3-month (VXV)",
                "The 3-month implied volatility of the S&P 500 — the "
                "denominator of the vol term structure. Spot above "
                "3-month (backwardation) marks dislocation onset; "
                "normalisation has historically been among the "
                "earliest signs a dislocation is ending.",
                "CBOE VIX 3-Month Volatility Index, daily close "
                "(FRED VXVCLS); daily, in vol points.",
                "Daily", "vol points"),
            _fred(
                "gvz", "GVZCLS", "Gold vol (GVZ)",
                "Implied volatility of gold. A companion to the equity "
                "vol panel: gold vol rising with equity vol marks a "
                "broad haven scramble; gold vol calm while equity vol "
                "spikes marks an equity-local event.",
                "CBOE Gold ETF Volatility Index, daily close (FRED "
                "GVZCLS); daily, in vol points.",
                "Daily", "vol points"),
            _fred(
                "ovx", "OVXCLS", "Oil vol (OVX)",
                "Implied volatility of crude oil. The companion gauge "
                "for energy shocks: an oil price move with OVX spiking "
                "is a supply-scare signature; the same move with OVX "
                "falling is an unwind.",
                "CBOE Crude Oil ETF Volatility Index, daily close "
                "(FRED OVXCLS); daily, in vol points.",
                "Daily", "vol points"),
            _fred(
                "t10y2y", "T10Y2Y", "Yield curve, 10y − 2y",
                "The classic slow lead: inversion precedes recessions "
                "by quarters. The nearer-term signal this series "
                "contributes is the inflection — re-steepening after "
                "inversion has historically sat much closer to "
                "recessions than the inversion itself (Section 4 of "
                "the methodology).",
                "10-year Treasury yield minus 2-year Treasury yield "
                "(FRED T10Y2Y); daily, in percentage points.",
                "Daily", "percentage points"),
            _fred(
                "t10y3m", "T10Y3M", "Yield curve, 10y − 3m",
                "The 10y−3m slope — the curve measure in the New York "
                "Fed's recession-probability model. Shown beside "
                "10y−2y because the two can disagree when the front end "
                "is pinned by the expected policy path.",
                "10-year Treasury yield minus 3-month Treasury yield "
                "(FRED T10Y3M); daily, in percentage points.",
                "Daily", "percentage points"),
            _fred(
                "claims", "ICSA", "Initial jobless claims",
                "The fastest hard-data labour series (weekly, one-week "
                "lag). A sustained turn up in claims is the standard "
                "early recession tell; claims staying low while markets "
                "fall is itself a classification signal — a financial "
                "event, not yet a macro event.",
                "Initial claims for unemployment insurance, seasonally "
                "adjusted (FRED ICSA); weekly, in persons.",
                "Weekly", "persons"),
            _fred(
                "sentiment", "UMCSENT", "Consumer sentiment (Michigan)",
                "Household expectations turn early but noisily. "
                "Included as context and weighted accordingly: monthly, "
                "survey-based, and prone to partisan swings that do "
                "not map to spending.",
                "University of Michigan Consumer Sentiment index "
                "(FRED UMCSENT); monthly, index (1966 = 100).",
                "Monthly", "index",
                "Labelled monthly and noisy on the page, per the "
                "design: context, not a trigger."),
        ]),
    },
    "sectors": {
        "title": "Sector grid and benchmarks",
        "subtitle": "Daily closes for the sector momentum grid, the "
                    "benchmarks rotation is measured against, and the "
                    "high-beta / low-vol factor pair. Unadjusted closes "
                    "(Yahoo Finance) — the repo's existing practice.",
        "indicators": dict([
            _yahoo(
                "spy", "SPY", "S&P 500 (SPY)",
                "The benchmark every relative-strength number on the "
                "page is measured against, and the equity leg of the "
                "credit-confirmation and snapback states.",
                "Daily unadjusted close of SPDR S&P 500 ETF Trust "
                "(SPY), in USD; trading days only."),
            _yahoo(
                "rsp", "RSP", "Equal-weight S&P 500 (RSP)",
                "The breadth proxy: the average stock, as opposed to "
                "the cap-weighted index. RSP minus SPY momentum is the "
                "broadening/narrowing fingerprint — true constituent "
                "breadth has no free source, so the proxy is labelled "
                "as one.",
                "Daily unadjusted close of Invesco S&P 500 "
                "Equal Weight ETF (RSP), in USD; trading days only."),
            _yahoo(
                "qqq", "QQQ", "Nasdaq-100 (QQQ)",
                "The mega-cap growth benchmark. Its rotation quadrant "
                "is half of the concentration-unwind test: leadership "
                "leaving QQQ while RSP holds up is broadening, not "
                "exit.",
                "Daily unadjusted close of Invesco QQQ Trust (QQQ), "
                "in USD; trading days only."),
            _yahoo(
                "iwm", "IWM", "Small caps — Russell 2000 (IWM)",
                "Small caps are the domestic-cycle tell: they lag in "
                "risk-off rotations and lead early in recoveries. "
                "(The Russell 2000 index itself does not resolve on "
                "FRED; the ETF is the free route.)",
                "Daily unadjusted close of iShares Russell 2000 ETF "
                "(IWM), in USD; trading days only."),
            _sector("XLK", "Technology"),
            _sector("XLF", "Financials"),
            _sector("XLE", "Energy"),
            _sector("XLV", "Health care"),
            _sector("XLY", "Consumer discretionary"),
            _sector("XLP", "Consumer staples"),
            _sector("XLI", "Industrials"),
            _sector("XLB", "Materials"),
            _sector("XLU", "Utilities"),
            _sector("XLRE", "Real estate"),
            _sector("XLC", "Communication services"),
            _yahoo(
                "soxx", "SOXX", "Semiconductors (SOXX)",
                "Semiconductors are the highest-beta expression of the "
                "AI-capex complex and usually its most volatile panel; "
                "a grid member in their own right, not folded into "
                "technology.",
                "Daily unadjusted close of iShares Semiconductor ETF "
                "(SOXX), in USD; trading days only."),
            _yahoo(
                "kre", "KRE", "Regional banks (KRE)",
                "Regional banks are where equity stress becomes "
                "funding stress first (2023 is the template). A grid "
                "member because its quadrant is an early read on "
                "whether a selloff is reaching the banking system.",
                "Daily unadjusted close of SPDR S&P Regional Banking "
                "ETF (KRE), in USD; trading days only."),
            _yahoo(
                "xhb", "XHB", "Homebuilders (XHB)",
                "Homebuilders are the most rate-sensitive equity "
                "panel: the equity market's own read on whether the "
                "rate path is breaking housing.",
                "Daily unadjusted close of SPDR S&P Homebuilders ETF "
                "(XHB), in USD; trading days only."),
            _yahoo(
                "sphb", "SPHB", "High beta (SPHB)",
                "The risk-appetite leg of the factor pair. The "
                "SPHB−SPLV 60-session spread (the pulse layer's "
                "factor_rotation series, same construction) is the "
                "displayed cross-check on the rotation fingerprint: "
                "high beta rolling over before the index is the "
                "classic early tell.",
                "Daily unadjusted close of Invesco S&P 500 High Beta "
                "ETF (SPHB), in USD; trading days only."),
            _yahoo(
                "splv", "SPLV", "Low volatility (SPLV)",
                "The defensive leg of the factor pair — see SPHB.",
                "Daily unadjusted close of Invesco S&P 500 Low "
                "Volatility ETF (SPLV), in USD; trading days only."),
        ]),
    },
    "crossasset": {
        "title": "Cross-asset",
        "subtitle": "Index levels, yields, the dollar, energy, gold, "
                    "Bitcoin, and credit ETF prices — the raw material "
                    "of the movers table.",
        "indicators": dict([
            _fred(
                "sp500", "SP500", "S&P 500 index",
                "The index level itself (FRED's daily S&P 500), shown "
                "beside the SPY ETF so index and traded price can be "
                "compared; the movers table's equity anchor.",
                "S&P 500 index level (FRED SP500); daily, in index "
                "points.",
                "Daily", "index points"),
            _fred(
                "nasdaq", "NASDAQCOM", "Nasdaq Composite",
                "The broader growth tape. Nasdaq's one-day snaps in a "
                "drawdown are the raw material the snapback state "
                "tests at the 5-session window.",
                "Nasdaq Composite index level (FRED NASDAQCOM); "
                "daily, in index points.",
                "Daily", "index points"),
            _fred(
                "dgs2", "DGS2", "2-year Treasury yield",
                "The policy-expectations yield. Its 21-session change "
                "separates a market fall the Fed is expected to answer "
                "from one it is expected to watch.",
                "2-year Treasury constant maturity rate (FRED DGS2); "
                "daily, in percent.",
                "Daily", "percent"),
            _fred(
                "dgs10", "DGS10", "10-year Treasury yield",
                "The long rate. A 21-session move of 40 bp or more, "
                "either way, is the rates-shock fingerprint — the "
                "direction decides whether it is a growth scare or an "
                "inflation scare.",
                "10-year Treasury constant maturity rate (FRED "
                "DGS10); daily, in percent.",
                "Daily", "percent"),
            _fred(
                "t10yie", "T10YIE", "10-year breakeven inflation",
                "The market's 10-year inflation expectation, priced "
                "daily: separates an inflation scare the market "
                "believes from one it expects policy to crush.",
                "10-year breakeven inflation rate (FRED T10YIE); "
                "daily, in percent.",
                "Daily", "percent"),
            _fred(
                "dfii10", "DFII10", "10-year real yield (TIPS)",
                "The real long rate — the tightness of financial "
                "conditions that does not depend on inflation "
                "expectations. Rising real yields into an equity fall "
                "mark a policy-driven event.",
                "10-year Treasury inflation-indexed constant maturity "
                "rate (FRED DFII10); daily, in percent.",
                "Daily", "percent"),
            _fred(
                "dollar", "DTWEXBGS", "Broad dollar index",
                "A rising dollar tightens global financial conditions "
                "with a lag; dollar momentum turns have led risk turns "
                "in tightening cycles. The dollar-turn inflection "
                "(Section 4 of the methodology) is computed from this "
                "series. Publication lags the observation by several "
                "days.",
                "Nominal broad US dollar index (FRED DTWEXBGS); "
                "daily, index (Jan 2006 = 100).",
                "Daily", "index",
                "Publication lag: the latest print is several business "
                "days old at any build."),
            _fred(
                "euro", "DEXUSEU", "Euro (USD per EUR)",
                "The bilateral leg of the dollar read and the spot "
                "rate behind the pulse layer's cross-currency basis: "
                "a euro falling with the broad dollar rising confirms "
                "a dollar event rather than a euro event.",
                "US dollar to one euro exchange rate (FRED DEXUSEU); "
                "daily, in USD per EUR.",
                "Daily", "USD per EUR"),
            _fred(
                "wti", "DCOILWTICO", "WTI crude",
                "The energy-shock leg: a 21-session move of 15% or "
                "more, either direction, is a fingerprint — up is a "
                "supply shock, down is the shock unwinding or demand "
                "failing, and the two have opposite inflation "
                "implications. Publication lags by about a week.",
                "Crude oil price, West Texas Intermediate (FRED "
                "DCOILWTICO); daily, in USD per barrel.",
                "Daily", "USD/barrel",
                "Publication lag: the latest print is about a week "
                "old at any build."),
            _fred(
                "brent", "DCOILBRENTEU", "Brent crude",
                "The global crude benchmark, beside WTI: the "
                "Brent–WTI gap separates a global supply event from a "
                "US logistics event.",
                "Crude oil price, Brent Europe (FRED "
                "DCOILBRENTEU); daily, in USD per barrel.",
                "Daily", "USD/barrel",
                "Publication lag: the latest print is about a week "
                "old at any build."),
            _fred(
                "bitcoin", "CBBTCUSD", "Bitcoin",
                "The most liquid 24/7 risk asset: it trades through "
                "weekends and holidays when nothing else on the page "
                "does, so its weekend moves are the first print of "
                "the next session's risk appetite.",
                "Coinbase Bitcoin price (FRED CBBTCUSD); daily, in "
                "USD.",
                "Daily", "USD"),
            _yahoo(
                "hyg", "HYG", "High-yield bonds (HYG)",
                "The traded price of high-yield credit — the equity "
                "market's own credit gauge, beside the OAS. Price and "
                "spread disagreeing is a flow signal, not a data "
                "error.",
                "Daily unadjusted close of iShares iBoxx $ High Yield "
                "Corporate Bond ETF (HYG), in USD; trading days only."),
            _yahoo(
                "lqd", "LQD", "Investment-grade bonds (LQD)",
                "The traded price of investment-grade credit — see "
                "HYG.",
                "Daily unadjusted close of iShares iBoxx $ "
                "Investment Grade Corporate Bond ETF (LQD), in USD; "
                "trading days only."),
            _yahoo(
                "tlt", "TLT", "Long Treasuries (TLT)",
                "Duration as an asset: when equities fall and TLT "
                "rises, duration is doing the haven work; when both "
                "fall, the shock is in rates themselves.",
                "Daily unadjusted close of iShares 20+ Year Treasury "
                "Bond ETF (TLT), in USD; trading days only."),
            _yahoo(
                "gld", "GLD", "Gold (GLD)",
                "Gold. FRED's spot fixing series were retired by "
                "their source, so the free route is the ETF. The "
                "haven-bid fingerprint needs gold *up* while equities "
                "fall — gold falling with equities is reported as a "
                "failed haven, which is itself the signal.",
                "Daily unadjusted close of SPDR Gold Shares (GLD), "
                "in USD; trading days only.",
                note="FRED retired both LBMA fixing series "
                     "(GOLDAMGBD228NLBM / GOLDPMGBD228NLBM); the ETF "
                     "is the free source."),
            _yahoo(
                "uso", "USO", "Oil fund (USO)",
                "The traded oil price, beside the FRED spot prints: "
                "USO trades daily with no publication lag, so it is "
                "the freshest energy read on the page (futures-based, "
                "so it carries roll yield).",
                "Daily unadjusted close of United States Oil Fund "
                "(USO), in USD; trading days only."),
        ]),
    },
    "derived": {
        "title": "Derived spreads",
        "subtitle": "Computed by this build from the archived series "
                    "above; the construction is stated in each "
                    "definition. No inputs beyond this section's own "
                    "archive.",
        "indicators": {
            "vix_spread": {
                "title": "Volatility term structure (VIX − VXV)",
                "why": "Spot vol above 3-month vol (backwardation) is "
                       "a coincident-to-slightly-leading stress "
                       "marker: it flips at the onset of dislocations, "
                       "and its normalisation has historically been "
                       "one of the earliest signs a dislocation is "
                       "ending. This spread is the vol-inversion "
                       "fingerprint and the vol-curve-flip inflection.",
                "source": "Computed by this build from FRED VIXCLS and "
                          "VXVCLS, archived in this section — "
                          "github.com/fcela/frb-heat-index (data/momentum)",
                "source_url": "https://github.com/fcela/frb-heat-index/tree/main/data/momentum",
                "series_id": "VIXCLS − VXVCLS",
                "definition": "CBOE Volatility Index minus CBOE VIX "
                              "3-Month Volatility Index, on dates both "
                              "are observed (FRED VIXCLS, VXVCLS); "
                              "daily, in vol points. Positive = "
                              "backwardation (inversion).",
                "frequency": "Daily",
                "status": "live",
                "unit": "vol points",
                "note": "",
                "route": "derived",
            },
            "def_cyc_spread": {
                "title": "Defensive-minus-cyclical momentum spread",
                "why": "The measured value behind the risk-off / "
                       "risk-on rotation fingerprint, as a line: the "
                       "21-session return of the defensive trio "
                       "(staples, utilities, health care) minus the "
                       "cyclical trio (discretionary, industrials, "
                       "technology). Sustained readings beyond ±3 pp "
                       "are a rotation regime, not a day of noise.",
                "source": "Computed by this build from the archived "
                          "sector closes in this section — "
                          "github.com/fcela/frb-heat-index (data/momentum)",
                "source_url": "https://github.com/fcela/frb-heat-index/tree/main/data/momentum",
                "series_id": "mean R21(XLP,XLU,XLV) − mean R21(XLY,XLI,XLK)",
                "definition": "Mean trailing 21-session return of "
                              "XLP, XLU, XLV minus mean trailing "
                              "21-session return of XLY, XLI, XLK, on "
                              "their common trading days; daily, in "
                              "percentage points.",
                "frequency": "Daily",
                "status": "live",
                "unit": "percentage points",
                "note": "",
                "route": "derived",
            },
        },
    },
}


def all_series():
    """Yield (group_key, series_key, entry) in registry order."""
    for gkey, group in GROUPS.items():
        for skey, entry in group["indicators"].items():
            yield gkey, skey, entry


# --- universes used by the state computations (states.py) ---------------

# The sector momentum grid: eleven SPDRs + SOXX, KRE, XHB, IWM.
GRID = ["xlk", "xlf", "xle", "xlv", "xly", "xlp", "xli", "xlb", "xlu",
        "xlre", "xlc", "soxx", "kre", "xhb", "iwm"]

# Quadrants are computed for the grid plus the benchmark/factor members
# the named patterns reference.
QUADRANT_UNIVERSE = GRID + ["rsp", "qqq", "sphb", "splv"]

DEFENSIVE = ["xlp", "xlu", "xlv"]
CYCLICAL = ["xly", "xli", "xlk"]

DISPLAY_NAMES = {
    "spy": "S&P 500 (SPY)", "rsp": "Equal-weight (RSP)",
    "qqq": "Nasdaq-100 (QQQ)", "iwm": "Small caps (IWM)",
    "xlk": "Technology", "xlf": "Financials", "xle": "Energy",
    "xlv": "Health care", "xly": "Consumer discretionary",
    "xlp": "Consumer staples", "xli": "Industrials", "xlb": "Materials",
    "xlu": "Utilities", "xlre": "Real estate",
    "xlc": "Communication services", "soxx": "Semiconductors",
    "kre": "Regional banks", "xhb": "Homebuilders",
    "sphb": "High beta", "splv": "Low volatility",
}
