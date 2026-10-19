"""Tests for the pulse-expansion indicators (8 new fetchers/feeds).

Run: python3 tests/test_pulse_expansion.py (plain python3, no pytest).

All network paths are exercised through injected transports backed by
fixtures under tests/fixtures/pulse/ — real trimmed source payloads — so
the tests make zero live calls and stay deterministic. The manual feeds
(NACM CMI, Duke CFO Survey optimism) are tested against the committed
data/pulse/manual/ CSVs themselves.
"""

import datetime as dt
import sys
import tempfile
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.pulse import fred, gpr_threats, manual_feeds, sf_news_sentiment, vvix
from src.pulse.build_pulse import fetch_indicator
from src.pulse.segments import FETCHABLE_STATUSES, SEGMENTS

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pulse"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


def file_transport(text: str):
    def transport(url, timeout=60):
        return text
    return transport


def expect_raise(fn, what: str):
    try:
        fn()
    except RuntimeError:
        return
    raise AssertionError(f"expected RuntimeError: {what}")


# ------------------------------------------------------------- vvix (CBOE)

def test_vvix_fetch_parses_cboe_csv():
    df = vvix.fetch_vvix(transport=file_transport(load("vvix.csv")))
    assert df.columns == ["date", "value"]
    assert df.height == 6
    assert df["date"][0] == dt.date(2006, 3, 6)
    assert df["date"].to_list() == sorted(df["date"].to_list())
    assert df["value"][0] == 71.73
    assert abs(df["value"].last() - 109.91) < 1e-9
    print("ok test_vvix_fetch_parses_cboe_csv")


def test_vvix_rejects_garbage_rows():
    bad = "DATE,VVIX\n03/06/2006,71.73\nnot-a-date,12.0\n"
    expect_raise(lambda: vvix.fetch_vvix(transport=file_transport(bad)),
                 "unparseable date must raise")
    bad2 = "DATE,VVIX\n03/06/2006,nonsense\n"
    expect_raise(lambda: vvix.fetch_vvix(transport=file_transport(bad2)),
                 "non-numeric VVIX must raise")
    bad3 = "COL1,COL2\n1,2\n"
    expect_raise(lambda: vvix.fetch_vvix(transport=file_transport(bad3)),
                 "unexpected columns must raise")
    print("ok test_vvix_rejects_garbage_rows")


# ------------------------------------------------ SF Fed news sentiment

def test_news_sentiment_parses_frbsf_csv():
    df = sf_news_sentiment.fetch_news_sentiment(
        transport=file_transport(load("news_sentiment.csv")))
    assert df.height == 6
    assert df["date"][0] == dt.date(1980, 1, 1)
    assert df["value"][0] == -0.04
    assert abs(df["value"].last() - 0.06) < 1e-12
    assert df["date"].to_list() == sorted(df["date"].to_list())
    print("ok test_news_sentiment_parses_frbsf_csv")


def test_news_sentiment_rejects_duplicate_dates():
    dup = ("date,Daily news sentiment\n"
           "01/01/2026,0.01\n01/01/2026,0.02\n")
    expect_raise(lambda: sf_news_sentiment.fetch_news_sentiment(
        transport=file_transport(dup)), "duplicate dates must raise")
    print("ok test_news_sentiment_rejects_duplicate_dates")


# -------------------------------------------------- GPR threats (GPT)

def test_gpr_threats_fixture_roundtrip():
    blob = (FIXTURES / "gpr_threats.xlsx").read_bytes()
    df = gpr_threats.fetch_gpr_threats(
        transport=lambda url, timeout=60: blob)
    # Pre-1985 nulls are the series' documented start, dropped; the 4
    # threats observations survive with exact values.
    assert df.height == 4
    assert df["date"].to_list() == [dt.date(1985, 1, 1), dt.date(1985, 2, 1),
                                    dt.date(2026, 8, 1), dt.date(2026, 9, 1)]
    assert df["value"].to_list() == [102.1, 110.31, 138.63, 173.09]
    print("ok test_gpr_threats_fixture_roundtrip")


def test_gpr_clean_rejects_bad_frames():
    # missing GPRT column
    expect_raise(lambda: gpr_threats.clean_gpr_sheet(
        pl.DataFrame({"month": [dt.date(2026, 1, 1)]})), "missing column")
    # non-numeric threats value
    expect_raise(lambda: gpr_threats.clean_gpr_sheet(
        pl.DataFrame({"month": [dt.date(2026, 1, 1)], "GPRT": ["nonsense"]})),
        "garbage GPRT")
    # duplicate months
    expect_raise(lambda: gpr_threats.clean_gpr_sheet(
        pl.DataFrame({"month": [dt.date(2026, 1, 1), dt.date(2026, 1, 1)],
                       "GPRT": ["1.0", "2.0"]})), "duplicate months")
    print("ok test_gpr_clean_rejects_bad_frames")


# ------------------------------------------------- FRED-backed additions

def test_fred_new_series_via_transport():
    # The three new FRED indicators ride fetch_fred_csv; fixtures pin the
    # exact series IDs the wiring must request.
    seen_urls = []

    def transport(url, timeout=60):
        seen_urls.append(url)
        fid = url.split("id=")[1]
        return load(f"fred_{fid}.csv")

    for fid, first, last in (
        ("USEPUINDXD", dt.date(1985, 1, 1), 149.21),
        ("NFCICREDIT", dt.date(1971, 1, 8), -0.036),
        ("DRTSCILM", dt.date(1990, 4, 1), 0.0),
    ):
        df = fred.fetch_fred_csv(fid, transport=transport)
        assert df.columns == ["date", "value"]
        assert df["date"][0] == first
        assert abs(df["value"].last() - last) < 1e-9
        assert f"id={fid}" in seen_urls[-1]
    print("ok test_fred_new_series_via_transport")


# --------------------------------------------------------- manual feeds

def test_manual_feeds_committed_content():
    # Values are transcribed facts: pin the seeded readings so silent
    # edits to history fail loudly.
    cmi = manual_feeds.fetch_manual_series("cmi")
    assert cmi.columns == ["date", "value"]
    got = {str(d): v for d, v in cmi.iter_rows()}
    assert got["2026-01-01"] == 54.5
    assert got["2026-04-01"] == 55.2
    assert got["2026-07-01"] == 56.4
    assert got["2026-08-01"] == 54.6  # NACM Aug 2026 report (issued Sep 1)

    cfo = manual_feeds.fetch_manual_series("cfo_optimism")
    got = {str(d): v for d, v in cfo.iter_rows()}
    assert got["2024-12-31"] == 66.0   # Q4 2024, per the Q1 2025 release
    assert got["2025-03-31"] == 62.1   # Q1 2025 tariff-episode drop
    assert got["2025-09-30"] == 62.9   # per the Q4 2025 release
    assert got["2025-12-31"] == 60.2
    assert got["2026-09-30"] == 60.3   # Q3 2026 reading
    # Missing quarters (2025Q2, 2026Q1, 2026Q2) stay absent — never filled.
    assert "2025-06-30" not in got and "2026-06-30" not in got
    print("ok test_manual_feeds_committed_content")


def test_manual_feeds_require_provenance():
    # Every row of every manual CSV must carry a non-empty source note
    # with the cited URL.
    for key in ("cmi", "cfo_optimism"):
        raw = pl.read_csv(REPO / "data" / "pulse" / "manual" / f"{key}.csv")
        assert raw.height > 0
        notes = raw["source_note"].to_list()
        assert all(n and "http" in n for n in notes), f"{key}: bad provenance"
    print("ok test_manual_feeds_require_provenance")


def test_manual_feeds_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # missing source_note column
        (d / "a.csv").write_text("date,value\n2026-01-01,50.0\n")
        expect_raise(lambda: manual_feeds.fetch_manual_series("a", manual_dir=d),
                     "missing source_note column")
        # empty note
        (d / "b.csv").write_text('date,value,source_note\n2026-01-01,50.0,""\n')
        expect_raise(lambda: manual_feeds.fetch_manual_series("b", manual_dir=d),
                     "empty note")
        # duplicate date
        (d / "c.csv").write_text(
            'date,value,source_note\n2026-01-01,50.0,x\n2026-01-01,51.0,x\n')
        expect_raise(lambda: manual_feeds.fetch_manual_series("c", manual_dir=d),
                     "duplicate dates")
        # non-numeric value
        (d / "e.csv").write_text('date,value,source_note\n2026-01-01,abc,x\n')
        expect_raise(lambda: manual_feeds.fetch_manual_series("e", manual_dir=d),
                     "non-numeric value")
        # missing file
        expect_raise(lambda: manual_feeds.fetch_manual_series("nope", manual_dir=d),
                     "missing file")
    print("ok test_manual_feeds_fail_closed")


def test_pipeline_reads_manual_feeds_without_network():
    df = fetch_indicator("cmi", {"status": "live-manual"})
    assert df.height == 4
    df = fetch_indicator("cfo_optimism", {"status": "live-manual"})
    assert df.height == 5
    print("ok test_pipeline_reads_manual_feeds_without_network")


# ------------------------------------------------------- wiring & commentary

NEW_WIRING = {
    "sentiment": ("epu_daily", "news_sentiment", "gpr_threats", "cfo_optimism"),
    "equities": ("vvix",),
    "ficc": ("sloos_tightening", "cmi"),
    "firmwide": ("nfci_credit",),
}

KNOWN_SOURCE_DOMAINS = (
    "fred.stlouisfed.org", "sec.gov", "finra.org", "fdic.gov", "cftc.gov",
    "newyorkfed.org", "fhlb-of.com", "finance.yahoo.com", "openinsider.com",
    "fool.com", "sifma.org", "pitchbook.com", "ici.org", "cboe.com",
    "federalreserve.gov", "multpl.com", "nacm.org", "frbsf.org",
    "matteoiacoviello.com", "fuqua.duke.edu",
)


def test_new_indicators_wired():
    assert "live-manual" in FETCHABLE_STATUSES
    for seg_key, keys in NEW_WIRING.items():
        assert seg_key in SEGMENTS, f"segment {seg_key} missing"
        for k in keys:
            assert k in SEGMENTS[seg_key]["indicators"], f"{seg_key}/{k} missing"
    # FRED-backed additions carry the right series IDs.
    assert SEGMENTS["sentiment"]["indicators"]["epu_daily"]["fred_id"] == "USEPUINDXD"
    assert SEGMENTS["firmwide"]["indicators"]["nfci_credit"]["fred_id"] == "NFCICREDIT"
    assert SEGMENTS["ficc"]["indicators"]["sloos_tightening"]["fred_id"] == "DRTSCILM"
    # Exactly the two manual feeds are live-manual.
    manual = [k for seg in SEGMENTS.values()
              for k, i in seg["indicators"].items() if i["status"] == "live-manual"]
    assert sorted(manual) == ["cfo_optimism", "cmi"], manual
    print("ok test_new_indicators_wired")


def test_commentary_bar():
    # Every fetchable card must meet the commentary standard: a full why
    # (what + why-it-leads), a caveat-bearing note, and a source that
    # points somewhere verifiable.
    n = 0
    for seg_key, seg in SEGMENTS.items():
        for k, ind in seg["indicators"].items():
            if ind["status"] not in FETCHABLE_STATUSES:
                continue
            assert len(ind["why"]) >= 140, f"{seg_key}/{k}: why too thin"
            assert len(ind.get("note", "")) >= 80, f"{seg_key}/{k}: note too thin"
            assert ind["frequency"], f"{seg_key}/{k}: frequency missing"
            src = ind["source"]
            assert any(dom in src for dom in KNOWN_SOURCE_DOMAINS), \
                f"{seg_key}/{k}: source has no verifiable pointer: {src}"
            n += 1
    assert n == 41, n
    print(f"ok test_commentary_bar ({n} fetchable cards)")


def test_vvix_threshold_documented():
    # The brief's explicit requirement: VVIX interpretation is
    # threshold-based and the card must say so.
    ind = SEGMENTS["equities"]["indicators"]["vvix"]
    assert "120" in ind["why"] and "regime" in ind["why"].lower()
    print("ok test_vvix_threshold_documented")


if __name__ == "__main__":
    test_vvix_fetch_parses_cboe_csv()
    test_vvix_rejects_garbage_rows()
    test_news_sentiment_parses_frbsf_csv()
    test_news_sentiment_rejects_duplicate_dates()
    test_gpr_threats_fixture_roundtrip()
    test_gpr_clean_rejects_bad_frames()
    test_fred_new_series_via_transport()
    test_manual_feeds_committed_content()
    test_manual_feeds_require_provenance()
    test_manual_feeds_fail_closed()
    test_pipeline_reads_manual_feeds_without_network()
    test_new_indicators_wired()
    test_commentary_bar()
    test_vvix_threshold_documented()
    print("all pulse-expansion tests pass")
