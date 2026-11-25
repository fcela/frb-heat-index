/* Early Warning standalone page. All data from ../data/pulse.json; nothing
   hardcoded. Observable Plot visual language, shared with the main site. */
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

// Categorical palette for multi-series indicators (readable on both themes).
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

function singleLine(series, unit) {
  const pts = series
    .map(d => ({ date: new Date(d.date + "T00:00:00"), v: d.value }))
    .filter(d => d.v != null && Number.isFinite(d.v));
  if (!pts.length) return null;
  const last = pts[pts.length - 1];
  return basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { label: unit || null, grid: true, nice: true },
    marks: [
      Plot.areaY(pts, { x: "date", y: "v", fill: T().accent, fillOpacity: 0.08, curve: "monotone-x" }),
      Plot.line(pts, { x: "date", y: "v", stroke: T().accent, strokeWidth: 1.8, curve: "monotone-x" }),
      Plot.dot([last], { x: "date", y: "v", fill: T().accent, stroke: T().card, strokeWidth: 1.5, r: 4 }),
      Plot.tip(pts, Plot.pointer({
        x: "date", y: "v",
        title: d => `${d.date.toISOString().slice(0, 10)}\n${fmt(d.v)}${unit ? " " + unit : ""}`,
      })),
    ],
  });
}

function multiLine(series, unit, groupBy, subseries) {
  const colors = {};
  subseries.forEach((s, i) => { colors[s] = SERIES_COLORS[i % SERIES_COLORS.length]; });
  const pts = series
    .map(d => ({ date: new Date(d.date + "T00:00:00"), v: d.value, g: d[groupBy] }))
    .filter(d => d.v != null && Number.isFinite(d.v) && d.g != null);
  if (!pts.length) return null;
  const marks = subseries.map(s => {
    const sp = pts.filter(d => d.g === s);
    return Plot.line(sp, {
      x: "date", y: "v", stroke: colors[s], strokeWidth: 1.8, curve: "monotone-x",
      title: d => `${s} · ${d.date.toISOString().slice(0, 10)}\n${fmt(d.v)}${unit ? " " + unit : ""}`,
    });
  });
  marks.push(Plot.tip(pts, Plot.pointer({
    x: "date", y: "v",
    title: d => `${d.g} · ${d.date.toISOString().slice(0, 10)}\n${fmt(d.v)}${unit ? " " + unit : ""}`,
  })));
  return basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { label: unit || null, grid: true, nice: true },
    marks,
  });
}

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

/* ---------------- staleness ---------------- */

/* Cadence in days, parsed from the bundle's own frequency strings
   ("Daily", "Weekly (Wednesdays)", "Monthly (~1 month lag)", ...).
   A series is stale when its last observation is older, at bundle
   build time, than one and a half cadence periods plus ten days. The
   ten days absorb the weekly page refresh and ordinary publication
   lag, so a series inside its source's normal lag is never flagged —
   and one that has missed a full cycle is. */
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

/* Series whose upstream publisher has halted the source feed: the
   chart is a frozen print, not a current reading. The freeze date is
   the bundle's own last_observation; the halt is a fact about the
   publisher that the bundle does not carry, so it is stated here and
   rendered only while the series is in fact stale. */
const KNOWN_HALTS = {
  fra_ois: "Publisher halt: FRED discontinued the underlying DCPF1M commercial-paper series, so this series is frozen at its last print and does not read current funding conditions.",
};

function haltNoteHTML(ind, st) {
  if (!st || !st.stale || !KNOWN_HALTS[ind.key]) return "";
  return `<p class="pulse-note">${esc(KNOWN_HALTS[ind.key])}</p>`;
}

/* Source text as a working link (provenance standard: institution +
   dataset name, linked — never text only). Falls back to plain text
   when the feed has no verified canonical URL. */
function sourceHTML(ind) {
  if (!ind.source_url) return esc(ind.source);
  return `<a href="${esc(ind.source_url)}" target="_blank" rel="noopener">${esc(ind.source)}</a>`;
}

/* Provenance block (PR #36 standard): series identifier, one-line
   definition, frequency, last observation date, source link. Pipeline-
   generated fields only — nothing hardcoded here. */
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

function lastValueHTML(ind) {
  // Returns {head, date} where head goes in the card header.
  if (ind.subseries && ind.subseries.length && ind.group_by) {
    const colors = {};
    ind.subseries.forEach((s, i) => { colors[s] = SERIES_COLORS[i % SERIES_COLORS.length]; });
    const lv = ind.last_value || {};
    const items = ind.subseries.map(s =>
      `<span class="m"><span class="sw" style="background:${colors[s]}"></span><b>${esc(s)}</b> ${fmt(lv[s])}</span>`
    ).join("");
    return `<div class="pulse-multi">${items}<span class="m"><span class="md">${esc(String(ind.last_observation || "").slice(0, 10))}</span></span></div>`;
  }
  const last = ind.series[ind.series.length - 1];
  return `<span class="pulse-last">${fmt(last.value)}${ind.unit ? " " + esc(ind.unit) : ""}</span>
    <span class="pulse-date">${esc(String(last.date).slice(0, 10))}</span>`;
}

/* ---------------- render ---------------- */

function renderTape(segments, builtAt) {
  const el = document.getElementById("tape");
  const items = [];
  for (const seg of segments) {
    for (const ind of seg.indicators) {
      if (!ind.series || !ind.series.length) continue;
      const st = staleness(ind, builtAt);
      const suffix = st && st.stale
        ? ` <span class="stale">(stale · ${esc(st.last)})</span>` : "";
      if (ind.subseries && ind.subseries.length) {
        const lv = ind.last_value || {};
        const parts = ind.subseries.map(s => `${esc(s)} <b>${fmt(lv[s])}</b>`).join(" · ");
        items.push(`<span class="tape-item">${esc(ind.title)}: ${parts}${suffix}</span>`);
      } else {
        const last = ind.series[ind.series.length - 1];
        items.push(`<span class="tape-item">${esc(ind.title)}: <b>${fmt(last.value)}</b>${ind.unit ? " " + esc(ind.unit) : ""}${suffix}</span>`);
      }
    }
  }
  el.innerHTML = items.join("");
}

function renderToc(segments) {
  const el = document.getElementById("pulse-toc");
  el.innerHTML = segments.map(s =>
    `<a href="#seg-${esc(s.key)}">${esc(s.title)}</a>`).join("");
}

function renderPulse(pulse) {
  const el = document.getElementById("pulse");
  el.innerHTML = "";
  const builtD = pulse.meta && pulse.meta.built_at_utc
    ? pulse.meta.built_at_utc.slice(0, 10) : null;
  let least = null;
  for (const seg of pulse.segments)
    for (const ind of seg.indicators)
      if (ind.last_observation && (!least || ind.last_observation < least.last_observation))
        least = ind;
  const banner = builtD
    ? `<div class="fresh-banner">Page last updated: <strong>${esc(builtD)}</strong>` +
      (least ? ` · Data through: <strong>${esc(least.last_observation)}</strong> — least recent series: ${esc(least.title)}` : "") +
      `.</div>` : "";
  const stamp = pulse.meta && pulse.meta.built_at_utc
    ? `<p class="pulse-stamp">Refreshed ${esc(pulse.meta.built_at_utc.slice(0, 10))} (UTC) · weekly cadence · raw market series in their own units, not heat quantiles</p>` : "";
  el.innerHTML = banner + stamp;
  for (const seg of pulse.segments) {
    const sec = document.createElement("div");
    sec.className = "pulse-seg";
    sec.id = `seg-${seg.key}`;
    const fetchable = seg.indicators.filter(i => i.series && i.series.length).length;
    sec.innerHTML = `<h3>${esc(seg.title)} <span class="pulse-count">${fetchable}/${seg.indicators.length} charted</span></h3>
      <p class="blurb">${esc(seg.subtitle)}</p>`;
    for (const ind of seg.indicators) {
      const card = document.createElement("div");
      card.className = "pulse-card";
      const st = staleness(ind, builtD);
      const staleHTML = st && st.stale ? staleBadge(st) : "";
      if (ind.series && ind.series.length) {
        card.innerHTML = `
          <div class="pulse-head">
            <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}${staleHTML}
              ${lastValueHTML(ind)}</div>
            <div class="pulse-src">${sourceHTML(ind)} · ${esc(ind.frequency)}</div>
          </div>
          <p class="blurb">${esc(ind.why)}</p>
          ${provenanceHTML(ind)}
          <div class="pulse-chart"></div>
          ${ind.note ? `<p class="pulse-note">${esc(ind.note)}</p>` : ""}
          ${haltNoteHTML(ind, st)}`;
        const fig = (ind.subseries && ind.subseries.length && ind.group_by)
          ? multiLine(ind.series, ind.unit, ind.group_by, ind.subseries)
          : singleLine(ind.series, ind.unit);
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
      sec.appendChild(card);
    }
    el.appendChild(sec);
  }
}

async function boot() {
  const pulse = await loadJSON("pulse.json");
  renderTape(pulse.segments,
    pulse.meta && pulse.meta.built_at_utc ? pulse.meta.built_at_utc.slice(0, 10) : null);
  renderToc(pulse.segments);
  renderPulse(pulse);
  document.getElementById("pulse").dataset.done = "1";
  window.__pulseBooted = true;
}

function fail(e) {
  console.error(e);
  const el = document.getElementById("pulse");
  if (el) el.innerHTML = `<p class="pulse-note">Could not load pulse data: ${esc(e.message)}</p>`;
}

document.getElementById("theme-toggle").addEventListener("click", () => {
  themeName = themeName === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = themeName;
  try { localStorage.setItem("frb-heat-theme", themeName); } catch (e) {}
  if (window.__pulseBooted) boot().catch(fail);
});

boot().catch(fail);
