# Phase 1: Source-by-Source Availability Audit

**Date:** 2026-10-14 (verified live)
**Scope:** All 43 series in `data/panel_quarterly.csv` (1990Q1–2025Q4 baseline)
**Question:** Why can't each series reach 2026Q2/Q3, and what is the binding constraint?

Today is 2026-10-14. 2026Q2 ended 105 days ago (passes the pipeline's 75-day rule); 2026Q3 ended 14 days ago (fails it — Q3 is nowcast territory, see Phase 2).

## Summary

| Category | Count | Reach 2025Q4 | Max reachable (verified) |
|---|---|---|---|
| FRED direct (daily/monthly/quarterly) | 20 | 20 | 2026Q2–Q3 / current |
| Z.1 via FRED (working codes) | 8 | 8 | 2026Q2 |
| Z.1 via FRED (dead codes) | 7 series-worth | 7 (from cache) | 2026Q2 (needs code remap) |
| Y-9C derived | 6 | 2 | 2026Q2 (4 fixed this audit; 2 need construction) |
| Manual/annual | 4 | 4 | 2023Q1–2025 (source-limited) |

## The single biggest win (done)

**Y-9C 2021Q1 cutoff was purely unprocessed data.** The `y9c_npw/` bulk files on disk run through **2026Q2** (3,599 filers in 2026Q2; Q1/Q3 files carry ~450 filers — the Fed's reduced panel). All four truncated series were rebuilt through 2026Q2 with Polars; splice at 2021Q1→2021Q2 verified smooth (tangible 8.08→7.93, loans/dep 61.94→61.42, top5 48.80→48.91, st_money 8.02→7.74).

## Series table

Format: series | panel latest | verified public latest | binding constraint | fixable?

### Y-9C derived

| Series | Panel | Public latest | Constraint | Fixable? |
|---|---|---|---|---|
| Top 5 BHC asset share | 2021Q1 | 2026Q2 (on disk) | We never processed it | **Yes — done this audit** |
| Loans / deposits | 2021Q1 | 2026Q2 (on disk) | We never processed it | **Yes — done this audit** |
| ST money / assets at BHCs | 2021Q1 | 2026Q2 (on disk) | We never processed it | **Yes — done this audit** |
| Tangible common equity / assets | 2021Q1 | 2026Q2 (on disk) | We never processed it | **Yes — done this audit** |
| Tier 1 common equity ratio | 2013Q4 | 2026Q2 (BHCA mnemonics on disk) | Basel III rewrote HC-R in 2014Q1; old BHCK8274 concept discontinued | **Yes** — CET1 ratio (100·ΣBHCAP859/ΣBHCAA223, HC-R reporters only, 99.9% of assets) validated at 12.81% for 2026Q2, in line with published aggregates. Splice as successor concept from 2014Q1. |
| Total capital ratio | 2010Q4 | 2026Q2 (BHCA on disk) | Sparse Call Report downloads; never attempted on Y-9C | **Yes** — total capital = Tier 1 (BHCA8274) + Tier 2 from Y-9C HC-R; same aggregation. |

### FRED direct (all verified live 2026-10-14)

| Series (FRED code) | Panel | Public latest | Constraint | Fixable? |
|---|---|---|---|---|
| DGS10 (10y Treasury) | 2025Q4 | 2026-10-13 (daily) | None — pipeline cutoff | Yes, trivially |
| VIXCLS | 2025Q4 | 2026-10-13 (daily) | None | Yes |
| BAA (Moody's Baa) | 2025Q4 | 2026-09-01 | None | Yes |
| CUSR0000SEHA (rent CPI) | 2025Q4 | 2026-09-01 | None | Yes |
| TOTALSL, REVOLSL, PSAVERT, DSPI | 2025Q4 | 2026-08-01 | None | Yes |
| DRTSCILM (SLOOS C&I standards) | 2025Q4 | 2026-07-01 (2026Q3!) | None | Yes |
| CSUSHPISA (Case-Shiller) | 2025Q4 | 2026-07-01 | 2-month publication lag | Yes, to 2026Q2 |
| GDP, GDPDEF, DPI, TDSP, MDSP, HHMSDODNS, TCMILBSNNB, NCBDBIQ027S | 2025Q4 | 2026Q2 | None | Yes |
| DRTSPM (SLOOS prime mortgage stds) | 2014Q4 | **Discontinued** (2014-10-01) | Fed removed prime-mortgage standards from SLOOS | **Yes** — successor SLOOS series exist: SUBLPDHMSGLGNQ (govt mortgage loans, large banks, 2015→2026-04-01), SUBLPDHMSSOTHNQ (subprime, other banks, →2026-04-01). Splice as post-2015 mortgage-standards concept. |
| EQTA (commercial bank leverage) | 2020-07-01 (stale) | Stale | Superseded by Y-9C tangible series | No need — replaced |

### Z.1 via FRED

| Series (FRED code) | Panel | Public latest | Constraint | Fixable? |
|---|---|---|---|---|
| BOGZ1FL794190005Q, BOGZ1FA674190005Q, BOGZ1FL664090005Q, BOGZ1FL664190005Q, BOGZ1FL662051003Q, BOGZ1FL662151003Q, BOGZ1FL792050005Q, BOGZ1FL792150005Q | 2025Q4 | **2026Q2** (verified 2026-04-01) | None — Z.1 2026Q2 released 2026-09-11 | Yes |
| BOGZ1FL794141005Q, BOGZ1FL793169175Q, BOGZ1FL793069175Q, BOGZ1FL634090005Q, BOGZ1FL793063005Q, BOGZ1FL794035005Q, BOGZ1FL794090005Q, BOGZ1FL104090103Q | 2025Q4 (from cache) | **404 on FRED** | FRED code churn (Z.1 taxonomy update; 2026Q2 release notes cite code changes) | **Yes** — data exists in Z.1 2026Q2 release. Fix via DDP-FRED crosswalk (federalreserve.gov/data/data-download-fred-information.htm) or direct Z.1 CSV download. Affects: maturity mismatch (net), ST wholesale (gross), illiquidity concentration, nonfinancial corporate assets. |
| EXHOSL | ? | Empty | Likely renamed/discontinued | Investigate |

### Manual / annual sources

| Series | Panel | Public latest | Constraint | Fixable? |
|---|---|---|---|---|
| Shiller E/P (ie_data.xls) | 2023Q1 | 2023Q1 (host frozen) | Yale host stopped updating | **Partially** — CAPE can be rebuilt from S&P 500 price + earnings (FRED: SP500, or Shiller's newer hosting). Needs reconstruction work. |
| EBP / GZ spread (ebp_raw.csv) | ? | Manual, stale | Fed's 2016 file | **Yes** — GZ spread updatable from ICE BofA indices; or use the Fed's updated EBP data. |
| BIS CRE prices | 2025Q4 | Manual | Manual download | Yes — refresh from BIS portal |
| Fannie Mae FICO (annual) | 2025Q4 | Manual annual | Annual by nature | Yes — 2026 when published |

## What blocks 2026Q2 today (ranked)

1. **Nothing structural for ~34 series.** FRED + working Z.1 codes + processed Y-9C all reach 2026Q2. The pipeline just hasn't run since the 2025Q4 build.
2. **Z.1 dead codes (7):** remap via crosswalk — data exists.
3. **Tier 1 common / total capital:** CET1/Y-9C constructions validated — implement.
4. **SLOOS mortgage:** splice successor series.
5. **Shiller E/P:** needs reconstruction (only series with no drop-in fix).
6. **Maturity gap:** still empty (see note below).

## Maturity gap note

The weighted-average maturity gap was never replicated (Call Report microdata, Brunnermeier-Oehmke construction). It remains the only series with no public proxy. Options not yet exhausted: FFIEC CDR bulk Call Report files *are* public and contain the maturity-bucket schedules (RC-O); the construction is labor-intensive but not impossible. Verdict: **fixable in principle, expensive** — defer unless the component needs it.
