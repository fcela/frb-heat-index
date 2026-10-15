/* FRB Heat Index — Observable Plot charts, single hierarchical page.
   All data from data/*.json; nothing hardcoded. Static, no build step. */
import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm";

/* ---------------- theme ---------------- */

const THEMES = {
  light: {
    ink: "#1b1a16", muted: "#75705f", faint: "#a8a294",
    grid: "#ece7d8", line: "#e9e4d6", missing: "#ece7d9",
    shade: "rgba(178,24,43,0.07)", accent: "#1d4ed8",
    card: "#ffffff", tipBg: "#1b1a16", tipFg: "#faf9f6",
  },
  dark: {
    ink: "#ede7d6", muted: "#a2987f", faint: "#6f675a",
    grid: "#2b261c", line: "#2e2a20", missing: "#2b261c",
    shade: "rgba(178,24,43,0.18)", accent: "#7aa2f7",
    card: "#1d1a13", tipBg: "#ede7d6", tipFg: "#14120d",
  },
};

// Paper's blue -> red heat scale (single color story, both themes).
const HEAT = ["#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7",
              "#fddbc7", "#f4a582", "#d6604d", "#b2182b"];

let themeName = document.documentElement.dataset.theme || "light";
const T = () => THEMES[themeName];

function hexRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [n >> 16 & 255, n >> 8 & 255, n & 255];
}
function heatColor(v) {
  const t = Math.max(0, Math.min(1, v)) * (HEAT.length - 1);
  const i = Math.min(Math.floor(t), HEAT.length - 2), f = t - i;
  const a = hexRgb(HEAT[i]), b = hexRgb(HEAT[i + 1]);
  return "#" + a.map((x, k) => Math.round(x + (b[k] - x) * f).toString(16).padStart(2, "0")).join("");
}
function luminance(hex) {
  const [r, g, b] = hexRgb(hex).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function heatBadge(v) {
  if (v == null || !Number.isFinite(v))
    return `<span class="heat-badge" title="${HEAT_TIP}" style="background:${T().missing};color:${T().muted}">n/a</span>`;
  const bg = heatColor(v);
  const fg = luminance(bg) > 0.5 ? "#1b1a16" : "#ffffff";
  return `<span class="heat-badge" title="${HEAT_TIP}" style="background:${bg};color:${fg}">${v.toFixed(2)}</span>`;
}

// One-line meaning of a heat value, reused as hover text wherever heat appears.
const HEAT_TIP = "Heat = the value's quantile in its own history: 0.00 is the lowest vulnerability on record, 1.00 the highest. Gray n/a = no observation, not zero.";

/* ---------------- data ---------------- */

const DATA = "../data/";
async function loadJSON(name) {
  const r = await fetch(DATA + name);
  if (!r.ok) throw new Error(`HTTP ${r.status} loading ${name}`);
  return r.json();
}

let AGG, COMP, IND, HIER, META;
let quarters = [], qIndex = {};
let yearTicks = [], decadeTicks = [];

function equantile(sortedVals, z) {
  // Empirical quantile of z in its own history (sorted ascending).
  if (!sortedVals.length || z == null || !Number.isFinite(z)) return null;
  let k = 0;
  for (const x of sortedVals) { if (x <= z) k++; else break; }
  return k / sortedVals.length;
}

/* ---------------- plot helpers ---------------- */

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

function heatStrip(vals, { w = 150, h = 18 } = {}) {
  // One-row heat strip of 0-1 values (for indicator detail).
  const data = vals.map((v, i) => ({ xi: i, v }));
  return basePlot({
    width: w, height: h,
    marginTop: 2, marginRight: 2, marginBottom: 2, marginLeft: 2,
    x: { axis: null }, y: { axis: null },
    color: { type: "linear", domain: [0, 1], range: HEAT },
    marks: [
      Plot.rect(data.filter(d => d.v != null),
        { x1: d => d.xi - 0.5, x2: d => d.xi + 0.5, y1: 0, y2: 1, fill: "v" }),
      Plot.rect(data.filter(d => d.v == null),
        { x1: d => d.xi - 0.5, x2: d => d.xi + 0.5, y1: 0, y2: 1, fill: T().missing }),
    ],
  });
}

/* ---------------- heatmap (hero) ---------------- */

function renderHeatmap() {
  const el = document.getElementById("heatmap");
  el.innerHTML = "";
  const components = META.components;
  const nQ = quarters.length;
  const cells = [], missing = [];
  components.forEach(c => {
    (COMP[c] || []).forEach((d, xi) => {
      if (d.v == null) missing.push({ q: d.q, c, xi });
      else cells.push({ q: d.q, c, v: d.v, xi });
    });
  });
  const fig = basePlot({
    width: 1020,
    height: components.length * 34 + 92,
    marginLeft: 172, marginRight: 16, marginTop: 8, marginBottom: 64,
    padding: 0,
    x: {
      domain: [-0.5, nQ - 0.5], ticks: yearTicks,
      tickFormat: i => quarters[i].slice(0, 4),
      label: null, tickSize: 0,
    },
    y: { domain: components, label: null, tickSize: 0 },
    color: {
      type: "linear", domain: [0, 1], range: HEAT, legend: true,
      label: "Quantile in own history →", ticks: [0, 0.25, 0.5, 0.75, 1],
      tickFormat: d => d.toFixed(2),
    },
    marks: [
      Plot.rect(cells, {
        x1: d => d.xi - 0.48, x2: d => d.xi + 0.48, y: "c", fill: "v",
        stroke: T().card, strokeWidth: 0.7,
      }),
      Plot.rect(missing, {
        x1: d => d.xi - 0.48, x2: d => d.xi + 0.48, y: "c", fill: T().missing,
        stroke: T().card, strokeWidth: 0.7,
      }),
      Plot.tip(cells, Plot.pointer({
        x: "xi", y: "c",
        title: d => `${d.c}\n${d.q} — ${d.v.toFixed(2)}\nHotter than ${Math.round(d.v * 100)}% of its history`,
      })),
    ],
  });
  el.appendChild(fig);
}

/* ---------------- aggregate ---------------- */

function renderAggregate() {
  const el = document.getElementById("aggchart");
  el.innerHTML = "";
  const pts = AGG.map((d, xi) => ({ ...d, xi })).filter(d => d.v != null);
  const peak = pts.reduce((a, b) => (b.v > a.v ? b : a), pts[0]);
  const peakLabel = `${peak.q} peak ${peak.v.toFixed(2)}`;
  const shades = [
    { q1: "2007Q1", q2: "2008Q4", label: "2007–08 — crisis buildup" },
  ].filter(s => qIndex[s.q1] != null);
  const fig = basePlot({
    width: 1020, height: 380,
    marginLeft: 52, marginRight: 20, marginTop: 18, marginBottom: 44,
    x: {
      domain: [-0.5, quarters.length - 0.5], ticks: yearTicks,
      tickFormat: i => quarters[i].slice(0, 4), label: null,
      grid: true, tickSize: 0,
    },
    y: { domain: [0, 1], label: "Index (0–1)", grid: true },
    marks: [
      ...shades.map(s => Plot.rect([s], {
        x1: qIndex[s.q1] - 0.5, x2: qIndex[s.q2] + 0.5,
        fill: T().shade,
      })),
      ...shades.map(s => Plot.text([s], {
        x: (qIndex[s.q1] + qIndex[s.q2]) / 2, y: 0.06,
        text: d => d.label, fontSize: 11, fill: T().muted, textAnchor: "middle",
      })),
      Plot.areaY(pts, { x: "xi", y: "v", fill: T().accent, fillOpacity: 0.10 }),
      Plot.line(pts, { x: "xi", y: "v", stroke: T().accent, strokeWidth: 2.2 }),
      Plot.dot([peak], { x: "xi", y: "v", fill: "#b2182b", stroke: T().card, strokeWidth: 1.5, r: 5 }),
      Plot.text([peak], {
        x: "xi", y: "v", text: () => peakLabel,
        dy: -18, dx: 34, fontSize: 11.5, fontWeight: 600, fill: T().ink, textAnchor: "start",
      }),
      Plot.tip(pts, Plot.pointer({
        x: "xi",
        title: d => `${d.q}\nAggregate index: ${d.v.toFixed(3)}`,
      })),
    ],
  });
  el.appendChild(fig);
}

/* ---------------- single-page hierarchy ---------------- */
/* Aggregate -> 3 categories -> 14 components, all on one scrolling page.
   Charts below the fold render lazily via IntersectionObserver. */

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function lastVal(series) {
  for (let i = series.length - 1; i >= 0; i--)
    if (series[i].v != null && Number.isFinite(series[i].v)) return series[i].v;
  return null;
}

function categorySeries(cat) {
  // Descriptive mean of member components' quantiles (not an official level).
  const n = quarters.length;
  const out = [];
  for (let xi = 0; xi < n; xi++) {
    let s = 0, k = 0;
    for (const comp of cat.components) {
      const d = (COMP[comp.name] || [])[xi];
      if (d && d.v != null) { s += d.v; k++; }
    }
    out.push({ q: quarters[xi], v: k ? s / k : null });
  }
  return out;
}

/* Component card chart: proper small multiples. Shared 0–1 y-domain with grid
   and labels (cross-component comparable), sparse decade x-labels, area fill,
   straight polylines. Y-domain padded so peaks never clip the plot edge. */
function componentChart(series, { w = 640, h = 240, stroke = null } = {}) {
  const s = stroke || T().accent;
  const data = series.map((d, xi) => ({ q: d.q, v: d.v, xi })).filter(d => d.v != null);
  const last = data[data.length - 1];
  return basePlot({
    width: w, height: h,
    marginTop: 10, marginRight: 10, marginBottom: 30, marginLeft: 38,
    x: {
      domain: [-0.5, quarters.length - 0.5], ticks: decadeTicks,
      tickFormat: i => quarters[i].slice(0, 4), label: null, tickSize: 0,
    },
    y: {
      domain: [-0.05, 1.05],
      ticks: [0, 0.5, 1], tickFormat: d => d.toFixed(1),
      grid: true, label: null,
    },
    marks: [
      Plot.areaY(data, { x: "xi", y: "v", fill: s, fillOpacity: 0.08 }),
      Plot.line(data, { x: "xi", y: "v", stroke: s, strokeWidth: 2 }),
      last ? Plot.dot([last], { x: "xi", y: "v", fill: s, stroke: T().card, strokeWidth: 1.5, r: 4 }) : null,
      Plot.tip(data, Plot.pointer({ x: "xi", title: d => `${d.q} — ${d.v.toFixed(2)}` })),
    ].filter(Boolean),
  });
}

/* Straight polyline time-series chart (no smoothing — splines distort the series). */
function seriesChart(pts, { w = 1020, h = 300, stroke = null, area = true, yLabel = null } = {}) {
  const s = stroke || T().accent;
  const data = pts.map((d, xi) => ({ q: d.q, v: d.v, xi })).filter(d => d.v != null);
  const last = data[data.length - 1];
  return basePlot({
    width: w, height: h,
    marginLeft: 46, marginRight: 14, marginTop: 12, marginBottom: 36,
    x: {
      domain: [-0.5, quarters.length - 0.5], ticks: yearTicks,
      tickFormat: i => quarters[i].slice(0, 4), label: null, grid: true, tickSize: 0,
    },
    y: { domain: [0, 1], label: yLabel, grid: true, ticks: [0, 0.5, 1], tickFormat: d => d.toFixed(1) },
    marks: [
      area ? Plot.areaY(data, { x: "xi", y: "v", fill: s, fillOpacity: 0.08 }) : null,
      Plot.line(data, { x: "xi", y: "v", stroke: s, strokeWidth: 2 }),
      last ? Plot.dot([last], { x: "xi", y: "v", fill: s, stroke: T().card, strokeWidth: 1.5, r: 4 }) : null,
      Plot.tip(data, Plot.pointer({ x: "xi", title: d => `${d.q} — ${d.v.toFixed(2)}` })),
    ].filter(Boolean),
  });
}

/* Lazy rendering: charts render when near the viewport, never as stuck spinners. */
const lazyObserver = ("IntersectionObserver" in window)
  ? new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const el = e.target;
        lazyObserver.unobserve(el);
        if (el._render && !el._rendered) {
          el.innerHTML = "";
          try { el.appendChild(el._render()); } catch (err) { /* fail() covers page-level errors */ }
          el._rendered = true;
        }
      }
    }, { rootMargin: "800px 0px" })
  : null;

function lazyChart(el, renderFn) {
  el.classList.add("lazy-chart");
  el._render = renderFn;
  el._rendered = false;
  if (lazyObserver) lazyObserver.observe(el);
  else { el.appendChild(renderFn()); el._rendered = true; }
}

function indicatorRows(comp) {
  const frag = document.createDocumentFragment();
  for (const ind of comp.indicators) {
    const zs = (IND[ind.name] || []).map(d => d.z);
    const sorted = zs.filter(Number.isFinite).sort((a, b) => a - b);
    const zLast = [...zs].reverse().find(Number.isFinite);
    const h = equantile(sorted, zLast);
    const row = document.createElement("div");
    row.className = "ind-row";
    row.innerHTML =
      `<div class="nm">${esc(ind.short)}<small>${esc(ind.name)}</small></div>` +
      `<div class="spark"></div>` +
      `<div class="ind-meta">${heatBadge(h)}<span class="zval" title="Latest oriented z-score">z ${zLast == null ? "n/a" : zLast.toFixed(2)}</span></div>`;
    const pts = (IND[ind.name] || []).map((d, xi) => ({ xi, v: equantile(sorted, d.z) }));
    row.querySelector(".spark").appendChild(heatStrip(pts.map(p => p.v), { w: 150, h: 22 }));
    frag.appendChild(row);
  }
  return frag;
}

function findComponent(name) {
  for (const cat of HIER.categories)
    for (const comp of cat.components)
      if (comp.name === name) return { comp, cat };
  return null;
}

function compHash(name) {
  return "#component/" + encodeURIComponent(name);
}

function renderComponentDetail(name) {
  const host = document.getElementById("hierarchy");
  if (!host) return;
  const found = findComponent(name);
  host.innerHTML = "";
  if (!found) {
    host.innerHTML = `<p class="err">Unknown component: ${esc(name)}.</p>
      <p><a href="#hierarchy-card" class="back-link">← All components</a></p>`;
    return;
  }
  const { comp, cat } = found;
  const series = COMP[comp.name] || [];
  const heat = lastVal(series);
  const wrap = document.createElement("div");
  wrap.className = "comp-detail";
  wrap.innerHTML =
    `<p><a href="#hierarchy-card" class="back-link">← All components</a></p>` +
    `<div class="comp-detail-head">` +
      `<div>` +
        `<p class="kicker">${esc(cat.name)}</p>` +
        `<h3>${esc(comp.name)} ${heatBadge(heat)}</h3>` +
        `<p class="blurb">${comp.indicators.length} indicators. ` +
        `Each row shows the indicator's history as a heat strip, its latest heat quantile, ` +
        `and its latest oriented z-score.</p>` +
      `</div>` +
    `</div>` +
    `<figure class="comp-detail-chart" role="img" aria-label="${esc(comp.name)} component heat over time"></figure>` +
    `<div class="ind-list"></div>`;
  wrap.querySelector(".ind-list").appendChild(indicatorRows(comp));
  host.appendChild(wrap);
  const chartEl = wrap.querySelector(".comp-detail-chart");
  lazyChart(chartEl, () => seriesChart(series, { w: 1020, h: 320 }));
  // Render immediately — the user asked for this view.
  if (chartEl._render && !chartEl._rendered) {
    if (lazyObserver) lazyObserver.unobserve(chartEl);
    chartEl.innerHTML = "";
    chartEl.appendChild(chartEl._render());
    chartEl._rendered = true;
  }
  // Back link uses default anchor behavior: hash -> #hierarchy-card,
  // which fires hashchange -> routeHash() -> renderHierarchy().
}

function routeHash() {
  const m = /^#component\/(.+)$/.exec(location.hash);
  if (m) renderComponentDetail(decodeURIComponent(m[1]));
  else renderHierarchy();
}

function renderHierarchy() {
  const host = document.getElementById("hierarchy");
  if (!host) return;
  host.innerHTML = "";

  HIER.categories.forEach((cat, ci) => {
    const sec = document.createElement("section");
    sec.className = "hier-cat";
    sec.id = `hier-cat-${ci}`;
    const catSeries = categorySeries(cat);
    const catHeat = lastVal(catSeries);
    sec.innerHTML =
      `<div class="hier-cat-head">` +
        `<h3>${esc(cat.name)} ${heatBadge(catHeat)}</h3>` +
        `<p class="blurb">${esc(cat.blurb || "")} ${cat.components.length} components; ` +
        `category heat is the mean of its components' quantiles (descriptive).</p>` +
      `</div>` +
      `<figure class="hier-cat-chart" role="img" aria-label="${esc(cat.name)} category heat over time"></figure>` +
      `<div class="comp-grid"></div>`;
    lazyChart(sec.querySelector(".hier-cat-chart"),
      () => seriesChart(catSeries, { w: 1020, h: 240 }));

    const grid = sec.querySelector(".comp-grid");
    for (const comp of cat.components) {
      const series = COMP[comp.name] || [];
      const heat = lastVal(series);
      const card = document.createElement("article");
      card.className = "comp-card comp-drill";
      card.tabIndex = 0;
      card.setAttribute("role", "link");
      card.setAttribute("aria-label", `${comp.name}: view ${comp.indicators.length} indicators`);
      card.innerHTML =
        `<h4>${esc(comp.name)} ${heatBadge(heat)}</h4>` +
        `<figure class="comp-chart" role="img" aria-label="${esc(comp.name)} component heat over time"></figure>` +
        `<span class="drill-hint">${comp.indicators.length} indicators →</span>`;
      const go = () => { location.hash = compHash(comp.name); };
      card.addEventListener("click", go);
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
      });
      lazyChart(card.querySelector(".comp-chart"),
        () => componentChart(series, { w: 640, h: 240 }));
      grid.appendChild(card);
    }
    host.appendChild(sec);
  });
}
/* ---------------- meta / stamp ---------------- */

function renderMeta() {
  const q = META.last_updated_quarter || "?";
  const first = META.start_quarter || "?";
  const n = META.n_quarters || "?";
  const built = META.built_at_utc ? META.built_at_utc.replace("T", " ").replace("Z", " UTC") : "unknown";
  document.getElementById("stamp").innerHTML =
    `Data through <strong>${esc(q)}</strong> · ${esc(n)} quarters (${esc(first)}–${esc(q)}) · panel built ${esc(built)}.`;
  document.getElementById("stat-quarters").textContent = n;
  document.getElementById("stat-indicators").textContent = META.n_indicators ?? "–";
  document.getElementById("stat-components").textContent = META.n_components ?? "–";
  const stale = META.stale_sources || [];
  if (stale.length) {
    const b = document.getElementById("stale-banner");
    b.classList.add("show");
    b.innerHTML = `<strong>Data note:</strong> flagged stale in the current build: ${stale.map(esc).join(", ")}. Values that depend on them may lag.`;
  }
}

/* ---------------- boot ---------------- */

function fail(err) {
  const msg = `<p class="err">Could not load chart data (${esc(err.message)}).</p>`;
  for (const id of ["heatmap", "aggchart"]) {
    const el = document.getElementById(id);
    if (el && !el.dataset.done) el.innerHTML = msg;
  }
  const m = document.getElementById("hierarchy");
  if (m) m.innerHTML = msg;
}

function renderAll() {
  renderHeatmap();
  renderAggregate();
  routeHash();
  document.getElementById("heatmap").dataset.done = "1";
  document.getElementById("aggchart").dataset.done = "1";
}

async function boot() {
  const [agg, comp, ind, hier, meta] = await Promise.all([
    loadJSON("aggregate.json"), loadJSON("components.json"),
    loadJSON("indicators.json"), loadJSON("hierarchy.json"), loadJSON("meta.json"),
  ]);
  AGG = agg; COMP = comp; IND = ind; HIER = hier; META = meta;
  quarters = AGG.map(d => d.q);
  quarters.forEach((q, i) => { qIndex[q] = i; });
  yearTicks = quarters.map((q, i) => i).filter(i => quarters[i].endsWith("Q1") && parseInt(quarters[i]) % 5 === 0);
  decadeTicks = quarters.map((q, i) => i).filter(i => quarters[i].endsWith("Q1") && parseInt(quarters[i]) % 10 === 0);
  renderMeta();
  renderAll();
  window.__frbBooted = true;
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  themeName = themeName === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = themeName;
  try { localStorage.setItem("frb-heat-theme", themeName); } catch (e) {}
  if (window.__frbBooted) renderAll();
});

window.addEventListener("hashchange", () => {
  if (window.__frbBooted) routeHash();
});

boot().catch(fail);
