/* Prediction Markets standalone page. All data from ../data/predmarkets.json;
   nothing hardcoded. Observable Plot visual language, shared with the main
   site: straight polylines (no smoothing — a close is a close), dashed ruleX
   event markers, square corners, and one fixed 0-1 probability axis on every
   probability chart. Realized overlays (fan markers, track records, FOMC
   rate fan, IPO counts, recession quarters) and the prediction-accuracy
   records (per-card forecast-vs-actual sections + the cross-indicator
   scorecard) come from the pipeline-built realized and accuracy datasets —
   never hand-entered. */
import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm";

/* ---------------- theme (same tokens as the main site) ---------------- */

const THEMES = {
  light: {
    ink: "#1b1a16", muted: "#75705f", faint: "#a8a294",
    grid: "#ece7d8", line: "#e9e4d6", missing: "#ece7d9",
    accent: "#1d4ed8", card: "#ffffff", tipBg: "#1b1a16", tipFg: "#faf9f6",
    realized: "#b2182b",
  },
  dark: {
    ink: "#ede7d6", muted: "#a2987f", faint: "#6f675a",
    grid: "#2b261c", line: "#2e2a20", missing: "#2b261c",
    accent: "#7aa2f7", card: "#1d1a13", tipBg: "#ede7d6", tipFg: "#14120d",
    realized: "#f87171",
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

/* Rate/level formatting: fixed decimals, trailing zeros trimmed. */
function fmtRate(v, maxDigits = 2) {
  if (v == null || !Number.isFinite(v)) return "n/a";
  let s = Number(v).toFixed(maxDigits);
  if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s;
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

function isCount(ind) {
  return (ind.unit || "").toLowerCase().includes("count");
}

function fmtVal(ind, v, digits = 1) {
  if (v == null || !Number.isFinite(v)) return "n/a";
  if (isCount(ind)) return Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
  return pct(v, digits);
}

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
  // Probability indicators share the fixed 0–1 axis; count indicators
  // (IPO pipeline) get a data-driven axis.
  const yAxis = isCount(ind)
    ? { grid: true, label: null, tickFormat: v => v }
    : { domain: [0, 1], ticks: 5, tickFormat: v => (v * 100) + "%", grid: true, label: null };
  const tipFmt = d => `${d.g} · ${d.date.toISOString().slice(0, 10)}\n${fmtVal(ind, d.v)}`;
  for (const s of ind.strikes || []) {
    const sp = pts.filter(d => d.g === s);
    if (!sp.length) continue;
    marks.push(Plot.line(sp, {
      x: "date", y: "v",
      stroke: colors[s] || T().accent,
      strokeWidth: 1.8,
      title: tipFmt,
    }));
  }
  marks.push(Plot.tip(pts, Plot.pointer({ x: "date", y: "v", title: tipFmt })));
  return basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: yAxis,
    marks,
  });
}

/* Fan chart for a distributional indicator, with the realized print
   overlaid as a dashed level + dot when the front event has resolved. */
function fanChart(dist) {
  // Bank-of-England-style fan: median line with 50% and 90% interval bands.
  const pts = (dist.fan || []).map(d => ({
    date: new Date(d.date + "T00:00:00"),
    q05: d.q05, q25: d.q25, q50: d.q50, q75: d.q75, q95: d.q95,
  })).filter(d => [d.q05, d.q25, d.q50, d.q75, d.q95].every(Number.isFinite));
  if (!pts.length) return null;
  const band90 = themeName === "dark" ? "#1d4ed833" : "#1d4ed822";
  const band50 = themeName === "dark" ? "#1d4ed855" : "#1d4ed844";
  const marks = [
    Plot.areaY(pts, { x: "date", y1: "q05", y2: "q95", fill: band90 }),
    Plot.areaY(pts, { x: "date", y1: "q25", y2: "q75", fill: band50 }),
    Plot.line(pts, {
      x: "date", y: "q50", stroke: T().accent, strokeWidth: 2,
      title: d => `${d.date.toISOString().slice(0, 10)}\nmedian ${d.q50.toFixed(2)}% · 50% interval ${d.q25.toFixed(2)}–${d.q75.toFixed(2)}%`,
    }),
  ];
  const rz = dist.realized || null;
  if (rz && Number.isFinite(rz.value)) {
    const rzDate = rz.date ? new Date(rz.date + "T00:00:00") : null;
    const okDate = rzDate instanceof Date && !isNaN(rzDate);
    marks.push(Plot.ruleY([rz.value], {
      stroke: T().realized, strokeWidth: 1.4, strokeDasharray: "4,3",
      title: `Realized: ${fmtRate(rz.value)}%${okDate ? " (" + fmtDate(rz.date) + ")" : ""}`,
    }));
    if (okDate) marks.push(Plot.dot([{ d: rzDate, v: rz.value }], {
      x: "d", y: "v", r: 4, fill: T().realized,
      title: () => `Realized ${fmtRate(rz.value)}% · ${fmtDate(rz.date)}`,
    }));
  }
  marks.push(Plot.tip(pts, Plot.pointer({
    x: "date", y: "q50",
    title: d => `${d.date.toISOString().slice(0, 10)}\nmedian ${d.q50.toFixed(2)}%\n90% interval ${d.q05.toFixed(2)}–${d.q95.toFixed(2)}%`,
  })));
  return basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { grid: true, label: null, tickFormat: v => v + "%" },
    marks,
  });
}

function heatmapChart(dist) {
  // Time on x, outcome level on y, color = implied probability mass.
  const hm = dist.heatmap || {};
  const grid = hm.grid || [];
  const cells = hm.cells || [];
  if (!grid.length || !cells.length) return null;
  const rows = [];
  for (const c of cells) {
    const date = new Date(c.date + "T00:00:00");
    (c.masses || []).forEach((m, i) => {
      if (i + 1 < grid.length && m > 0.0005) {
        rows.push({ date, lo: grid[i], hi: grid[i + 1], mid: (grid[i] + grid[i + 1]) / 2, m });
      }
    });
  }
  if (!rows.length) return null;
  const maxM = Math.max(...rows.map(r => r.m));
  return basePlot({
    width: 1020, height: 240,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { grid: true, label: null, tickFormat: v => v + "%" },
    color: { scheme: "blues", domain: [0, maxM], label: "probability mass" },
    marks: [
      Plot.rect(rows, {
        x1: d => d.date, x2: d => new Date(d.date.getTime() + 86400000),
        y1: "lo", y2: "hi", fill: "m",
        title: d => `${d.date.toISOString().slice(0, 10)} · ${d.lo.toFixed(2)}–${d.hi.toFixed(2)}%\nprobability ${pct(d.m)}`,
      }),
    ],
  });
}

/* FOMC decision buckets translated to the implied post-meeting target-
   range upper bound (levels/deltas/notes all come from the pipeline). */
function fomcFanChart(fan) {
  const rows = (fan.rows || []).map(d => ({
    date: new Date(d.date + "T00:00:00"),
    q05: d.q05, q25: d.q25, q50: d.q50, q75: d.q75, q95: d.q95,
  })).filter(d => [d.q05, d.q25, d.q50, d.q75, d.q95].every(Number.isFinite));
  if (!rows.length) return null;
  const rz = (fan.realized || []).filter(r => r.as_of && Number.isFinite(r.post_meeting_upper));
  const band90 = themeName === "dark" ? "#1d4ed833" : "#1d4ed822";
  const band50 = themeName === "dark" ? "#1d4ed855" : "#1d4ed844";
  const marks = [
    Plot.areaY(rows, { x: "date", y1: "q05", y2: "q95", fill: band90 }),
    Plot.areaY(rows, { x: "date", y1: "q25", y2: "q75", fill: band50 }),
  ];
  if (fan.meeting) {
    const md = new Date(fan.meeting + "T00:00:00");
    if (!isNaN(md)) marks.push(Plot.ruleX([md], {
      stroke: T().faint, strokeWidth: 1, strokeDasharray: "5,4",
      title: `FOMC meeting ${fan.meeting} · base (pre-meeting upper bound) ${fmtRate(fan.base_rate)}%`,
    }));
  }
  for (const r of rz) {
    const d = new Date(r.as_of + "T00:00:00");
    marks.push(Plot.ruleX([{ d, y1: r.q05, y2: r.q95 }], {
      x: "d", y1: "y1", y2: "y2", stroke: T().realized, strokeWidth: 1.1,
      title: `${r.meeting} meeting — day-before 90% interval ${fmtRate(r.q05)}–${fmtRate(r.q95)}%`,
    }));
    marks.push(Plot.ruleX([{ d, y1: r.q25, y2: r.q75 }], {
      x: "d", y1: "y1", y2: "y2", stroke: T().realized, strokeWidth: 2.6,
      title: `${r.meeting} meeting — day-before 50% interval ${fmtRate(r.q25)}–${fmtRate(r.q75)}%`,
    }));
    marks.push(Plot.dot([{ d, v: r.post_meeting_upper }], {
      x: "d", y: "v", r: 4.5, fill: T().realized,
      title: `${r.meeting} meeting — realized post-meeting upper bound ${fmtRate(r.post_meeting_upper)}%`,
    }));
  }
  marks.push(Plot.line(rows, {
    x: "date", y: "q50", stroke: T().accent, strokeWidth: 2,
    title: d => `${d.date.toISOString().slice(0, 10)}\nmedian ${fmtRate(d.q50)}% · 50% interval ${fmtRate(d.q25)}–${fmtRate(d.q75)}%`,
  }));
  marks.push(Plot.tip(rows, Plot.pointer({
    x: "date", y: "q50",
    title: d => `${d.date.toISOString().slice(0, 10)}\nmedian ${fmtRate(d.q50)}%\n90% interval ${fmtRate(d.q05)}–${fmtRate(d.q95)}%`,
  })));
  return basePlot({
    width: 1020, height: 260,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 40,
    x: { type: "time", label: null, grid: true, tickSize: 0, ticks: 8 },
    y: { grid: true, label: null, tickFormat: v => fmtRate(v) + "%" },
    marks,
  });
}

/* Realized overlay for the recession ladder: published quarterly real-GDP
   growth with the exchange's two-negative-quarter rule flagged. */
function recessionQuartersChart(rec) {
  const qs = (rec.quarters || [])
    .map(q => ({ q: q.date, v: q.value, flag: !!q.rec_flag }))
    .filter(q => Number.isFinite(q.v));
  if (!qs.length) return null;
  return basePlot({
    width: 1020, height: 200,
    marginLeft: 64, marginRight: 20, marginTop: 14, marginBottom: 52,
    x: { type: "band", label: null, tickSize: 0, tickRotate: -45 },
    y: { grid: true, label: null, tickFormat: v => fmtRate(v) + "%" },
    marks: [
      Plot.ruleY([0], { stroke: T().muted, strokeWidth: 1 }),
      Plot.barY(qs, {
        x: "q", y: "v",
        fill: d => d.flag ? T().realized : T().faint,
        fillOpacity: 0.9,
        title: d => `${d.q}: ${fmtRate(d.v)}% real GDP growth (SAAR, latest published estimate)${d.flag ? " — flagged: part of a two-quarter negative run" : ""}`,
      }),
    ],
  });
}

/* ---------------- card fragments ---------------- */

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
    `<span class="m"><span class="sw" style="background:${colors[s] || "#a8a294"}"></span><b>${esc(s)}</b> ${fmtVal(ind, lv[s])}</span>`
  ).join("");
  return `<div class="pulse-multi">${items}<span class="m"><span class="md">${esc(fmtDate(ind.last_observation))}</span></span></div>`;
}

function paperHTML(ind) {
  if (!ind.paper) return "";
  const doi = ind.paper_doi || "https://doi.org/10.17016/FEDS.2026.010";
  return `<p class="pulse-paper"><span class="pulse-paper-label">Paper:</span> ${esc(ind.paper)} <a href="${esc(doi)}" target="_blank" rel="noopener">FEDS 2026-010</a></p>`;
}

/* "Measures:" block — exact quantity, reference period, source release,
   resolution day, market-data as-of, spec-verification date, and links to
   the exchange's market page(s). All text is pipeline-generated. */
function measuresHTML(ind) {
  const m = ind.measures;
  if (!m || !m.text) return "";
  const hzLabels = {};
  (ind.horizons || []).forEach(h => { if (h.event_ticker) hzLabels[h.event_ticker] = h.label; });
  const links = [];
  const seen = new Set();
  const pushLink = (ticker, url) => {
    if (!url || seen.has(url)) return;
    seen.add(url);
    links.push(`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(hzLabels[ticker] || ticker)} ↗</a>`);
  };
  if (ind.kalshi_url) pushLink(ind.event_ticker, ind.kalshi_url);
  for (const [t, u] of Object.entries(ind.kalshi_urls || {})) pushLink(t, u);
  const metaBits = [];
  if (ind.market_data_through) metaBits.push(`<span>market data through ${esc(ind.market_data_through)}</span>`);
  if (m.verified) metaBits.push(`<span>contract specs verified ${esc(m.verified)}</span>`);
  const meta = metaBits.concat(links).length
    ? `<p class="pulse-def-meta">${metaBits.concat(links).join('<span class="sep">·</span>')}</p>`
    : "";
  return `<div class="pulse-def"><b>Measures:</b> ${esc(m.text)}${meta}</div>`;
}

function trackRecordHTML(ind) {
  const rec = ind.track_record || [];
  if (!rec.length) return "";
  const rows = rec.map(r =>
    `<tr><td>${esc(r.meeting)}</td><td>${esc(r.mode_bucket)} (${pct(r.mode_probability, 0)})</td><td>${esc(r.realized_bucket)}</td><td>${r.hit ? "✓" : "✗"}</td></tr>`
  ).join("");
  const note = ind.track_record_note ? `<p class="pulse-note">${esc(ind.track_record_note)}</p>` : "";
  return `<div class="pulse-sub"><h4>Track record: day-before mode vs realized decision</h4>
    <table class="pulse-table"><thead><tr><th>Meeting</th><th>Mode (day before)</th><th>Realized</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table>${note}</div>`;
}

/* Last-day implied distribution vs the realized print, per resolved event
   in the archive (extends the FOMC track-record pattern to CPI, GDP,
   unemployment, fed funds). */
function realizedTrackHTML(ind) {
  const tr = (ind.realized_track || []).slice()
    .sort((a, b) => String(a.period || a.event).localeCompare(String(b.period || b.event)));
  const note = ind.realized_track_note ? `<p class="pulse-note">${esc(ind.realized_track_note)}</p>` : "";
  if (!tr.length) return note
    ? `<div class="pulse-sub"><h4>Track record: last-day implied distribution vs realized print</h4>${note}</div>`
    : "";
  const rows = tr.map(r => {
    const med = Number.isFinite(r.q50) ? `${fmtRate(r.q50)}% (${fmtRate(r.q25)}–${fmtRate(r.q75)}%)` : "n/a";
    const int90 = Number.isFinite(r.q05) ? `${fmtRate(r.q05)}–${fmtRate(r.q95)}%` : "n/a";
    const rz = Number.isFinite(r.realized) ? `${fmtRate(r.realized)}%` : "not yet available";
    const h50 = r.hit_50 == null ? "" : r.hit_50 ? '<span class="pm-hit">✓</span>' : '<span class="pm-miss">✗</span>';
    const h90 = r.hit_90 == null ? "" : r.hit_90 ? '<span class="pm-hit">✓</span>' : '<span class="pm-miss">✗</span>';
    return `<tr><td>${esc(r.period || r.event)}</td><td>${esc(fmtDate(r.as_of))}</td><td>${med}</td><td>${int90}</td><td>${rz}</td><td>${h50}</td><td>${h90}</td></tr>`;
  }).join("");
  return `<div class="pulse-sub"><h4>Track record: last-day implied distribution vs realized print</h4>
    <table class="pulse-table"><thead><tr><th>Reference</th><th>Day before</th><th>Median (50% int.)</th><th>90% int.</th><th>Realized</th><th>50%</th><th>90%</th></tr></thead>
    <tbody>${rows}</tbody></table>${note}</div>`;
}

function top5HTML(ind) {
  const top = ind.top5 || {};
  const names = Object.keys(top);
  if (!names.length) return "";
  const items = names.map(n => `<span class="m"><b>${esc(n)}</b> ${pct(top[n], 0)}</span>`).join("");
  return `<div class="pulse-sub"><h4>Front-runners by announcement probability</h4><div class="pulse-multi">${items}</div></div>`;
}

/* IPO precision: named companies, realized confirmations, resolution. */
function ipoPrecisionHTML(ind) {
  if (ind.key !== "ipo_pipeline") return "";
  const rz = ind.realized_ipo || null;
  const names = ind.ipo_names || [];
  const bits = [];
  if (rz) {
    const conf = (rz.confirmed || []);
    bits.push(`<p class="pulse-note">Realized so far: <b>${rz.confirmed_count} of ${ind.n_named != null ? ind.n_named : names.length}</b> confirmed`
      + (conf.length ? ` — ${conf.map(esc).join(", ")}` : "")
      + `. ${esc(rz.note || "")}</p>`);
  }
  if (names.length) {
    const chips = names.map(n =>
      `<span class="pm-ipo-name${n.confirmed ? " confirmed" : ""}">${n.confirmed ? "✓ " : ""}${esc(n.name)}</span>`
    ).join("");
    bits.push(`<details class="pm-names"><summary>The ${names.length} named companies (exchange listing as of ${esc(ind.market_data_through)})</summary><div class="pm-ipo-names">${chips}</div></details>`);
  }
  if (!bits.length) return "";
  return `<div class="pulse-sub"><h4>Named companies and realized confirmations</h4>${bits.join("")}</div>`;
}

/* Recession ladder realized overlay: rule-window statuses + quarterly
   growth with flagged negative runs. */
function recessionRealizedHTML(ind) {
  const rec = ind.recession_realized || null;
  if (!rec) return "";
  const hzLabels = {};
  (ind.horizons || []).forEach(h => { if (h.event_ticker) hzLabels[h.event_ticker] = h.label; });
  const statuses = (rec.statuses || []).map(s =>
    `<li><b>${esc(hzLabels[s.event] || s.event)}:</b> ${esc(s.note || "")}</li>`
  ).join("");
  return `<div class="pulse-sub"><h4>Realized overlay: the exchange's recession rule, applied to published BEA estimates</h4>
    ${statuses ? `<ul class="pm-rec-status">${statuses}</ul>` : ""}
    ${rec.note ? `<p class="pulse-note">${esc(rec.note)}</p>` : ""}</div>`;
}

/* Prediction accuracy: the market's recorded forecast at the documented
   lead time vs the realized outcome, per resolved event. Records are
   append-only (pipeline-built from the committed archive + realized
   dataset); unresolved outcomes and events read as pending, never 0/0. */
function accuracyHeadline(ind, a) {
  const s = a.summary || {};
  if (!s.n_scored) {
    const next = (a.pending || []).map(p => fmtDate(p.resolves)).sort()[0];
    return `pending — no scored events yet` + (next ? `; next resolves ${next}` : "");
  }
  if (ind.key === "fomc_decision") {
    const hr = s.hit_rate == null ? "n/a" : `${pct(s.hit_rate, 0)} (${s.n_hits}/${s.n_scored})`;
    return `${hr} of meetings called` + (s.brier_mean != null ? ` · mean Brier ${Number(s.brier_mean).toFixed(3)}` : "");
  }
  if (ind.key === "recession") {
    return s.brier_mean == null ? "n/a" : `mean Brier ${Number(s.brier_mean).toFixed(3)} over ${s.n_scored} settled market${s.n_scored === 1 ? "" : "s"}`;
  }
  const unit = ind.key === "ipo_pipeline" ? " IPOs" : "pp";
  return `MAE ${fmtRate(s.mae)}${unit} over ${s.n_scored} resolved event${s.n_scored === 1 ? "" : "s"}`;
}

function accuracyRowsHTML(ind, rows) {
  return rows.map(r => {
    const ref = r.period || fmtDate(r.event_date);
    const lead = r.lead_days === 0 ? "final close" : `${r.lead_days}d before`;
    const asOf = `${fmtDate(r.as_of)} (${lead})`;
    let pred, act, score;
    if (ind.key === "fomc_decision") {
      pred = `${esc(r.predicted_label)} (${pct(r.predicted_probability, 0)})`;
      act = r.actual_label != null ? esc(r.actual_label) : "pending";
      score = r.hit == null ? "pending"
        : (r.hit ? '<span class="pm-hit">✓</span>' : '<span class="pm-miss">✗</span>');
      if (r.brier != null) score += ` · Brier ${Number(r.brier).toFixed(3)}`;
    } else if (ind.key === "recession") {
      pred = pct(r.predicted, 0) + " Yes";
      act = r.actual_label != null ? esc(r.actual_label) : "pending";
      score = r.abs_error != null ? `|err| ${pct(r.abs_error, 0)}` : "pending";
      if (r.brier != null) score += ` · Brier ${Number(r.brier).toFixed(3)}`;
    } else if (ind.key === "ipo_pipeline") {
      pred = Number.isFinite(r.predicted) ? `${Math.round(r.predicted * 10) / 10} expected` : "n/a";
      act = Number.isFinite(r.actual) ? `${Math.round(r.actual)} confirmed` : "pending";
      score = r.abs_error != null ? `|err| ${fmtRate(r.abs_error)}` : "pending";
    } else {
      pred = Number.isFinite(r.predicted) ? `${fmtRate(r.predicted)}%` : "n/a";
      act = Number.isFinite(r.actual) ? `${fmtRate(r.actual)}%` : "pending";
      score = r.abs_error != null ? `|err| ${fmtRate(r.abs_error)}pp` : "pending";
    }
    const tip = `${esc(r.event)} — ${esc(r.note || "")}`;
    return `<tr><td title="${tip}">${esc(ref)}</td><td>${esc(asOf)}</td><td>${pred}</td><td>${act}</td><td>${score}</td></tr>`;
  }).join("");
}

function accuracyHTML(ind) {
  const a = ind.accuracy;
  if (!a) return "";
  const rows = a.rows || [];
  const pending = (a.pending || [])
    .map(p => `${esc(p.event)} resolves ${fmtDate(p.resolves)}`).join("; ");
  const table = rows.length
    ? `<table class="pulse-table"><thead><tr><th>Event</th><th>Forecast as of</th><th>Predicted</th><th>Actual</th><th>Score</th></tr></thead>
      <tbody>${accuracyRowsHTML(ind, rows)}</tbody></table>`
    : "";
  return `<div class="pulse-sub"><h4>Prediction accuracy: forecast vs realized outcome</h4>
    <p class="pulse-note"><b>${esc(accuracyHeadline(ind, a))}</b>${pending ? ` · pending: ${pending}` : ""}</p>
    ${table}
    <p class="pulse-note">${esc(a.convention)}</p></div>`;
}

/* Cross-indicator scorecard: every accuracy-covered market's track
   record side by side. */
function renderScorecard(pm) {
  const el = document.getElementById("predmarkets-accuracy");
  if (!el) return;
  const acc = pm.accuracy;
  if (!acc || !(acc.cards || []).length) { el.innerHTML = ""; return; }
  const rows = acc.cards.map(c => {
    const s = c.summary || {};
    let val = "pending";
    if (s.n_scored) {
      if (c.metric === "hit_rate")
        val = `${pct(s.hit_rate, 0)} (${s.n_hits}/${s.n_scored})`;
      else if (c.metric === "mae")
        val = `MAE ${fmtRate(s.mae)}${c.key === "ipo_pipeline" ? " IPOs" : "pp"}`;
      else
        val = `Brier ${Number(s.brier_mean).toFixed(3)}`;
    }
    const nPending = s.n_pending != null ? s.n_pending : 0;
    return `<tr><td><a href="#ind-${esc(c.key)}">${esc(c.title)}</a></td>
      <td>${esc(val)}</td><td>${s.n_scored}</td><td>${s.n_events}</td><td>${nPending}</td>
      <td><details class="pm-conv"><summary>lead-time convention</summary>${esc(c.convention)}</details></td></tr>`;
  }).join("");
  el.innerHTML = `<div class="pulse-card pm-scorecard">
    <div class="pulse-head"><div><strong>Prediction accuracy — scorecard</strong></div>
      <div class="pulse-src">market forecast vs realized outcome · append-only record</div></div>
    <p class="blurb">${esc(acc.note)}</p>
    <table class="pulse-table"><thead><tr><th>Indicator</th><th>Track record</th><th>Scored</th><th>Recorded</th><th>Pending</th><th>Convention</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <p class="pulse-note">Scored = resolved with the outcome on record. Recorded = entered the append-only archive (an outcome can still be pending). Pending = listed events still trading. Records freeze at first write; the daily run only adds newly resolved events and fills outcomes still pending.</p>
  </div>`;
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
  const builtD = pm.meta && pm.meta.built_at_utc
    ? pm.meta.built_at_utc.slice(0, 10) : null;
  let least = null;
  for (const ind of pm.indicators)
    if (ind.last_observation && (!least || ind.last_observation < least.last_observation))
      least = ind;
  const banner = builtD
    ? `<div class="fresh-banner">Page last updated: <strong>${esc(builtD)}</strong>` +
      (least ? ` · Data through: <strong>${esc(least.last_observation)}</strong> — least recent series: ${esc(least.title)}` : "") +
      `.</div>` : "";
  const stamp = pm.meta && pm.meta.built_at_utc
    ? `<p class="pulse-stamp">Refreshed ${esc(pm.meta.built_at_utc.slice(0, 10))} (UTC) · daily cadence · daily closes of $1 binary contracts read as probabilities · shared 0–1 axes</p>` : "";
  el.innerHTML = banner + stamp;
  for (const ind of pm.indicators) {
    const card = document.createElement("div");
    card.className = "pulse-card";
    card.id = `ind-${ind.key}`;
    const extras = [
      paperHTML(ind),
      trackRecordHTML(ind),
      realizedTrackHTML(ind),
      accuracyHTML(ind),
      ipoPrecisionHTML(ind),
      recessionRealizedHTML(ind),
      top5HTML(ind),
    ].join("");
    if (ind.series && ind.series.length) {
      card.innerHTML = `
        <div class="pulse-head">
          <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}
            ${lastValueHTML(ind)}</div>
          <div class="pulse-src">${esc(ind.source)} · ${esc(ind.frequency)}</div>
        </div>
        <p class="blurb">${esc(ind.why)}</p>
        ${measuresHTML(ind)}
        <div class="pulse-chart"></div>
        <div class="pulse-dist"></div>
        ${extras}
        ${ind.note ? `<p class="pulse-note">${esc(ind.note)}</p>` : ""}`;
      const fig = indicatorChart(ind);
      if (fig) card.querySelector(".pulse-chart").appendChild(fig);
      const dw = card.querySelector(".pulse-dist");
      if (ind.distribution) {
        const fan = fanChart(ind.distribution);
        if (fan) {
          const h = document.createElement("h4");
          h.className = "pulse-subhead";
          h.textContent = "Implied distribution over time — fan chart (median, 50% and 90% intervals)";
          dw.appendChild(h);
          dw.appendChild(fan);
          const rz = ind.distribution.realized;
          if (rz && Number.isFinite(rz.value)) {
            const p = document.createElement("p");
            p.className = "pulse-note";
            p.textContent = `Realized outcome: ${fmtRate(rz.value)}%${rz.date ? " (" + fmtDate(rz.date) + ")" : ""} — marked on the fan. ${rz.note || ""}`;
            dw.appendChild(p);
          }
        }
        const hm = heatmapChart(ind.distribution);
        if (hm) {
          const h = document.createElement("h4");
          h.className = "pulse-subhead";
          h.textContent = "Implied distribution over time — density heatmap";
          dw.appendChild(h);
          dw.appendChild(hm);
          const note = document.createElement("p");
          note.className = "pulse-note";
          note.textContent = ind.distribution.note || "";
          dw.appendChild(note);
        }
      }
      // FOMC decision -> implied post-meeting rate fan (additional view).
      if (ind.fomc_fan) {
        const ff = ind.fomc_fan;
        if ((ff.rows || []).length) {
          const h = document.createElement("h4");
          h.className = "pulse-subhead";
          h.textContent = "Implied post-meeting funds rate — fan chart (target-range upper bound; median, 50% and 90% intervals)";
          dw.appendChild(h);
          const fig2 = fomcFanChart(ff);
          if (fig2) dw.appendChild(fig2);
        }
        const notes = [ff.level_note, ff.note, ff.track_note].filter(Boolean);
        if (notes.length) {
          const p = document.createElement("p");
          p.className = "pulse-note";
          p.textContent = notes.join(" ");
          dw.appendChild(p);
        }
        const rz = (ff.realized || []);
        if (rz.length) {
          const rows = rz.map(r => {
            const med = r.as_of && Number.isFinite(r.q50)
              ? `${fmtRate(r.q50)}% (${fmtRate(r.q25)}–${fmtRate(r.q75)}%)` : "n/a";
            const base = Number.isFinite(r.base_rate) ? `${fmtRate(r.base_rate)}%` : "n/a";
            const post = Number.isFinite(r.post_meeting_upper) ? `${fmtRate(r.post_meeting_upper)}%` : "n/a";
            const h50 = r.hit_50 == null ? "" : r.hit_50 ? '<span class="pm-hit">✓</span>' : '<span class="pm-miss">✗</span>';
            const h90 = r.hit_90 == null ? "" : r.hit_90 ? '<span class="pm-hit">✓</span>' : '<span class="pm-miss">✗</span>';
            return `<tr><td>${esc(r.meeting)}</td><td>${base}</td><td>${med}</td><td>${post}</td><td>${h50}</td><td>${h90}</td></tr>`;
          }).join("");
          const div = document.createElement("div");
          div.className = "pulse-sub";
          div.innerHTML = `<h4>Rate fan track record: day-before implied rate vs realized post-meeting rate</h4>
            <table class="pulse-table"><thead><tr><th>Meeting</th><th>Pre-meeting base</th><th>Median (50% int.)</th><th>Realized post-meeting</th><th>50%</th><th>90%</th></tr></thead>
            <tbody>${rows}</tbody></table>`;
          dw.appendChild(div);
        }
      }
      // Recession ladder: quarterly growth bars with flagged runs.
      if (ind.recession_realized) {
        const fig3 = recessionQuartersChart(ind.recession_realized);
        if (fig3) {
          const h = document.createElement("h4");
          h.className = "pulse-subhead";
          h.textContent = "Realized quarterly real-GDP growth (published BEA estimates; negative two-quarter runs flagged)";
          dw.appendChild(h);
          dw.appendChild(fig3);
        }
      }
    } else {
      card.innerHTML = `
        <div class="pulse-head">
          <div><strong>${esc(ind.title)}</strong>${statusBadge(ind.status)}</div>
          <div class="pulse-src">${esc(ind.source)} · ${esc(ind.frequency)}</div>
        </div>
        <p class="blurb">${esc(ind.why)}</p>
        ${measuresHTML(ind)}
        ${extras}
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
  renderScorecard(pm);
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
