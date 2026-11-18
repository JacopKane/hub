"""The texts of the planned PR test gate and of the PR body's tested part.

The gate will stop a push to a branch with an open PR, a new PR and marking a
PR ready until the commit has a passing test report, and it will check that a
new PR's body says how it was tested and what was not. Its logic comes in a
later PR. These scenarios pin what that logic fills in and what each message
tells the reader, so the texts can be reviewed on their own.
"""
import os
import re

from conftest import DASH, PLUGIN
from steps import and_, given, then, when

GATE_TEXT = os.path.join(PLUGIN, "parts", "pr_test_gate", "text")
PR_TEXT = os.path.join(PLUGIN, "parts", "pr", "text")
STEP = "{{step}} pr-tested"
# Each message and the values the gate fills into it.
GATE_MESSAGES = {
    "no_report.md": {"short", "branch"},
    "failed.md": {"short", "which"},
    "dirty.md": {"short"},
    "no_smoke.md": {"short"},
    "no_not_tested.md": {"short"},
    "texts_has_code.md": {"short", "file"},
    "unreadable_push.md": {"why"},
    "pr_head_unknown.md": {"number", "why"},
    "report_unreadable.md": {"short"},
    "near_miss.md": {"heading"},
    "slow.md": set(),
}
# The refusals a new test run or a new report fixes, so they name the step that records one.
NAMES_THE_STEP = {"no_report.md", "failed.md", "dirty.md", "no_smoke.md", "no_not_tested.md",
                  "texts_has_code.md", "report_unreadable.md"}
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")


def read(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as handle:
        return handle.read()


def one_line(text):
    return text.endswith("\n") and "\n" not in text[:-1] and text.strip() != ""


def test_the_gate_messages_are_one_line_each_fill_their_values_and_point_to_the_step_that_records_a_report():
    with given("the gate's text folder"):
        shipped = sorted(name for name in os.listdir(GATE_TEXT) if name.endswith(".md"))
    with then("it holds exactly the planned messages"):
        assert shipped == sorted(GATE_MESSAGES)
    for name, values in GATE_MESSAGES.items():
        with when(f"{name} is read"):
            message = read(GATE_TEXT, name)
        with then(f"{name} is one line that fills exactly {sorted(values)}"):
            assert one_line(message), name
            assert set(VALUE.findall(message)) == values, name
        with and_(f"{name} has no em dash and names no home folder"):
            assert DASH not in message, name
            assert not re.search(r"~/|/Users/|/home/", message), name
        with and_(f"{name} {'names' if name in NAMES_THE_STEP else 'leaves out'} hub's pr-tested step"):
            assert (STEP in message) == (name in NAMES_THE_STEP), name
    with then("the refusal for a missing report states the rule, draft or not, and both ways to record a report"):
        no_report = read(GATE_TEXT, "no_report.md")
        for words in ("draft or not", "scenarios", "smoke run", "outermost seam", f"{STEP} run",
                      f"{STEP} texts", "--not-tested", "--no-smoke", "How it was tested"):
            assert words in no_report, words


def test_the_pr_body_asks_how_it_was_tested_and_the_rules_and_findings_say_what_was_not():
    with given("the PR body template"):
        headings = [line for line in read(PR_TEXT, "body.md").split("\n") if line.startswith("#")]
    with then("its tested part follows To check it and comes before What is decided"):
        assert headings.index("### How it was tested") == headings.index("### To check it") + 1
        assert headings.index("## What is decided") == headings.index("### How it was tested") + 1
    with when("the start-text rule line for testing is read"):
        rule = read(PR_TEXT, "rule_tested.md")
    with then("it is one rule line, like the others, that asks for scenarios before every push, draft or not"):
        assert one_line(rule) and rule.startswith("- ")
        assert "draft or not" in rule and "scenarios" in rule and "smoke run" in rule
        assert "How it was tested" in rule and "not tested" in rule
    with when("the body check's finding for a tested part without a not-tested line is read"):
        finding = read(PR_TEXT, "check_missing_not_tested.md")
    with then("it is one line that names the line and asks what was not tested and why"):
        assert one_line(finding) and VALUE.findall(finding) == ["line"]
        assert "not tested and why" in finding
    with and_("no new text has an em dash"):
        assert DASH not in rule + finding


def test_the_readme_lists_the_gate_as_planned_and_the_pr_part_as_testing_before_each_push():
    with given("the README's table of parts"):
        rows = {line.split("|")[1].strip(): line for line in read(os.path.dirname(os.path.dirname(PLUGIN)),
                                                                  "README.md").split("\n")
                if line.startswith("| ") and line.count("|") == 6}
    with then("the pr row says it tests before each push"):
        assert "tests before each push" in rows["pr"]
    with and_("the gate has its own row, planned, on from the start, for Claude Code only"):
        cells = [cell.strip() for cell in rows["pr_test_gate"].split("|")[1:-1]]
        assert cells[1].startswith("Planned, not in this version yet.")
        assert cells[2:4] == ["on", "Claude Code"]
