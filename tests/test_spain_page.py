"""Spain heat index: the v3 presentation pass — heat maps, glance
table, and downloads must agree with the committed bundle.

The heat maps are static SVG rendered by build_spain.render_page;
every data cell carries data-q / data-v attributes so this test can
verify the grid against docs/data/spain.json cell by cell, including
the no-data rows for all-pending components and source-pending
indicators (shown, never dropped).
"""
import csv
import json
import re
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BUNDLE = json.loads((REPO / "docs/data/spain.json").read_text())
META = json.loads((REPO / "docs/data/spain_meta.json").read_text())
PAGE = (REPO / "docs/spain/index.html").read_text()
QUARTERS = BUNDLE["quarters"]


class HeatmapParser(HTMLParser):
    """Collect {figure_id: {row_key: {kind, cells: {q: v}, miss: [q]}}}."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fig = None
        self.row = None
        self.maps = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "figure" and a.get("id", "").startswith("sp-heatmap-"):
            self.fig = a["id"]
            self.maps[self.fig] = {}
        elif self.fig and tag == "g" and a.get("class") == "hm-row":
            self.row = {"kind": a.get("data-kind"), "cells": {}, "miss": []}
            self.maps[self.fig][a["data-row"]] = self.row
        elif self.fig and tag == "rect" and self.row is not None:
            if "data-v" in a:
                self.row["cells"][a["data-q"]] = float(a["data-v"])
            elif a.get("class") == "miss":
                self.row["miss"].append(a["data-q"])

    def handle_endtag(self, tag):
        if tag == "figure":
            self.fig = None
        elif tag == "g":
            self.row = None


def _maps():
    p = HeatmapParser()
    p.feed(PAGE)
    return p.maps


def _check_row(row, series):
    expect = {q: v for q, v in zip(QUARTERS, series) if v is not None}
    assert set(row["cells"]) == set(expect)
    for q, v in expect.items():
        assert abs(row["cells"][q] - v) < 1e-6, q
    assert set(row["miss"]) == set(QUARTERS) - set(expect)


def test_component_heatmap_matches_bundle():
    rows = _maps()["sp-heatmap-components"]
    assert set(rows) == set(BUNDLE["components"])
    for comp, series in BUNDLE["components"].items():
        row = rows[comp]
        if all(v is None for v in series):
            # all-pending component: explicit no-data row, no cells
            assert row["kind"] == "nodata", comp
            assert row["cells"] == {} and row["miss"] == [], comp
        else:
            assert row["kind"] == "data", comp
            _check_row(row, series)


def test_indicator_heatmap_matches_bundle():
    rows = _maps()["sp-heatmap-indicators"]
    by_id = {i["id"]: i for i in META["indicators"]}
    data_rows = {k: v for k, v in rows.items() if v["kind"] != "group"}
    assert set(data_rows) == set(by_id)
    groups = {k for k, v in rows.items() if v["kind"] == "group"}
    assert groups == set(BUNDLE["components"])
    for ind_id, ind in by_id.items():
        row = data_rows[ind_id]
        if ind["status"] == "source-pending":
            assert row["kind"] == "nodata", ind_id
            assert row["cells"] == {} and row["miss"] == [], ind_id
        else:
            assert row["kind"] == "data", ind_id
            _check_row(row, BUNDLE["indicators"][ind_id]["percentile"])


def test_glance_table_matches_bundle():
    m = re.search(r'id="sp-glance".*?<tbody>(.*?)</tbody>', PAGE, re.S)
    assert m, "glance table missing"
    body = m.group(1)
    for comp, series in BUNDLE["components"].items():
        row = re.search(rf"<tr><td>{re.escape(comp)}</td>(.*?)</tr>",
                        body, re.S)
        assert row, comp
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
        if all(v is None for v in series):
            assert "source-pending" in row.group(0), comp
            continue
        live = [(q, v) for q, v in zip(QUARTERS, series) if v is not None]
        latest_q, latest_v = live[-1]
        peak_q, peak_v = max(live, key=lambda t: t[1])
        chip = re.search(r">(\d\.\d\d)<", cells[1])
        assert chip and abs(float(chip.group(1)) - latest_v) < 0.005, comp
        assert cells[2] == latest_q, comp
        assert abs(float(cells[3]) - peak_v) < 1e-3, comp
        assert cells[4] == peak_q, comp


def _read_csv(name):
    with (REPO / "data/spain" / name).open() as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def test_downloads_exist_and_match_bundle():
    ids = [i["id"] for i in META["indicators"]]

    header, rows = _read_csv("components.csv")
    assert header == ["quarter"] + list(BUNDLE["components"])
    assert [r[0] for r in rows] == QUARTERS
    for r in rows:
        for c, comp in enumerate(BUNDLE["components"], start=1):
            v = BUNDLE["components"][comp][QUARTERS.index(r[0])]
            assert (r[c] == "") == (v is None), (comp, r[0])
            if v is not None:
                assert abs(float(r[c]) - v) < 1e-9, (comp, r[0])

    header, rows = _read_csv("headline.csv")
    assert header == ["quarter", "headline"]
    assert [r[0] for r in rows] == QUARTERS
    for r, v in zip(rows, BUNDLE["headline"]):
        assert (r[1] == "") == (v is None)
        if v is not None:
            assert abs(float(r[1]) - v) < 1e-9

    header, rows = _read_csv("panel_percentiles.csv")
    assert header == ["quarter"] + ids
    assert [r[0] for r in rows] == QUARTERS
    for r in rows:
        qi = QUARTERS.index(r[0])
        for c, ind_id in enumerate(ids, start=1):
            pct = BUNDLE["indicators"][ind_id]["percentile"]
            v = pct[qi] if pct else None
            assert (r[c] == "") == (v is None), (ind_id, r[0])
            if v is not None:
                assert abs(float(r[c]) - v) < 1e-9, (ind_id, r[0])

    # the pre-existing raw panel download is still there and linked
    assert (REPO / "data/spain/panel_quarterly.csv").exists()
    for name in ("panel_quarterly.csv", "panel_percentiles.csv",
                 "components.csv", "headline.csv"):
        assert f'data/spain/{name}' in PAGE, name
