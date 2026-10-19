# Manual pulse feeds

Two Market Pulse indicators have no machine-readable public feed. Their
values are transcribed by hand from the cited primary releases into the
CSVs in this directory, and the pipeline (status `live-manual`) charts them
like any other series. Values are **never** invented, interpolated, or
spliced; every row carries the source it was transcribed from in
`source_note`.

| Feed | File | Cadence | Primary source |
|---|---|---|---|
| NACM Credit Managers' Index (combined) | `cmi.csv` | monthly, released ~1st of the following month | NACM monthly CMI report PDFs, e.g. https://nacm.org/images/CMI/August_2026_CMI.pdf |
| Duke/Richmond/Atlanta Fed CFO Survey — economic optimism index | `cfo_optimism.csv` | quarterly, released ~3 weeks after fielding | Quarterly press releases, https://www.fuqua.duke.edu/duke-fuqua-insights/ (archive: `/archives`) |

## Why manual (not scraped)

- **NACM CMI**: the PDF text is machine-readable, but the archive URL
  slugs are not stable across months (as of 2026-10 only some months
  resolve under `nacm.org/images/CMI/<Month>_<YYYY>_CMI.pdf`; June/May
  2026 and most of 2025 404 under that pattern). A scraper would silently
  miss months; a hand-checked CSV does not.
- **CFO Survey**: the quarterly releases live at unpredictable slugs and
  the headline optimism index is published in release charts rather than
  stable data files; several release pages carry no numeric value in the
  HTML at all.

## Update procedure

1. **CMI (first business days of each month)**: fetch the new month's
   report PDF from nacm.org (linked from the CMI page). If the canonical
   `Month_YYYY_CMI.pdf` URL 404s, locate the PDF via the site's CMI page
   for that month. Transcribe the *combined sectors* seasonally adjusted
   index from the headline paragraph ("...combined Credit Managers' Index
   (CMI) for <Month> <YYYY> ... to <value>"), dated the first of the
   survey month. Append one row to `cmi.csv` with the PDF URL in
   `source_note`.
2. **CFO Survey (quarterly, ~3-4 weeks after quarter end)**: find the new
   release at fuqua.duke.edu/duke-fuqua-insights (or the Richmond/Atlanta
   Fed's CFO Survey pages, which mirror it). Transcribe the *economic*
   optimism index (0-100 scale, US-economy outlook — not own-firm
   optimism), dated the survey quarter's end. If the release text states
   the prior quarter's value, add that too when missing. Append the row
   to `cfo_optimism.csv` with the release URL in `source_note`.
3. Run `python3 -m src.pulse.manual_feeds` — it fails loudly on missing
   provenance, duplicate dates, or non-numeric values — then
   `python3 -m src.pulse.build_pulse --incremental` and commit the
   refreshed `data/pulse/` CSVs, `_meta.json`, and `docs/data/pulse.json`
   together with the updated manual CSV.

## Ground rules

- One row per release period; never backfill from memory or secondary
  press summaries — transcribe from the primary release (or, when first
  seeding, from the literature research report's citation of it, and say
  so in the note, as the 2026Q3 CFO row does).
- Gaps stay gaps: quarters/months whose values are not verifiable are
  simply absent (see the missing CFO quarters of 2025-26 and the CMI
  months between the seeded readings). The chart shows the gap honestly
  rather than a spliced line.
- The credit managers' unfavorable-factors sub-index (<50 flags payment
  stress) is worth adding as a second column if a verifiable history can
  be transcribed; do not mix it into the combined-index series.
