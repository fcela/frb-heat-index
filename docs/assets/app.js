/* FRB Heat Index — Observable Plot charts + hierarchical explorer.
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
    return `<span class="heat-badge" style="background:${T().missing};color:${T().muted}">n/a</span>`;
  const bg = heatColor(v);
  const fg = luminance(bg) > 0.5 ? "#1b1a16" : "#ffffff";
  return `<span class="heat-badge" style="background:${bg};color:${fg}">${v.toFixed(2)}</span>`;
}

/* ---------------- data ---------------- */

const DATA = "data/";
async function loadJSON(name) {
  const r = await fetch(DATA + name);
  if (!r.ok) throw new Error(`HTTP ${r.status} loading ${name}`);
  return r.json();
}

let AGG, COMP, IND, HIER, META;
let quarters = [], qIndex = {};
let yearTicks = [];

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

function sparkline(pts, { w = 150, h = 42, ymin = 0, ymax = 1, stroke = null } = {}) {
  const s = stroke || T().ink;
  const data = pts.map((d, i) => ({ xi: i, v: d.v }));
  return basePlot({
    width: w, height: h,
    marginTop: 4, marginRight: 4, marginBottom: 4, marginLeft: 4,
    x: { axis: null }, y: { axis: null, domain: [ymin, ymax] },
    marks: [
      Plot.ruleY([0], { stroke: T().faint, strokeOpacity: 0.6 }),
      Plot.line(data, { x: "xi", y: "v", stroke: s, strokeWidth: 1.5, curve: "monotone-x" }),
      Plot.dot(data.slice(-1), { x: "xi", y: "v", fill: s, r: 2.5 }),
    ],
  });
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
      Plot.areaY(pts, { x: "xi", y: "v", fill: T().accent, fillOpacity: 0.10, curve: "monotone-x" }),
      Plot.line(pts, { x: "xi", y: "v", stroke: T().accent, strokeWidth: 2.2, curve: "monotone-x" }),
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

/* ---------------- hierarchical explorer ---------------- */

const EXP = { level: "index", category: null, component: null, indicator: null };

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

function setCrumbs(items) {
  // items: [{label, go}] — last item is current (disabled).
  const nav = document.getElementById("crumbs");
  nav.innerHTML = "";
  items.forEach((it, i) => {
    if (i > 0) {
      const sep = document.createElement("span");
      sep.className = "sep"; sep.textContent = "›";
      nav.appendChild(sep);
    }
    const b = document.createElement("button");
    b.textContent = it.label;
    if (i === items.length - 1) b.disabled = true;
    else b.onclick = it.go;
    nav.appendChild(b);
  });
}

function mount(node) {
  const el = document.getElementById("explorer");
  el.innerHTML = "";
  el.appendChild(node);
}

function goIndex() { Object.assign(EXP, { level: "index", category: null, component: null, indicator: null }); renderExplorer(); }
function goCategory(name) { Object.assign(EXP, { level: "category", category: name, component: null, indicator: null }); renderExplorer(); }
function goComponent(catName, compName) { Object.assign(EXP, { level: "component", category: catName, component: compName, indicator: null }); renderExplorer(); }
function goIndicator(catName, compName, indName) { Object.assign(EXP, { level: "indicator", category: catName, component: compName, indicator: indName }); renderExplorer(); }

function renderExplorer() {
  const { level } = EXP;
  if (level === "index") return viewIndex();
  if (level === "category") return viewCategory();
  if (level === "component") return viewComponent();
  return viewIndicator();
}

function viewIndex() {
  setCrumbs([{ label: "Aggregate index", go: goIndex }]);
  const wrap = document.createElement("div");
  wrap.innerHTML = `<p class="exp-note">One headline index, built from three vulnerability
    categories. Category heat is the mean of its components' quantiles — descriptive,
    not an official index level (the official aggregate averages all fourteen at once).</p>`;
  const grid = document.createElement("div");
  grid.className = "exp-grid";
  for (const cat of HIER.categories) {
    const s = categorySeries(cat);
    const heat = lastVal(s);
    const card = document.createElement("div");
    card.className = "exp-card";
    card.innerHTML = `<h3>${esc(cat.name)} ${heatBadge(heat)}</h3>
      <p class="blurb">${esc(cat.blurb)}</p><div class="spark"></div>`;
    card.querySelector(".spark").appendChild(sparkline(s, { w: 320, h: 56 }));
    card.onclick = () => goCategory(cat.name);
    grid.appendChild(card);
  }
  wrap.appendChild(grid);
  mount(wrap);
}

function viewCategory() {
  const cat = HIER.categories.find(c => c.name === EXP.category);
  setCrumbs([{ label: "Aggregate index", go: goIndex }, { label: cat.name, go: () => goCategory(cat.name) }]);
  const s = categorySeries(cat);
  const heat = lastVal(s);
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div class="exp-detail-head"><h3>${esc(cat.name)}</h3>${heatBadge(heat)}</div>
    <p class="exp-note">${esc(cat.blurb)} ${cat.components.length} components; latest category heat
    ${heat == null ? "n/a" : heat.toFixed(2)}.</p>`;
  const grid = document.createElement("div");
  grid.className = "exp-grid";
  for (const comp of cat.components) {
    const series = COMP[comp.name] || [];
    const h = lastVal(series);
    const card = document.createElement("div");
    card.className = "exp-card";
    card.innerHTML = `<h3>${esc(comp.name)} ${heatBadge(h)}</h3>
      <p class="blurb">${comp.indicators.length} indicators</p><div class="spark"></div>`;
    card.querySelector(".spark").appendChild(sparkline(series, { w: 320, h: 56 }));
    card.onclick = () => goComponent(cat.name, comp.name);
    grid.appendChild(card);
  }
  wrap.appendChild(grid);
  mount(wrap);
}

function viewComponent() {
  const cat = HIER.categories.find(c => c.name === EXP.category);
  const comp = cat.components.find(c => c.name === EXP.component);
  setCrumbs([
    { label: "Aggregate index", go: goIndex },
    { label: cat.name, go: () => goCategory(cat.name) },
    { label: comp.name, go: () => goComponent(cat.name, comp.name) },
  ]);
  const series = COMP[comp.name] || [];
  const heat = lastVal(series);
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div class="exp-detail-head"><h3>${esc(comp.name)}</h3>${heatBadge(heat)}</div>
    <p class="exp-note">Component quantile in its own history (official index level).
    Mean of ${comp.indicators.length} standardized indicators, mapped through the
    Gaussian kernel CDF. Click an indicator to inspect it.</p>
    <div class="spark" style="margin-bottom:1.5rem"></div>
    <h3 style="font-family:var(--font-serif);font-size:1.15rem;margin:0 0 0.75rem">Indicators</h3>`;
  const big = document.createElement("div");
  big.appendChild(sparkline(series, { w: 1020, h: 170, stroke: T().accent }));
  wrap.querySelector(".spark").replaceWith(big);

  for (const ind of comp.indicators) {
    const zs = (IND[ind.name] || []).map(d => d.z);
    const sorted = zs.filter(Number.isFinite).sort((a, b) => a - b);
    const zLast = [...zs].reverse().find(Number.isFinite);
    const h = equantile(sorted, zLast);
    const row = document.createElement("div");
    row.className = "ind-row";
    row.innerHTML = `
      <div class="nm">${esc(ind.short)}<small>${esc(ind.name)}</small></div>
      <div class="spark"></div>
      <div>${heatBadge(h)}</div>`;
    const pts = (IND[ind.name] || []).map((d, xi) => ({ xi, v: equantile(sorted, d.z) }));
    const sp = heatStrip(pts.map(p => p.v), { w: 150, h: 22 });
    row.querySelector(".spark").appendChild(sp);
    row.onclick = () => goIndicator(cat.name, comp.name, ind.name);
    wrap.appendChild(row);
  }
  mount(wrap);
}

function viewIndicator() {
  const cat = HIER.categories.find(c => c.name === EXP.category);
  const comp = cat.components.find(c => c.name === EXP.component);
  const ind = comp.indicators.find(i => i.name === EXP.indicator);
  setCrumbs([
    { label: "Aggregate index", go: goIndex },
    { label: cat.name, go: () => goCategory(cat.name) },
    { label: comp.name, go: () => goComponent(cat.name, comp.name) },
    { label: ind.short, go: () => goIndicator(cat.name, comp.name, ind.name) },
  ]);
  const rows = IND[ind.name] || [];
  const zs = rows.map(d => d.z);
  const sorted = zs.filter(Number.isFinite).sort((a, b) => a - b);
  const heats = rows.map((d, xi) => ({ ...d, xi, h: equantile(sorted, d.z) }));
  const zLast = [...zs].reverse().find(Number.isFinite);
  const hLast = equantile(sorted, zLast);
  const firstQ = rows.find(d => d.z != null)?.q || "—";
  const lastQ = [...rows].reverse().find(d => d.z != null)?.q || "—";

  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div class="exp-detail-head"><h3>${esc(ind.short)}</h3>${heatBadge(hLast)}</div>
    <p class="exp-note">${esc(ind.name)} · ${esc(comp.name)} · ${esc(cat.name)}.<br>
    Oriented standardized value (higher = more vulnerable) — the actual input to the
    component mean. Heat is the value's quantile in its own history.</p>
    <div id="ind-chart"></div>
    <div id="ind-strip" style="margin-top:0.5rem"></div>
    <div class="ind-stats">
      <div><span class="stat-num">${zLast == null ? "n/a" : zLast.toFixed(2)}</span><span class="stat-label">latest z-score (${esc(lastQ)})</span></div>
      <div><span class="stat-num">${hLast == null ? "n/a" : hLast.toFixed(2)}</span><span class="stat-label">heat (own-history quantile)</span></div>
      <div><span class="stat-num">${sorted.length}</span><span class="stat-label">quarterly observations (${esc(firstQ)}–${esc(lastQ)})</span></div>
    </div>`;
  mount(wrap);

  const finite = rows.map((d, xi) => ({ xi, q: d.q, z: d.z })).filter(d => d.z != null);
  const zmin = Math.min(0, ...finite.map(d => d.z)), zmax = Math.max(0, ...finite.map(d => d.z));
  const chart = basePlot({
    width: 1020, height: 300,
    marginLeft: 52, marginRight: 20, marginTop: 20, marginBottom: 40,
    x: { domain: [-0.5, rows.length - 0.5], ticks: yearTicks, tickFormat: i => quarters[i].slice(0, 4), label: null, grid: true, tickSize: 0 },
    y: { domain: [zmin - 0.3, zmax + 0.3], label: "z-score", grid: true },
    color: { type: "linear", domain: [0, 1], range: HEAT, legend: true, label: "Heat", ticks: [0, 0.5, 1] },
    marks: [
      Plot.ruleY([0], { stroke: T().faint }),
      Plot.line(finite, { x: "xi", y: "z", stroke: T().accent, strokeWidth: 2, curve: "monotone-x" }),
      Plot.dot(heats.filter(d => d.z != null), {
        x: "xi", y: "z", fill: "h", r: 3.5, stroke: T().card, strokeWidth: 1,
      }),
      Plot.tip(heats.filter(d => d.z != null), Plot.pointer({
        x: "xi",
        title: d => `${d.q}\nz = ${d.z.toFixed(2)} · heat ${d.h.toFixed(2)}`,
      })),
    ],
  });
  document.getElementById("ind-chart").appendChild(chart);
  document.getElementById("ind-strip").appendChild(
    heatStrip(heats.map(d => d.h), { w: 1020, h: 26 }));
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
  const m = document.getElementById("explorer");
  if (m) m.innerHTML = msg;
}

function renderAll() {
  renderHeatmap();
  renderAggregate();
  renderExplorer();
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

boot().catch(fail);
