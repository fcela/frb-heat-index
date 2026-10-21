#!/usr/bin/env python3
"""Cluster the 46 bottom-level indicators by their score trajectories.

Feeds the "Clustered" heat-map view on docs/frb/: a 46 x 146 matrix whose
row order comes from hierarchical clustering, so indicators that heat up
and cool down together end up adjacent, with the merge tree drawn beside
the matrix as a dendrogram.

Method (pipeline-computed, deterministic; NOT in the browser):
  features  each indicator's quarterly 0-1 heat score over the full sample
            (the same empirical quantile of its oriented z-score in its own
            history that the site's indicator drill-down shows).
  distance  correlation distance, 1 - Pearson r, computed on the
            PAIRWISE-COMPLETE observations of each pair (no imputation, no
            zero-filling). Pairs with fewer than MIN_OVERLAP common
            quarters raise, so a ragged rebuild fails loudly instead of
            silently clustering on thin overlap.
  linkage   average (UPGMA). Ward is NOT compatible here: Ward's objective
            needs complete raw vectors / squared Euclidean distances, which
            the ragged pairwise-complete design cannot provide.
  ties      distances are rounded to 12 decimals; among equal minimum
            distances the merge pair with the smallest leaf indices wins
            (cluster ids ordered by their minimum member leaf). Rebuilds
            therefore produce the identical order.
  order     leaves are laid out by an in-order traversal of the merge tree
            (children ordered by their minimum leaf index), so every
            subtree occupies one contiguous block of rows.

The result is shipped as docs/data/clustered.json next to the other site
bundles; the browser only draws what is computed here.

Usage:
    python src/cluster_indicators.py \
        [--indicators data/indicators_standardized.csv] \
        [--out docs/data/clustered.json]

Called automatically by src/make_site.py when data/indicators_standardized.csv
exists. Requires: polars, numpy (scipy is deliberately NOT used: the
quarterly CI workflow installs only polars+numpy, and the hand-rolled
UPGMA below gives explicit, documented tie-breaking).

Requires: polars, numpy.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent.parent

# Minimum pairwise-complete quarters for a usable correlation. The current
# panel's smallest overlap is 76 of 146 quarters, so this only trips on a
# genuinely broken rebuild.
MIN_OVERLAP = 24

# Number of top-level groups the bundle ships as "major cluster" boundaries
# for the view's restrained separator rules (cut = drop the last k-1 merges;
# UPGMA heights are monotone, so this is well defined).
CUT_K = 3

# Distances are rounded to this many decimals before comparison so that
# floating-point noise can never reorder a rebuild, and genuine ties fall
# through to the documented index-based tie-break.
_DECIMALS = 12


def heat_scores(mat: np.ndarray) -> np.ndarray:
    """Map each indicator's z-score series to its 0-1 heat score series.

    Empirical quantile of each value within its own indicator's finite
    history (fraction of the indicator's observed values <= the value),
    matching docs/assets/app.js equantile(): 0.0 = lowest vulnerability
    reading on record, 1.0 = highest. NaN stays NaN.
    """
    n = mat.shape[1]
    out = np.full_like(mat, np.nan)
    for j in range(n):
        col = mat[:, j]
        finite = col[np.isfinite(col)]
        if finite.size == 0:
            continue
        ordered = np.sort(finite)
        for i in range(mat.shape[0]):
            if np.isfinite(col[i]):
                out[i, j] = np.searchsorted(ordered, col[i], side="right") / ordered.size
    return out


def correlation_distance(scores: np.ndarray, min_overlap: int = MIN_OVERLAP) -> np.ndarray:
    """Pairwise correlation distance (1 - Pearson r) on pairwise-complete
    observations. Returns a symmetric (n x n) matrix, diagonal 0, distances
    rounded to _DECIMALS. Raises ValueError if any pair shares fewer than
    min_overlap observed quarters or if a correlation is undefined (zero
    variance on the common sample)."""
    n = scores.shape[1]
    observed = np.isfinite(scores)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            m = observed[:, i] & observed[:, j]
            k = int(m.sum())
            if k < min_overlap:
                raise ValueError(
                    f"indicators {i} and {j} share only {k} observed "
                    f"quarters (< {min_overlap}); refusing to cluster on "
                    "insufficient overlap (no imputation)")
            a = scores[m, i]
            b = scores[m, j]
            a = a - a.mean()
            b = b - b.mean()
            sa = float((a * a).sum())
            sb = float((b * b).sum())
            if sa <= 0.0 or sb <= 0.0:
                raise ValueError(
                    f"indicators {i} and {j} have zero variance on their "
                    f"{k}-quarter common sample; correlation undefined")
            r = float((a * b).sum()) / np.sqrt(sa * sb)
            if not np.isfinite(r):
                raise ValueError(
                    f"correlation of indicators {i} and {j} is not finite")
            # |r| can exceed 1 by ~1e-16; clamp into the valid metric range.
            d = 1.0 - min(max(r, -1.0), 1.0)
            D[i, j] = D[j, i] = round(d, _DECIMALS)
    return D


def average_linkage(D: np.ndarray) -> list[tuple[int, int, float]]:
    """Deterministic UPGMA. At every step every active cluster pair's
    distance is recomputed as the exact mean of member-pair distances from D
    (no incremental update, no drift). Among equal minima the pair with the
    smallest (min leaf a, min leaf b) wins. Returns merges as
    (a_id, b_id, height) where leaves are ids 0..n-1 and each merge step t
    creates internal node id n + t; the last merge is the root."""
    n = D.shape[0]
    clusters: dict[int, tuple[int, ...]] = {i: (i,) for i in range(n)}
    merges: list[tuple[int, int, float]] = []
    nid = n
    while len(clusters) > 1:
        best_key = None
        best_pair = None
        best_h = None
        ids = sorted(clusters)
        for x in range(len(ids)):
            A = clusters[ids[x]]
            for y in range(x + 1, len(ids)):
                B = clusters[ids[y]]
                h = float(D[np.ix_(A, B)].mean())
                key = (round(h, _DECIMALS), min(A), min(B))
                if best_key is None or key < best_key:
                    best_key, best_pair, best_h = key, (ids[x], ids[y]), h
        a, b = best_pair
        merges.append((a, b, best_h))
        clusters[nid] = clusters.pop(a) + clusters.pop(b)
        nid += 1
    return merges


def _min_leaf(merges: list[tuple[int, int, float]], n: int) -> dict[int, int]:
    """node id -> smallest leaf index it contains."""
    lo = {i: i for i in range(n)}
    for t, (a, b, _) in enumerate(merges):
        lo[n + t] = min(lo[a], lo[b])
    return lo


def leaf_order(merges: list[tuple[int, int, float]], n: int) -> list[int]:
    """Row order: in-order traversal of the merge tree, children ordered by
    their minimum leaf index (deterministic; subtrees stay contiguous)."""
    if n == 1:
        return [0]
    lo = _min_leaf(merges, n)

    def visit(node: int) -> list[int]:
        if node < n:
            return [node]
        a, b, _ = merges[node - n]
        left, right = (a, b) if lo[a] <= lo[b] else (b, a)
        return visit(left) + visit(right)

    return visit(n + len(merges) - 1)


def cut_groups(merges: list[tuple[int, int, float]], n: int, k: int) -> list[list[int]]:
    """Partition of leaf ids into the k clusters obtained by dropping the
    last k-1 merges (UPGMA heights are monotone, so this equals cutting the
    dendrogram at the k-1 largest heights)."""
    if not 1 <= k <= n:
        raise ValueError(f"k must be in 1..{n}, got {k}")
    clusters: dict[int, tuple[int, ...]] = {i: (i,) for i in range(n)}
    dropped = len(merges) - (k - 1)
    for t, (a, b, _) in enumerate(merges):
        if t >= dropped:
            break
        clusters[n + t] = clusters.pop(a) + clusters.pop(b)
    # Order groups by their first leaf's position in the row order.
    order = leaf_order(merges, n)
    pos = {leaf: p for p, leaf in enumerate(order)}
    return sorted(
        ([int(x) for x in members] for members in clusters.values()),
        key=lambda g: pos[g[0]],
    )


def compute_bundle(std: pl.DataFrame, min_overlap: int = MIN_OVERLAP,
                   cut_k: int = CUT_K) -> dict:
    """std: the indicators_standardized frame ('quarter' + indicator
    columns). Returns the JSON-ready clustered-view bundle:

      leaves   [indicator full name, ...] in leaf-id order (SPEC order)
      order    row order as leaf ids (permutation of 0..n-1)
      merges   [[a, b, height], ...] merge steps; internal node id = n + step
      cut      leaf-id partition into the top-level clusters, in row order
      method   human-readable method note (also shown on the page)
    """
    cols = [c for c in std.columns if c != "quarter"]
    if not cols:
        raise ValueError("no indicator columns in standardized frame")
    mat = np.column_stack([
        std[c].cast(pl.Float64, strict=False).to_numpy() for c in cols
    ])
    scores = heat_scores(mat)
    D = correlation_distance(scores, min_overlap)
    merges = average_linkage(D)
    n = len(cols)
    order = leaf_order(merges, n)
    if sorted(order) != list(range(n)):
        raise ValueError("leaf order is not a permutation of all indicators")
    # A tiny panel may not support k top-level groups; degrade gracefully.
    k = min(cut_k, n)
    return {
        "leaves": cols,
        "order": order,
        "merges": [[a, b, round(h, 6)] for a, b, h in merges],
        "cut": cut_groups(merges, n, k),
        "method": (
            "Hierarchical clustering (average linkage / UPGMA) of the 46 "
            "indicator score series over all quarters, using correlation "
            "distance (1 - Pearson r) on pairwise-complete observations "
            f"(>= {min_overlap} common quarters required). Row order is "
            "deterministic: distances are rounded to 12 decimals and merge "
            "ties break by smallest leaf index."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--indicators", default=str(HERE / "data" / "indicators_standardized.csv"))
    ap.add_argument("--out", default=str(HERE / "docs" / "data" / "clustered.json"))
    args = ap.parse_args()

    std = pl.read_csv(args.indicators)
    bundle = compute_bundle(std)
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bundle, indent=1) + "\n")
    n = len(bundle["leaves"])
    print(f"wrote {p}: {n} indicators, {len(bundle['merges'])} merges, "
          f"{len(bundle['cut'])} top-level groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
