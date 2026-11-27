"""Spain heat index: metadata completeness for every indicator."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.spain.series import COMPONENTS, INDICATORS  # noqa: E402

META = json.loads((REPO / "docs/data/spain_meta.json").read_text())
BUNDLE = json.loads((REPO / "docs/data/spain.json").read_text())


def test_registry_matches_meta():
    assert [i["id"] for i in META["indicators"]] == \
        [i["id"] for i in INDICATORS]
    assert set(BUNDLE["components"]) == set(COMPONENTS)


def test_every_indicator_has_provenance():
    for ind in META["indicators"]:
        assert ind["source"], ind["id"]
        assert ind["definition"], ind["id"]
        assert ind["frequency"], ind["id"]
        assert ind["component"] in COMPONENTS, ind["id"]
        if ind["status"] == "source-pending":
            assert ind["pending_reason"], ind["id"]
            assert ind["first_observation"] is None
        else:
            assert ind["series_id"], ind["id"]
            assert ind["source_url"], ind["id"]
            assert ind["first_observation"] >= "1999Q1", ind["id"]
            assert ind["last_observation"], ind["id"]
            assert ind["n_observations"] > 0, ind["id"]
            assert ind["last_value"] is not None, ind["id"]


def test_pending_components_do_not_enter_headline():
    live_comps = {i["component"] for i in META["indicators"]
                  if i["status"] != "source-pending"}
    for comp, series in BUNDLE["components"].items():
        if comp not in live_comps:
            assert all(v is None for v in series), comp
