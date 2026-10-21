// End-to-end render test for docs/pulse/app.js with stubbed DOM + Plot.
// Runs the real boot() against the committed bundle (docs/data/pulse.json)
// and asserts, per indicator card, the provenance standard: the source as a
// working link, series identifier, one-line definition, frequency, and last
// observation date. URL-less cards (pending-sourcing) must render plain text
// with no anchor. Usage: node render-harness.mjs <repo-root> <tmp-dir>
import { readFileSync, writeFileSync, copyFileSync } from "fs";
import { join } from "path";

const REPO = process.argv[2];
const TMP = process.argv[3];
const FIX = join(REPO, "tests/fixtures/pulse");

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
const src = readFileSync(join(REPO, "docs/pulse/app.js"), "utf8")
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

const bundle = JSON.parse(readFileSync(join(REPO, "docs/data/pulse.json"), "utf8"));
const flat = bundle.segments.flatMap(s => s.indicators);

await import(join(TMP, "app-test.mjs") + "?x=" + Date.now());
await new Promise(r => setTimeout(r, 50));  // let the async boot() land

const results = { cards: 0, figures: 0, sections: 0, provenanceCards: 0,
                  tapeItems: 0, tocAnchors: 0, errors: [] };

const root = byId["pulse"];
results.sections = root._children.length;
if (results.sections !== bundle.segments.length)
  results.errors.push(`expected ${bundle.segments.length} sections, got ${results.sections}`);

const cards = root._children.flatMap(sec => sec._children);
results.cards = cards.length;
if (results.cards !== flat.length)
  results.errors.push(`expected ${flat.length} cards, got ${results.cards}`);

flat.forEach((ind, i) => {
  const card = cards[i];
  if (!card) return;
  const html = card._html;
  const tag = ind.key;

  if (html.includes("undefined") || html.includes("[object"))
    results.errors.push(`${tag}: template leak (undefined/[object) in card HTML`);

  // Provenance block: definition + frequency + last observation always.
  if (!html.includes(`class="pulse-def"`))
    results.errors.push(`${tag}: no provenance block`);
  else results.provenanceCards++;
  if (ind.definition && !html.includes(esc(ind.definition)))
    results.errors.push(`${tag}: definition text missing from card HTML`);
  if (!html.includes(esc(ind.frequency)))
    results.errors.push(`${tag}: frequency missing from card HTML`);
  const last = ind.last_observation ? String(ind.last_observation).slice(0, 10) : "none";
  if (!html.includes(`last observation: ${last}`))
    results.errors.push(`${tag}: last observation '${last}' missing`);

  // Series identifier where one exists.
  if (ind.series_id && !html.includes(`<b>Series:</b> ${esc(ind.series_id)}`))
    results.errors.push(`${tag}: series id '${ind.series_id}' missing`);
  if (!ind.series_id && html.includes("<b>Series:</b>"))
    results.errors.push(`${tag}: series line rendered without a series_id`);

  // Source as a working link where verified; plain text otherwise.
  if (ind.source_url) {
    if (!html.includes(`href="${esc(ind.source_url)}"`))
      results.errors.push(`${tag}: link to ${ind.source_url} not rendered`);
  } else if (html.includes("href=")) {
    results.errors.push(`${tag}: URL-less indicator must not render an anchor`);
  }

  // Chart figure on every card that carries data.
  const chartSlot = card._slots[".pulse-chart"];
  const figs = chartSlot ? chartSlot._children.filter(c => c && c.__figure) : [];
  if (ind.series && ind.series.length) {
    results.figures += figs.length;
    if (!figs.length) results.errors.push(`${tag}: no chart figure rendered`);
  } else if (figs.length) {
    results.errors.push(`${tag}: figure rendered without data`);
  }
});

results.tapeItems = (byId["tape"]._html.match(/tape-item/g) || []).length;
results.tocAnchors = (byId["pulse-toc"]._html.match(/<a /g) || []).length;
if (results.tapeItems < 1) results.errors.push("tape empty");
if (results.tocAnchors !== bundle.segments.length)
  results.errors.push(`TOC anchors ${results.tocAnchors} != ${bundle.segments.length}`);

// Theme toggle: re-render must rebuild the same card set (no doubling).
byId["theme-toggle"].click();
await new Promise(r => setTimeout(r, 50));
const after = byId["pulse"]._children.flatMap(sec => sec._children);
if (after.length !== flat.length)
  results.errors.push(`theme re-render: expected ${flat.length} cards, got ${after.length}`);

console.log(JSON.stringify(results));
