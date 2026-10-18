"""Recover implied probability distributions from Kalshi strike ladders.

Methodology follows Diercks/Katz/Wright (2026), "Kalshi and the Rise of Macro
Markets," FEDS 2026-010, Section 3:

- Each binary contract pays $1 if the outcome EXCEEDS the strike; the "Yes"
  price is the risk-neutral probability of exceeding the strike.
- Bin probability = Yes_price(previous strike) - Yes_price(current strike),
  with strikes sorted ascending. The bottom bin (outcome below the lowest
  strike) is 1 - P(>= lowest); the top bin (outcome at/above the highest
  strike) is P(>= highest).
- Days without trades carry the exchange's previous close (volume = 0 marks
  them in the CSVs), so stale strikes are handled upstream by price carryover.
- Two adjacent strikes with the same price (common in thin tails): the paper
  assigns the probability to the outcome closer to the mode. Implemented here
  as mode-outward monotonicity enforcement: walking outward from the mode bin
  in each direction, no bin may carry more mass than the one nearer the mode.
- Remaining negative bin masses (illiquid-tail artifacts) are clamped to 0
  and the distribution renormalized to sum to 1.

Caveats surfaced wherever these views render: these are risk-neutral
(Q-measure) probabilities, not physical ones; tail contracts are thin and
can be stale.

For mutually-exclusive outcome markets (e.g. FOMC decision buckets), prices
are used directly and renormalized.
"""

import math


def parse_level(label: str) -> float | None:
    """'≥4.25%' -> 4.25; '4.25%' -> 4.25; '3.1' -> 3.1; 'Hike 25bps' -> None."""
    s = str(label).strip().lstrip("≥").rstrip("%").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def recover_threshold_bins(levels: list[float], probs: list[float]) -> list[dict]:
    """Discrete distribution from a threshold ("greater") strike ladder.

    levels[i] is the strike, probs[i] = P(outcome >= levels[i]).
    Returns bins as {low, high, mass} with low/high possibly None at the
    open ends (below-lowest / at-or-above-highest).
    """
    pairs = sorted(zip(levels, probs), key=lambda p: p[0])
    n = len(pairs)
    if n == 0:
        return []
    if n == 1:
        # Single strike: all mass split below/above it.
        p = _clamp01(probs[0])
        return [
            {"low": None, "high": pairs[0][0], "mass": 1.0 - p},
            {"low": pairs[0][0], "high": None, "mass": p},
        ]
    bins = []
    # Bottom bin: outcome below the lowest strike.
    bins.append({"low": None, "high": pairs[0][0],
                 "mass": 1.0 - _clamp01(pairs[0][1])})
    for i in range(n - 1):
        lo, p_lo = pairs[i]
        hi, p_hi = pairs[i + 1]
        bins.append({"low": lo, "high": hi,
                     "mass": _clamp01(p_lo) - _clamp01(p_hi)})
    # Top bin: outcome at or above the highest strike.
    bins.append({"low": pairs[-1][0], "high": None,
                 "mass": _clamp01(pairs[-1][1])})
    return _enforce_mode_monotonicity(bins)


def recover_exclusive_buckets(probs: list[float]) -> list[float]:
    """Mutually-exclusive outcomes: normalize prices to a distribution."""
    ps = [_clamp01(p) for p in probs]
    total = sum(ps)
    if total <= 0:
        return [0.0] * len(ps)
    return [p / total for p in ps]


def _clamp01(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return min(1.0, max(0.0, f))


def _enforce_mode_monotonicity(bins: list[dict]) -> list[dict]:
    """Cap bin masses walking outward from the mode (paper Section 3).

    No bin may carry more mass than the bin nearer the mode on its side.
    Residual negatives are clamped to 0; the distribution is renormalized.
    """
    if not bins:
        return bins
    masses = [b["mass"] for b in bins]
    mode = max(range(len(masses)), key=lambda i: masses[i])
    # Walk left (toward lower outcomes) and right (toward higher outcomes).
    for step in (-1, 1):
        i = mode + step
        cap = masses[mode]
        while 0 <= i < len(masses):
            if masses[i] > cap:
                masses[i] = cap
            cap = masses[i]
            i += step
    masses = [max(0.0, m) for m in masses]
    total = sum(masses)
    if total <= 0:
        # Degenerate ladder: fall back to uniform over finite bins.
        masses = [1.0 / len(masses)] * len(masses)
    else:
        masses = [m / total for m in masses]
    return [{**b, "mass": m} for b, m in zip(bins, masses)]


def bin_midpoint(b: dict) -> float | None:
    lo, hi = b["low"], b["high"]
    if lo is None or hi is None:
        return None
    return (lo + hi) / 2.0


def distribution_quantiles(bins: list[dict], qs: list[float]) -> dict[float, float | None]:
    """Quantile levels by linear interpolation within bins.

    Open-ended tail bins are assigned their finite edge (conservative:
    the quantile never lands beyond the observed ladder).
    """
    out: dict[float, float | None] = {}
    if not bins:
        return {q: None for q in qs}
    # Cumulative mass at each bin's upper edge.
    edges = []  # (edge_level, cum_mass)
    cum = 0.0
    for b in bins:
        cum += b["mass"]
        edge = b["high"] if b["high"] is not None else b["low"]
        edges.append((edge, cum))
    for q in qs:
        level = None
        for i, (edge, c) in enumerate(edges):
            if c >= q and edge is not None:
                prev_c = edges[i - 1][1] if i else 0.0
                prev_edge = edges[i - 1][0] if i else None
                b = bins[i]
                lo = b["low"] if b["low"] is not None else edge
                hi = b["high"] if b["high"] is not None else edge
                if c > prev_c and hi != lo:
                    f = (q - prev_c) / (c - prev_c)
                    level = lo + f * (hi - lo)
                else:
                    level = edge
                break
        out[q] = level
    return out


def distribution_moments(bins: list[dict]) -> dict[str, float | None]:
    """Mean and variance via bin midpoints; open tails use their finite edge."""
    total = sum(b["mass"] for b in bins)
    if total <= 0:
        return {"mean": None, "variance": None}
    mus = []
    for b in bins:
        mid = bin_midpoint(b)
        if mid is None:
            mid = b["low"] if b["low"] is not None else b["high"]
        if mid is None:
            return {"mean": None, "variance": None}
        mus.append((mid, b["mass"] / total))
    mean = sum(m * p for m, p in mus)
    var = sum(p * (m - mean) ** 2 for m, p in mus)
    return {"mean": mean, "variance": var}


def allocate_to_grid(bins: list[dict], grid: list[float]) -> list[float]:
    """Allocate each bin's mass onto a fixed outcome grid by interval overlap.

    grid is ascending cell edges; returns mass per cell (len(grid)-1 values).
    Open-ended tail bins are assigned to the outermost cell they touch.
    """
    cells = [0.0] * (len(grid) - 1)
    for b in bins:
        lo = b["low"] if b["low"] is not None else grid[0]
        hi = b["high"] if b["high"] is not None else grid[-1]
        lo = max(lo, grid[0])
        hi = min(hi, grid[-1])
        if hi <= lo:
            continue
        for i in range(len(grid) - 1):
            c_lo, c_hi = grid[i], grid[i + 1]
            overlap = max(0.0, min(hi, c_hi) - max(lo, c_lo))
            if overlap > 0:
                cells[i] += b["mass"] * overlap / (hi - lo)
    return cells
