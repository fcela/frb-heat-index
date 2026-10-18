// Minimal Observable Plot stub: records marks, returns marker figures.
function mark(kind, data, opts) { return { __mark: kind, data, opts }; }
export function line(data, opts) { return mark("line", data, opts); }
export function ruleX(data, opts) { return mark("ruleX", data, opts); }
export function rect(data, opts) { return mark("rect", data, opts); }
export function tip(data, opts) { return mark("tip", data, opts); }
export function pointer(opts) { return { __pointer: true, opts }; }
export function plot(opts) { return { __figure: true, marks: opts.marks, caption: opts.caption }; }
