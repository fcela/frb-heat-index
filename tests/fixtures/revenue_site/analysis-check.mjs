// Math verification for docs/revenue/analysis.js against the real site bundles.
// Usage: node analysis-check.mjs <repo-root>
// Prints one JSON object: per (frequency, line) edge properties, window
// share-sum deviations, firm counts, and sample HHI values.
import { readFileSync } from "fs";
import { join } from "path";
import { pathToFileURL } from "url";

const REPO = process.argv[2];
const {
  buildPeriods, poolAndShares, concentration, migration, windows, INDEPENDENTS,
} = await import(pathToFileURL(join(REPO, "docs/revenue/analysis.js")).href);
const out = {};
for (const [file, freq] of [["docs/data/revenue.json", "a"], ["docs/data/revenue_quarterly.json", "q"]]) {
  const bundle = JSON.parse(readFileSync(join(REPO, file), "utf8"));
  for (const line of bundle.lines) {
    const P = buildPeriods(line, freq);
    const keep = line.line === "advisory" ? new Set(INDEPENDENTS) : new Set();
    const mig = migration(P, freq, { keepSet: keep });
    const conc = concentration(P, freq);
    let maxShareDev = 0;
    for (const w of mig.windows) {
      const s = [...w.segs.values()].reduce((a, b) => a + b, 0);
      maxShareDev = Math.max(maxShareDev, Math.abs(s - 1));
    }
    // Edge-rule invariants.
    let edgeOk = true;
    const errs = [];
    if (P.xs.length) {
      const lastCount = P.perX.get(P.cutEnd).size;
      if (lastCount < 0.5 * P.maxC) { edgeOk = false; errs.push("trailing period below 50% kept"); }
      if (P.stableStart != null) {
        const sc = P.perX.get(P.stableStart).size;
        if (sc < 0.8 * P.maxC) { edgeOk = false; errs.push("stableStart below 80%"); }
        for (const x of P.xs) {
          if (x < P.stableStart && P.perX.get(x).size >= 0.8 * P.maxC) {
            edgeOk = false; errs.push("earlier period qualifies as stable"); break;
          }
        }
      }
    }
    const key = `${freq}/${line.line}`;
    out[key] = {
      nFirms: line.firms.length,
      maxC: P.maxC,
      stableStart: P.stableStart,
      cutEnd: P.cutEnd,
      nDroppedTrailing: P.nDroppedTrailing,
      nWindows: mig.nWindows,
      maxShareDev,
      edgeOk,
      edgeErrs: errs,
      balanced: mig.balanced,
      // Sample HHI values for the python hand-check to compare against.
      hhiSample: conc.filter((d) => d.hhi != null).map((d) => [d.x, d.hhi]),
    };
  }
}
console.log(JSON.stringify(out));
