# Paper findings: Diercks/Katz/Wright (2026), "Kalshi and the Rise of Macro Markets," FEDS 2026-010

Full PDF: `~/workspace/frb-work/papers/kalshi-rise-of-macro-markets.pdf` (text extracted to `/tmp/kalshi_paper.txt` — ephemeral, re-extract with pdftotext if needed).
DOI: https://doi.org/10.17016/FEDS.2026.010

## Accuracy results (Table 3) — for per-card citations

**Panel A (day of release, vs Bloomberg consensus):**
- Headline CPI: Bloomberg MAE 0.081 → Kalshi mean 0.069, median 0.063*, mode 0.063* (*p<0.10). RMSE: 0.100 → 0.080** (**p<0.05). Statistically significant improvement.
- Core CPI: MAE 0.070 both — statistically similar.
- Unemployment: MAE 0.109 → 0.107 (median/mode) / 0.117 (mean) — no significant difference. Mode moves in 0.1pp increments.
- "In no case is Kalshi significantly worse than the Bloomberg consensus."

**Panel B (day of FOMC, vs fed funds futures):**
- MAE: 0.010 → 0.000** (median, mode). RMSE: 0.020 → 0.000** (median, mode). **p<0.05.
- "The mode of the Kalshi distribution has perfectly matched the realized federal funds rate by the day of each meeting since 2022, a feat not achieved by either surveys or futures." September 2024: Kalshi put greater weight on the 50bp cut (correct) vs 25bp.

## Methodology (Section 3) — risk-neutral pdf construction

- Binary contract pays $1 if outcome EXCEEDS the strike; "Yes" price = risk-neutral P(exceed strike).
- Bin probability = Yes_price(previous strike) − Yes_price(current strike).
- Stale strike (no trades that day): carry over last-traded price.
- Same price on two strikes (tails, bounded $0.01–$0.99): assign probability to outcome closer to the mode.
- Enforce CDF monotonicity constructing OUTWARD FROM THE MODE toward tails.
- Caveats: Q-measure not P-measure (risk premia); tail contracts thin/stale. Bid-ask midpoints NOT recommended (large tail spreads).
- Paper's recommendation: "Kalshi should be used to provide risk-neutral pdfs concerning FOMC decisions at specific meetings" — fed funds futures force a 2-outcome assumption; SOFR options too far removed (basis + horizon mismatch).

## New-indicator candidates from the paper

- **Stagflation tail risks (Figure 13):** daily P(CPI > 3%), P(GDP < 1.5%), P(CPI > 4%), P(GDP < 0%) by summing bins. Marginal densities only — no joint probability measurable.
- **Event-response (Figure 6):** probability of most-likely outcome with news annotations (e.g. July 2025: cut prob rose to 25% after Waller/Bowman remarks, fell after strong June jobs report). Annotations are manual.
- **Moments (Section 7):** variance falls on news days (sharpest on zero-surprise CPI); positive CPI shocks move the mean ~4x more than negative; FOMC statement shocks raise variance; press-conference shocks reduce skewness.
- **Calibration (Section 6.1, PIT):** "fairly well calibrated"; some evidence risk premia overstate odds of higher inflation/unemployment.
- **Unique coverage:** no other market-based distributions exist for GDP, unemployment, core CPI, payrolls; Kalshi is the only actively traded option directly on the fed funds rate at specific meetings.
- Authors planned EconFutures.com + public code on the paper's GitHub (verify if live).
