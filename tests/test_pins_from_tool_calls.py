"""A value the user set by hand (a pin) changes only from the user's own doors.

Claude Code starts a tool's command with AI_AGENT ending in "_agent", and a
hook with AI_AGENT ending in "_harness" (seen in Claude Code 2.1.319). hubctl
treats the first, and CURSOR_AGENT=1, as a call the model made.
"""

import pytest

from steps import and_, given, then, when

TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1"}
HOOK = {"AI_AGENT": "claude-code_2-1-319_harness", "CLAUDECODE": "1"}
CURSOR_TOOL_CALL = {"CURSOR_AGENT": "1"}


def as_caller(home, extra):
    home.env = dict(home.env, **extra)


@pytest.mark.parametrize("caller", [TOOL_CALL, CURSOR_TOOL_CALL], ids=["claude_code", "cursor"])
def test_a_pinned_flag_does_not_change_from_a_command_the_model_runs(home, caller):
    with given("the user pinned public_text_gate.dashes off from their own terminal"):
        home.install()
        home.hubctl("off", "public_text_gate.dashes")
        before = home.settings()
    with when("the model runs hubctl on for it inside a tool call"):
        as_caller(home, caller)
        done = home.hubctl("on", "public_text_gate.dashes", check=False)
    with then("hubctl refuses and says only the user changes it, with /hub in Claude Code"):
        assert done.returncode == 1
        assert "only the user" in done.stderr.lower()
        assert "/hub in Claude Code" in done.stderr and "terminal" not in done.stderr
    and_("the settings file is unchanged")
    assert home.settings() == before
    with when("the model tries set, undo and pin"):
        set_done = home.hubctl("set", "public_text_gate.dashes", "on", check=False)
        undo_done = home.hubctl("undo", check=False)
        pin_done = home.hubctl("pin", "public_text_gate.private_paths", check=False)
    with then("each is refused and nothing changed"):
        assert [set_done.returncode, undo_done.returncode, pin_done.returncode] == [1, 1, 1]
        assert home.settings() == before


def test_a_flag_nobody_pinned_still_changes_from_a_tool_call_so_the_cursor_skill_works(home):
    with given("hub is installed and public_text_gate.dashes holds its default"):
        home.install()
    with when("the model runs hubctl off for it inside a tool call"):
        as_caller(home, TOOL_CALL)
        done = home.hubctl("off", "public_text_gate.dashes", check=False)
    with then("the change lands"):
        assert done.returncode == 0, done.stderr
        assert home.settings()["flags"]["public_text_gate.dashes"]["value"] == "off"


def test_a_hook_process_is_not_a_tool_call(home):
    with given("the user pinned public_text_gate.dashes off"):
        home.install()
        home.hubctl("off", "public_text_gate.dashes")
    with when("hubctl runs with a hook's environment"):
        as_caller(home, HOOK)
        done = home.hubctl("on", "public_text_gate.dashes", check=False)
    with then("the change lands"):
        assert done.returncode == 0, done.stderr


def test_the_clock_reads_hub_test_now(home):
    with given("HUB_TEST_NOW holds a fixed time"):
        home.install()
        home.env["HUB_TEST_NOW"] = "2026-03-04T05:06:07Z"
    with when("a change is made"):
        home.hubctl("off", "public_text_gate.dashes")
    with then("its history line carries that time"):
        line = home.history()[-1]
        assert (line["flag"], line["time"]) == ("public_text_gate.dashes", "2026-03-04T05:06:07Z")
