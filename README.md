# FRB Heat Index — a public-data replication

A quarterly index of heat (vulnerability) in the U.S. financial system,
rebuilt from public sources following the methodology of the Federal Reserve's
FEDS 2015-059 "Mapping Heat in the U.S. Financial System" (14 components,
44 indicators, aggregate index on (0,1)).

## The replication story, in five lines

1. The paper's 44 indicators were assembled from public data: 9 replicated
   directly (FRED, university-hosted long-run valuation data), 34 built as
   documented proxies (Z.1 financial accounts, Y-9C bank holding company
   filings, Call Reports, SLOOS, BIS property prices, NIPA).
2. One indicator — the weighted-average maturity gap at commercial banks —
   has no workable public proxy and is left as missing (documented, not fudged).
3. The paper's documented pipeline (sign orientation, log / 10-year moving-average
   detrend, full-sample standardization, component means, Gaussian-kernel CDF)
   applied to the proxy panel reproduces the published components closely.
4. The panel is extended past the paper's 2015Q1 endpoint on current public
   vintages; every quarterly refresh re-runs the full pipeline from raw inputs.
5. Ragged-edge rule: the published panel is always truncated at the last full
   calendar quarter — a partial quarter is never published.

## How the quarterly pipeline works

```
src/fetch_series.py   raw downloads (FRED keyless CSVs, NIPA bulk, Y-9C bulk, …)
        -> work/raw/
src/build_panel.py    raw -> data/panel_quarterly.csv   (Polars; 44 indicators)
src/run_pipeline.py   panel -> data/components.csv + data/aggregate.csv
        sign -> log/10y-MA -> standardize -> component means -> kernel CDF
src/make_site.py      data/*.csv -> docs/data/*.json   (website feed)
```

Two guards from the end-of-sample audit are built into the pipeline:
truncation at the last full quarter, and a tail check that flags any
indicator whose standardized value jumps by more than 2.0 in the final
quarter before the index is written.

## Run the build locally

Python 3.12:

```bash
pip install polars numpy
python src/fetch_series.py      # downloads raw inputs into work/raw/
python src/build_panel.py       # work/raw/ -> data/panel_quarterly.csv
python src/run_pipeline.py      # panel -> data/components.csv, data/aggregate.csv
python src/make_site.py         # data/*.csv -> docs/data/*.json
```

`fetch_series.py` reports any source that needs a manual download
(BIS property prices, SLOOS historical tables, the annual FICO transcription,
the Fed-posted excess-bond-premium file) instead of failing on it.
The long-run E/P workbook downloads as `.xls`; convert it once to
`work/raw/ie_data_ep.csv` (columns `date,ep`) before running `build_panel.py`.
`build_panel.py --end 2014Q4` pins a reproducible vintage; the default is the
last full quarter. All data work in this repo is Polars-only.

## Layout

```
src/                  pipeline scripts (fetch, build_panel, run_pipeline, make_site)
data/                 processed data: panel_quarterly.csv, components.csv,
                      aggregate.csv, proxy_catalog.csv, _meta.json
paper/                the replication paper (tex + pdf) and its figures
docs/                 website (data feed written by src/make_site.py)
work/                 local raw downloads (not committed)
```

`data/proxy_catalog.csv` is the per-series record: for each of the 44
indicators, its status (replicated / proxy / failed), construction, source,
and caveats. Start there to audit any number in the panel.
