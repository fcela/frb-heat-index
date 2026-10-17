/* Bank revenue tracker page. All data from ../data/revenue.json; nothing
   hardcoded. Observable Plot visual language, shared with the main site.
   Straight polylines only — no smoothing. */
import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm";
import {
  buildPeriods, poolAndShares, concentration, migration, INDEPENDENTS,
} from "./analysis.js";

/* ---------------- theme (same tokens as the main site) ---------------- */

const THEMES = {
  light: {
    ink: "#1b1a16", muted: "#75705f", faint: "#a8a294",
    grid: "#ece7d8", line: "#e9e4d6", missing: "#ece7d9",
    accent: "#1d4ed8", card: "#ffffff", tipBg: "#1b1a16", tipFg: "#faf9f6",
    brk: "#b45309",
  },
  dark: {
    ink: "#ede7d6", muted: "#a2987f", faint: "#6f675a",
    grid: "#2b261c", line: "#2e2a20", missing: "#2b261c",
    accent: "#7aa2f7", card: "#1d1a13", tipBg: "#ede7d6", tipFg: "#14120d",
    brk: "#f59e0b",
  },
};

let themeName = document.documentElement.dataset.theme || "light";
const T = () => THEMES[themeName];

// Categorical palette for firms (readable on both themes).
const FIRM_COLORS = [
  "#1d4ed8", "#b45309", "#059669", "#7c3aed", "#be185d", "#0369a1",
  "#a16207", "#0d9488", "#4d7c0f", "#c2410c", "#6d28d9", "#0e7490",
];

const LINE_LABELS = {
  advisory: "Advisory",
  ecm: "ECM",
  dcm: "DCM",
  ecm_dcm_combined: "ECM + DCM (combined disclosure)",
  ficc: "FICC",
  equities: "Equities",
  awm: "Asset & wealth management",
  private_banking: "Private banking",
  awm_incentive_fees: "AWM incentive fees",
  awm_investments: "AWM investments",
  placement_fees: "Placement fees",
};

const QUALITY_NOTE = {
  D: "direct disclosure",
  A: "approximate mapping",
};

/* ---------------- utils ---------------- */

const DATA = "../data/";
async function loadJSON(name) {
  const r = await fetch(DATA + name);
  if (!r.ok) throw new Error(`HTTP ${r.status} loading ${name}`);
  return r.json();
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtM(v) {
  if (v == null || !Number.isFinite(v)) return "n/a";
  const a = Math.abs(v);
  if (a >= 1000) return (v / 1000).toFixed(1) + "B";
  return Math.round(v) + "M";
}

function basePlot(opts) {
  return Plot.plot(Object.assign({
    style: {
      background: "transparent",
      color: T().ink,
      fontFamily: "Inter, -apple-system, sans-serif",
      fontSize: "12px",
      overflow: "visible",
    },
  }, opts));
}

/* ---------------- charts ---------------- */

/* Multi-firm straight-line chart for one business line. Regime breaks are
   drawn as vertical rules (never spliced across). Quarterly series carry
   t = year + (quarter-1)/4; annual series use year. */
function lineChart(line, unit, freq) {
  const colors = {};
  line.firms.forEach((f, i) => { colors[f.firm] = FIRM_COLORS[i % FIRM_COLORS.length]; });
  const pts = [];
  const breaks = new Set();
  const xOf = d => (d.t != null ? d.t : d.year);
  for (const f of line.firms) {
    let prevRegime = null;
    for (const d of f.series) {
      if (d.value == null || !Number.isFinite(d.value)) { prevRegime = d.regime; continue; }
      // A regime change between consecutive periods -> visual break.
      if (prevRegime !== null && d.regime !== prevRegime) breaks.add(xOf(d) - (freq === "q" ? 0.125 : 0.5));
      pts.push({ x: xOf(d), v: d.value, g: f.firm, q: d.map_quality,
                 year: d.year, quarter: d.quarter, regime: d.regime });
      prevRegime = d.regime;
    }
  }
  if (!pts.length) return null;
  const marks = [...breaks].sort().map(b =>
    Plot.ruleX([b], { stroke: T().brk, strokeDasharray: "4,3", strokeWidth: 1 })
  );
  for (const f of line.firms) {
    const sp = pts.filter(d => d.g === f.firm);
    if (!sp.length) continue;
    marks.push(Plot.line(sp, {
      x: "x", y: "v", stroke: colors[f.firm], strokeWidth: 1.8,
      // straight segments: no curve option
    }));
  }
  marks.push(Plot.tip(pts, Plot.pointer({
    x: "x", y: "v",
    title: d => {
      const per = d.quarter != null ? `${d.year}Q${d.quarter}` : `FY${d.year}`;
      return `${d.g} · ${per}\n$${fmtM(d.v)}${d.q && d.q !== "D" ? ` (${QUALITY_NOTE[d.q] || d.q})` : ""}`;
    },
  })));
  return basePlot({
    width: 1020, height: 260,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { label: null, grid: true, tickSize: 0 },
    y: { label: unit || null, grid: true, nice: true, tickFormat: d => "$" + fmtM(d) },
    marks,
    caption: breaks.size
      ? "Dashed verticals: segment reorganization breaks — values not spliced across them."
      : null,
  });
}

function legendHTML(firms) {
  return `<div class="rev-legend">` + firms.map((f, i) =>
    `<span class="rev-legend-item"><span class="rev-swatch" style="background:${FIRM_COLORS[i % FIRM_COLORS.length]}"></span>${esc(f.firm)}</span>`
  ).join("") + `</div>`;
}

/* ---------------- pool & share ---------------- */

/* Aggregate one business line into per-period industry pool (sum over
   disclosing firms) and per-firm shares of that pool. Edge handling lives
   in analysis.js buildPeriods: the incomplete trailing run is dropped and
   the thin-coverage ramp is shaded, never hidden. */
function aggregateLine(line, freq) {
  const P = buildPeriods(line, freq);
  const colors = {};
  line.firms.forEach((f, i) => { colors[f.firm] = FIRM_COLORS[i % FIRM_COLORS.length]; });
  const { poolPts, shareSeries } = poolAndShares(P);
  return { colors, poolPts, shareSeries, breaks: P.breaks, P };
}

function breakMarks(breaks) {
  return breaks.map(b =>
    Plot.ruleX([b], { stroke: T().brk, strokeDasharray: "4,3", strokeWidth: 1 })
  );
}

/* Shaded thin-coverage ramp: [first period, stableStart). Pool totals and
   shares inside the ramp are understated while coverage builds. */
function rampMark(P) {
  if (P.stableStart == null || P.stableStart <= P.firstX) return [];
  return [Plot.rect([{
    x1: P.firstX - P.step, x2: P.stableStart - P.step,
  }], { x1: "x1", x2: "x2", fill: T().faint, fillOpacity: 0.14 })];
}

function edgeCaption(P, freq) {
  const bits = [];
  if (P.stableStart != null && P.stableStart > P.firstX) {
    bits.push(`shaded: thin-coverage ramp before ${fmtX(P.stableStart, freq)}`);
  }
  if (P.nDroppedTrailing) {
    bits.push(`${P.nDroppedTrailing} trailing incomplete period${P.nDroppedTrailing === 1 ? "" : "s"} dropped (not all firms filed)`);
  }
  return bits.length ? " " + bits.join("; ") + "." : "";
}

function fmtX(x, freq) {
  if (freq !== "q" || Number.isInteger(x)) return `${Math.round(x)}`;
  return `${Math.floor(x)}Q${Math.round((x % 1) * 4) + 1}`;
}

/* Industry pool: total disclosed revenue per period. Straight line.
   The incomplete trailing run is dropped (buildPeriods); the thin-coverage
   ramp is shaded. */
function poolChart(agg, unit, freq) {
  if (!agg.poolPts.length) return null;
  const P = agg.P;
  const marks = [
    ...rampMark(P),
    ...breakMarks(agg.breaks),
    Plot.line(agg.poolPts, { x: "x", y: "v", stroke: T().accent, strokeWidth: 2 }),
  ];
  marks.push(Plot.tip(agg.poolPts, Plot.pointer({
    x: "x", y: "v",
    title: d => {
      const t = freq === "q" ? fmtX(d.x, freq) : `${d.x}`;
      return `Industry pool · ${t}\n$${fmtM(d.v)} across ${d.n} disclosing firms`;
    },
  })));
  return basePlot({
    width: 1020, height: 200,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { label: null, grid: true, tickSize: 0 },
    y: { label: unit || null, grid: true, nice: true, tickFormat: d => "$" + fmtM(d) },
    marks,
    caption: "Sum across disclosing firms only — the pool moves when coverage changes. " +
      "Dashed verticals: reorg breaks or coverage changes; values not spliced across them." +
      edgeCaption(P, freq),
  });
}

/* Market share: per-firm share of the disclosed pool, 0–1 axis, straight
   lines. Small firms that vanish on the absolute chart get their own line. */
function shareChart(line, agg, freq) {
  const pts = agg.shareSeries.flatMap(s => s.sp);
  if (!pts.length) return null;
  const P = agg.P;
  const marks = [...rampMark(P), ...breakMarks(agg.breaks)];
  for (const s of agg.shareSeries) {
    if (!s.sp.length) continue;
    marks.push(Plot.line(s.sp, {
      x: "x", y: "sh", stroke: agg.colors[s.firm], strokeWidth: 1.8,
    }));
  }
  marks.push(Plot.tip(pts, Plot.pointer({
    x: "x", y: "sh",
    title: d => {
      const per = d.quarter != null ? `${d.year}Q${d.quarter}` : `FY${d.year}`;
      return `${d.g} · ${per}\n${(d.sh * 100).toFixed(1)}% of disclosed pool` +
        `${d.q && d.q !== "D" ? ` (${QUALITY_NOTE[d.q] || d.q})` : ""}`;
    },
  })));
  return basePlot({
    width: 1020, height: 260,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { label: null, grid: true, tickSize: 0 },
    y: { label: "share of disclosed pool", grid: true, domain: [0, 1],
         tickFormat: d => `${Math.round(d * 100)}%` },
    marks,
    caption: "Each firm's share of the disclosed industry pool. " +
      "Dashed verticals: reorg breaks or coverage changes." +
      edgeCaption(P, freq),
  });
}

/* ---------------- concentration ---------------- */

/* HHI (0–1) over time: solid = all disclosing firms, dashed = balanced
   panel (firms disclosing in every window of the stable range). The gap
   between them is the coverage effect, not consolidation. Gray: CR4.
   Straight lines only. */
function concentrationChart(conc, P, freq) {
  const pts = conc.filter(d => d.hhi != null);
  if (!pts.length) return null;
  const bal = pts.filter(d => d.hhiBal != null);
  const marks = [...rampMark(P), ...breakMarks(P.breaks)];
  marks.push(Plot.line(pts, { x: "x", y: "hhi", stroke: T().accent, strokeWidth: 2 }));
  if (bal.length > 1) {
    marks.push(Plot.line(bal, {
      x: "x", y: "hhiBal", stroke: T().muted, strokeWidth: 1.6, strokeDasharray: "5,3",
    }));
  }
  marks.push(Plot.line(pts, { x: "x", y: "cr4", stroke: T().faint, strokeWidth: 1.2 }));
  marks.push(Plot.tip(pts, Plot.pointer({
    x: "x", y: "hhi",
    title: d => {
      const t = fmtX(d.x, freq);
      let s = `Concentration · ${t}\nHHI ${d.hhi.toFixed(3)} (≈${d.effN.toFixed(1)} effective firms)\nCR4 ${(d.cr4 * 100).toFixed(1)}% · ${d.n} disclosing firms`;
      if (d.hhiBal != null) s += `\nBalanced-panel HHI ${d.hhiBal.toFixed(3)} (≈${d.effNBal.toFixed(1)} effective)`;
      return s;
    },
  })));
  const first = pts[0], last = pts[pts.length - 1];
  // Caption endpoints use the stable-coverage range: the ramp's single-firm
  // HHI of 1.0 is a coverage artifact, not a concentration fact.
  const spts = P.stableStart != null ? pts.filter(d => d.x >= P.stableStart) : pts;
  const sfirst = spts[0] || first, slast = spts[spts.length - 1] || last;
  const balNote = bal.length > 1
    ? " Dashed: HHI among firms disclosing throughout (balanced panel) — the gap to the solid line is the coverage effect, not consolidation."
    : " No balanced-panel line: fewer than two firms disclose throughout the stable range, so every HHI move here mixes coverage and competition.";
  const negNote = conc.some(d => d.neg)
    ? " HHI undefined in periods with negative net revenue (investment losses); those points are omitted, not interpolated."
    : "";
  return {
    figure: basePlot({
      width: 1020, height: 220,
      marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
      x: { label: null, grid: true, tickSize: 0 },
      y: { label: "HHI / CR4 (0–1)", grid: true, domain: [0, 1],
           tickFormat: d => d.toFixed(1) },
      marks,
      caption: `HHI ${sfirst.hhi.toFixed(2)} → ${slast.hhi.toFixed(2)} ` +
        `(≈${sfirst.effN.toFixed(1)} → ≈${slast.effN.toFixed(1)} effective firms, stable-coverage range). ` +
        "Solid: all disclosing firms. Gray: CR4." + balNote + negNote + edgeCaption(P, freq),
    }),
    legend: `<span class="rev-legend-item"><span class="rev-swatch" style="background:${T().accent}"></span>HHI, all disclosing firms</span>` +
      (bal.length > 1 ? `<span class="rev-legend-item"><span class="rev-swatch" style="background:${T().muted}"></span>HHI, balanced panel</span>` : "") +
      `<span class="rev-legend-item"><span class="rev-swatch" style="background:${T().faint}"></span>CR4</span>`,
  };
}

/* ---------------- share migration (chained alluvial) ---------------- */

/* Hand-rolled alluvial: one stacked bar per time window, straight-edged
   ribbons between them (no splines, per the chart language). Bands that
   start mid-diagram entered coverage (▲); bands that end left it (▼) —
   disclosure changes, never competitive moves. Firms below 3% max window
   share fold into "Other", except advisory independents, always labeled. */
function alluvialChart(mig, colors, freq) {
  if (mig.nWindows < 2) return null;
  const nW = mig.nWindows;
  const W = 1020, H = 400, mL = 168, mR = 24, mT = 26, mB = 46;
  const availW = W - mL - mR, availH = H - mT - mB;
  const barW = Math.min(96, (availW - (nW - 1) * 40) / nW);
  const gap = nW > 1 ? (availW - nW * barW) / (nW - 1) : 0;
  const y = s => mT + (1 - s) * availH;
  const ink = T().ink, muted = T().muted, faint = T().faint;
  const colOf = f => f === "Other" ? faint : (colors[f] || muted);

  // Stacked segments per window, fixed firm order, largest on top.
  // Data space: y0 < y1 are cumulative shares from the TOP (y0=1-sh_first).
  const stacks = mig.windows.map(w => {
    let acc = 1;
    const segs = [];
    for (const f of mig.order) {
      const sh = w.segs.get(f) || 0;
      if (sh <= 0) continue;
      segs.push({ firm: f, sh, y0: acc - sh, y1: acc });
      acc -= sh;
    }
    return { label: w.label, segs, present: new Set(segs.map(s => s.firm)) };
  });
  const xBar = i => mL + i * (barW + gap);
  const escA = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" ` +
    `style="max-width:${W}px;display:block" font-family="Inter,-apple-system,sans-serif">`;
  // Ribbons between adjacent windows.
  for (let i = 0; i < nW - 1; i++) {
    const A = stacks[i], B = stacks[i + 1];
    const x0 = xBar(i) + barW, x1 = xBar(i + 1);
    for (const f of mig.order) {
      const a = A.segs.find(s => s.firm === f), b = B.segs.find(s => s.firm === f);
      const col = colOf(f);
      if (a && b) {
        // Continuing band: straight-edged quad.
        svg += `<polygon points="${x0},${y(a.y0).toFixed(1)} ${x1},${y(b.y0).toFixed(1)} ` +
          `${x1},${y(b.y1).toFixed(1)} ${x0},${y(a.y1).toFixed(1)}" fill="${col}" fill-opacity="0.45">` +
          `<title>${escA(f)}: ${(a.sh * 100).toFixed(1)}% → ${(b.sh * 100).toFixed(1)}% ` +
          `(${A.label} → ${B.label})</title></polygon>`;
      } else if (!a && b) {
        // Entering coverage: triangle from the previous bar's edge + ▲ marker.
        const ym = y((b.y0 + b.y1) / 2);
        svg += `<polygon points="${x0},${ym.toFixed(1)} ${x1},${y(b.y0).toFixed(1)} ` +
          `${x1},${y(b.y1).toFixed(1)}" fill="${col}" fill-opacity="0.45">` +
          `<title>${escA(f)} entered coverage in ${B.label} ` +
          `(${(b.sh * 100).toFixed(1)}% of window pool) — disclosure change, not a competitive gain</title></polygon>`;
        svg += `<text x="${x0 + 4}" y="${(ym - 6).toFixed(1)}" font-size="11" fill="${muted}">▲</text>`;
      } else if (a && !b) {
        // Leaving coverage: triangle to the next bar's edge + ▼ marker.
        const ym = y((a.y0 + a.y1) / 2);
        svg += `<polygon points="${x0},${y(a.y0).toFixed(1)} ${x0},${y(a.y1).toFixed(1)} ` +
          `${x1},${ym.toFixed(1)}" fill="${col}" fill-opacity="0.45">` +
          `<title>${escA(f)} left coverage after ${A.label} ` +
          `(${(a.sh * 100).toFixed(1)}% of window pool) — disclosure change, not a competitive loss</title></polygon>`;
        svg += `<text x="${x1 - 16}" y="${(ym - 6).toFixed(1)}" font-size="11" fill="${muted}">▼</text>`;
      }
    }
  }
  // Window bars + labels.
  stacks.forEach((st, i) => {
    const x = xBar(i);
    for (const s of st.segs) {
      const h = Math.max(y(s.y0) - y(s.y1), 0.75);
      svg += `<rect x="${x.toFixed(1)}" y="${y(s.y1).toFixed(1)}" width="${barW.toFixed(1)}" ` +
        `height="${h.toFixed(1)}" fill="${colOf(s.firm)}" fill-opacity="0.85">` +
        `<title>${escA(s.firm)} · ${st.label}: ${(s.sh * 100).toFixed(1)}% of window pool</title></rect>`;
    }
    svg += `<text x="${(x + barW / 2).toFixed(1)}" y="${H - 22}" font-size="12" ` +
      `text-anchor="middle" fill="${muted}">${escA(st.label)}</text>`;
    // Firm labels left of the first bar for readable segments.
    if (i === 0) {
      for (const s of st.segs) {
        if ((s.y1 - s.y0) * availH < 15) continue;
        svg += `<text x="${x - 8}" y="${(y((s.y0 + s.y1) / 2) + 4).toFixed(1)}" ` +
          `font-size="12" text-anchor="end" fill="${ink}">${escA(s.firm)}</text>`;
      }
    }
  });
  svg += "</svg>";
  const nBal = mig.balanced.length;
  return {
    svg,
    caption: `Each band is a firm's share of the window pool (${freq === "q" ? "2-year" : "3-year"} windows, ` +
      `stable-coverage range only). ▲ share entered coverage here; ▼ share left coverage here — ` +
      `disclosure changes, never competitive moves. ${nBal} firm${nBal === 1 ? "" : "s"} disclose ` +
      `in every window${nBal ? ` (${mig.balanced.map(escA).join(", ")})` : ""}; the rest enter or exit mid-range.`,
  };
}

function lineSection(line, unit, freq) {
  const id = "line-" + line.line.replace(/_/g, "-");
  const chart = lineChart(line, unit, freq);
  const agg = aggregateLine(line, freq);
  const pool = poolChart(agg, unit, freq);
  const share = shareChart(line, agg, freq);
  const conc = concentrationChart(concentration(agg.P, freq), agg.P, freq);
  const keepSet = line.line === "advisory" ? new Set(INDEPENDENTS) : new Set();
  const mig = alluvialChart(migration(agg.P, freq, { keepSet }), agg.colors, freq);
  const label = LINE_LABELS[line.line] || line.line;
  const per = freq === "q" ? "quarterly" : "annual";
  const sub = (title, c, legend) =>
    `<h3 class="rev-subhead">${title}</h3>` +
    (legend ? `<div class="rev-legend">${legend}</div>` : "") +
    `<div class="chart" role="img" aria-label="${esc(label)} ${per} ${esc(title.toLowerCase())}">${c ? "" : "<p class='pulse-stamp'>no data</p>"}</div>`;
  const subSvg = (title, m) =>
    `<h3 class="rev-subhead">${title}</h3>` +
    `<div class="chart" role="img" aria-label="${esc(label)} ${per} ${esc(title.toLowerCase())}">${m ? m.svg : "<p class='pulse-stamp'>no data</p>"}</div>` +
    (m ? `<p class="rev-cap">${esc(m.caption)}</p>` : "");
  const tmp = document.createElement("div");
  tmp.innerHTML = `<section class="card rev-card" id="${id}">
    <h2>${esc(label)}</h2>
    <p class="sub">${line.firms.length} firm${line.firms.length === 1 ? "" : "s"} disclose this line.
    Firms that don't disclose it are omitted, not estimated.</p>
    ${legendHTML(line.firms)}
    ${sub("Industry pool", pool)}
    ${sub("Market share", share)}
    ${sub("Concentration", conc && conc.figure, conc && conc.legend)}
    ${subSvg("Share migration", mig)}
    ${sub("Revenue by firm", chart)}
  </section>`;
  const sec = tmp.firstChild;
  const charts = sec.querySelectorAll(".chart");
  if (pool) charts[0].appendChild(pool);
  if (share) charts[1].appendChild(share);
  if (conc && conc.figure) charts[2].appendChild(conc.figure);
  if (chart) charts[4].appendChild(chart);
  return sec;
}

/* ---------------- page ---------------- */

let freq = "a"; // "a" = annual, "q" = quarterly
const BUNDLE_FOR = { a: "revenue.json", q: "revenue_quarterly.json" };

function tocHTML(lines) {
  return lines.map(l =>
    `<a href="#line-${l.line.replace(/_/g, "-")}">${esc(LINE_LABELS[l.line] || l.line)}</a>`
  ).join("");
}

async function main() {
  const el = document.getElementById("revenue");
  try {
    const bundle = await loadJSON(BUNDLE_FOR[freq]);
    const unit = bundle.meta.unit || "USD millions";
    document.getElementById("revenue-toc").innerHTML = tocHTML(bundle.lines);
    const sections = bundle.lines.map(l => lineSection(l, unit, freq));
    el.innerHTML = "";
    for (const s of sections) el.appendChild(s);
  } catch (err) {
    el.innerHTML = `<p class="pulse-stamp">failed to load revenue data: ${esc(err.message)}</p>`;
  }
}

function setFreq(next) {
  if (next === freq) return;
  freq = next;
  for (const [id, f] of [["freq-annual", "a"], ["freq-quarterly", "q"]]) {
    const b = document.getElementById(id);
    const on = f === freq;
    b.classList.toggle("freq-active", on);
    b.setAttribute("aria-pressed", String(on));
  }
  main();
}

document.getElementById("freq-annual").addEventListener("click", () => setFreq("a"));
document.getElementById("freq-quarterly").addEventListener("click", () => setFreq("q"));

document.getElementById("theme-toggle").addEventListener("click", () => {
  themeName = themeName === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = themeName;
  try { localStorage.setItem("frb-heat-theme", themeName); } catch (e) {}
  main(); // re-render with new theme tokens
});

main();
