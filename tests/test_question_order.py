"""One dialog meets the question parts in a fixed order, and Cursor runs none of this slice's parts."""
import json
import os

import pytest

from chats import SESSIONS, Chats
from dialogs import SESSION_LOBBY, ask, cursor_send, prompt, question, set_clock, state_lines
from steps import and_, given, then, when

SLICE_PARTS = ["question_check", "away_guard", "knowledge_only", "chrome_lock", "slack_write_gate",
               "sentry_write_gate"]
BARE = [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def away(home):
    set_clock(home, "2026-03-02T08:00:00Z")
    prompt(home, "Add the layout test.")
    set_clock(home, "2026-03-02T09:01:00Z")


def test_question_check_refuses_a_thin_dialog_before_away_guard_holds_or_logs_it(home):
    with given("question_check and away_guard are on, and Alex is away"):
        home.install()
        home.hubctl("on", "question_check")
        home.hubctl("on", "away_guard")
        away(home)
    with when("LOBBY opens an urgent dialog without context"):
        result = ask(home, [question("Ship it?", BARE)], session=SESSION_LOBBY,
                     metadata={"source": "urgent: the release breaks at 09:00"})
    with then("question_check refuses it, and away_guard neither holds nor logs it"):
        assert result.refused and "question_check" in result.reason
        assert "urgent: <why>" not in result.reason
        assert state_lines(home, "away_urgent.jsonl") == []
    with when("LOBBY opens a thin dialog with no urgent mark"):
        result = ask(home, [question("Ship it?", BARE)], session=SESSION_LOBBY)
    with then("question_check's reason comes, not the hold"):
        assert result.refused and "question_check" in result.reason and "urgent: <why>" not in result.reason
    with when("LOBBY sends the full dialog"):
        result = ask(home, [question()], session=SESSION_LOBBY)
    with then("now away_guard holds it"):
        assert result.refused and "urgent: <why>" in result.reason


def test_the_coordinators_refusal_comes_before_the_question_parts(home, chats):
    with given("the coordinator named LOBBY, question_check and away_guard are on, LOBBY is live, and Alex is away"):
        home.install()
        for part in ("coordinator", "question_check", "away_guard"):
            home.hubctl("on", part)
        home.hubctl("set", "coordinator.name", "LOBBY")
        lobby = chats.start("LOBBY", title="LOBBY", live=True, inbox=True)
        chats.start("WEB", title="WEB")
        chats.set_listing([chats.row("LOBBY", pid=lobby.pid), chats.row("WEB", pid=os.getpid())])
        away(home)
    with when("WEB opens a dialog without context while LOBBY is live"):
        result = ask(home, [question("Ship it?", BARE)], session=SESSIONS["WEB"])
    with then("the coordinator's refusal comes first, and the other two parts do not run"):
        assert result.refused and "coordinator" in result.reason
        assert "question_check" not in result.reason and "urgent: <why>" not in result.reason


def test_in_cursor_the_questions_family_and_these_gates_do_not_run_and_the_doctor_says_why(home):
    with given("every part of this slice is on, and the Cursor link runs"):
        home.install()
        for part in SLICE_PARTS + ["cursor_link"]:
            home.hubctl("on", part)
        away(home)
    with when("Cursor sends its hook input for the same events"):
        typed = cursor_send(home, "beforeSubmitPrompt", "beforeSubmitPrompt.json",
                            prompt="fyi https://example.com/notes")
        calls = [cursor_send(home, "preToolUse", "preToolUse.Shell.json", tool_name=name, tool_input=tool_input)
                 for name, tool_input in [("AskUserQuestion", {"questions": [question("Ok?", BARE)]}),
                                          ("mcp__claude-in-chrome__navigate", {"url": "https://example.com"}),
                                          ("mcp__slack__reactions_add", {}),
                                          ("mcp__sentry__update_issue", {}),
                                          ("Shell", {"command": "hubctl browser-lock clear"})]]
    with then("nothing is refused, asked or added"):
        assert typed.output == {}
        assert [call.output for call in calls] == [{}] * len(calls)
    with when("the user runs hubctl doctor"):
        text = home.hubctl("doctor").stdout
        report = json.loads(home.hubctl("doctor", "--json").stdout)
    with then("it names each part with its reason from parts.md"):
        gaps = {gap["flag"]: gap["reason"] for gap in report["cursor_gaps"]}
        for part in SLICE_PARTS:
            assert gaps.get(part), part
            assert f"  {part}: {gaps[part]}" in text
        assert "no question tool" in gaps["question_check"]
        assert "does not enforce ask" in gaps["sentry_write_gate"]
    with and_("it shows the tested server version of each experimental gate"):
        assert "slack_write_gate: the read list comes from slack-mcp-server 1.3.0" in text
        assert "sentry_write_gate: the read list comes from @sentry/mcp-server 0.42.0" in text
