// TTM verification for docs/revenue/analysis.js.
// Usage: node ttm-check.mjs <repo-root>
// Verifies toTTM: 4-quarter sums, requires complete windows, drops incomplete.
import { join } from "path";
import { pathToFileURL } from "url";

const REPO = process.argv[2];
const { toTTM } = await import(pathToFileURL(join(REPO, "docs/revenue/analysis.js")).href);

const line = {
  line: "advisory",
  firms: [
    { firm: "A", series: [
      { year: 2024, quarter: 1, value: 100 }, { year: 2024, quarter: 2, value: 110 },
      { year: 2024, quarter: 3, value: 120 }, { year: 2024, quarter: 4, value: 130 },
      { year: 2025, quarter: 1, value: 140 },
    ]},
    { firm: "B", series: [
      { year: 2024, quarter: 2, value: 200 }, { year: 2024, quarter: 3, value: 210 },
      { year: 2024, quarter: 4, value: 220 }, { year: 2025, quarter: 1, value: 230 },
      // missing 2024-Q1 -> no TTM at 2024-Q4
    ]},
  ],
};

const t = toTTM(line);
const a = t.firms.find(f => f.firm === "A");
const b = t.firms.find(f => f.firm === "B");

// Firm A: TTM defined at 2024-Q4 (100+110+120+130=460) and 2025-Q1 (110+120+130+140=500).
const aVals = Object.fromEntries(a.series.map(d => [`${d.year}-Q${d.quarter}`, d.value]));
console.log("A:", JSON.stringify(aVals));
if (aVals["2024-Q4"] !== 460 || aVals["2025-Q1"] !== 500) {
  console.error("FAIL: firm A TTM values wrong");
  process.exit(1);
}
// Firm B: TTM only at 2025-Q1 (200+210+220+230=860); 2024-Q4 excluded (missing Q1).
const bVals = Object.fromEntries(b.series.map(d => [`${d.year}-Q${d.quarter}`, d.value]));
console.log("B:", JSON.stringify(bVals));
if (bVals["2024-Q4"] !== undefined || bVals["2025-Q1"] !== 860) {
  console.error("FAIL: firm B TTM window handling wrong");
  process.exit(1);
}
console.log("TTM OK");
