// End-to-end render test for docs/predmarkets/app.js with stubbed DOM + Plot.
// Runs the real boot() against the committed bundle (docs/data/predmarkets.json)
// and asserts, per indicator, that the definition/realized features render:
// Measures blocks, exchange links, market-data as-of dates, fan charts,
// realized overlays, the FOMC rate fan, IPO precision, recession quarters.
// Usage: node render-harness.mjs <repo-root> <tmp-dir>
import { readFileSync, writeFileSync, copyFileSync } from "fs";
import { join } from "path";

const REPO = process.argv[2];
const TMP = process.argv[3];
const FIX = join(REPO, "tests/fixtures/predmarkets");

function mkEl() {
  const el = {
    _html: "", _children: [], _slots: {}, _listeners: {},
    className: "", id: "", textContent: "", dataset: {},
    setAttribute() {},
    addEventListener(ev, fn) { (this._listeners[ev] ??= []).push(fn); },
    click() { for (const fn of this._listeners.click || []) fn(); },
    appendChild(c) { this._children.push(c); return c; },
    querySelector(sel) { return (this._slots[sel] ??= mkEl()); },
  };
  Object.defineProperty(el, "innerHTML", {
    set(h) { this._html = h; this._children = []; this._slots = {}; },
    get() { return this._html; },
    configurable: true,
  });
  return el;
}

const byId = {};
globalThis.window = globalThis;
globalThis.document = {
  createElement: () => mkEl(),
  getElementById: (id) => (byId[id] ??= mkEl()),
  documentElement: { dataset: { theme: "light" } },
};
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.fetch = async (url) => {
  const name = url.split("/").pop();
  const body = readFileSync(join(REPO, "docs/data", name), "utf8");
  return { ok: true, json: async () => JSON.parse(body) };
};

// Rewrite the Plot CDN import to the local stub.
const src = readFileSync(join(REPO, "docs/predmarkets/app.js"), "utf8")
  .replace(
    'from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm"',
    `from "${join(TMP, "plot-stub.mjs")}"`
  );
writeFileSync(join(TMP, "app-test.mjs"), src);
copyFileSync(join(FIX, "plot-stub.mjs"), join(TMP, "plot-stub.mjs"));

const esc = (s) => String(s).replace(/[&<>"']/g, c => ({
    "&": "&" + "amp;",
    "<": "&" + "lt;",
    ">": "&" + "gt;",
    '"': "&" + "quot;",
    "'": "&" + "#39;",
  }[c]));

const bundle = JSON.parse(readFileSync(join(REPO, "docs/data/predmarkets.json"), "utf8"));
const byKey = Object.fromEntries(bundle.indicators.map(i => [i.key, i]));

await import(join(TMP, "app-test.mjs") + "?x=" + Date.now());
await new Promise(r => setTimeout(r, 50));  // let the async boot() land

const results = { cards: 0, figures: 0, errors: [], tocAnchors: 0, tapeItems: 0 };

const root = byId["predmarkets"];
results.cards = root._children.length;
if (results.cards !== bundle.indicators.length)
  results.errors.push(`expected ${bundle.indicators.length} cards, got ${results.cards}`);

const figures = (el) => el._children.filter(c => c && c.__figure);
const slotFigs = (card, sel) => figures(card._slots[sel] || { _children: [] });
const marksOf = (fig, kind) => (fig.marks || []).filter(m => m.__mark === kind);

for (const card of root._children) {
  const ind = bundle.indicators[root._children.indexOf(card)];
  const html = card._html;
  const tag = ind.key;

  // Definitions: Measures text (escaped identically), as-of date, links.
  const m = ind.measures || {};
  if (m.text && !html.includes(esc(m.text)))
    results.errors.push(`${tag}: Measures text missing from card HTML`);
  if (ind.market_data_through && !html.includes(`market data through ${ind.market_data_through}`))
    results.errors.push(`${tag}: market-data as-of date missing`);
  if (m.verified && !html.includes(`contract specs verified ${m.verified}`))
    results.errors.push(`${tag}: spec verification date missing`);
  const urls = Object.values(ind.kalshi_urls || {});
  if (!urls.length)
    results.errors.push(`${tag}: no exchange URLs in bundle`);
  for (const u of urls)
    if (!html.includes(`href="${esc(u)}"`))
      results.errors.push(`${tag}: link to ${u} not rendered`);
  if (html.includes("undefined") || html.includes("[object"))
    results.errors.push(`${tag}: template leak (undefined/[object) in card HTML`);

  if (ind.distribution) {
    // Fan chart: bands + median line; realized overlay iff the front event
    // has a realized print in the bundle.
    const fanFigs = slotFigs(card, ".pulse-dist").filter(f => marksOf(f, "areaY").length);
    if (!fanFigs.length) results.errors.push(`${tag}: no fan chart figure`);
    for (const f of fanFigs) {
      if (!marksOf(f, "line").length) results.errors.push(`${tag}: fan has no median line`);
      const rz = ind.distribution.realized;
      const ruleYs = marksOf(f, "ruleY").length;
      if (rz && Number.isFinite(rz.value) && ruleYs === 0)
        results.errors.push(`${tag}: realized print not overlaid on fan`);
      if ((!rz || !Number.isFinite(rz.value)) && ruleYs > 0)
        results.errors.push(`${tag}: unexpected realized overlay on fan`);
    }
    // Heatmap: rect marks from the density grid.
    const hmFigs = slotFigs(card, ".pulse-dist").filter(f => marksOf(f, "rect").length);
    if (!hmFigs.length) results.errors.push(`${tag}: no density heatmap figure`);
    // Track record: table iff resolved events exist, note either way.
    const nTrack = (ind.realized_track || []).length;
    if (nTrack > 0 && html.includes("No resolved events"))
      results.errors.push(`${tag}: resolved events exist but note says none`);
    if (!html.includes(esc((ind.realized_track_note || "").slice(0, 40))))
      results.errors.push(`${tag}: realized track note missing`);
    if (nTrack > 0 && !html.includes("Track record: last-day implied distribution"))
      results.errors.push(`${tag}: track-record table missing`);
  }

  if (ind.key === "fomc_decision") {
    const ff = ind.fomc_fan;
    const fanFigs = slotFigs(card, ".pulse-dist").filter(f => marksOf(f, "areaY").length);
    if (!fanFigs.length) {
      results.errors.push("fomc_decision: no rate fan figure");
    } else {
      const f = fanFigs[0];
      const dots = marksOf(f, "dot").length;
      const expectedDots = (ff.realized || []).filter(r => Number.isFinite(r.post_meeting_upper)).length;
      if (dots !== expectedDots)
        results.errors.push(`fomc_decision: rate fan realized dots ${dots} != expected ${expectedDots}`);
      if (ff.meeting && marksOf(f, "ruleX").length === 0)
        results.errors.push("fomc_decision: meeting-day marker missing from rate fan");
    }
    // The outcome-bars view must remain alongside the fan.
    if (!slotFigs(card, ".pulse-chart").length)
      results.errors.push("fomc_decision: outcome-bars chart missing");
    // The rate-fan track-record table is appended at runtime as a DOM child
    // of the distribution slot (not part of the card's innerHTML string).
    const distSlot = card._slots[".pulse-dist"];
    const distHtml = distSlot ? distSlot._children.map(c => c._html || "").join("") : "";
    if ((ff.realized || []).length && !distHtml.includes("Rate fan track record"))
      results.errors.push("fomc_decision: rate fan track-record table missing");
  }

  if (ind.key === "recession") {
    const rec = ind.recession_realized;
    if (!rec) results.errors.push("recession: realized overlay missing from bundle");
    const barFigs = slotFigs(card, ".pulse-dist").filter(f => marksOf(f, "barY").length);
    if (!barFigs.length) results.errors.push("recession: quarterly growth bars missing");
    for (const f of barFigs)
      if (marksOf(f, "barY")[0].data.length !== (rec.quarters || []).length)
        results.errors.push("recession: bar count != bundle quarters");
    if (!html.includes("Realized overlay")) results.errors.push("recession: rule-window statuses missing");
  }

  if (ind.key === "ipo_pipeline") {
    const rz = ind.realized_ipo || {};
    const names = ind.ipo_names || [];
    if (!html.includes(`The ${names.length} named companies`))
      results.errors.push("ipo_pipeline: named-company count missing");
    if (rz.confirmed_count != null &&
        !html.includes(`${rz.confirmed_count} of ${ind.n_named}`))
      results.errors.push("ipo_pipeline: realized confirmation count missing");
    for (const n of names.slice(0, 3))
      if (!html.includes(esc(n.name))) results.errors.push(`ipo_pipeline: name ${n.name} missing`);
    for (const t of Object.keys(ind.top5 || {}))
      if (!html.includes(esc(t))) results.errors.push(`ipo_pipeline: top-5 name ${t} missing`);
  }

  if (ind.track_record && ind.track_record.length && !html.includes("day-before mode"))
    results.errors.push(`${tag}: FOMC track-record table missing`);
  results.figures += slotFigs(card, ".pulse-chart").length + slotFigs(card, ".pulse-dist").length;
}

// TOC and tape populated from the same bundle.
results.tocAnchors = (byId["predmarkets-toc"]._html.match(/<a /g) || []).length;
results.tapeItems = (byId["tape"]._html.match(/tape-item/g) || []).length;
if (results.tocAnchors !== bundle.indicators.length)
  results.errors.push(`TOC anchors ${results.tocAnchors} != ${bundle.indicators.length}`);
if (results.tapeItems < 1) results.errors.push("tape empty");

// Theme toggle: re-render must rebuild the same card set (no doubling).
byId["theme-toggle"].click();
await new Promise(r => setTimeout(r, 50));
const after = byId["predmarkets"];
if (after._children.length !== bundle.indicators.length)
  results.errors.push(`theme re-render: expected ${bundle.indicators.length} cards, got ${after._children.length}`);

console.log(JSON.stringify(results));
