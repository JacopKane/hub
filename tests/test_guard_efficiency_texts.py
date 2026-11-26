"""The texts for the switch that lets throwaway work through self-protection, and two refusal hints.

The precision PR shows the hint to quote a value and the hint to use a
container. The containers PR adds the containers switch to self_protection.
These scenarios pin what each text tells the reader, so the words can be
reviewed before the logic.
"""
import os
import re

from conftest import DASH, PLUGIN, REPO
from steps import and_, given, then, when

PARTS = os.path.join(PLUGIN, "parts")
# Each new hint, by the folder of the part that shows it.
NEW_HINTS = {
    os.path.join(PARTS, "self_protection", "text", "quote_the_value.md"): (
        "could change which program or code runs", "in full, with no variable", "in double quotes"),
    os.path.join(PARTS, "self_protection", "text", "use_a_container.md"): (
        "more than read HOME, an XDG folder or CLAUDE_CONFIG_DIR", "eval, source or the . command",
        "belong to your whole account", "on a line of its own", "container with no mounts"),
}
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
# A home or temp folder path, which must never reach a public text. Written in pieces, as in
# scrub_scan.py, so the scan does not find its own pattern.
PRIVATE_PATH = re.compile(r"~/|/Users/|/home/|/tmp/|/var/(?:folders)/")
CONTAINERS_ROW = ("| containers (in self_protection) | Lets hub's switch step run in a container that can not "
                  "reach your home folder, where it changes only the container's copy of hub's settings | on | "
                  "Claude Code, Cursor | \"turn off containers\" |")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def one_line(text):
    return text.endswith("\n") and "\n" not in text[:-1] and text.strip() != ""


def table_rows(readme):
    flags = readme.split("## Feature flags", 1)[1].split("\n## ", 1)[0]
    return [line for line in flags.split("\n") if line.startswith("| ")]


def row_after(rows, start):
    return rows[next(index for index, row in enumerate(rows) if row.startswith(start)) + 1]


def test_each_new_hint_is_one_plain_line_that_says_what_to_do_instead():
    for path, words in NEW_HINTS.items():
        name = os.path.basename(path)
        with when(f"{name} is read"):
            hint = read(path)
        with then(f"{name} is one line that fills no values"):
            assert one_line(hint), name
            assert VALUE.findall(hint) == [], name
        with and_(f"{name} has no em dash and no home or temp folder path"):
            assert DASH not in hint, name
            assert not PRIVATE_PATH.search(hint), name
        with and_(f"{name} tells Claude why the command stopped and what to write instead"):
            for phrase in words:
                assert phrase in hint, (name, phrase)
    with then("the quote hint gives an example of a setting that may keep its variable"):
        assert '"TMPDIR=$' in read(next(path for path in NEW_HINTS if path.endswith("quote_the_value.md")))


def test_the_readme_lists_the_containers_switch_under_self_protection():
    with given("the README"):
        readme = read(os.path.join(REPO, "README.md"))
    with when("the feature flags table is read"):
        rows = table_rows(readme)
    with then("the containers row comes right after self_protection and says it starts on"):
        assert row_after(rows, "| self_protection |") == CONTAINERS_ROW
    with and_("the row has no em dash"):
        assert DASH not in CONTAINERS_ROW


def test_the_limits_page_names_the_container_as_a_design_limit():
    with given("the self-protection limits page"):
        page = read(os.path.join(REPO, "docs", "self-protection-limits.md"))
    with when("its part on what it does not watch is read"):
        part = page.split("## What it does not watch\n", 1)[1].split("\n## ", 1)[0].strip()
    with then("it says the switch step in a container with no reach to the home folder is not checked against "
              "the user's settings, and why"):
        assert ("hub does not check its own switch step against your settings when the step runs in a "
                "container that can not reach your home folder. There the step changes only the container's "
                "copy of hub's settings.") in part
    with and_("it still says self-protection is not a sandbox, before the container limit"):
        assert part.index("It is not a sandbox.") < part.index("hub does not check its own switch step")
