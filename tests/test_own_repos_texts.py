"""The texts for own repos, a part that lets Claude deliver its own PRs in the repos a user lists.

The own repos PR adds the part, its repos option and its `does` strings, and
writes these two lines into the start text while the list holds a repo. These
scenarios pin what each line tells Claude, so the words can be reviewed before
the logic.
"""
import os
import re

from conftest import DASH, PLUGIN, REPO
from steps import and_, given, then, when

TEXT = os.path.join(PLUGIN, "parts", "own_repos", "text")
# Each new line, with the values it fills and the whole line once {repos} is filled.
REPOS = "example/demo and example/tools"
NEW_LINES = {
    os.path.join(TEXT, "delivers.md"): (
        ["repos"],
        "In example/demo and example/tools, push your own branches, open and update your own PRs, mark them ready "
        "and merge them without asking the user. Merge with `gh pr merge --match-head-commit` after the PR's tests "
        "and smoke run pass. If the change could lose data, lock the user out or expose a secret, a review agent "
        "must pass it first. After the merge, delete that PR's branch. These steps do not wait for quiet hours."),
    os.path.join(TEXT, "still_asks.md"): (
        [],
        "Some steps in these repos still wait for the user's order: a push straight to the default branch, a "
        "force-push, a release or tag, a change to the repo's settings, deleting any other branch, and any comment, "
        "review or reply on someone else's PR or issue."),
}
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
# A home or temp folder path, which the gate's private paths check stops in a push. Written in pieces, as in
# scrub_scan.py, so the scan does not find its own pattern.
PRIVATE_PATH = re.compile(r"~/|/Users/|/home/|/tmp/|/var/(?:folders)/")
OWN_REPOS_ROW = ("| own_repos | Lets Claude push, open, mark ready and merge its own PRs in the repos you list, "
                 "without asking. A push to the default branch, a release and comments on other people's work still "
                 "wait for your order | off | Claude Code, Cursor | \"turn on own repos\" |")


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


def test_each_own_repos_line_is_one_plain_line_that_names_what_runs_and_what_still_waits():
    for path, (values, filled) in NEW_LINES.items():
        name = os.path.basename(path)
        with when(f"{name} is read"):
            line = read(path)
        with then(f"{name} is one line that fills exactly its values"):
            assert one_line(line), name
            assert VALUE.findall(line) == values, name
        with and_(f"{name} has no em dash and no home or temp folder path, so a push of it passes the gate"):
            assert DASH not in line, name
            assert not PRIVATE_PATH.search(line), name
        with and_(f"{name}, with two repos filled in, reads as the agreed line"):
            assert line.rstrip("\n").replace("{repos}", REPOS) == filled, name
    with then("the merge line keeps only the commit that passed its checks"):
        assert "--match-head-commit" in read(os.path.join(TEXT, "delivers.md"))


def test_the_readme_lists_own_repos_after_the_pr_rules_and_says_it_starts_off():
    with given("the README"):
        readme = read(os.path.join(REPO, "README.md"))
    with when("the feature flags table is read"):
        rows = table_rows(readme)
    with then("the own_repos row comes right after the pr row and says it starts off"):
        assert row_after(rows, "| pr |") == OWN_REPOS_ROW
    with and_("the row has no em dash"):
        assert DASH not in OWN_REPOS_ROW
