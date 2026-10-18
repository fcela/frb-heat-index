"""Indicator registry for the prediction-markets pipeline.

The series that made the Phase-1 cut (dynamic discovery, honest gaps):
  KXFEDDECISION  FOMC decision buckets at the next meeting (front event)
  KXFED          Fed funds rate level thresholds after the next meeting
  KXCPIYOY       CPI YoY thresholds at the next monthly print
  KXGDP          Real GDP growth thresholds for the current quarter
  KXU3           Unemployment rate thresholds at the next monthly print
  KXRECSSNBER    NBER recession ladder (all open calendar years)

Rejected in Phase 1 and deliberately absent: KXPAYROLLS (thin, coarse
strikes, settlement exposed to jobs-report delay/cancellation), KXUSNFP
(zero open events), KXNBERRECESSQ (rolling quarter-ladder needs bespoke
splicing; the annual ladder covers it), KXCPI MoM (redundant with YoY),
KXU3EOY/annual variants (thin or redundant).

Strike labels: mutually-exclusive buckets keep Kalshi's own subtitles
verbatim; threshold ("greater") markets get ">=X%" labels; recession
years are derived from the event ticker. Event dates are never hardcoded:
they come from each market's close_time (or, for annual recession markets,
the calendar year in the event ticker).
"""

INDICATORS = {
    "fomc_decision": {
        "series_ticker": "KXFEDDECISION",
        "title": "FOMC rate decision",
        "why": "The single most-watched policy event on the calendar: market-implied "
              "probability of each decision bucket (cut, hold, hike) at the next "
              "scheduled FOMC meeting, from the exchange's highest-volume Fed series.",
        "source": "Kalshi public API v2 — series KXFEDDECISION",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Buckets are mutually exclusive and use Kalshi's own wording; "
                "\"Hike 0bps\" means no change to the target range.",
        "paper": "The paper finds the mode and median of the Kalshi distribution "
                 "perfectly matched the realized federal funds rate on the day before "
                 "every FOMC meeting since 2022 \u2014 a statistically significant "
                 "improvement over fed funds futures (MAE 0.000 vs 0.010, RMSE 0.000 "
                 "vs 0.020, p<0.05).",
        "event_scope": "front",
        "release_kind": "meeting",
        "palette": "categorical",
        "strike_order": "fomc",
        "event_date_mode": "close_time",
    },
    "fed_funds": {
        "series_ticker": "KXFED",
        "title": "Fed funds rate level",
        "why": "The market's full policy-rate distribution: threshold probabilities "
              "for the funds-rate setting after the next FOMC meeting, complementing "
              "the decision buckets with the level the market actually prices.",
        "source": "Kalshi public API v2 — series KXFED",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Each strike is the probability that the funds rate is at or above "
                "that level after the meeting; strikes are not mutually exclusive.",
        "paper": "The paper recommends Kalshi as the source for risk-neutral probability "
                 "densities of FOMC decisions at specific meetings: fed funds futures "
                 "force a two-outcome assumption, and SOFR options are too far removed "
                 "(basis and horizon mismatch).",
        "event_scope": "front",
        "release_kind": "meeting",
        "palette": "sequential",
        "strike_order": "numeric",
        "event_date_mode": "close_time",
    },
    "cpi_yoy": {
        "series_ticker": "KXCPIYOY",
        "title": "CPI inflation (YoY)",
        "why": "Real-time inflation expectations between quarterly heat-map updates: "
              "the odds the headline CPI print clears each year-over-year threshold.",
        "source": "Kalshi public API v2 — series KXCPIYOY",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Strikes are probabilities that YoY CPI prints above each threshold. "
                "The exchange sometimes lists a MoM market without the matching YoY "
                "market for a given print; those gaps are marked, never interpolated.",
        "paper": "For headline CPI the paper finds Kalshi statistically significantly "
                 "more accurate than the Bloomberg consensus: MAE 0.069 (mean) / 0.063 "
                 "(median, mode) vs 0.081; RMSE 0.080 vs 0.100. For core CPI the two "
                 "are statistically similar (MAE 0.070 both).",
        "event_scope": "front",
        "release_kind": "monthly",
        "palette": "sequential",
        "strike_order": "numeric",
        "event_date_mode": "close_time",
    },
    "gdp_quarterly": {
        "series_ticker": "KXGDP",
        "title": "GDP growth (quarterly)",
        "why": "Growth-nowcast risk priced ahead of the release: odds that real GDP "
              "growth for the current quarter clears each threshold.",
        "source": "Kalshi public API v2 — series KXGDP",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Strikes are probabilities that quarterly real GDP growth prints "
                "above each threshold; strikes are not mutually exclusive.",
        "paper": "The paper notes no other market-based distribution exists for GDP "
                 "growth \u2014 Kalshi is unique here.",
        "event_scope": "front",
        "release_kind": "quarterly",
        "palette": "sequential",
        "strike_order": "numeric",
        "event_date_mode": "close_time",
    },
    "unemployment": {
        "series_ticker": "KXU3",
        "title": "Unemployment rate",
        "why": "Labor-market slack as a priced probability: odds that the headline "
              "unemployment rate clears each threshold at the next monthly print.",
        "source": "Kalshi public API v2 — series KXU3",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Strikes are probabilities that the U-3 unemployment rate prints "
                "above each threshold; strikes are not mutually exclusive.",
        "paper": "The paper finds Kalshi comparable to the Bloomberg consensus for "
                 "unemployment (no significant accuracy difference); the Kalshi mode "
                 "moves in 0.1pp increments.",
        "event_scope": "front",
        "release_kind": "monthly",
        "palette": "sequential",
        "strike_order": "numeric",
        "event_date_mode": "close_time",
    },
    "recession": {
        "series_ticker": "KXRECSSNBER",
        "title": "NBER recession",
        "why": "The clearest market summary of macro tail risk: the probability that "
              "a recession is underway within each calendar year, per the NBER "
              "business-cycle dating the contracts settle on.",
        "source": "Kalshi public API v2 — series KXRECSSNBER",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Annual ladder: every open calendar-year market is charted. Trading "
                "runs into the following January while NBER dating catches up.",
        "paper": "Listed in the paper\u2019s Table 1 as a Kalshi macro series; the paper\u2019s "
                 "calibration analysis finds the implied probability densities fairly "
                 "well calibrated.",
        "event_scope": "ladder",
        "release_kind": "annual",
        "palette": "categorical",
        "strike_order": "year",
        "event_date_mode": "year_end",
    },
    "tail_risks": {
        # Derived indicator (no direct fetch): tail probabilities summed from
        # the CPI YoY and GDP strike ladders, following Figure 13 of the paper.
        "series_ticker": None,
        "derived_from": ["cpi_yoy", "gdp_quarterly"],
        "title": "Macro tail risks",
        "why": "Priced tail risk at a glance: the market-implied probability of "
              "high inflation prints and weak growth quarters, summed from the "
              "CPI and GDP strike ladders each day.",
        "source": "Derived from the CPI YoY and GDP series above (Kalshi public API v2)",
        "frequency": "Daily close, event markets",
        "unit": "probability",
        "status": "live",
        "note": "Each series is the probability the outcome clears its threshold, "
                "read off the day's strike ladder. Marginal densities only: no joint "
                "probability of high inflation AND weak growth is measurable from "
                "these markets.",
        "paper": "Follows Figure 13 of the paper, which plots the same tail-risk "
                 "time series; the paper stresses these are marginal densities.",
        "event_scope": "front",
        "release_kind": "monthly",
        "palette": "categorical",
        "strike_order": "tail",
        "event_date_mode": "close_time",
    },
    "ipo_pipeline": {
        "series_ticker": "KXIPO",
        "title": "IPO pipeline",
        "why": "A market-implied ECM pipeline gauge: the expected number of IPOs "
              "this year among the companies the exchange lists, plus the "
              "front-runners by announcement probability.",
        "source": "Kalshi public API v2 — series KXIPO",
        "frequency": "Daily close, event markets",
        "unit": "count / probability",
        "status": "live",
        "note": "Each company market pays $1 if it officially announces an IPO "
                "this calendar year; the expected count is the sum of the "
                "probabilities. The candidate set changes as companies are added "
                "or removed, so the count is a pipeline read, not a forecast.",
        "paper": "Equity-market expectations sit outside the paper's macro scope; "
                 "included here as the exchange's closest proxy for investment-"
                 "banking ECM activity.",
        "event_scope": "front",
        "release_kind": "annual",
        "palette": "categorical",
        "strike_order": "company",
        "event_date_mode": "year_end",
    },
}

# Business-line market exploration (2026-10-18, live API discovery):
#   KXIPO        INCLUDED as "ipo_pipeline" — annual "which companies will
#                announce an IPO this year" markets; the only liquid,
#                recurring IB/ECM-relevant series found.
#   KXINX        REJECTED — S&P 500 price-range markets exist but are all
#                daily (intraday horizon); day-trading noise, not the macro
#                expectations this section tracks.
#   KXBTC/KXETH  REJECTED — crypto price ranges; outside bank IB / global
#                markets / AWM coverage and mostly daily horizons.
#   KXOIL/KXGOLD REJECTED — 2022 events only; no live markets.
#   VIX, 10Y Treasury, credit spreads, M&A: no Kalshi series found — documented
#   as absent, not as an oversight.

FETCHABLE_STATUS = ("live",)

# Paper citation shared by every card that draws on Diercks/Katz/Wright (2026).
PAPER_CITE = ("Diercks/Katz/Wright (2026), \u201cKalshi and the Rise of Macro Markets,\u201d "
              "FEDS 2026-010")
PAPER_DOI = "https://doi.org/10.17016/FEDS.2026.010"

# FOMC decision buckets in policy-stance order (Kalshi's own subtitle wording).
FOMC_STRIKE_ORDER = {
    "Cut >25bps": 0,
    "Cut 25bps": 1,
    "Hike 0bps": 2,
    "Hike 25bps": 3,
    "Hike >25bps": 4,
}

# How far out a front market may close before a nearer scheduled release is
# presumed missing (a listed-print gap, marked honestly).
RELEASE_GAP_DAYS = {
    "meeting": 45,
    "monthly": 45,
    "quarterly": 130,
}

MONTH_CODES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
