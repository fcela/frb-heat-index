// Minimal Observable Plot stub for the heat-index render harness: records
// every mark (kind + data + options) and returns marker figures, so tests
// can assert that the clustered view actually renders its matrix cells,
// dendrogram segments, component chips, row labels, and tooltips.
function mark(kind, data, opts) { return { __mark: kind, data, opts }; }
export function line(data, opts) { return mark("line", data, opts); }
export function areaY(data, opts) { return mark("areaY", data, opts); }
export function ruleX(data, opts) { return mark("ruleX", data, opts); }
export function ruleY(data, opts) { return mark("ruleY", data, opts); }
export function dot(data, opts) { return mark("dot", data, opts); }
export function rect(data, opts) { return mark("rect", data, opts); }
export function text(data, opts) { return mark("text", data, opts); }
export function tip(data, opts) { return mark("tip", data, opts); }
export function pointer(opts) { return { __pointer: true, opts }; }
export function plot(opts) { return { __figure: true, marks: opts.marks, options: opts }; }
