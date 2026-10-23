// End-to-end render test for docs/assets/app.js (the heat-index page) with
// stubbed DOM + Plot. Boots the real app against the committed bundles
// (docs/data/*.json), then flips the view switch to "Clustered" and asserts
// the clustered heat map renders: 46 rows in the shipped order, all
// quarter cells present, dendrogram segments, component chips, top-level
// cluster rules, tooltips, and the component legend. Also checks the
// hierarchy view and hash routing are untouched by the toggle.
// Usage: node render-harness.mjs <repo-root> <tmp-dir>
import { readFileSync, writeFileSync, copyFileSync } from "fs";
import { join } from "path";

const REPO = process.argv[2];
const TMP = process.argv[3];
const FIX = join(REPO, "tests/fixtures/frb");

function mkEl() {
  const el = {
    _html: "", _children: [], _slots: {}, _listeners: {},
    className: "", id: "", textContent: "", dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
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
const handlers = {};
globalThis.window = globalThis;
globalThis.document = {
  createElement: () => mkEl(),
  createDocumentFragment: () => mkEl(),
  getElementById: (id) => (byId[id] ??= mkEl()),
  documentElement: { dataset: { theme: "light" } },
  body: mkEl(),
};
globalThis.addEventListener = (ev, fn) => { handlers[ev] = fn; };
globalThis.location = { hash: "", pathname: "/", search: "" };
globalThis.history = { replaceState() {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.fetch = async (url) => {
  const name = url.split("/").pop();
  const body = readFileSync(join(REPO, "docs/data", name), "utf8");
  return { ok: true, json: async () => JSON.parse(body) };
};

// Rewrite the Plot CDN import to the local stub.
const src = readFileSync(join(REPO, "docs/assets/app.js"), "utf8").replace(
  'from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.17/+esm"',
  `from "${join(TMP, "plot-stub.mjs")}"`,
);
writeFileSync(join(TMP, "app-test.mjs"), src);
copyFileSync(join(FIX, "plot-stub.mjs"), join(TMP, "plot-stub.mjs"));

const readJSON = (name) =>
  JSON.parse(readFileSync(join(REPO, "docs/data", name), "utf8"));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const META = readJSON("meta.json");
const HIER = readJSON("hierarchy.json");
const CLUSTER = readJSON("clustered.json");

const results = { errors: [] };
const err = (m) => results.errors.push(m);

await import(join(TMP, "app-test.mjs") + "?x=" + Date.now());
await new Promise(r => setTimeout(r, 50));  // let the async boot() land

/* --- 1. default boot: hierarchy view, untouched --- */
if (!window.__frbBooted) err("boot() did not complete");
if (document.body.dataset.view) err("default view must stay hierarchy (unset)");

/* --- 1a. freshness banner: build date + least-recent observed series --- */
const bannerText = byId["fresh-banner"]?._html || "";
if (!bannerText.includes("Page last updated")) err("freshness banner did not render");
if (META.built_at_utc && !bannerText.includes(META.built_at_utc.slice(0, 10)))
  err("freshness banner missing the build date");
if (META.freshness) {
  if (!bannerText.includes(META.freshness.observed_through))
    err("freshness banner missing the observed-through quarter");
  if (!bannerText.includes(esc(META.freshness.least_series)))
    err("freshness banner missing the least-recent series name");
}
if (byId["heatmap"]?._children.length !== 1) err("component heat map did not render");
if (byId["aggchart"]?._children.length !== 1) err("aggregate chart did not render");
if (byId["hierarchy"]?._children.length !== HIER.categories.length)
  err("hierarchy view did not render its categories");
if ((byId["clustermap"]?._children.length ?? 0) !== 0)
  err("clustered figure must not render before the view is switched");

/* --- 1b. indicator heat map: all 46 indicators, hierarchy groups marked --- */
const ifig = byId["indmap"]?._children[0];
if (!ifig || !ifig.__figure) err("indicator heat map did not render a figure");
const indRowsExp = [];
const compNamesExp = [], catNamesExp = [];
for (const cat of HIER.categories) {
  catNamesExp.push(cat.name);
  for (const comp of cat.components) {
    compNamesExp.push(comp.name);
    for (const ind of comp.indicators) indRowsExp.push(ind.short);
  }
}
let iCells = [], iMissing = [], iCompRules = 0, iCatRules = 0;
if (ifig) {
  if (ifig.options.width !== 1280) err("unexpected indicator figure width");
  const nSlots = indRowsExp.length + HIER.categories.length;
  if (ifig.options.height !== nSlots * 16 + 34)
    err(`unexpected indicator figure height ${ifig.options.height}`);
  const imarks = (kind) => ifig.marks.filter(m => m.__mark === kind);
  const irects = imarks("rect");
  if (irects.length !== 2) err(`expected 2 rect marks (cells, missing) in indicator map, got ${irects.length}`);
  const icm = irects.find(m => m.data.every(c => c.v != null));
  const imm = irects.find(m => m.data.every(c => c.v == null));
  if (!icm || !imm) err("could not tell indicator cells from missing cells");
  iCells = icm ? icm.data : [];
  iMissing = imm ? imm.data : [];
  if (iCells.length + iMissing.length !== indRowsExp.length * META.n_quarters)
    err(`expected ${indRowsExp.length * META.n_quarters} indicator cells, got ${iCells.length} + ${iMissing.length} missing`);
  const iseen = new Set(iCells.concat(iMissing).map(c => `${c.p}:${c.q}`));
  if (iseen.size !== indRowsExp.length * META.n_quarters)
    err("duplicate or absent (row, quarter) cells in indicator map");
  for (const c of iCells)
    if (!(c.v >= 0 && c.v <= 1)) err(`indicator cell value out of [0,1]: ${c.v}`);
  // text marks: 46 row labels in hierarchy order, 14 component labels, 3 category headers
  const itexts = imarks("text");
  const byLen = (n) => itexts.filter(m => m.data.length === n);
  if (byLen(indRowsExp.length).length !== 1) err("indicator map: row-label mark not found");
  else {
    const got = byLen(indRowsExp.length)[0].data.map(d => d.text);
    for (let i = 0; i < indRowsExp.length; i++)
      if (got[i] !== indRowsExp[i]) err(`indicator row ${i} label "${got[i]}" != hierarchy order "${indRowsExp[i]}"`);
  }
  if (byLen(compNamesExp.length).length !== 1) err("indicator map: component-label mark not found");
  else {
    const got = byLen(compNamesExp.length)[0].data.map(d => d.text);
    for (let i = 0; i < compNamesExp.length; i++)
      if (got[i] !== compNamesExp[i]) err(`component label ${i} "${got[i]}" != "${compNamesExp[i]}"`);
  }
  if (byLen(catNamesExp.length).length !== 1) err("indicator map: category-header mark not found");
  else {
    const got = byLen(catNamesExp.length)[0].data.map(d => d.text);
    for (let i = 0; i < catNamesExp.length; i++)
      if (got[i] !== catNamesExp[i]) err(`category header ${i} "${got[i]}" != "${catNamesExp[i]}"`);
  }
  // rules: one at every component boundary (13), stronger ones at the
  // two interior category boundaries
  const irules = imarks("ruleY");
  const compRuleMark = irules.find(m => m.data.length === compNamesExp.length - 1);
  const catRuleMark = irules.find(m => m.data.length === catNamesExp.length - 1);
  if (!compRuleMark) err("indicator map: component boundary rules not found");
  if (!catRuleMark) err("indicator map: category boundary rules not found");
  iCompRules = compRuleMark ? compRuleMark.data.length : 0;
  iCatRules = catRuleMark ? catRuleMark.data.length : 0;
  const itips = imarks("tip");
  if (itips.length !== 1) err("indicator map: expected one tip mark");
  else {
    const tOpts = itips[0].opts.opts ?? itips[0].opts;
    const s = iCells[Math.floor(iCells.length / 2)];
    const t = tOpts.title(s);
    for (const part of [s.comp, s.cat, s.q, s.v.toFixed(2)])
      if (!t.includes(part)) err(`indicator tooltip missing "${part}": ${t}`);
  }
}

/* --- 2. flip the toggle to Clustered --- */
byId["vs-clustered"].click();
if (document.body.dataset.view !== "clustered") err("toggle did not set clustered view");
if (location.hash !== "#clustered") err("clustered view must be addressable via #clustered");
const fig = byId["clustermap"]._children[0];
if (!fig || !fig.__figure) err("clustered view did not render a figure");
if (fig.options.width !== 1280) err("unexpected clustered figure width");
const nRows = CLUSTER.leaves.length;
if (fig.options.height !== nRows * 21 + 34) err("unexpected clustered figure height");

const marksOf = (kind) => fig.marks.filter(m => m.__mark === kind);
const rects = marksOf("rect");
if (rects.length !== 3) err(`expected 3 rect marks (cells, missing, chips), got ${rects.length}`);
const texts = marksOf("text");
const ruleXs = marksOf("ruleX");
const ruleYs = marksOf("ruleY");
const tips = marksOf("tip");

/* --- 3. matrix cells: every indicator x every quarter, values 0..1 --- */
const nQ = META.n_quarters;
// distinguish the marks by data shape: chips have `color`; cell marks have
// `xi` (cells all v != null, missing all v == null)
const chipMark = rects.find(m => m.data.length && "color" in m.data[0]);
if (!chipMark) err("no component-chip mark rendered");
const cellMarks = rects.filter(m => m !== chipMark);
if (cellMarks.length !== 2) err(`expected cells + missing marks, got ${cellMarks.length}`);
const cellsMark = cellMarks.find(m => m.data.every(c => c.v != null));
const missingMark = cellMarks.find(m => m.data.every(c => c.v == null));
if (!cellsMark || !missingMark) err("could not tell cells from missing cells");
const cells = cellsMark ? cellsMark.data : [];
const missing = missingMark ? missingMark.data : [];
if (cells.length + missing.length !== nRows * nQ)
  err(`expected ${nRows * nQ} cells, got ${cells.length} + ${missing.length} missing`);
for (const c of cells)
  if (!(c.v >= 0 && c.v <= 1)) err(`cell value out of [0,1]: ${c.v}`);
// every (row, quarter) pair present exactly once
const seen = new Set(cells.concat(missing).map(c => `${c.p}:${c.q}`));
if (seen.size !== nRows * nQ) err("duplicate or absent (row, quarter) cells");

/* --- 4. row order + labels + chips match the shipped bundle --- */
const shortOf = {};
for (const cat of HIER.categories)
  for (const comp of cat.components)
    for (const ind of comp.indicators) shortOf[ind.name] = ind.short;
const expectedLabels = CLUSTER.order.map(l => shortOf[CLUSTER.leaves[l]]);
if (texts.length !== 1) err("expected one row-label text mark");
const labels = texts[0].data;
if (labels.length !== nRows) err(`expected ${nRows} labels, got ${labels.length}`);
const gotLabels = labels.map(d => d.text);
for (let i = 0; i < nRows; i++)
  if (gotLabels[i] !== expectedLabels[i])
    err(`row ${i} label "${gotLabels[i]}" != shipped order "${expectedLabels[i]}"`);

const chips = chipMark.data;
if (chips.length !== nRows) err(`expected ${nRows} chips, got ${chips.length}`);
// component of each row via hierarchy
const compOf = {};
for (const cat of HIER.categories)
  for (const comp of cat.components)
    for (const ind of comp.indicators) compOf[ind.name] = comp.name;
const chipByComp = {};
for (let i = 0; i < nRows; i++) {
  const comp = compOf[CLUSTER.leaves[CLUSTER.order[i]]];
  if (chipByComp[comp] === undefined) chipByComp[comp] = chips[i].color;
  else if (chipByComp[comp] !== chips[i].color)
    err(`component "${comp}" has inconsistent chip colors`);
}
if (Object.keys(chipByComp).length !== META.components.length)
  err("chips do not cover every component");
if (new Set(Object.values(chipByComp)).size !== META.components.length)
  err("component chip colors are not distinct per component");

/* --- 5. dendrogram: n-1 merges, 2(n-1) horizontal segments, root at left --- */
if (ruleXs.length !== 1) err("expected one ruleX mark (dendrogram verticals)");
const vsegs = ruleXs[0].data;
if (vsegs.length !== nRows - 1)
  err(`expected ${nRows - 1} dendrogram verticals, got ${vsegs.length}`);
const dendroH = ruleYs.filter(m => m.data.length && "x1" in m.data[0]);
if (dendroH.length !== 1) err("expected one ruleY mark for dendrogram horizontals");
if (dendroH[0].data.length !== 2 * (nRows - 1))
  err(`expected ${2 * (nRows - 1)} dendrogram horizontals, got ${dendroH[0].data.length}`);
const cutRuleMark = ruleYs.find(m => m !== dendroH[0]);
if (!cutRuleMark) err("missing top-level cluster boundary rules");
if (cutRuleMark.data.length !== CLUSTER.cut.length - 1)
  err(`expected ${CLUSTER.cut.length - 1} boundary rules, got ${cutRuleMark.data.length}`);
const hmax = Math.max(...CLUSTER.merges.map(m => m[2]));
const rootX = vsegs.find(s => Math.abs(s.x - (-(34 + 20))) < 1e-6 * (1 + hmax) - 1e-9 || s.x === -(34 + 20));
if (!rootX) err("dendrogram root is not at the left edge of its lane");
for (const s of vsegs)
  if (!(Math.min(s.y1, s.y2) >= -0.5 && Math.max(s.y1, s.y2) <= nRows - 0.5))
    err(`dendrogram vertical out of row range: ${JSON.stringify(s)}`);
// cut rules sit exactly between the shipped top-level groups
const bounds = CLUSTER.cut.slice(0, -1)
  .map(g => Math.max(...g.map(l => CLUSTER.order.indexOf(l))) + 0.5);
for (const b of bounds)
  if (!cutRuleMark.data.some(r => r.y === b))
    err(`missing boundary rule at y=${b}`);

/* --- 6. tooltips carry component + sub-index + quarter + value --- */
if (tips.length !== 1) err("expected one tip mark on the cells");
const tipOpts = tips[0].opts.opts ?? tips[0].opts;
const sample = cells[Math.floor(cells.length / 2)];
const title = tipOpts.title(sample);
for (const part of [sample.comp, sample.cat, sample.q, sample.v.toFixed(2)])
  if (!title.includes(part)) err(`tooltip missing "${part}": ${title}`);

/* --- 7. component legend lists all components with their chip colors --- */
const legendHtml = byId["cluster-legend"].innerHTML;
for (const c of META.components)
  if (!legendHtml.includes(esc(c))) err(`legend missing component "${c}"`);

/* --- 8. hash routing: anchors never flip the view; #component/ does --- */
location.hash = "#methodology";
handlers["hashchange"]();
if (document.body.dataset.view !== "clustered")
  err("a plain anchor flipped the clustered view back to hierarchy");
location.hash = "#component/" + encodeURIComponent("Business credit");
handlers["hashchange"]();
if (document.body.dataset.view !== "hierarchy")
  err("component deep link must switch back to the hierarchy view");

console.log(JSON.stringify({
  errors: results.errors,
  rows: nRows,
  cells: cells.length,
  missing: missing.length,
  dendroV: vsegs.length,
  dendroH: dendroH[0].data.length,
  cutRules: cutRuleMark.data.length,
  legendComponents: META.components.length,
  indRows: indRowsExp.length,
  indCells: iCells.length,
  indMissing: iMissing.length,
  indCompRules: iCompRules,
  indCatRules: iCatRules,
}));
if (results.errors.length) process.exit(1);
