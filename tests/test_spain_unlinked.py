"""Spain heat index: the section is unlinked by design.

The Spain page exists (so this guard is not vacuous), but no existing
page — landing or section — may link to it. If a future change links
the section deliberately, this test is the thing to update, in the
same commit as the link.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EXISTING_PAGES = [
    "docs/index.html",
    "docs/frb/index.html",
    "docs/pulse/index.html",
    "docs/momentum/index.html",
    "docs/predmarkets/index.html",
    "docs/revenue/index.html",
    "docs/hf/index.html",
]


def test_spain_page_exists():
    page = REPO / "docs/spain/index.html"
    assert page.exists()
    assert "Spain" in page.read_text()


def test_no_existing_page_links_to_spain():
    for rel in EXISTING_PAGES:
        p = REPO / rel
        if not p.exists():
            continue
        hrefs = re.findall(r'href="([^"]*)"', p.read_text())
        bad = [h for h in hrefs if "spain" in h.lower()]
        assert not bad, f"{rel} links to Spain: {bad}"
