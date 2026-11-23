"""The words that describe the status band.

The band draws two rows. The top one holds the other chats and features, and
the second holds the newest event and its buttons. The layout itself comes in a
later PR, so these scenarios pin only the words, which can be reviewed on their
own.
"""
import json
import os

from conftest import DASH, PLUGIN, REPO
from steps import and_, given, then

DOES = ("Two rows above the prompt. The top row shows your other chats and the features button. "
        "The second shows the newest change or offer with its buttons, or hub's state when there is none.")
README_CELL = "Two rows above the prompt with your other chats, hub's state, recent changes and offers"
FIXTURES = ("day9.ts", "picker-day9.ts")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def readme_cells(name):
    rows = [line for line in read(os.path.join(REPO, "README.md")).split("\n")
            if line.startswith("| ") and line.count("|") == 6]
    return next([cell.strip() for cell in row.split("|")[1:-1]] for row in rows
                if row.split("|")[1].strip() == name)


def test_the_status_band_says_it_takes_two_rows_in_the_picker_the_readme_and_the_mod_fixtures():
    with given("the status band's part file"):
        part = json.loads(read(os.path.join(PLUGIN, "parts", "status_band", "part.json")))
    with then("its description says two rows, and what each row shows"):
        assert part["does"] == DOES
        assert "A line above the prompt" not in part["does"]
    with and_("the README row and both mod fixtures carry the same words, with no em dash"):
        assert readme_cells("status_band")[1] == README_CELL
        for name in FIXTURES:
            fixture = read(os.path.join(PLUGIN, "mod", "tests", name))
            assert f'"does": {json.dumps(DOES)}' in fixture, name
            assert "A line above the prompt" not in fixture, name
        assert DASH not in DOES + README_CELL
