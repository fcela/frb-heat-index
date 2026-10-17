/* Bank revenue tracker page. All data from ../data/revenue.json; nothing
   hardcoded. Observable Plot visual language, shared with the main site.
   Straight polylines only — no smoothing. */
import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm";

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
   disclosing firms) and per-firm shares of that pool. Regime breaks are
   collected wherever any firm's reporting regime changes; coverage breaks
   where the set of disclosing firms changes between consecutive periods.
   Both break comparability of the pool across the vertical. */
function aggregateLine(line, freq) {
  const colors = {};
  line.firms.forEach((f, i) => { colors[f.firm] = FIRM_COLORS[i % FIRM_COLORS.length]; });
  const xOf = d => (d.t != null ? d.t : d.year);
  const step = freq === "q" ? 0.125 : 0.5;
  const perX = new Map(); // x -> Map(firm -> value)
  const metaX = new Map(); // x -> Map(firm -> {year, quarter, q})
  const breaks = new Set();
  for (const f of line.firms) {
    let prevRegime = null;
    for (const d of f.series) {
      if (d.value == null || !Number.isFinite(d.value)) { prevRegime = d.regime; continue; }
      const x = xOf(d);
      if (prevRegime !== null && d.regime !== prevRegime) breaks.add(x - step);
      if (!perX.has(x)) { perX.set(x, new Map()); metaX.set(x, new Map()); }
      perX.get(x).set(f.firm, d.value);
      metaX.get(x).set(f.firm, { year: d.year, quarter: d.quarter, q: d.map_quality });
      prevRegime = d.regime;
    }
  }
  const xs = [...perX.keys()].sort((a, b) => a - b);
  // Coverage breaks: the disclosing set changed vs the previous period.
  let prevFirms = null;
  for (const x of xs) {
    const firms = [...perX.get(x).keys()].sort().join("|");
    if (prevFirms !== null && firms !== prevFirms) breaks.add(x - step);
    prevFirms = firms;
  }
  const poolPts = xs.map(x => {
    const m = perX.get(x);
    return { x, v: [...m.values()].reduce((a, b) => a + b, 0), n: m.size };
  });
  const shareSeries = line.firms.map(f => {
    const sp = [];
    for (const x of xs) {
      const m = perX.get(x);
      const tot = [...m.values()].reduce((a, b) => a + b, 0);
      const v = m.get(f.firm);
      if (v == null || tot <= 0) continue;
      const meta = metaX.get(x).get(f.firm) || {};
      sp.push({ x, sh: v / tot, g: f.firm, year: meta.year, quarter: meta.quarter, q: meta.q });
    }
    return { firm: f.firm, sp };
  });
  return { colors, poolPts, shareSeries, breaks: [...breaks].sort((a, b) => a - b) };
}

function breakMarks(breaks) {
  return breaks.map(b =>
    Plot.ruleX([b], { stroke: T().brk, strokeDasharray: "4,3", strokeWidth: 1 })
  );
}

/* Industry pool: total disclosed revenue per period. Straight line. */
function poolChart(agg, unit, freq) {
  if (!agg.poolPts.length) return null;
  const marks = [
    ...breakMarks(agg.breaks),
    Plot.line(agg.poolPts, { x: "x", y: "v", stroke: T().accent, strokeWidth: 2 }),
  ];
  marks.push(Plot.tip(agg.poolPts, Plot.pointer({
    x: "x", y: "v",
    title: d => {
      const t = freq === "q" ? `${d.x.toFixed(2)}` : `${d.x}`;
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
      "Dashed verticals: reorg breaks or coverage changes; values not spliced across them.",
  });
}

/* Market share: per-firm share of the disclosed pool, 0–1 axis, straight
   lines. Small firms that vanish on the absolute chart get their own line. */
function shareChart(line, agg, freq) {
  const pts = agg.shareSeries.flatMap(s => s.sp);
  if (!pts.length) return null;
  const marks = [...breakMarks(agg.breaks)];
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
      "Dashed verticals: reorg breaks or coverage changes.",
  });
}

function lineSection(line, unit, freq) {
  const id = "line-" + line.line.replace(/_/g, "-");
  const chart = lineChart(line, unit, freq);
  const agg = aggregateLine(line, freq);
  const pool = poolChart(agg, unit, freq);
  const share = shareChart(line, agg, freq);
  const label = LINE_LABELS[line.line] || line.line;
  const per = freq === "q" ? "quarterly" : "annual";
  const sub = (title, c) =>
    `<h3 class="rev-subhead">${title}</h3><div class="chart" role="img" aria-label="${esc(label)} ${per} ${esc(title.toLowerCase())}">${c ? "" : "<p class='pulse-stamp'>no data</p>"}</div>`;
  const tmp = document.createElement("div");
  tmp.innerHTML = `<section class="card rev-card" id="${id}">
    <h2>${esc(label)}</h2>
    <p class="sub">${line.firms.length} firm${line.firms.length === 1 ? "" : "s"} disclose this line.
    Firms that don't disclose it are omitted, not estimated.</p>
    ${legendHTML(line.firms)}
    ${sub("Industry pool", pool)}
    ${sub("Market share", share)}
    ${sub("Revenue by firm", chart)}
  </section>`;
  const sec = tmp.firstChild;
  const charts = sec.querySelectorAll(".chart");
  if (pool) charts[0].appendChild(pool);
  if (share) charts[1].appendChild(share);
  if (chart) charts[2].appendChild(chart);
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
