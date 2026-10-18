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
        "event_scope": "ladder",
        "release_kind": "annual",
        "palette": "categorical",
        "strike_order": "year",
        "event_date_mode": "year_end",
    },
}

FETCHABLE_STATUS = ("live",)

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
