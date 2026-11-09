"""Cursor fixtures built from the docs say so, so nobody mistakes them for captures."""
import glob
import json
import os

from conftest import REPO
from steps import given, then, when

CURSOR_FIXTURES = os.path.join(REPO, "tests", "fixtures", "hook_input", "cursor", "*.json")


def test_every_cursor_fixture_says_it_was_built_from_the_docs_and_not_captured():
    with given("the Cursor hook input fixtures"):
        paths = sorted(glob.glob(CURSOR_FIXTURES))
        assert paths
    with when("each fixture is read"):
        fixtures = {path: json.load(open(path)) for path in paths}
    with then("each carries a docs_based marker that names Cursor's docs"):
        for path, fixture in fixtures.items():
            marker = fixture.get("docs_based")
            assert marker, os.path.basename(path) + " has no docs_based marker"
            assert "docs" in marker and "not captured" in marker
