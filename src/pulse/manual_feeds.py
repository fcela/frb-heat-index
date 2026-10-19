"""Manually-maintained pulse feeds (status: live-manual).

Two research-ranked indicators have no machine-readable feed: the NACM
Credit Managers' Index (monthly PDFs at nacm.org, URL slugs not stable
across months) and the Duke/Richmond/Atlanta Fed CFO Survey optimism index
(quarterly press releases, headline figure not exposed as data). For both,
the honest build is a hand-maintained CSV of values transcribed from the
cited primary releases — never invented, never spliced, each row carrying
the source it was transcribed from.

The CSVs live in data/pulse/manual/ next to a README documenting the
update procedure. This module reads + validates them; the pipeline then
treats them exactly like any fetched series (status "live-manual" is
fetchable, so they chart, but the card says plainly they are manual).

Validation is fail-closed: every row needs a non-empty source note; dates
must be unique ISO dates; values finite. A stale feed is not an error here
(below-frequency publication is documented per indicator) — an *empty or
unprovenanced* one is.
"""

from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[2]
MANUAL_DIR = REPO / "data" / "pulse" / "manual"


def fetch_manual_series(key: str, manual_dir: Path | None = None) -> pl.DataFrame:
    """Read data/pulse/manual/<key>.csv -> date/value frame, ascending.

    Columns required: date (YYYY-MM-DD), value (float), source_note
    (non-empty — where the value was transcribed from).
    """
    p = (manual_dir or MANUAL_DIR) / f"{key}.csv"
    if not p.exists():
        raise RuntimeError(f"manual feed missing: {p}")
    df = pl.read_csv(p)
    for col in ("date", "value", "source_note"):
        if col not in df.columns:
            raise RuntimeError(f"manual feed {key}: column {col!r} missing")
    if df.height == 0:
        raise RuntimeError(f"manual feed {key}: no rows")
    try:
        df = df.with_columns(
            pl.col("date").str.to_date("%Y-%m-%d"),
            pl.col("value").cast(pl.Float64, strict=True),
            pl.col("source_note").cast(pl.String),
        )
    except Exception as e:
        raise RuntimeError(f"manual feed {key}: unparseable row ({e})")
    if df.filter(pl.col("source_note").str.strip_chars().str.len_chars() == 0).height:
        raise RuntimeError(f"manual feed {key}: row without a source note")
    if df.filter(pl.col("date").is_duplicated()).height:
        raise RuntimeError(f"manual feed {key}: duplicate dates")
    if df.filter(~pl.col("value").is_finite()).height:
        raise RuntimeError(f"manual feed {key}: non-finite value")
    return df.select("date", "value").sort("date")


if __name__ == "__main__":
    for key in ("cmi", "cfo_optimism"):
        d = fetch_manual_series(key)
        print(key, d.height, "rows, last", d["date"].max(), "->", d["value"].last())
