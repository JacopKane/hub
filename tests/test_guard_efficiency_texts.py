"""The texts for two switches that let throwaway work through hub's guards, and three refusal hints.

Three later PRs use these texts. The precision PR shows the hint to quote a
value, the hint to use a container and the gh api read hint. The containers
PR adds the containers switch to self_protection, and the temp repos PR adds
the temp repos switch to the public text gate. These scenarios pin what each
text tells the reader, so the words can be reviewed before the logic.
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
    os.path.join(PARTS, "public_text_gate", "text", "api_read_hint.md"): (
        "in double quotes", "starts with fixed text", "possible write"),
}
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
# A home or temp folder path, which the gate's private paths check stops in a push. Written in pieces, as in
# scrub_scan.py, so the scan does not find its own pattern.
PRIVATE_PATH = re.compile(r"~/|/Users/|/home/|/tmp/|/var/(?:folders)/")
CONTAINERS_ROW = ("| containers (in self_protection) | Lets hub's switch step run in a container that can not "
                  "reach your home folder, where it changes only the container's copy of hub's settings | on | "
                  "Claude Code, Cursor | \"turn off containers\" |")
TEMP_REPOS_ROW = ("| temp repos (in public_text_gate) | Skips the checks on commits in a repo inside a temp folder "
                  "with no remote. A push of those commits is still checked | on | Claude Code, Cursor | "
                  "\"turn off temp repos\" |")


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
        with and_(f"{name} has no em dash and no home or temp folder path, so a push of it passes the gate"):
            assert DASH not in hint, name
            assert not PRIVATE_PATH.search(hint), name
        with and_(f"{name} tells Claude why the command stopped and what to write instead"):
            for phrase in words:
                assert phrase in hint, (name, phrase)
    with then("the quote hint gives an example of a setting that may keep its variable"):
        assert '"TMPDIR=$' in read(next(path for path in NEW_HINTS if path.endswith("quote_the_value.md")))
    with and_("the gh api hint gives an example endpoint with a quoted variable"):
        assert '"repos/example/demo/pulls/$number/files"' in read(
            next(path for path in NEW_HINTS if path.endswith("api_read_hint.md")))


def test_the_readme_lists_both_switches_under_their_parts_and_says_when_temp_repo_commits_are_checked():
    with given("the README"):
        readme = read(os.path.join(REPO, "README.md"))
    with when("the feature flags table is read"):
        rows = table_rows(readme)
    with then("the containers row comes right after self_protection and says it starts on"):
        assert row_after(rows, "| self_protection |") == CONTAINERS_ROW
    with and_("the temp repos row comes right after the em dash check and says it starts on"):
        assert row_after(rows, "| em dash check (in public_text_gate) |") == TEMP_REPOS_ROW
    with and_("both rows have no em dash"):
        assert DASH not in CONTAINERS_ROW and DASH not in TEMP_REPOS_ROW
    with when("the known limits are read"):
        limits = readme.split("Known limits:", 1)[1].split("\n## ", 1)[0]
    with then("the temp folder path limit says commits in a temp repo with no remote are checked at push"):
        line = next(item for item in limits.split("\n") if "temp folder path on every try" in item)
        assert line.endswith(" In a repo inside a temp folder with no remote, the gate checks the commits "
                             "when they are pushed.")


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
