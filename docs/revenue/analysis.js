/* Revenue-tracker analysis math: series edges, aggregation, concentration,
   share migration. Pure functions — no DOM. Imported by docs/revenue/app.js
   and by the node-based regression tests (tests/test_revenue_concentration.py).
   All data from the site bundles; nothing hardcoded.

   EDGE RULE (documented on the page and in the PR):
   - maxC = most firms disclosing in any single period of the line.
   - Trailing periods with fewer than 50% of maxC disclosing firms are
     DROPPED: they are incomplete reporting runs (not all firms have filed),
     and a trailing stub reads as a pool collapse.
   - stableStart = first period with >= 80% of maxC ("stable coverage").
   - The ramp [first period, stableStart) is SHADED on charts, never hidden:
     early pool totals and shares are understated while coverage builds.
   - Concentration is plotted over the full kept range; the balanced-panel
     HHI (firms disclosing in every window of the stable range) isolates the
     coverage artifact from real consolidation.
   - Sankey windows are restricted to [stableStart, cutEnd]: a window with
     one disclosing firm carries no migration information. */

export const INDEPENDENTS = [
  "Evercore", "Lazard", "Moelis",
  "Houlihan Lokey", "PJT Partners", "Jefferies",
];

export function xOf(d) {
  return (d.t != null ? d.t : d.year);
}

function isQ(freq) { return freq === "q"; }

/* TTM transform for quarterly series: per firm, each quarter's value is
   replaced by the trailing-twelve-months sum (the four quarters ending at
   that quarter). Only defined when all four quarters are present; a firm
   missing any quarter of the window is excluded from that TTM point (not
   estimated). This makes quarterly share/HHI robust to reporting-calendar
   quirks (fiscal-year ends, off-schedule filings) and to real quarterly
   seasonality: a share gain that reverses next quarter washes out of TTM.
   The per-firm absolute chart keeps raw quarters. */
export function toTTM(line) {
  const firms = line.firms.map(f => {
    const pts = f.series
      .filter(d => d.quarter != null && d.value != null && Number.isFinite(d.value))
      .map(d => ({ x: d.year + (d.quarter - 1) / 4, v: d.value, d }))
      .sort((a, b) => a.x - b.x);
    const byX = new Map();
    for (const p of pts) byX.set(Math.round(p.x * 4) / 4, p);
    const ttm = [];
    for (const p of pts) {
      const x = Math.round(p.x * 4) / 4;
      const win = [0, 1, 2, 3].map(k => Math.round((x - k * 0.25) * 4) / 4);
      if (win.every(w => byX.has(w))) {
        const v = win.reduce((a, w) => a + byX.get(w).v, 0);
        ttm.push({ ...p.d, value: v });
      }
    }
    return { firm: f.firm, series: ttm };
  }).filter(f => f.series.length);
  return { ...line, firms };
}

/* Build per-period firm->value maps with the edge rule applied.
   Returns { xs, perX, metaX, breaks, maxC, stableStart, cutEnd, firstX,
             step, nDroppedTrailing, lastDroppedX }. */
export function buildPeriods(line, freq) {
  const periodW = isQ(freq) ? 0.25 : 1;
  const perX = new Map();   // x -> Map(firm -> value)
  const metaX = new Map();  // x -> Map(firm -> {year, quarter, q})
  const breaks = new Set();
  for (const f of line.firms) {
    let prevRegime = null;
    for (const d of f.series) {
      if (d.value == null || !Number.isFinite(d.value)) { prevRegime = d.regime; continue; }
      const x = xOf(d);
      if (prevRegime !== null && d.regime !== prevRegime) breaks.add(x - periodW / 2);
      if (!perX.has(x)) { perX.set(x, new Map()); metaX.set(x, new Map()); }
      perX.get(x).set(f.firm, d.value);
      metaX.get(x).set(f.firm, { year: d.year, quarter: d.quarter, q: d.map_quality });
      prevRegime = d.regime;
    }
  }
  let xs = [...perX.keys()].sort((a, b) => a - b);
  if (!xs.length) {
    return { xs, perX, metaX, breaks: [], maxC: 0, stableStart: null,
             cutEnd: null, firstX: null, step: periodW / 2,
             nDroppedTrailing: 0, lastDroppedX: null };
  }
  const maxC = Math.max(...xs.map(x => perX.get(x).size));
  // Coverage breaks: the disclosing set changed vs the previous period.
  let prevFirms = null;
  for (const x of xs) {
    const key = [...perX.get(x).keys()].sort().join("|");
    if (prevFirms !== null && key !== prevFirms) breaks.add(x - periodW / 2);
    prevFirms = key;
  }
  // Right edge: drop the incomplete trailing run (< 50% of maxC).
  let nDroppedTrailing = 0, lastDroppedX = null;
  while (xs.length && perX.get(xs[xs.length - 1]).size < 0.5 * maxC) {
    lastDroppedX = xs.pop();
    perX.delete(lastDroppedX); metaX.delete(lastDroppedX);
    nDroppedTrailing++;
  }
  const stableStart = xs.find(x => perX.get(x).size >= 0.8 * maxC) ?? null;
  return {
    xs, perX, metaX, breaks: [...breaks].sort((a, b) => a - b),
    maxC, stableStart, cutEnd: xs[xs.length - 1], firstX: xs[0],
    step: periodW / 2, nDroppedTrailing, lastDroppedX,
  };
}

/* Industry pool (sum over disclosing firms) and per-firm shares per period. */
export function poolAndShares(P) {
  const poolPts = P.xs.map(x => {
    const m = P.perX.get(x);
    const vals = [...m.values()];
    return { x, v: vals.reduce((a, b) => a + b, 0), n: m.size };
  });
  const firms = [];
  for (const m of P.perX.values()) for (const f of m.keys()) {
    if (!firms.includes(f)) firms.push(f);
  }
  const shareSeries = firms.map(firm => {
    const sp = [];
    for (const x of P.xs) {
      const m = P.perX.get(x);
      const tot = [...m.values()].reduce((a, b) => a + b, 0);
      const v = m.get(firm);
      if (v == null || tot <= 0) continue;
      const meta = P.metaX.get(x).get(firm) || {};
      sp.push({ x, sh: v / tot, g: firm, year: meta.year, quarter: meta.quarter, q: meta.q });
    }
    return { firm, sp };
  }).filter(s => s.sp.length);
  return { poolPts, shareSeries };
}

/* Concentration per period: HHI (0-1), CR4, effective N, plus the
   balanced-panel HHI (firms disclosing in every window of the stable
   range). The gap between hhi and hhiBal is the coverage artifact. */
export function concentration(P, freq) {
  const wins = windows(P, freq);
  const balSet = new Set(
    wins.length
      ? [...wins[0].firms.keys()].filter(f => wins.every(w => w.firms.has(f)))
      : []
  );
  return P.xs.map(x => {
    const m = P.perX.get(x);
    const vals = [...m.values()];
    const tot = vals.reduce((a, b) => a + b, 0);
    // HHI is undefined when any share is negative (net losses); the pool
    // chart keeps the true signed total, but concentration is marked n/a.
    if (tot <= 0 || vals.some(v => v < 0)) {
      return { x, hhi: null, cr4: null, effN: null, hhiBal: null,
               effNBal: null, n: m.size, neg: vals.some(v => v < 0) };
    }
    const shares = vals.map(v => v / tot);
    const hhi = shares.reduce((a, s) => a + s * s, 0);
    const cr4 = [...shares].sort((a, b) => b - a).slice(0, 4).reduce((a, s) => a + s, 0);
    let hhiBal = null;
    if (x >= P.stableStart && balSet.size >= 2) {
      const bv = [...balSet].map(f => m.get(f)).filter(v => v != null);
      if (bv.length >= 2) {
        const bt = bv.reduce((a, b) => a + b, 0);
        if (bt > 0) hhiBal = bv.reduce((a, v) => a + (v / bt) * (v / bt), 0);
      }
    }
    return {
      x, hhi, cr4, effN: 1 / hhi,
      hhiBal, effNBal: hhiBal ? 1 / hhiBal : null, n: m.size, neg: false,
    };
  });
}

/* Time windows inside [stableStart, cutEnd]: 3-year blocks annual,
   2-year blocks quarterly. Returns [{label, x0, x1, firms: Map(firm->value),
   total}]. */
export function windows(P, freq) {
  if (P.stableStart == null) return [];
  const wlen = isQ(freq) ? 2 : 3; // years per window
  const wins = [];
  for (let s = Math.floor(P.stableStart); s <= P.cutEnd; s += wlen) {
    const firms = new Map();
    let total = 0;
    for (const x of P.xs) {
      if (x < Math.max(s, P.stableStart) || x >= s + wlen || x > P.cutEnd) continue;
      for (const [f, v] of P.perX.get(x)) {
        firms.set(f, (firms.get(f) || 0) + v);
        total += v;
      }
    }
    if (total <= 0 || !firms.size) continue;
    const endY = Math.min(s + wlen - 1, Math.floor(P.cutEnd));
    const label = endY === s ? `${s}` : `${s}–${String(endY).slice(2)}`;
    wins.push({ label, x0: Math.max(s, P.stableStart), x1: endY, firms, total });
  }
  return wins;
}

/* Share migration across windows. Firms whose best window share is below
   `otherThreshold` are folded into "Other", except firms in keepSet
   (advisory independents stay labeled). Returns
   { windows: [{label, segs: [{firm, share, present}]}], order, nWindows,
     balanced: [firms present in every window] }. */
export function migration(P, freq, { otherThreshold = 0.03, keepSet = new Set() } = {}) {
  const wins = windows(P, freq);
  if (wins.length < 2) return { windows: [], order: [], nWindows: wins.length, balanced: [] };
  // Best (max) window share per firm; fold the tiny ones into "Other".
  const maxShare = new Map();
  for (const w of wins) {
    for (const [f, v] of w.firms) {
      const s = v / w.total;
      if (s > (maxShare.get(f) || 0)) maxShare.set(f, s);
    }
  }
  const fold = new Set(
    [...maxShare].filter(([f, s]) => s < otherThreshold && !keepSet.has(f)).map(([f]) => f)
  );
  // Fixed display order: total value desc across windows, "Other" last.
  const tot = new Map();
  for (const w of wins) for (const [f, v] of w.firms) {
    if (!fold.has(f)) tot.set(f, (tot.get(f) || 0) + v);
  }
  const order = [...tot].sort((a, b) => b[1] - a[1]).map(([f]) => f);
  const hasOther = wins.some(w => [...w.firms.keys()].some(f => fold.has(f)));
  if (hasOther) order.push("Other");
  const proc = wins.map(w => {
    const segs = new Map();   // firm -> share of window total
    const present = new Set(); // unfolded firms with any value in this window
    let otherV = 0;
    for (const [f, v] of w.firms) {
      if (fold.has(f)) { otherV += v; continue; }
      segs.set(f, (segs.get(f) || 0) + v / w.total);
      present.add(f);
    }
    if (hasOther && otherV > 0) segs.set("Other", otherV / w.total);
    return { label: w.label, segs, present };
  });
  const balanced = order.filter(f => f !== "Other" && proc.every(w => w.present.has(f)));
  return { windows: proc, order, nWindows: proc.length, balanced };
}
