"""The texts of the own-words fixes and the page of self-protection's limits.

Four later PRs use these texts: the switch step's words, the words door with
its check of Claude runs, relayed pins, and the repo files lock. Nothing reads
the new messages yet. These scenarios pin what each one fills in and what it
tells the reader, and that the refusals already shown name both of the user's
doors, so the texts can be reviewed on their own.
"""
import importlib.util
import os
import re
import sys

from conftest import DASH, PLUGIN, REPO
from steps import and_, given, then, when

sys.path.insert(0, os.path.join(PLUGIN, "lib"))
from hub import changes, protect  # noqa: E402

PART = os.path.join(PLUGIN, "parts", "self_protection")
TEXT = os.path.join(PART, "text")
DOOR = "/hub where Claude Code loads plugin mods"
LIMITS = os.path.join(REPO, "docs", "self-protection-limits.md")
# Each new message and the values its PR fills into it.
NEW_MESSAGES = {
    "leave_hub_folder.md": {"folder"},
    "claude_run_order.md": {"flag"},
    "claude_run_unreadable.md": set(),
    "relayed_pin.md": {"flag"},
    "repo_value_skipped.md": {"flag", "scope"},
    "repo_scope_refused.md": {"flag", "scope"},
}
# The refusals that hand the change back to the user, so they name both of the user's doors.
ASKS_THE_USER = {"claude_run_order.md", "relayed_pin.md"}
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")


def read(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as handle:
        return handle.read()


def one_line(text):
    return text.endswith("\n") and "\n" not in text[:-1] and text.strip() != ""


def handler_what():
    spec = importlib.util.spec_from_file_location("own_words_texts_handler", os.path.join(PART, "handler.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.WHAT


def test_the_new_messages_are_one_line_each_fill_their_values_and_hand_the_change_back_to_the_user():
    for name, values in NEW_MESSAGES.items():
        with when(f"{name} is read"):
            message = read(TEXT, name)
        with then(f"{name} is one line that fills exactly {sorted(values)}"):
            assert one_line(message), name
            assert set(VALUE.findall(message)) == values, name
        with and_(f"{name} has no em dash and names no home folder"):
            assert DASH not in message, name
            assert not re.search(r"~/|/Users/|/home/", message), name
        with and_(f"{name} {'names' if name in ASKS_THE_USER else 'leaves out'} the user's own words and /hub"):
            assert ("own words" in message and DOOR in message) == (name in ASKS_THE_USER), name
    with then("the folder hint says to leave hub's folder and run the step again"):
        hint = read(TEXT, "leave_hub_folder.md")
        assert "hub's own folder" in hint and "another folder" in hint and "run it again" in hint
    with and_("the two Claude run refusals say the run writes its prompt as if the user typed it"):
        for name in ("claude_run_order.md", "claude_run_unreadable.md"):
            assert "as if the user typed it" in read(TEXT, name), name
    with and_("the one for a prompt hub can not read asks for the prompt as plain text, so hub can check it"):
        assert "plain text, so hub can check it" in read(TEXT, "claude_run_unreadable.md")
    with and_("the relayed pin refusal says why hub can not tell the step from the user's own terminal"):
        assert "another program" in read(TEXT, "relayed_pin.md") and "own terminal" in read(TEXT, "relayed_pin.md")
    with then("show's note says a repo file can not change a pin or weaken a protection, and the user's own "
              "settings still apply"):
        note = read(TEXT, "repo_value_skipped.md")
        assert "pinned value" in note and "weaken a protection" in note and "own settings still applies" in note
    with and_("the refusal for a repo scope says nothing changed and points to the user scope, not the "
              "project or local scope in any of their spellings"):
        refused = read(TEXT, "repo_scope_refused.md")
        assert refused.startswith("Nothing changed.")
        assert "--scope user" in refused and "not --project or --local" in refused
        assert "not a project or local scope" in refused


def test_the_refusals_already_shown_name_the_user_own_words_and_hub_and_the_settings_files_check_names_programs():
    with given("the refusals for a protected flag, a stopped flag and a pin"):
        refusals = {"ASK_THE_USER": protect.ASK_THE_USER, "NO_DOOR": protect.NO_DOOR,
                    "STOPS_ASK_THE_USER": protect.STOPS_ASK_THE_USER, "STOPS_NO_DOOR": protect.STOPS_NO_DOOR,
                    "PINNED_BY_USER": changes.PINNED_BY_USER, "ask_the_user.md": read(TEXT, "ask_the_user.md")}
    for name, message in refusals.items():
        with then(f"{name} names the user's own words and {DOOR}"):
            assert "own words" in message and DOOR in message, name
        with and_(f"{name} has no em dash"):
            assert DASH not in message, name
    with then("the refusals keep the values the code fills in"):
        assert set(VALUE.findall(protect.ASK_THE_USER)) == {"flag"}
        assert set(VALUE.findall(protect.NO_DOOR)) == {"flag"}
        assert set(VALUE.findall(protect.STOPS_ASK_THE_USER)) == {"flag", "stopped"}
        assert set(VALUE.findall(protect.STOPS_NO_DOOR)) == {"flag", "stopped"}
        assert set(VALUE.findall(changes.PINNED_BY_USER)) == {"flag"}
    with when("the handler's words for each check are read"):
        what = handler_what()
    with then("the settings files check also names a program hub can not read or that changes an unasked switch"):
        assert what["settings_files"].startswith("write hub's settings or state files")
        assert "a program that hub can not read" in what["settings_files"]
        assert "a switch the user did not ask for" in what["settings_files"]


def test_the_limits_page_is_linked_from_the_readme_says_it_is_no_sandbox_and_names_each_limit():
    with given("the README's known limits"):
        readme = read(REPO, "README.md")
        limits = readme.split("Known limits:", 1)[1].split("\n## ", 1)[0]
    with then("one of them links the limits page and says what self-protection reads in the page's words"):
        line = next(item for item in limits.split("\n") if "](docs/self-protection-limits.md)" in item)
        assert line == ("- Self-protection reads the chat and the commands Claude writes. It is not a sandbox. "
                        "[Self-protection limits](docs/self-protection-limits.md) names where its checks end.")
    with when("the limits page is read"):
        with open(LIMITS, encoding="utf-8") as handle:
            page = handle.read()
    with then("it says self-protection is not a sandbox and holds no code block, em dash or home folder"):
        assert "It is not a sandbox" in page
        assert "```" not in page and DASH not in page
        assert not re.search(r"~/|/Users/|/home/", page)
    with and_("it has one part for each kind of limit"):
        headings = [line for line in page.split("\n") if line.startswith("## ")]
        assert headings == ["## How hub reads your order", "## What it does not watch", "## Cursor"]
    with and_("it names each design limit as hub has it today"):
        for words in ("Your order is the newest message you typed", "another chat arrives after it",
                      "Try to turn off", "uninstall hub", "hub's settings and state files"):
            assert words in page, words
    with and_("the parts on what it does not watch and on Cursor state design limits only, word for word"):
        sections = {part.split("\n", 1)[0]: part.split("\n", 1)[1].strip() for part in page.split("\n## ")[1:]}
        assert sections["What it does not watch"] == (
            "Self-protection reads the chat and the commands Claude writes. Anything else on your machine "
            "that can change those files is outside its checks. It is not a sandbox.")
        assert sections["Cursor"] == "Cursor support is in beta."
    with and_("it names no planned fix, and claims no fix as in place"):
        assert "plans" not in page and "planned" not in page
        for claim in ("does not end your order", "works even while the lowering check is off",
                      "with the lowering check on or off", "can never change a value you pinned"):
            assert claim not in page, claim
