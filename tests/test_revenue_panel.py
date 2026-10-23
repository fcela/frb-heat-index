"""Regression tests for the revenue panel builder.

Run: python3 tests/test_revenue_panel.py (no pytest needed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import polars as pl

from src.revenue.taxonomy import TAXONOMY_LINES, map_quality
from src.revenue.reorg_registry import REORGS, regime_for, is_break, NO_REORG_FIRMS
from src.revenue.build_revenue import build_panel


def test_taxonomy_lines():
    assert len(TAXONOMY_LINES) == 11  # 9 core + ecm_dcm_combined + placement_fees
    assert "advisory" in TAXONOMY_LINES
    assert "placement_fees" in TAXONOMY_LINES
    assert "ecm_dcm_combined" in TAXONOMY_LINES


def test_map_quality_goldman():
    line, q, _ = map_quality("Goldman Sachs", "Asset & Wealth Management")
    assert line == "awm" and q == "D"
    line, q, _ = map_quality("PJT Partners", "Placement Fees")
    assert line == "placement_fees" and q == "D"


def test_map_quality_unmappable():
    line, q, note = map_quality("Wells Fargo", "Consumer Banking and Lending")
    assert line is None and q == "U"
    assert note  # reason recorded, never silent


def test_map_quality_citi_banking_not_advisory():
    # PR #35: the post-reorg Banking division total mixes IB fees with
    # Corporate Lending NII and Services/Markets revenue share (FY2025
    # 10-K: $8,215M division total vs $1,908M advisory fees), so it must
    # never map to advisory. The stale quarterly bundle was built with
    # the pre-fix mapping and showed a ~$8,031M advisory TTM.
    line, q, note = map_quality("Citigroup", "Banking")
    assert line is None and q == "U", (line, q, note)
    assert "corporate lending" in note
    # The exact-match rule must not over-match other segments.
    line, q, _ = map_quality("Citigroup", "Global Consumer Banking")
    assert line is None and q == "U"


def test_map_quality_jefferies_ibcm_not_advisory():
    # Jefferies' XBRL layer reports division-level segments only. The
    # substring rule "Investment Banking" -> advisory mapped the IB&CM
    # division total (advisory + underwriting + equities + fixed income)
    # -- and the pre-reorg "Investment Banking, Capital Markets and
    # Asset Management" whole-firm segment -- to advisory, producing
    # annual 2017 = $3,198M (whole-firm revenue) and a quarterly
    # advisory TTM of $12,480M. Those segments must stay unmapped; the
    # advisory fee line comes from the MD&A revenues-by-source tables.
    for seg in ("Investment Banking And Capital Markets",
                "Investment Banking & Capital Markets",
                "Investment Banking, Capital Markets and Asset Management"):
        line, q, note = map_quality("Jefferies", seg)
        assert line is None and q == "U", (seg, line, q, note)
        assert "revenues-by-source" in note, note
    # Asset Management remains a legitimate (approximate) awm source.
    line, q, _ = map_quality("Jefferies", "Asset Management")
    assert line == "awm" and q == "A"


def test_map_quality_jefferies_leucadia_awm_unmapped():
    # The Leucadia-era 10-Ks (CIK 0000096223 was Leucadia National Corp
    # until the 2018 JFG rename) report the parent's own
    # "Leucadia Asset Management and Berkadia Transferred" segment --
    # the holding company's other-investments portfolio ($49.6M in
    # fiscal 2017), not an asset-management business line. The "Asset
    # Management" substring mapped it into awm, so the panel carried a
    # bogus 2017 = $49.6M cell. Explicitly unmappable: fiscal 2013-2019
    # have no separable Jefferies AM segment and stay documented gaps.
    line, q, note = map_quality(
        "Jefferies", "Leucadia Asset Management and Berkadia Transferred")
    assert line is None and q == "U", (line, q, note)
    assert "Berkadia" in note, note
    # The true post-2020 segment still maps.
    line, q, _ = map_quality("Jefferies", "Asset Management")
    assert line == "awm" and q == "A"


def test_map_quality_citi_wealth_exact_not_pbwm():
    # Citi 2020 read $25,140M in the awm line: the substring "Wealth"
    # matched "Personal Banking and Wealth Management" (PBWM = US
    # Personal Banking + Wealth, two-thirds consumer banking). The
    # mapping is now exact, and PBWM is explicitly unmappable -- no
    # 10-K reports a 2020 "Wealth" segment (earliest restated
    # comparative is 2021, FY2023 10-K), so 2020 stays a documented
    # gap instead of a wrong-segment splice.
    line, q, note = map_quality("Citigroup", "Wealth")
    assert line == "awm" and q == "D", (line, q, note)
    line, q, note = map_quality("Citigroup",
                                "Personal Banking and Wealth Management")
    assert line is None and q == "U", (line, q, note)
    assert "US Personal Banking" in note, note


def test_citi_awm_2020_is_a_documented_gap():
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    s = (panel.filter((pl.col("firm") == "Citigroup")
                      & (pl.col("taxonomy_line") == "awm"))
         .sort("year"))
    got = dict(zip(s["year"].to_list(), s["net_revenue_m"].to_list()))
    assert 2020 not in got, got
    assert set(got) == {2021, 2022, 2023, 2024, 2025}, got
    # The kept cells are the true Wealth segment, never PBWM.
    assert got[2021] == 7542.0 and got[2025] == 8559.0, got
    print("test_citi_awm_2020_is_a_documented_gap: OK")


def test_reorg_registry_coverage():
    firms = {r["firm"] for r in REORGS}
    # Every firm with a reorg is registered; the no-reorg firms are explicit.
    assert "Goldman Sachs" in firms
    assert "Citigroup" in firms
    for f in NO_REORG_FIRMS:
        assert f not in firms


def test_regime_breaks():
    # GS reorg Q4 2022 -> FY2022 reported under the new structure
    assert is_break("Goldman Sachs", 2021, 2022)
    assert not is_break("Goldman Sachs", 2022, 2023)
    assert not is_break("Goldman Sachs", 2023, 2024)
    # Citi Q4 2023 reorg -> FY2023 under the new 5-segment structure
    assert is_break("Citigroup", 2022, 2023)
    assert not is_break("Citigroup", 2023, 2024)
    # MS rename-only is not a break
    assert not is_break("Morgan Stanley", 2012, 2014)
    # Lazard: no breaks at all
    assert regime_for("Lazard", 2010) == regime_for("Lazard", 2025) == "r0"


def test_build_panel_dedupe_and_regime():
    seg = pl.DataFrame([
        {"firm": "Goldman Sachs", "cik": "0000886982", "segment_native": "Asset & Wealth Management",
         "year": 2023, "net_revenue_m": 13000.0, "accession": "0000886982-24-000001", "r_file": "R33.htm",
         "source": "xbrl:R33"},
        # Same cell restated in a later filing -> later wins
        {"firm": "Goldman Sachs", "cik": "0000886982", "segment_native": "Asset & Wealth Management",
         "year": 2023, "net_revenue_m": 13100.0, "accession": "0000886982-25-000001", "r_file": "R33.htm",
         "source": "xbrl:R33"},
        # Unmappable segment -> recorded, not in panel
        {"firm": "Wells Fargo", "cik": "0000072971", "segment_native": "Consumer Banking and Lending",
         "year": 2023, "net_revenue_m": 37000.0, "accession": "0000072971-24-000001", "r_file": "R33.htm",
         "source": "xbrl:R33"},
    ])
    sub = pl.DataFrame({
        "firm": [], "cik": [], "taxonomy_line": [], "year": [],
        "net_revenue_m": [], "source": [], "accession": [],
    })
    panel, unmapped = build_panel(seg, sub)
    # Restated value wins
    row = panel.filter(
        (pl.col("firm") == "Goldman Sachs") & (pl.col("year") == 2023)
    )
    assert len(row) == 1
    assert row["net_revenue_m"][0] == 13100.0
    assert row["taxonomy_line"][0] == "awm"
    # Unmappable recorded separately
    assert len(unmapped) == 1
    assert unmapped[0]["segment_native"] == "Consumer Banking and Lending"


def test_build_panel_never_splices_breaks():
    # Two years on either side of a break keep distinct regimes.
    seg = pl.DataFrame([
        {"firm": "Citigroup", "cik": "0000831001", "segment_native": "Wealth",
         "year": y, "net_revenue_m": 8000.0 + y, "accession": "x", "r_file": "R33.htm",
         "source": "xbrl:R33"}
        for y in (2022, 2023)
    ])
    sub = pl.DataFrame({
        "firm": [], "cik": [], "taxonomy_line": [], "year": [],
        "net_revenue_m": [], "source": [], "accession": [],
    })
    panel, _ = build_panel(seg, sub)
    regimes = panel.sort("year")["regime"].to_list()
    assert regimes[0] != regimes[1]  # Q4-2023 reorg break preserved


def test_build_panel_direct_beats_approximate():
    # Citi "Banking" division maps to advisory with quality A; the MD&A
    # advisory fee split is quality D. The direct disclosure must win even
    # when the approximate mapping comes from a later filing.
    seg = pl.DataFrame([
        {"firm": "Citigroup", "cik": "0000831001", "segment_native": "Banking",
         "year": 2023, "net_revenue_m": 39000.0,
         "accession": "0000831001-26-000011", "r_file": "R1.htm",
         "source": "XBRL R-file"},
    ])
    sub = pl.DataFrame([
        {"firm": "C", "cik": "0000831001", "taxonomy_line": "advisory",
         "year": 2023, "net_revenue_m": 1017.0,
         "source": "mda:Banking business metrics",
         "accession": "0000831001-24-000033"},
    ])
    panel, _ = build_panel(seg, sub)
    row = panel.filter((pl.col("firm") == "Citigroup")
                       & (pl.col("taxonomy_line") == "advisory"))
    assert len(row) == 1
    assert row["net_revenue_m"][0] == 1017.0
    assert row["map_quality"][0] == "D"
    assert row["layer"][0] == "mda"


def test_reconcile_splits_pass_and_flag():
    from src.revenue.build_revenue import reconcile_splits
    seg = pl.DataFrame([
        {"firm": "Morgan Stanley", "cik": "0000895421",
         "segment_native": "Institutional Securities", "year": 2025,
         "net_revenue_m": 33080.0, "accession": "a1", "r_file": "R1.htm",
         "source": "XBRL R-file"},
        {"firm": "Morgan Stanley", "cik": "0000895421",
         "segment_native": "Institutional Securities", "year": 2024,
         "net_revenue_m": 10000.0, "accession": "a1", "r_file": "R1.htm",
         "source": "XBRL R-file"},
    ])
    sub = pl.DataFrame([
        # 2025: splits sum 2888+1965+2766+15631+8716 = 31966 <= 33080*1.05+25
        {"firm": "MS", "cik": "0000895421", "taxonomy_line": line,
         "year": 2025, "net_revenue_m": v,
         "source": "mda:t", "accession": "a2"}
        for line, v in [("advisory", 2888.0), ("ecm", 1965.0),
                        ("dcm", 2766.0), ("equities", 15631.0),
                        ("ficc", 8716.0)]
    ] + [
        # 2024: splits sum far above the parent -> flag, not fudge
        {"firm": "MS", "cik": "0000895421", "taxonomy_line": "advisory",
         "year": 2024, "net_revenue_m": 99999.0,
         "source": "mda:t", "accession": "a2"},
    ])
    rep = reconcile_splits(sub, seg)
    by = {(c["firm"], c["year"]): c["status"] for c in rep["checks"]}
    assert by[("Morgan Stanley", 2025)] == "pass", rep["checks"]
    assert by[("Morgan Stanley", 2024)] == "flag", rep["checks"]
    assert rep["n_pass"] == 1 and rep["n_flag"] == 1
    assert rep["match_rate"] == 0.5


# Jefferies advisory, from the filings (Leucadia-era "net revenues by
# source" MD&A tables 2013-2017; ASC 606 disaggregation note 2018+).
# 2013 is the stub period from the Jefferies acquisition (March 13,
# 2013) through December 31, 2013. Regression: before the fix the
# panel showed 2013 = $2,134M (whole IB&CM segment) and 2017 = $3,198M
# (whole firm), an impossible 74% drop into 2018.
EXPECTED_JEF_ADVISORY = {
    2013: 369.191, 2014: 559.418, 2015: 632.354, 2016: 654.19,
    2017: 770.092, 2018: 820.042, 2019: 767.421, 2020: 1053.5,
    2021: 1873.56, 2022: 1778.003, 2023: 1198.915, 2024: 1811.633,
    2025: 2145.422,
}


def test_jefferies_annual_advisory_matches_filings():
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    jef = (panel.filter((pl.col("firm") == "Jefferies")
                        & (pl.col("taxonomy_line") == "advisory"))
           .sort("year"))
    got = dict(zip(jef["year"].to_list(), jef["net_revenue_m"].to_list()))
    assert got == EXPECTED_JEF_ADVISORY, got
    # Every cell is the direct MD&A disclosure, never an XBRL segment.
    assert (jef["map_quality"] == "D").all(), jef
    assert (jef["layer"] == "mda").all(), jef
    # 2014-2016 are backfilled -- the completeness gap is closed.
    assert {2014, 2015, 2016} <= set(got), got
    print("test_jefferies_annual_advisory_matches_filings: OK")


# Jefferies equities and FICC are sales-and-trading lines, from the
# MD&A net revenues summary tables of the JFG-era 10-Ks (restated
# comparatives win: 2021/2022 from the FY2023 10-K, 2023 from the
# FY2024 10-K). Regression: before the fix both lines carried the ASC
# 606 note's contract-revenue subset -- FICC $7-16M/yr (2020: $15.9M
# vs the filing's $1,340.8M) and equities ~30% understated (2020:
# $806.3M vs $1,128.9M).
EXPECTED_JEF_EQUITIES = {
    2017: 674.424, 2018: 665.557, 2019: 773.979, 2020: 1128.910,
    2021: 1294.392, 2022: 1054.064, 2023: 1139.425, 2024: 1592.793,
    2025: 1907.866,
}
EXPECTED_JEF_FICC = {
    2017: 618.388, 2018: 559.712, 2019: 681.362, 2020: 1340.792,
    2021: 984.540, 2022: 800.492, 2023: 1092.736, 2024: 1166.761,
    2025: 909.869,
}


def test_jefferies_trading_lines_match_filings():
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    for line, expected in (("equities", EXPECTED_JEF_EQUITIES),
                           ("ficc", EXPECTED_JEF_FICC)):
        s = (panel.filter((pl.col("firm") == "Jefferies")
                          & (pl.col("taxonomy_line") == line))
             .sort("year"))
        got = dict(zip(s["year"].to_list(), s["net_revenue_m"].to_list()))
        assert got == expected, (line, got)
        # Direct MD&A disclosure, never an XBRL segment total.
        assert (s["map_quality"] == "D").all(), s
        assert (s["layer"] == "mda").all(), s
        # Plausible band for a fixed-income/equities trading franchise
        # of Jefferies' size (the pre-fix values were $7-16M).
        assert (s["net_revenue_m"].is_between(300, 2500)).all(), s
    print("test_jefferies_trading_lines_match_filings: OK")


# Jefferies Asset Management, from the "Asset Management | Operating
# Segments" Net revenues rows of the FY2022-FY2025 10-Ks ($M):
# 2020 = 814.6 (FY2022 10-K), 2021 = 1,084.8 (FY2023 10-K),
# 2022 = 1,243.5 (FY2023/FY2024 10-Ks), 2023 = 188.3
# (FY2023/FY2024/FY2025 10-Ks), 2024 = 803.7, 2025 = 710.2.
# The fiscal-2023 collapse is a real business event -- the legacy
# other-investments portfolio left the segment during the year (Vitesse
# Energy spin-off, January 2023) -- and each 10-K keeps one segment
# scope across its comparatives (2022 = 1,243.5 next to 2023 = 188.3 in
# the FY2023 10-K's own note), so this is not a reorg splice. Regression:
# the panel previously carried the FY2025 10-K's gross "Revenues" rows
# (2023-2025 = 368.4 / 1,113.7 / 1,006.9) and the Leucadia parent
# segment (2017 = 49.6).
EXPECTED_JEF_AWM = {
    2020: 814.6, 2021: 1084.8, 2022: 1243.5, 2023: 188.3,
    2024: 803.7, 2025: 710.2,
}


def test_jefferies_awm_matches_filings():
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    s = (panel.filter((pl.col("firm") == "Jefferies")
                      & (pl.col("taxonomy_line") == "awm"))
         .sort("year"))
    got = dict(zip(s["year"].to_list(), s["net_revenue_m"].to_list()))
    assert got == EXPECTED_JEF_AWM, got
    # The gross "Revenues" rows and the Leucadia parent cell must not
    # reappear.
    assert not {368.4, 1113.7, 1006.9, 49.6} & set(got.values()), got
    # No pre-2020 cell: fiscal 2013-2019 are documented gaps (no
    # separable Jefferies AM segment in the Leucadia-era filings).
    assert min(got) == 2020, got
    print("test_jefferies_awm_matches_filings: OK")


# Morgan Stanley FICC and equities (sales & trading), from the filings.
# Layout eras: <=FY2015 filings "Fixed income and commodities" rows;
# FY2016-FY2017 GAAP/non-GAAP rows; FY2018-FY2020 "By Business" tables;
# FY2021+ filings the income statement's S&T rows (which add commissions
# and asset management fees -- the restated basis 2019+ carries).
# Regression: 2015-2018 were byte-identical clones of the DCM/ECM cells
# (equities 2015-2018 = 1398/887/1484/1726, ficc = 1643/1369/1976/1926).
EXPECTED_MS_FICC = {
    2010: 5895.0, 2011: 7506.0, 2012: 2358.0, 2013: 3594.0, 2014: 4214.0,
    2015: 4758.0, 2016: 5117.0, 2017: 4928.0, 2018: 5005.0, 2019: 5985.0,
    2020: 8847.0, 2021: 7516.0, 2022: 9022.0, 2023: 7673.0, 2024: 8418.0,
    2025: 8716.0,
}
EXPECTED_MS_EQUITIES = {
    2010: 4840.0, 2011: 7263.0, 2012: 4811.0, 2013: 6529.0, 2014: 7135.0,
    2015: 8288.0, 2016: 8037.0, 2017: 7982.0, 2018: 8976.0, 2019: 8133.0,
    2020: 9921.0, 2021: 11435.0, 2022: 10769.0, 2023: 9986.0, 2024: 12230.0,
    2025: 15631.0,
}


def test_ms_trading_lines_match_filings():
    from src.revenue.reorg_registry import regime_for
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    ms = panel.filter(pl.col("firm") == "Morgan Stanley")
    for line, expected in (("ficc", EXPECTED_MS_FICC),
                           ("equities", EXPECTED_MS_EQUITIES)):
        s = (ms.filter(pl.col("taxonomy_line") == line).sort("year"))
        got = dict(zip(s["year"].to_list(), s["net_revenue_m"].to_list()))
        assert got == expected, (line, got)
        # Never a clone of the underwriting lines, any year.
        other = dict(ms.filter(pl.col("taxonomy_line")
                               == ("dcm" if line == "ficc" else "ecm"))
                     .sort("year")
                     .select(["year", "net_revenue_m"]).iter_rows())
        assert all(got[y] != other.get(y) for y in got if y in other), line
    # The 2019 presentation break (income-statement S&T rows incl.
    # commissions and asset management fees; comparatives restated back
    # to 2019 only) is registered, so the 2018->2019 basis seam is a
    # documented regime change, never a silent splice.
    assert regime_for("Morgan Stanley", 2018) == "r0"
    assert regime_for("Morgan Stanley", 2019) == "r1"
    print("test_ms_trading_lines_match_filings: OK")


# Wells Fargo AWM (Wealth and Investment Management), from the filings.
# Each year is the latest restated comparative: 2016/2017 carry the 1Q2018
# reclass basis (FY2018/FY2019 10-Ks), 2018/2019 the two- and three-segment
# bases (FY2020/FY2021 10-Ks), with the 2020-10-01 reorg registered so the
# seams are drawn breaks. Regression: 2017 was $4,641M -- the ASC 606
# note's WIM block summed to NII-only because it has no bare
# "noninterest income" row, and the note's report-order tie beat the true
# segment note's $17,072M (FY2018/FY2019 10-K restated comparatives agree).
EXPECTED_WF_AWM = {
    2013: 14330.0, 2014: 15269.0, 2015: 15777.0, 2016: 16278.0,
    2017: 17072.0, 2018: 15869.0, 2019: 14412.0, 2020: 13213.0,
    2021: 14346.0, 2022: 14822.0, 2023: 14691.0, 2024: 15436.0,
    2025: 16328.0,
}


def test_wells_fargo_awm_matches_filings():
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    s = (panel.filter((pl.col("firm") == "Wells Fargo")
                      & (pl.col("taxonomy_line") == "awm"))
         .sort("year"))
    got = dict(zip(s["year"].to_list(), s["net_revenue_m"].to_list()))
    assert got == EXPECTED_WF_AWM, got
    # Contiguous 2013-2025: no interior holes.
    assert set(got) == set(EXPECTED_WF_AWM), got
    # Every cell within the audit band: never <25% or >400% of both
    # neighbors (the 2017 hole read 28% of its 2016 neighbor).
    ys = sorted(got)
    for i in range(1, len(ys) - 1):
        v, p, n = got[ys[i]], got[ys[i - 1]], got[ys[i + 1]]
        assert not (v < 0.25 * p and v < 0.25 * n), (ys[i], v, p, n)
        assert not (v > 4 * p and v > 4 * n), (ys[i], v, p, n)
    print("test_wells_fargo_awm_matches_filings: OK")


def test_advisory_no_impossible_drop():
    # No firm's advisory revenue may halve year-over-year without a
    # documented reorg break: fee-based advisory collapses of >50% do
    # not happen (client bases persist), while >50% BOOMS are real
    # market cycles (e.g. the 2020->2021 M&A surge, +51% to +98%
    # across every firm). The signature of the Jefferies bug was the
    # impossible 74% drop ($3,198M -> $820M) from mapping a division
    # total to advisory.
    from src.revenue.reorg_registry import is_break
    panel = pl.read_csv(Path(__file__).resolve().parents[1]
                        / "data" / "revenue" / "panel_annual.csv")
    adv = panel.filter(pl.col("taxonomy_line") == "advisory") \
        .sort(["firm", "year"])
    offenders = []
    for firm in adv["firm"].unique().to_list():
        s = adv.filter(pl.col("firm") == firm).sort("year")
        prev = None
        for r in s.iter_rows(named=True):
            if prev is not None and prev["net_revenue_m"]:
                chg = (r["net_revenue_m"] - prev["net_revenue_m"]) \
                    / abs(prev["net_revenue_m"])
                if chg < -0.5 and not is_break(firm, prev["year"],
                                               r["year"]):
                    offenders.append((firm, prev["year"], r["year"], chg))
            prev = r
    assert not offenders, offenders
    print("test_advisory_no_impossible_drop: OK")


def _panel():
    return pl.read_csv(Path(__file__).resolve().parents[1]
                       / "data" / "revenue" / "panel_annual.csv")


def test_no_identical_values_across_lines():
    # The MS 2015-2018 failure mode: the extractor pulled the ECM line
    # into the equities slot, so two lines of the same firm carried
    # byte-identical values for four consecutive years. No two distinct
    # taxonomy lines of one firm may share an identical value in a year.
    panel = _panel()
    dup = (panel.group_by(["firm", "year", "net_revenue_m"])
           .agg(pl.col("taxonomy_line").unique().alias("lines"))
           .filter(pl.col("lines").list.len() > 1))
    bad = [(r["firm"], r["year"], r["net_revenue_m"], r["lines"])
           for r in dup.iter_rows(named=True)]
    assert not bad, bad
    print("test_no_identical_values_across_lines: OK")


# Interior-year swings beyond 4x / below 0.25x of BOTH neighbors that
# are verified real (checked against the filings), not extraction bugs.
_VERIFIED_SWINGS = {
    # JPM FICC 2008 was crushed by crisis markdowns ($1,957M) before
    # the record 2009 rebound ($17,564M); both cells are filing values
    # and the 2008 cell is interior only because 2007/2009 flank it.
    ("JPMorgan", "ficc", 2008),
    # Jefferies' Asset Management segment carries merchant-banking
    # principal marks, so its revenue is investment-driven, not
    # fee-driven: FY2022 ($1,243.5M) held outsized investment gains,
    # FY2023 ($188.3M) did not. The 2023 cell is the FY2025 10-K
    # segment-note net revenue, fixture-asserted in
    # test_revenue_xbrl.py (JEF_FY25_NET) and
    # test_jefferies_awm_matches_filings.
    ("Jefferies", "awm", 2023),
}


def test_no_implausible_interior_swing():
    # A one-year value <25% or >400% of BOTH neighbors is an extraction
    # signature (wrong line / wrong units), unless a registered reorg
    # break sits on either side or the swing is verified real above.
    panel = _panel()
    offenders = []
    for (firm, line), grp in panel.group_by(["firm", "taxonomy_line"]):
        s = grp.sort("year")
        yrs = s["year"].to_list()
        vals = s["net_revenue_m"].to_list()
        for i in range(1, len(yrs) - 1):
            if yrs[i] - yrs[i - 1] != 1 or yrs[i + 1] - yrs[i] != 1:
                continue
            v, pv, nv = vals[i], vals[i - 1], vals[i + 1]
            if pv <= 0 or nv <= 0 or v <= 0:
                continue
            implausible = (v < 0.25 * pv and v < 0.25 * nv) or \
                          (v > 4.0 * pv and v > 4.0 * nv)
            if not implausible:
                continue
            if is_break(firm, yrs[i - 1], yrs[i]) or \
                    is_break(firm, yrs[i], yrs[i + 1]):
                continue
            if (firm, line, yrs[i]) in _VERIFIED_SWINGS:
                continue
            offenders.append((firm, line, yrs[i], pv, v, nv))
    assert not offenders, offenders
    print("test_no_implausible_interior_swing: OK")


def test_gs_no_interior_gaps():
    # Audit item: GS 2019 was missing from every annual line. Every GS
    # line's year set must be contiguous over its own span.
    panel = _panel()
    gs = panel.filter(pl.col("firm") == "Goldman Sachs")
    for (line,), grp in gs.group_by(["taxonomy_line"]):
        yrs = sorted(grp["year"].to_list())
        assert yrs == list(range(yrs[0], yrs[-1] + 1)), (line, yrs)
    print("test_gs_no_interior_gaps: OK")


def test_jefferies_ficc_plausible_band():
    # The 2018-2025 bug read $7-16M/yr (a contract-revenue row) for a
    # franchise whose FICC revenue is ~$0.6-2.5B/yr. Exact filing values
    # are asserted in test_jefferies_trading_lines_match_filings; this
    # band catches a future unit/line slip anywhere in the span.
    panel = _panel()
    s = panel.filter((pl.col("firm") == "Jefferies")
                     & (pl.col("taxonomy_line") == "ficc")
                     & (pl.col("year") >= 2018))
    assert s.height >= 8, s.height
    for r in s.iter_rows(named=True):
        assert 300.0 <= r["net_revenue_m"] <= 3000.0, r
    print("test_jefferies_ficc_plausible_band: OK")


def main():
    test_taxonomy_lines()
    test_map_quality_goldman()
    test_map_quality_unmappable()
    test_map_quality_citi_banking_not_advisory()
    test_map_quality_jefferies_ibcm_not_advisory()
    test_map_quality_jefferies_leucadia_awm_unmapped()
    test_map_quality_citi_wealth_exact_not_pbwm()
    test_citi_awm_2020_is_a_documented_gap()
    test_reorg_registry_coverage()
    test_regime_breaks()
    test_build_panel_dedupe_and_regime()
    test_build_panel_never_splices_breaks()
    test_build_panel_direct_beats_approximate()
    test_reconcile_splits_pass_and_flag()
    test_jefferies_annual_advisory_matches_filings()
    test_jefferies_trading_lines_match_filings()
    test_jefferies_awm_matches_filings()
    test_ms_trading_lines_match_filings()
    test_wells_fargo_awm_matches_filings()
    test_advisory_no_impossible_drop()
    test_no_identical_values_across_lines()
    test_no_implausible_interior_swing()
    test_gs_no_interior_gaps()
    test_jefferies_ficc_plausible_band()
    print("test_revenue_panel: all 22 tests passed")


if __name__ == "__main__":
    main()
