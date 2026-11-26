"""The texts of the planned zones, ledger, learn and setup questions.

Zones will tell how far a file's folder reaches (this machine, a sync folder or
a git remote), ask before private content goes further, and check a folder for
leaks before it is shared. The coordinator will keep a local ledger. The learn
skill will turn a correction into one lasting rule and check the zone first.
Setup will ask what to turn off and which of two same-job skills to keep, and
show what each part costs. Their logic comes in later PRs. These scenarios pin
what that logic fills in and what each text tells the reader, so the texts can
be reviewed on their own.
"""
import os
import re
import sys

from conftest import DASH, PLUGIN
from steps import and_, given, then, when

PARTS = os.path.join(PLUGIN, "parts")
CORE_TEXT = os.path.join(PARTS, "core", "text")
LEARN_SKILL = os.path.join(PARTS, "learn", "skill")
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
# The values the zone and share logic passes to each text.
ZONE_FILL = {"path", "name", "zone", "reach", "instead", "folder"}
# Each zone, share, leak and ledger text and the values it uses.
ZONE_TEXTS = {
    ("core", "reach_local.md"): set(),
    ("core", "reach_share.md"): {"remote"},
    ("core", "reach_sync.md"): {"root"},
    ("core", "reach_unknown.md"): set(),
    ("core", "reach_listed.md"): {"zone"},
    ("core", "instead_unknown.md"): {"name", "projects"},
    ("core", "zone_question.md"): ZONE_FILL,
    ("core", "share_dry_run.md"): {"folder", "reach", "undo", "private_words", "private_paths", "secrets"},
    ("live_notes", "zone_line.md"): set(),
    ("coordinator", "ledger.md"): set(),
    ("coordinator", "ledger_line.md"): {"path"},
}
# Each setup text and the values setup passes to it.
SETUP_TEXTS = {
    "turn_off_question.md": {"menu"},
    "turn_off_option.md": {"parts", "tokens"},
    "doubles_question.md": {"yours", "hub_skill"},
    "skill_cost_line.md": {"name", "tokens"},
    "setup_summary.md": {"start_costs", "skill_costs", "ledger_path", "ledger_zone", "notes_path", "notes_zone"},
}
ONE_LINE = {"reach_local.md", "reach_share.md", "reach_sync.md", "reach_unknown.md", "reach_listed.md",
            "instead_unknown.md", "zone_line.md", "ledger_line.md",
            "turn_off_option.md", "skill_cost_line.md"}
# The reach texts end the sentence "its folder ...", and the logic adds the full stop.
REACH = {"reach_local.md", "reach_share.md", "reach_sync.md", "reach_unknown.md", "reach_listed.md"}
OPTION_LINE = re.compile(r"^- \*\*(.+?)\*\*\. (.+)$")
LEARN_SECTIONS = ["learn.instructions.hooks", "learn.instructions.ledger_and_memory",
                  "learn.instructions.editor_copies"]
# The text no switch may remove, because it keeps the user safe or carries the method.
LEARN_FIXED = ("Quote the user's exact words", "~/.claude/CLAUDE.md", "Search before writing",
               "{{step}} zone <path> --for sync", "If the first word is `ask`, write nothing",
               "Never soften or delete one of the user's rules", "Apply it now")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def one_line(text):
    return text.endswith("\n") and "\n" not in text[:-1] and text.strip() != ""


def filled(text, values):
    """The text with each value set to its own name, the way str.format fills it."""
    return text.format(**{value: f"<{value}>" for value in values})


def plain(name, text):
    assert DASH not in text, name
    assert not re.search(r"~/|/Users/|/home/", text), name
    assert "hubctl" not in text.casefold(), name


def test_the_zone_share_leak_and_ledger_texts_fill_the_values_their_logic_passes():
    with given("the new core text folder"):
        shipped = sorted(os.listdir(CORE_TEXT))
    with then("it holds exactly the planned zone and share texts"):
        assert shipped == sorted(name for part, name in ZONE_TEXTS if part == "core")
    for (part, name), values in ZONE_TEXTS.items():
        with when(f"{part}'s {name} is read"):
            text = read(PARTS, part, "text", name)
        with then(f"it uses exactly {sorted(values)} and leaves no brace once filled"):
            assert set(VALUE.findall(text)) == values, name
            assert not re.search(r"[{}]", filled(text, values)), name
        with and_("it has no em dash, names no home folder and no hubctl"):
            plain(name, text)
        if name in ONE_LINE:
            with and_("it is one line"):
                assert one_line(text), name
        if name in REACH:
            with and_("it ends a sentence the logic closes, so it has no full stop of its own"):
                assert not text.strip().endswith("."), name
    with then("the zone question offers keep it local first, then share it, then skip"):
        question = read(CORE_TEXT, "zone_question.md")
        options = [match[1] for match in map(OPTION_LINE.match, question.splitlines()) if match]
        assert options == ["Keep it local (Recommended)", "Share it", "Skip"]
    with and_("the question waits for the user's own share message"):
        assert 'Wait for their own "share {folder}" message' in question
    with then("the share dry run lists the leak check's three kinds of finding and how to take it back"):
        dry_run = read(CORE_TEXT, "share_dry_run.md")
        for heading in ("Private words", "Home and temp paths", "Secrets and chat session links"):
            assert f"\n{heading}\n" in dry_run, heading
        assert 'say "undo {undo}"' in dry_run
    with then("the blank ledger has open items and decisions, and carries the live notes' zone line"):
        ledger = read(PARTS, "coordinator", "text", "ledger.md")
        assert ledger.startswith("# Ledger\n") and "\n## Open items\n" in ledger and "\n## Decided\n" in ledger
        assert " " + read(PARTS, "live_notes", "text", "zone_line.md").strip() in ledger


def test_setup_texts_fill_the_turn_off_doubles_and_summary_values():
    for name, values in SETUP_TEXTS.items():
        with when(f"the tuner's {name} is read"):
            text = read(PARTS, "tuner", "text", name)
        with then(f"it uses exactly {sorted(values)} and leaves no brace once filled"):
            assert set(VALUE.findall(text)) == values, name
            assert not re.search(r"[{}]", filled(text, values)), name
        with and_("it has no em dash, names no home folder and no hubctl"):
            plain(name, text)
        if name in ONE_LINE:
            with and_("it is one line"):
                assert one_line(text), name
    with then("the turn-off question says skipping keeps everything on and how to turn a part off later"):
        turn_off = read(PARTS, "tuner", "text", "turn_off_question.md")
        assert "Everything stays on if you skip" in turn_off and "/hub-flags" in turn_off
    with then("the doubles question asks first, then offers keep yours before keep both"):
        first, *rest = read(PARTS, "tuner", "text", "doubles_question.md").splitlines()
        assert first.endswith("?") and set(VALUE.findall(first)) == {"yours", "hub_skill"}
        assert [match[1] for match in map(OPTION_LINE.match, rest) if match] == ["Keep yours (Recommended)",
                                                                                 "Keep both"]
    with then("the summary keeps each value on its own line, so a value hub can not work out drops only its line"):
        summary = read(PARTS, "tuner", "text", "setup_summary.md")
        for line in summary.splitlines():
            assert len(VALUE.findall(line)) <= 2 and ("{ledger" not in line or "{notes" not in line), line
        assert summary.rstrip().endswith('say "turn off <name>", or open /hub-flags.')


def test_the_learn_skill_checks_the_zone_before_each_write_and_marks_only_its_three_switchable_sections():
    with given("the learn skill's files"):
        skill = read(LEARN_SKILL, "SKILL.md")
        batch = read(LEARN_SKILL, "references", "batch.md")
    with then("its front matter names hub-learn and its body points to the batch reference"):
        assert skill.startswith("---\nname: hub-learn\n")
        assert "`references/batch.md`" in skill
    with and_("the batch reference sends each write through the zone check too"):
        assert "zone check in step 5" in batch
    markers = re.findall(r"<!-- hub:section ([a-z_.]+) -->", skill)
    with then("it marks exactly the hooks, ledger and memory, and editor copy sections, each one closed"):
        assert sorted(set(markers)) == sorted(LEARN_SECTIONS)
        assert len(markers) == skill.count("<!-- hub:end -->")
    with and_("the fixed lines sit outside every section, so no switch removes them"):
        outside = re.sub(r"<!-- hub:section [a-z_.]+ -->.*?<!-- hub:end -->", "", skill, flags=re.S)
        for words in LEARN_FIXED:
            assert words in outside, words
    with and_("it asks before a write that reaches further, and waits for the user's own share"):
        assert 'wait for their own typed "share <folder>"' in outside
    with and_("neither file has an em dash or names hubctl"):
        for name, text in (("SKILL.md", skill), ("batch.md", batch)):
            assert DASH not in text and "hubctl" not in text.casefold(), name
    with when("hub reads its parts"):
        sys.path.insert(0, os.path.join(PLUGIN, "lib"))
        from hub import tree
        manifests = tree.manifest_paths(PARTS)
    with then("learn has no manifest yet, so nothing builds or links its skill"):
        assert os.path.join(PARTS, "learn", "part.json") not in manifests
        assert not os.path.exists(os.path.join(PARTS, "learn", "part.json"))
