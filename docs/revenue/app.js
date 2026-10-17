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

function lineSection(line, unit, freq) {
  const id = "line-" + line.line.replace(/_/g, "-");
  const chart = lineChart(line, unit, freq);
  const label = LINE_LABELS[line.line] || line.line;
  const per = freq === "q" ? "quarterly" : "annual";
  return `<section class="card rev-card" id="${id}">
    <h2>${esc(label)}</h2>
    <p class="sub">${line.firms.length} firm${line.firms.length === 1 ? "" : "s"} disclose this line.
    Firms that don't disclose it are omitted, not estimated.</p>
    ${legendHTML(line.firms)}
    <div class="chart" role="img" aria-label="${esc(label)} ${per} revenue by firm">${chart ? "" : "<p class='pulse-stamp'>no data</p>"}</div>
  </section>`;
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
    const sections = bundle.lines.map(l => {
      const tmp = document.createElement("div");
      tmp.innerHTML = lineSection(l, unit, freq);
      const chart = lineChart(l, unit, freq);
      if (chart) tmp.querySelector(".chart").appendChild(chart);
      return tmp.firstChild;
    });
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
