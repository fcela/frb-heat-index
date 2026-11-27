# FRB Heat Index

A public-data replication and ongoing extension of the Federal Reserve's
*Mapping Heat in the U.S. Financial System* (FEDS 2015-059). The static site
also tracks market conditions, prediction markets, bank revenue, and an
experimental heat index for Spain.

## Original replication and current release

The original replication assembled 44 indicators across 14 components.
Nine indicators were reconstructed directly from public sources and 34
used documented proxies. The weighted-average maturity gap initially
remained missing. The [replication paper](paper/fed_heat_map-replication.pdf)
records the original sample, construction, and comparisons.

The published U.S. extension uses **52 indicators across 15 components**.
It distinguishes observed values, model estimates, assumptions, and
regression extensions. The [proxy catalog](data/proxy_catalog.csv) describes
the constructions. The observed inputs are in
[panel_quarterly.csv](data/panel_quarterly.csv). The charts are calculated
from [panel_complete.csv](data/panel_complete.csv), whose estimates are
identified in [nowcast_provenance.csv](data/nowcast_provenance.csv) and
[panel_complete_flags.csv](data/panel_complete_flags.csv).

## Replay a release offline

Check out the Git revision for the release. Use Python 3.12 and the pinned
dependencies. The tests that exercise the site also use Node.js 24.8.0.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python -m src.quarterly_release
python -m src.spain.build_spain --no-fetch
python -m pytest -q
```

The U.S. command checks that the observed panel, completed panel, flags,
and provenance agree, then scores the completed panel and regenerates
the site bundle. The Spain command reads the committed normalized source
CSVs and reuses the source snapshot's build date. Both commands accept
`--root PATH` to replay against a separate copy of `data/` and `docs/`.

The [U.S. manifest](data/release_manifest.json) and
[Spain manifest](data/spain/release_manifest.json) record the build command,
build date, final quarter, and SHA-256 of each index input. The U.S.
manifest starts at the committed panel and provenance, so replay does not
depend on retrieving the historical upstream responses. The repository's
Git history preserves each committed release and its build code.

The [repository checks](.github/workflows/checks.yml) run the fixture-based
suite and both release replays on pull requests and pushes to `main`.
Tests that exercise optional local SEC downloads skip when those files are
absent.

## Refresh from upstream

The [U.S. updater](.github/workflows/update.yml) checks each Wednesday for
calendar quarters that ended more than 75 days ago. It looks for nonempty
FRED observations inside each target quarter according to source frequency,
and requires the quarter's Y-9C report. Daily series must cover at least
80% of weekdays and reach both ends of the quarter within seven days.
Monthly series need a value in each month. A source delay defers that quarter
and later ones. The updater accepts an already downloaded Y-9C CSV in
`work/raw/y9c/` for local runs. A completed release replaces estimates with
newly collected observations and retains the provenance of estimates still in
use. New gaps remain visible until observations or reviewed estimates are
available.
The updater stages and validates the candidate before publishing a single
commit.

```bash
python .github/scripts/update.py --dry-run
python -m src.spain.build_spain
```

For a fresh U.S. research build from currently available source data, run
`python src/fetch_series.py` and `python src/build_panel.py` in a separate
checkout. The fetcher lists manual inputs, including BIS property prices,
historical lending standards, the annual FICO transcription, and the Fed's
excess bond premium file. Convert the long-run earnings/price workbook
to `work/raw/ie_data_ep.csv` with columns `date,ep` for the panel builder.
`build_panel.py --end 2014Q4` selects the last quarter in the output.
The input files can still contain later revisions to earlier quarters.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/` | Data ingestion, models, scoring, and site generation |
| `data/` | Published datasets, source snapshots, and release manifests |
| `docs/` | Static site and generated bundles |
| `paper/` and `research/` | Replication paper and source audits |
| `tests/` | Offline regressions and source fixtures |
| `work/` | Local raw downloads |
