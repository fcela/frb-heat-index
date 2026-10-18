/* Prediction Markets standalone page. All data from ../data/predmarkets.json;
   nothing hardcoded. Observable Plot visual language, shared with the main
   site: straight polylines (no smoothing — a close is a close), dashed ruleX
   event markers, and one fixed 0-1 probability axis on every chart. */
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

function pct(v, digits = 1) {
  if (v == null || !Number.isFinite(v)) return "n/a";
  return (Number(v) * 100).toLocaleString("en-US", { maximumFractionDigits: digits }) + "%";
}

function fmtDate(s) {
  return String(s || "").slice(0, 10);
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

function indicatorChart(ind) {
  const pts = ind.series
    .map(d => ({ date: new Date(d.date + "T00:00:00"), v: d.value, g: d.strike }))
    .filter(d => d.v != null && Number.isFinite(d.v));
  if (!pts.length) return null;
  const colors = ind.strike_colors || {};
  const events = (ind.horizons || [])
    .map(h => ({ date: new Date(h.date + "T00:00:00"), label: h.label, ticker: h.event_ticker }))
    .filter(h => h.date instanceof Date && !isNaN(h.date));
  const marks = [];
  if (events.length) {
    marks.push(Plot.ruleX(events, {
      x: "date",
      stroke: T().faint,
      strokeWidth: 1,
      strokeDasharray: "5,4",
      title: h => `${h.label}\nresolves ${h.date.toISOString().slice(0, 10)}`,
    }));
  }
  for (const s of ind.strikes || []) {
    const sp = pts.filter(d => d.g === s);
    if (!sp.length) continue;
    marks.push(Plot.line(sp, {
      x: "date", y: "v",
      stroke: colors[s] || T().accent,
      strokeWidth: 1.8,
      title: d => `${s} · ${d.date.toISOString().slice(0, 10)}\n${pct(d.v)}`,
    }));
  }
  marks.push(Plot.tip(pts, Plot.pointer({
    x: "date", y: "v",
    title: d => `${d.g} · ${d.date.toISOString().slice(0, 10)}\n${pct(d.v)}`,
  })));
  return basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { domain: [0, 1], ticks: 5, tickFormat: v => (v * 100) + "%", grid: true, label: null },
    marks,
  });
}

/* ---------------- render ---------------- */

function statusBadge(status) {
  const label = {
    "live": "live",
    "no-open-market": "no open market",
    "error": "unavailable",
  }[status] || status;
  return `<span class="status-badge status-${esc(status)}">${esc(label)}</span>`;
}

function lastValueHTML(ind) {
  const colors = ind.strike_colors || {};
  const lv = ind.last_value || {};
  const items = (ind.strikes || []).map(s =>
    `<span class="m"><span class="sw" style="background:${colors[s] || "#a8a294"}"></span><b>${esc(s)}</b> ${pct(lv[s])}</span>`
  ).join("");
  return `<div class="pulse-multi">${items}<span class="m"><span class="md">${esc(fmtDate(ind.last_observation))}</span></span></div>`;
}

function renderTape(inds) {
  const el = document.getElementById("tape");
  el.innerHTML = inds.map(ind => {
    const h = (ind.horizons || [])[0];
    const q = ind.event_title ? `<b>${esc(ind.event_title)}</b>` : "";
    const when = h && h.date ? `resolves <b>${esc(fmtDate(h.date))}</b>` : "";
    if (!q && !when) return "";
    const sep = q && when ? " · " : "";
    return `<span class="tape-item">${esc(ind.title)}: ${q}${sep}${when}</span>`;
  }).join("");
}

function renderToc(inds) {
  document.getElementById("predmarkets-toc").innerHTML = inds.map(i =>
    `<a href="#ind-${esc(i.key)}">${esc(i.title)}</a>`).join("");
}

function renderMarkets(pm) {
  const el = document.getElementById("predmarkets");
  el.innerHTML = "";
  const stamp = pm.meta && pm.meta.built_at_utc
    ? `<p class="pulse-stamp">Refreshed ${esc(pm.meta.built_at_utc.slice(0, 10))} (UTC) · daily cadence · daily closes of $1 binary contracts read as probabilities · shared 0–1 axes</p>` : "";
  el.innerHTML = stamp;
  for (const ind of pm.indicators) {
    const card = document.createElement("div");
    card.className = "pulse-card";
    card.id = `ind-${ind.key}`;
    if (ind.series && ind.series.length) {
      card.innerHTML = `
        <div class="pulse-head">
          <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}
            ${lastValueHTML(ind)}</div>
          <div class="pulse-src">${esc(ind.source)} · ${esc(ind.frequency)}</div>
        </div>
        <p class="blurb">${esc(ind.why)}</p>
        <div class="pulse-chart"></div>
        ${ind.note ? `<p class="pulse-note">${esc(ind.note)}</p>` : ""}`;
      const fig = indicatorChart(ind);
      if (fig) card.querySelector(".pulse-chart").appendChild(fig);
    } else {
      card.innerHTML = `
        <div class="pulse-head">
          <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}</div>
          <div class="pulse-src">${esc(ind.source)} · ${esc(ind.frequency)}</div>
        </div>
        <p class="blurb">${esc(ind.why)}</p>
        ${ind.note ? `<p class="pulse-note">${esc(ind.note)}</p>` : ""}`;
    }
    el.appendChild(card);
  }
}

async function boot() {
  const pm = await loadJSON("predmarkets.json");
  renderTape(pm.indicators);
  renderToc(pm.indicators);
  renderMarkets(pm);
  document.getElementById("predmarkets").dataset.done = "1";
  window.__predBooted = true;
}

function fail(e) {
  console.error(e);
  const el = document.getElementById("predmarkets");
  if (el) el.innerHTML = `<p class="pulse-note">Could not load prediction-markets data: ${esc(e.message)}</p>`;
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  themeName = themeName === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = themeName;
  try { localStorage.setItem("frb-heat-theme", themeName); } catch (e) {}
  if (window.__predBooted) boot().catch(fail);
});

boot().catch(fail);
