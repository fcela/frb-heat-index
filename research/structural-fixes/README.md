# Structural fixes: series extension through 2026Q2

Implements the 6 structural fixes from the Phase 1 availability audit
(`research/phase1-availability-audit.md`), bringing every fixable series
through 2026Q2.

## Fix 1 — Tier 1 common equity ratio: CET1 splice across the Basel III break
- Pre-2014: 100*(BHCK8274-BHCK3283-BHCK3000-BHCKC502)/BHCKB704 (Y-9C, all filers)
- 2014Q1+: 100*sum(BHCAP859)/sum(BHCAA223), HC-R reporters only
- Splice: 11.37% (2013Q4) -> 12.33% (2014Q1). 2026Q2 = 12.81%.

## Fix 2 — Total capital ratio rebuilt from Y-9C (replaces sparse Call Reports)
- Pre-2014: 100*(BHCK8274+BHCK8275)/BHCKB704
- 2014Q1+: 100*(BHCA8274+BHCAP863)/BHCAA223, HC-R reporters only
  (BHCAP863 selected as Tier 2: smoothest splice at the 2014 break,
  -1.04pp vs -1.32pp/-1.44pp for alternatives; 2026Q2 = 14.75%)
- 1990-2014 correlation vs prior Call Report construction: 0.9754
  (prior was sparse: 84/100 quarters null; new series is complete).

## Fix 3 — SLOOS mortgage lending standards: successor splice
- 2007Q2-2014Q4: DRTSPM (discontinued)
- 2015Q1-2026Q2: SUBLPDHMSGLGNQ (government mortgages, large banks)
- Splice: -11.1 (2014Q4) -> 0.0 (2015Q1), net % tightening diffusion index.

## Fixes 4-6 — Dead Z.1 FRED codes remapped via Z.1 bulk extraction
The 8 BOGZ1 codes 404 on FRED but the underlying Z.1 mnemonics are live
in the Federal Reserve Z.1 bulk release (`FRB_Z1_csv.zip`). Extracted
directly; recomputed derived indicators correlate 1.0000 with the panel's
cached FRED-based values on overlap.
- BOGZ1FL794141005Q / FL793169175Q / FL793069175Q (ST wholesale net/gross)
- BOGZ1FL634090005Q (runnable: MMF total financial assets)
- BOGZ1FL793063005Q / FL794035005Q / FL794090005Q (illiquidity)
- BOGZ1FL104090103Q (NFCB total assets for risky leverage)

## Scripts
- `build_y9c_fixed.py`: unified Y-9C panel 1990Q1-2026Q2 (old csv format
  1990Q1-2021Q1 via y9c_raw/ + y9c_new/, NPW ^-delimited 2021Q2-2026Q2).
- `extract_z1.py`: Z.1 bulk extraction for the 16 series used.
- `assemble_panel.py`: applies all fixes to data/panel_quarterly.csv.

## Validation
- 1990-2014 new-vs-old panel correlations: 1.0000 for all replaced series
  except Total Capital Ratio (0.9754, intended source change).
- 2026Q2 tail: aggregate index 0.284 (2025 range 0.18-0.32); no jumps >2.0
  sigma in the pipeline tail check.
