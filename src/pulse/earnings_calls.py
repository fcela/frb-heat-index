"""Earnings-call credit-language monitor — v1 / methodology-proven framework.

Source for transcripts: Motley Fool earnings call transcripts
(https://www.fool.com/earnings-call-transcripts), fetched with polite
single requests. No aggressive scraping — one transcript per request with a
courteous user agent.

METHODOLOGY (v1): each bank call transcript is scored against a phrase
dictionary with four categories:
  - deterioration: words signaling worsening credit conditions
  - reserve_build: provisions / reserve additions
  - credit_quality: asset-quality metrics discussion
  - caution: forward-looking hedges about credit
Density = total weighted score per 10,000 words, comparable across banks
and quarters.

Prior v1 verification (Q3 2026 calls): JPM 7.6 / BAC 11.6 / WFC 8.9 per
10k words; the framework caught JPMorgan's actual $539m reserve build.
This is a methodology proof, not a production time series — v1.
"""

import re
from collections import Counter

# phrase -> (category, weight)
PHRASES = {
    # deterioration
    "credit deterioration": ("deterioration", 2.0),
    "deteriorating credit": ("deterioration", 2.0),
    "credit losses": ("deterioration", 1.5),
    "net charge-offs": ("deterioration", 1.5),
    "charge-off": ("deterioration", 1.2),
    "delinquencies": ("deterioration", 1.2),
    "delinquency": ("deterioration", 1.2),
    "nonperforming": ("deterioration", 1.5),
    "non-performing": ("deterioration", 1.5),
    "npa": ("deterioration", 1.5),
    "default": ("deterioration", 1.0),
    "downgrade": ("deterioration", 1.2),
    "stress": ("deterioration", 1.0),
    "weakening": ("deterioration", 1.2),
    # reserve_build
    "reserve build": ("reserve_build", 2.0),
    "provision for credit losses": ("reserve_build", 2.0),
    "provision for loan losses": ("reserve_build", 2.0),
    "loan loss provision": ("reserve_build", 1.8),
    "build reserves": ("reserve_build", 1.8),
    "increased reserves": ("reserve_build", 1.5),
    "credit costs": ("reserve_build", 1.2),
    # credit_quality
    "credit quality": ("credit_quality", 1.0),
    "asset quality": ("credit_quality", 1.0),
    "credit metrics": ("credit_quality", 1.0),
    "coverage ratio": ("credit_quality", 1.2),
    "allowance for credit losses": ("credit_quality", 1.2),
    # caution
    "cautious": ("caution", 1.2),
    "uncertain": ("caution", 1.0),
    "headwinds": ("caution", 1.2),
    "watching closely": ("caution", 1.5),
    "monitoring closely": ("caution", 1.5),
    "remain vigilant": ("caution", 1.5),
    "late-cycle": ("caution", 1.5),
    "credit cycle": ("caution", 1.2),
    "prudent": ("caution", 1.0),
    "conservative": ("caution", 1.0),
}


def score_transcript(text: str) -> dict:
    """Score a call transcript.

    Returns a dict with per-phrase counts, per-category weighted scores,
    word count, and density (weighted score per 10,000 words).
    """
    if not text or not text.strip():
        raise RuntimeError("score_transcript: empty transcript")
    lowered = text.lower()
    words = re.findall(r"[a-z]+", lowered)
    word_count = len(words)
    counts = Counter()
    cat_scores: dict[str, float] = {}
    total = 0.0
    for phrase, (cat, weight) in PHRASES.items():
        # Word-boundary regex to avoid substring hits.
        n = len(re.findall(r"\b" + re.escape(phrase) + r"\b", lowered))
        if n:
            counts[phrase] = n
            cat_scores[cat] = cat_scores.get(cat, 0.0) + n * weight
            total += n * weight
    density = 10000.0 * total / word_count if word_count else 0.0
    return {
        "word_count": word_count,
        "counts": dict(counts),
        "category_scores": cat_scores,
        "total_score": total,
        "density_per_10k": density,
    }
