// End-to-end render test for docs/revenue/app.js with stubbed DOM + Plot.
// Usage: node render-harness.mjs <repo-root> <tmp-dir>
import { readFileSync, writeFileSync, copyFileSync } from "fs";
import { join } from "path";

const REPO = process.argv[2];
const TMP = process.argv[3];
const FIX = join(REPO, "tests/fixtures/revenue_site");

function mkEl() {
  const el = {
    _html: "", _charts: [], children: [], _listeners: {},
    classList: { toggle() {} },
    setAttribute() {},
    addEventListener(ev, fn) { (this._listeners[ev] ??= []).push(fn); },
    click() { for (const fn of this._listeners.click || []) fn(); },
    appendChild(c) { this.children.push(c); return c; },
    querySelectorAll(sel) {
      if (sel === ".chart") return this._charts;
      return [];
    },
    querySelector() { return null; },
  };
  Object.defineProperty(el, "innerHTML", {
    set(h) {
      this._html = h;
      this.children = [];
      const n = (h.match(/class="chart"/g) || []).length;
      this._charts = Array.from({ length: n }, () => mkEl());
    },
    get() { return this._html; },
    configurable: true,
  });
  Object.defineProperty(el, "firstChild", { get() { return this; }, configurable: true });
  return el;
}

const byId = {};
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

// Rewrite the Plot CDN import to the local stub, and analysis.js to TMP.
const src = readFileSync(join(REPO, "docs/revenue/app.js"), "utf8")
  .replace(
    'from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm"',
    `from "${join(TMP, "plot-stub.mjs")}"`
  )
  .replace('from "./analysis.js"', `from "${join(TMP, "analysis-test.mjs")}"`);
writeFileSync(join(TMP, "app-test.mjs"), src);
copyFileSync(join(REPO, "docs/revenue/analysis.js"), join(TMP, "analysis-test.mjs"));
copyFileSync(join(FIX, "plot-stub.mjs"), join(TMP, "plot-stub.mjs"));

const results = { sections: 0, errors: [] };
const mod = await import(join(TMP, "app-test.mjs") + "?x=" + Date.now());
// main() ran on import; inspect the revenue container.
const rev = byId["revenue"];
results.sections = rev.children.length;
// Expected migration-SVG presence per line (recomputed from the bundle).
const { buildPeriods, migration, INDEPENDENTS } = await import(join(TMP, "analysis-test.mjs"));
const bundle = JSON.parse(readFileSync(join(REPO, "docs/data/revenue.json"), "utf8"));
const expectSvg = bundle.lines.map((line) => {
  const P = buildPeriods(line, "a");
  const keep = line.line === "advisory" ? new Set(INDEPENDENTS) : new Set();
  return migration(P, "a", { keepSet: keep }).nWindows >= 2;
});
rev.children.forEach((sec, i) => {
  const charts = sec.querySelectorAll(".chart");
  const filled = charts.filter((c) => c.children.length > 0).length;
  const html = sec.innerHTML;
  const hasSvg = html.includes("<svg");
  if (charts.length !== 5) results.errors.push(`annual section ${i}: expected 5 chart divs, got ${charts.length}`);
  if (expectSvg[i] && !hasSvg) results.errors.push(`annual section ${i}: expected migration SVG, none found`);
  if (!expectSvg[i] && hasSvg) results.errors.push(`annual section ${i}: unexpected migration SVG`);
  if (filled !== 4) results.errors.push(`annual section ${i}: expected 4 appended figures, got ${filled}`);
});

// Exercise the quarterly toggle too: click it, let the async re-render land,
// and re-check section count + migration SVG presence per line.
byId["freq-quarterly"].click();
await new Promise((r) => setTimeout(r, 50));
const revQ = byId["revenue"];
results.qSections = revQ.children.length;
const qBundle = JSON.parse(readFileSync(join(REPO, "docs/data/revenue_quarterly.json"), "utf8"));
const qExpect = qBundle.lines.map((line) => {
  const P = buildPeriods(line, "q");
  const keep = line.line === "advisory" ? new Set(INDEPENDENTS) : new Set();
  return migration(P, "q", { keepSet: keep }).nWindows >= 2;
});
revQ.children.forEach((sec, i) => {
  const charts = sec.querySelectorAll(".chart");
  const hasSvg = sec.innerHTML.includes("<svg");
  if (charts.length !== 5) results.errors.push(`quarterly section ${i}: expected 5 chart divs, got ${charts.length}`);
  if (qExpect[i] && !hasSvg) results.errors.push(`quarterly section ${i}: expected migration SVG, none found`);
  if (!qExpect[i] && hasSvg) results.errors.push(`quarterly section ${i}: unexpected migration SVG`);
});
console.log(JSON.stringify(results));
