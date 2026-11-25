/* Pulse of the Market standalone page. All data from ../data/momentum.json;
   nothing hardcoded. Observable Plot visual language, shared with the main
   site. Charts use straight line segments throughout (the site's chart
   rules: never splines), area fill, square corners. */
import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm";

/* ---------------- theme (same tokens as the main site) ---------------- */

const THEMES = {
  light: {
    ink: "#1b1a16", muted: "#75705f", faint: "#a8a294",
    grid: "#ece7d8", line: "#e9e4d6", missing: "#ece7d9",
    accent: "#1d4ed8", card: "#ffffff", tipBg: "#1b1a16", tipFg: "#faf9f6",
  },
  dark: {
    ink: "#ede7d6", muted: "#a2987f", faint: "#6f675a",
    grid: "#2b261c", line: "#2e2a20", missing: "#2b261c",
    accent: "#7aa2f7", card: "#1d1a13", tipBg: "#ede7d6", tipFg: "#14120d",
  },
};

let themeName = document.documentElement.dataset.theme || "light";
const T = () => THEMES[themeName];

const SERIES_COLORS = ["#1d4ed8", "#b45309", "#059669", "#7c3aed", "#be185d", "#0369a1"];

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

function fmt(v, digits = 2) {
  if (v == null || !Number.isFinite(v)) return "n/a";
  return Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
}

function fmtSigned(v, digits = 1, suffix = "") {
  if (v == null || !Number.isFinite(v)) return "n/a";
  return `${v > 0 ? "+" : ""}${fmt(v, digits)}${suffix}`;
}

function cls(v) {
  if (v == null || !Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "pos" : "neg";
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

function toPts(series) {
  return series
    .map(d => ({ date: new Date(d.date + "T00:00:00"), v: d.value }))
    .filter(d => d.v != null && Number.isFinite(d.v));
}

/* Straight-segment line + area (no curve option: Plot's default is
   linear, per the site's chart rules). */
function lineChart(series, unit, { width = 1020, height = 240, zero = false } = {}) {
  const pts = toPts(series);
  if (!pts.length) return null;
  const last = pts[pts.length - 1];
  const marks = [
    Plot.areaY(pts, { x: "date", y: "v", fill: T().accent, fillOpacity: 0.08 }),
    Plot.line(pts, { x: "date", y: "v", stroke: T().accent, strokeWidth: 1.8 }),
    Plot.dot([last], { x: "date", y: "v", fill: T().accent, stroke: T().card, strokeWidth: 1.5, r: 4 }),
    Plot.tip(pts, Plot.pointer({
      x: "date", y: "v",
      title: d => `${d.date.toISOString().slice(0, 10)}\n${fmt(d.v)}${unit ? " " + unit : ""}`,
    })),
  ];
  if (zero) marks.unshift(Plot.ruleY([0], { stroke: T().faint, strokeDasharray: "3,3" }));
  return basePlot({
    width, height,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { label: unit || null, grid: true, nice: true },
    marks,
  });
}

/* ---------------- badges & provenance (pulse standard) ---------------- */

function statusBadge(status) {
  const label = {
    "live": "live",
    "live-proxy": "live · proxy",
    "live-experimental": "experimental",
    "live-manual": "live · manual feed",
    "pending-sourcing": "source pending",
    "error": "unavailable",
  }[status] || status;
  return `<span class="status-badge status-${esc(status)}">${esc(label)}</span>`;
}

const CADENCE_DAYS = { daily: 1, weekly: 7, monthly: 31, quarterly: 92, annual: 365 };

function cadenceDays(frequency) {
  const m = /^(daily|weekly|monthly|quarterly|annual)/i.exec(String(frequency || ""));
  return m ? CADENCE_DAYS[m[1].toLowerCase()] : null;
}

function staleness(ind, builtAt) {
  const period = cadenceDays(ind.frequency);
  const last = ind.last_observation ? String(ind.last_observation).slice(0, 10) : null;
  if (!period || !last || !builtAt) return null;
  const age = Math.round(
    (new Date(builtAt + "T00:00:00Z") - new Date(last + "T00:00:00Z")) / 86400000);
  if (!Number.isFinite(age)) return null;
  return { stale: age > period * 1.5 + 10, ageDays: age, last };
}

function staleBadge(st) {
  return `<span class="status-badge status-stale">stale · last obs ${esc(st.last)}</span>`;
}

function sourceHTML(ind) {
  if (!ind.source_url) return esc(ind.source);
  return `<a href="${esc(ind.source_url)}" target="_blank" rel="noopener">${esc(ind.source)}</a>`;
}

function provenanceHTML(ind) {
  const bits = [];
  if (ind.series_id) bits.push(`<b>Series:</b> ${esc(ind.series_id)}`);
  if (ind.definition) bits.push(esc(ind.definition));
  if (!bits.length) return "";
  const meta = [];
  if (ind.frequency) meta.push(`<span>${esc(ind.frequency)}</span>`);
  const last = ind.last_observation ? String(ind.last_observation).slice(0, 10) : null;
  meta.push(`<span>last observation: ${last ? esc(last) : "none"}</span>`);
  if (ind.source_url) {
    let host = "";
    try { host = new URL(ind.source_url).hostname.replace(/^www\./, ""); } catch (e) {}
    meta.push(`<a href="${esc(ind.source_url)}" target="_blank" rel="noopener">${esc(host || ind.source_url)} ↗</a>`);
  }
  return `<div class="pulse-def">${bits.join(" — ")}<p class="pulse-def-meta">${meta.join('<span class="sep">·</span>')}</p></div>`;
}

/* ---------------- measured-value rendering ---------------- */

const MEASURE_LABELS = {
  vix: ["VIX", 2], vix_max_5s: ["max VIX, 5 sessions", 2],
  vix_21s_ago: ["VIX 21 sessions ago", 2],
  vix_minus_vxv: ["VIX − VXV", 2],
  spread_pp: ["defensive − cyclical spread", 1, " pp"],
  factor_spread_60_pp: ["SPHB − SPLV, 60 sessions (cross-check)", 1, " pp"],
  factor_agrees: ["factor cross-check agrees", 0],
  rsp_minus_spy_pp: ["RSP − SPY, 21 sessions", 1, " pp"],
  rsp_r21_pct: ["RSP, 21 sessions", 1, "%"], spy_r21_pct: ["SPY, 21 sessions", 1, "%"],
  hy_c21_bp: ["HY OAS change, 21 sessions", 0, " bp"],
  gld_r21_pct: ["GLD, 21 sessions", 1, "%"],
  dollar_c21: ["broad dollar change, 21 sessions", 2],
  dgs10_c21_bp: ["10y change, 21 sessions", 0, " bp"],
  wti_r21_pct: ["WTI, 21 sessions", 1, "%"],
  nfci: ["NFCI level", 3], nfci_c13: ["NFCI change, 13 weeks", 3],
  spy_r5_pct: ["SPY, 5 sessions", 1, "%"],
  direction: ["direction", 0],
};

function measuredHTML(measured) {
  if (!measured) return "n/a";
  const parts = [];
  for (const [k, v] of Object.entries(measured)) {
    const spec = MEASURE_LABELS[k];
    if (typeof v === "boolean") {
      parts.push(`${spec ? spec[0] : k}: ${v ? "yes" : "no"}`);
    } else if (typeof v === "string") {
      parts.push(`${spec ? spec[0] : k}: ${esc(v)}`);
    } else if (typeof v === "number") {
      parts.push(`${spec ? spec[0] : k}: ${fmtSigned(v, spec ? spec[1] : 2, spec && spec[2] ? spec[2] : "")}`);
    }
  }
  return parts.join("<br>");
}

/* ---------------- render ---------------- */

function renderTape(data) {
  const el = document.getElementById("tape");
  const items = [];
  for (const e of data.board) {
    items.push(`<span class="tape-item">${esc(e.title)}: <b>${esc(stateLabel(e))}</b></span>`);
  }
  const seg = data.segments.find(s => s.key === "leading");
  if (seg) {
    for (const key of ["vix", "hy_oas"]) {
      const ind = seg.indicators.find(i => i.key === key);
      if (ind && ind.series && ind.series.length) {
        const last = ind.series[ind.series.length - 1];
        items.push(`<span class="tape-item">${esc(ind.title)}: <b>${fmt(last.value)}</b>${ind.unit ? " " + esc(ind.unit) : ""}</span>`);
      }
    }
  }
  el.innerHTML = items.join("");
}

function stateLabel(e) {
  const map = {
    active: "active", inactive: "not active",
    risk_off: "risk-off rotation", risk_on: "risk-on rotation", none: "no rotation",
    broadening: "broadening", narrowing: "narrowing",
    confirmed: "credit-confirmed stress", divergence: "equity-only divergence",
  };
  return map[e.state] || e.state || "n/a";
}

function renderFresh(data) {
  const el = document.getElementById("fresh");
  const m = data.meta || {};
  el.innerHTML =
    `<div class="fresh-banner">Page last updated: <strong>${esc((m.built_at_utc || "").slice(0, 10))}</strong>` +
    (m.as_of ? ` · Data through: <strong>${esc(m.as_of)}</strong> (last completed U.S. session in the archive)` : "") +
    ` · Refresh cadence: <strong>${esc(m.cadence || "Weekly")}</strong> — the archived series are daily.</div>` +
    `<p class="pulse-stamp">Fingerprint states are computed from the archived series only; definitions in the <a href="methodology.md">methodology</a>.</p>`;
}

function renderRead(data) {
  document.getElementById("read").textContent = (data.read || []).join(" ");
}

function renderBoard(data) {
  const el = document.getElementById("board");
  const rows = data.board.map(e => `
    <tr>
      <td><strong>${esc(e.title)}</strong></td>
      <td><span class="state-pill ${e.active ? "state-on" : "state-off"}">${esc(stateLabel(e))}</span></td>
      <td>${e.active && e.since ? esc(e.since) : "—"}</td>
      <td>${measuredHTML(e.measured)}</td>
      <td>${esc(e.definition)}</td>
    </tr>`).join("");
  el.innerHTML = `<table class="pulse-table">
    <thead><tr><th>State</th><th>Status</th><th>Since</th><th>Measured</th><th>Definition</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function indicatorCard(ind, builtD, chartOpts) {
  const card = document.createElement("div");
  card.className = "pulse-card";
  const st = staleness(ind, builtD);
  const staleHTML = st && st.stale ? staleBadge(st) : "";
  if (ind.series && ind.series.length) {
    const last = ind.series[ind.series.length - 1];
    card.innerHTML = `
      <div class="pulse-head">
        <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}${staleHTML}
          <span class="pulse-last">${fmt(last.value)}${ind.unit ? " " + esc(ind.unit) : ""}</span>
          <span class="pulse-date">${esc(String(last.date).slice(0, 10))}</span></div>
        <div class="pulse-src">${sourceHTML(ind)} · ${esc(ind.frequency)}</div>
      </div>
      <p class="blurb">${esc(ind.why)}</p>
      ${provenanceHTML(ind)}
      <div class="pulse-chart"></div>
      ${ind.note ? `<p class="pulse-note">${esc(ind.note)}</p>` : ""}`;
    const fig = lineChart(ind.series, ind.unit, chartOpts || {});
    if (fig) card.querySelector(".pulse-chart").appendChild(fig);
  } else {
    card.innerHTML = `
      <div class="pulse-head">
        <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}${staleHTML}</div>
        <div class="pulse-src">${sourceHTML(ind)} · ${esc(ind.frequency)}</div>
      </div>
      <p class="blurb">${esc(ind.why)}</p>
      ${provenanceHTML(ind)}
      ${ind.note ? `<p class="pulse-note">${esc(ind.note)}</p>` : ""}`;
  }
  return card;
}

function renderLeading(data, builtD) {
  const el = document.getElementById("leading");
  for (const gkey of ["leading", "derived"]) {
    const seg = data.segments.find(s => s.key === gkey);
    if (!seg) continue;
    const head = document.createElement("p");
    head.className = "blurb";
    head.innerHTML = `<strong>${esc(seg.title)}.</strong> ${esc(seg.subtitle)}`;
    el.appendChild(head);
    for (const ind of seg.indicators) el.appendChild(indicatorCard(ind, builtD));
  }
}

const DETAIL_LABELS = {
  direction: ["direction", v => esc(v)],
  current_spread: ["VIX − VXV now", v => fmtSigned(v, 2)],
  current_r21_pct: ["SPY 21-session return now", v => fmtSigned(v, 1, "%")],
  regime_since: ["regime since", v => esc(v)],
  current_share: ["panels in positive momentum now", v => fmt(v * 100, 0) + "%"],
  panels_positive: ["panels positive", v => String(v)],
  panels_total: ["panels", v => String(v)],
  extreme_date: ["extreme printed", v => esc(v)],
  extreme_level: ["extreme level", v => fmt(v, 2) + "%"],
  current_c21: ["21-session change now", v => fmtSigned(v, 2)],
  current_level: ["level now", v => fmtSigned(v, 2)],
};

function detailHTML(detail) {
  if (!detail) return "";
  const parts = [];
  for (const [k, v] of Object.entries(detail)) {
    if (v == null) continue;
    const spec = DETAIL_LABELS[k];
    if (spec) parts.push(`${spec[0]}: ${spec[1](v)}`);
    else if (typeof v === "number") parts.push(`${k}: ${fmt(v)}`);
    else parts.push(`${k}: ${esc(v)}`);
  }
  return parts.join("<br>");
}

function renderInflections(data) {
  const el = document.getElementById("inflections");
  const rows = data.inflections.map(s => `
    <tr>
      <td><strong>${esc(s.title)}</strong></td>
      <td>${s.last_fired ? esc(s.last_fired) : "—"}</td>
      <td>${detailHTML(s.detail)}</td>
      <td>${esc(s.definition)}</td>
    </tr>`).join("");
  el.innerHTML = `<table class="pulse-table">
    <thead><tr><th>Signal</th><th>Last fired</th><th>Detail</th><th>Definition</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderRotation(data, builtD) {
  const el = document.getElementById("rotation");
  const pats = data.rotation.patterns.map(p => `
    <tr>
      <td><strong>${esc(p.title)}</strong></td>
      <td><span class="state-pill ${p.active ? "state-on" : "state-off"}">${p.active ? "active" : "not active"}</span></td>
      <td>${esc(p.definition)}${p.measured ? `<br>${measuredHTML(p.measured)}` : ""}</td>
    </tr>`).join("");
  const quads = data.rotation.quadrants.map(q => `
    <tr>
      <td><strong>${esc(q.key.toUpperCase())}</strong></td>
      <td class="num ${cls(q.rs_trend_pp)}">${fmtSigned(q.rs_trend_pp, 1)}</td>
      <td class="num ${cls(q.rs_momentum_pp)}">${fmtSigned(q.rs_momentum_pp, 1)}</td>
      <td><span class="quad-chip quad-${esc(q.quadrant)}">${esc(q.quadrant)}</span></td>
    </tr>`).join("");
  el.innerHTML = `
    <table class="pulse-table">
      <thead><tr><th>Named pattern</th><th>Status</th><th>Definition</th></tr></thead>
      <tbody>${pats}</tbody></table>
    <table class="pulse-table" style="margin-top:1rem">
      <thead><tr><th>Universe member</th><th class="num">RS trend, 63s (pp vs SPY)</th><th class="num">RS momentum, 21s (pp vs SPY)</th><th>Quadrant</th></tr></thead>
      <tbody>${quads}</tbody></table>`;
  // The defensive-minus-cyclical spread line, from the derived segment.
  const seg = data.segments.find(s => s.key === "derived");
  const ind = seg && seg.indicators.find(i => i.key === "def_cyc_spread");
  const host = document.getElementById("rotation-spread");
  if (ind && ind.series && ind.series.length) {
    host.appendChild(indicatorCard(ind, builtD, { zero: true }));
  }
}

function renderGrid(data) {
  const el = document.getElementById("grid");
  // Shared y domain across panels: the small-multiples rule.
  let lo = Infinity, hi = -Infinity;
  for (const g of data.grid) for (const p of g.chart) {
    if (p.value < lo) lo = p.value;
    if (p.value > hi) hi = p.value;
  }
  for (const g of data.grid) {
    const card = document.createElement("div");
    card.className = "pulse-card";
    card.innerHTML = `
      <div class="pulse-head">
        <div><strong>${esc(g.title)}</strong>
          <span class="pulse-last">${fmt(g.last)}</span>
          <span class="pulse-date">${esc(g.last_date)}</span></div>
      </div>
      <p class="blurb">5s <b class="${cls(g.r5_pct)}">${fmtSigned(g.r5_pct, 1, "%")}</b> ·
        21s <b class="${cls(g.r21_pct)}">${fmtSigned(g.r21_pct, 1, "%")}</b> ·
        63s <b class="${cls(g.r63_pct)}">${fmtSigned(g.r63_pct, 1, "%")}</b><br>
        from 52-week high: <b>${fmtSigned(g.dist_high_pct, 1, "%")}</b> ·
        realized vol (21s): <b>${fmt(g.vol21_pct, 0)}%</b></p>
      ${provenanceHTML({ series_id: g.series_id, definition: g.definition,
        frequency: g.frequency, last_observation: g.last_date, source_url: g.source_url })}
      <div class="pulse-chart"></div>`;
    const pts = toPts(g.chart);
    const fig = basePlot({
      width: 340, height: 130,
      marginLeft: 36, marginRight: 8, marginTop: 8, marginBottom: 22,
      x: { type: "time", label: null, tickSize: 0, ticks: 3 },
      y: { label: null, grid: true, nice: false, domain: [lo, hi] },
      marks: [
        Plot.ruleY([100], { stroke: T().faint, strokeDasharray: "3,3" }),
        Plot.areaY(pts, { x: "date", y: "v", fill: T().accent, fillOpacity: 0.08 }),
        Plot.line(pts, { x: "date", y: "v", stroke: T().accent, strokeWidth: 1.5 }),
      ],
    });
    card.querySelector(".pulse-chart").appendChild(fig);
    el.appendChild(card);
  }
}

function renderMovers(data) {
  const el = document.getElementById("movers");
  const cell = (row, n) => {
    const v = row["c" + n];
    if (v == null) return `<td class="num">n/a</td>`;
    return row.kind === "bp"
      ? `<td class="num ${cls(v)}">${fmtSigned(v, 0)} bp</td>`
      : `<td class="num ${cls(v)}">${fmtSigned(v, 1)}%</td>`;
  };
  const rows = data.movers.map(r => `
    <tr>
      <td><strong>${esc(r.title)}</strong></td>
      <td class="num">${fmt(r.last)}${r.unit ? " " + esc(r.unit) : ""}</td>
      <td>${esc(r.last_date)}</td>
      ${cell(r, 5)}${cell(r, 21)}${cell(r, 63)}
    </tr>`).join("");
  el.innerHTML = `<table class="pulse-table">
    <thead><tr><th>Series</th><th class="num">Last</th><th>Date</th>
    <th class="num">5 sessions</th><th class="num">21 sessions</th><th class="num">63 sessions</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function renderVolPanel(data) {
  const host = document.getElementById("volpanel");
  const vp = data.vol_panel;
  if (!vp || !vp.vix) return;
  const card = document.createElement("div");
  card.className = "pulse-card";
  card.innerHTML = `
    <div class="pulse-head"><div><strong>The volatility complex</strong></div></div>
    <p class="blurb">VIX (spot, 30-day) against VXV (3-month): when the spot line crosses
      above the 3-month line the curve is inverted — the vol-inversion fingerprint.
      Below, their spread; above zero is backwardation.</p>
    <div class="vp-lines"></div>
    <div class="vp-spread"></div>`;
  const mk = (key, color) => toPts(vp[key]).map(d => ({ ...d, g: key.toUpperCase() }));
  const lines = [...mk("vix", 0), ...mk("vxv", 1)];
  const fig1 = basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { label: "volatility (%)", grid: true, nice: true },
    color: { legend: true, domain: ["VIX", "VXV"], range: [T().accent, "#b45309"] },
    marks: [
      Plot.line(lines, { x: "date", y: "v", stroke: "g", strokeWidth: 1.8 }),
      Plot.tip(lines, Plot.pointer({
        x: "date", y: "v",
        title: d => `${d.g} · ${d.date.toISOString().slice(0, 10)}\n${fmt(d.v)}`,
      })),
    ],
  });
  card.querySelector(".vp-lines").appendChild(fig1);
  const fig2 = lineChart(vp.spread, "VIX − VXV", { zero: true, height: 160 });
  if (fig2) card.querySelector(".vp-spread").appendChild(fig2);
  host.appendChild(card);
}

async function boot() {
  const data = await loadJSON("momentum.json");
  const builtD = data.meta && data.meta.built_at_utc
    ? data.meta.built_at_utc.slice(0, 10) : null;
  renderTape(data);
  renderFresh(data);
  renderRead(data);
  renderBoard(data);
  renderLeading(data, builtD);
  renderInflections(data);
  renderRotation(data, builtD);
  renderGrid(data);
  renderMovers(data);
  renderVolPanel(data);
  window.__momentumBooted = true;
}

function fail(e) {
  console.error(e);
  const el = document.getElementById("read");
  if (el) el.textContent = `Could not load momentum data: ${e.message}`;
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  themeName = themeName === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = themeName;
  try { localStorage.setItem("frb-heat-theme", themeName); } catch (e) {}
  if (window.__momentumBooted) {
    document.getElementById("leading").innerHTML = "";
    document.getElementById("rotation-spread").innerHTML = "";
    document.getElementById("grid").innerHTML = "";
    document.getElementById("volpanel").innerHTML = "";
    boot().catch(fail);
  }
});

boot().catch(fail);
