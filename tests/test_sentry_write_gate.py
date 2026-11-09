"""sentry_write_gate: the Sentry gate asks before any write, also inside execute_sentry_tool."""
import json
import os

from dialogs import decision, tool_call
from steps import and_, given, then, when

CONFLICT = '<<<<<<< HEAD\n{"version": 1, "flags": {}}\n=======\n{"version": 1}\n>>>>>>> other\n'
PATTERN = "sentry_write_gate.server_pattern"


def reason(result):
    return result.output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def hand_edit_pattern(home, pattern):
    """Write the pattern into the user file as a person editing it would, past hubctl's check."""
    data = home.settings()
    data.setdefault("flags", {})[PATTERN] = {"by": "user", "value": pattern}
    home.write_json(home.user_file, data)


def test_the_sentry_gate_asks_before_any_write_also_inside_execute_sentry_tool(home):
    with given("sentry_write_gate is on"):
        home.install()
        home.hubctl("on", "sentry_write_gate")

    with when("a chat looks up issues, on either server name"):
        local = tool_call(home, "mcp__sentry__search_issues", {"query": "is:unresolved"})
        hosted = tool_call(home, "mcp__claude_ai_Sentry__get_issue_details", {"issueId": "WEB-1"})
        wrapped_read = tool_call(home, "mcp__sentry__execute_sentry_tool", {"name": "find_projects"})
    with then("the calls run quietly"):
        for result in (local, hosted, wrapped_read):
            assert decision(result) is None and not result.context

    with when("a chat resolves an issue, calls execute_sentry_tool with a write tool inside, or calls an unknown tool"):
        calls = {"resolve": tool_call(home, "mcp__sentry__update_issue", {"status": "resolved"}),
                 "wrapped": tool_call(home, "mcp__sentry__execute_sentry_tool", {"name": "update_issue"}),
                 "empty": tool_call(home, "mcp__sentry__execute_sentry_tool", {}),
                 "unknown": tool_call(home, "mcp__sentry__brand_new_tool", {})}
    with then("each call asks the user first, and the reason names the tool"):
        for name, result in calls.items():
            assert decision(result) == "ask", name
        assert "update_issue" in calls["resolve"].output["hookSpecificOutput"]["permissionDecisionReason"]
        assert "update_issue" in calls["wrapped"].output["hookSpecificOutput"]["permissionDecisionReason"]

    with when("the user turns sentry_write_gate.read_tools off"):
        home.hubctl("off", "sentry_write_gate.read_tools")
        result = tool_call(home, "mcp__sentry__search_issues", {"query": "is:unresolved"})
    with then("reads ask too"):
        assert decision(result) == "ask"
    with and_("a tool from another server is not touched"):
        assert decision(tool_call(home, "mcp__docs__search", {"query": "x"})) is None


def test_the_sentry_gate_asks_before_writes_when_hub_can_not_read_its_settings(home):
    with given("sentry_write_gate is on, and then the user settings file gets merge conflict markers"):
        home.install()
        home.hubctl("on", "sentry_write_gate")
        with open(home.user_file, "w") as handle:
            handle.write(CONFLICT)

    with when("a chat resolves an issue, directly and through execute_sentry_tool"):
        calls = [tool_call(home, "mcp__sentry__update_issue", {"status": "resolved"}, errors_expected=True),
                 tool_call(home, "mcp__sentry__execute_sentry_tool", {"name": "update_issue"}, errors_expected=True)]
    with then("each asks the user first, and the reason names the settings file"):
        for result in calls:
            assert decision(result) == "ask"
            assert "could not read its settings" in reason(result) and "hub.json" in reason(result)
    with and_("a read of the tested list and a tool from another server still run quietly"):
        assert decision(tool_call(home, "mcp__sentry__search_issues", {"query": "x"}, errors_expected=True)) is None
        assert decision(tool_call(home, "mcp__docs__search", {"query": "x"}, errors_expected=True)) is None


def test_the_sentry_gate_asks_before_every_call_when_its_read_list_is_missing_or_broken(home):
    read_list = os.path.join(home.plugin, "parts", "sentry_write_gate", "read_tools.json")
    with given("sentry_write_gate is on, and its read_tools.json is gone"):
        home.install()
        home.hubctl("on", "sentry_write_gate")
        os.remove(read_list)

    with when("a chat looks up issues and resolves one"):
        calls = [tool_call(home, "mcp__sentry__search_issues", {"query": "x"}),
                 tool_call(home, "mcp__sentry__update_issue", {"status": "resolved"})]
    with then("both ask the user first, because hub can not tell a read from a write, and the reason says so"):
        for result in calls:
            assert decision(result) == "ask"
            assert "read_tools.json" in reason(result)
    with and_("the doctor names the missing list"):
        doctor = home.hubctl("doctor").stdout
        assert "warning: sentry_write_gate:" in doctor and "read_tools.json" in doctor

    with when("the file is back but holds broken JSON"):
        with open(read_list, "w") as handle:
            handle.write("{broken")
        result = tool_call(home, "mcp__sentry__search_issues", {"query": "x"})
    with then("the read asks too"):
        assert decision(result) == "ask" and "read_tools.json" in reason(result)


def test_hubctl_refuses_a_broken_sentry_pattern_and_a_hand_edited_one_still_gates_sentry(home):
    with given("sentry_write_gate is on with its default pattern"):
        home.install()
        home.hubctl("on", "sentry_write_gate")
        before = home.settings()

    with when("the user sets a pattern that is not valid, and an empty one"):
        broken = home.hubctl("set", PATTERN, "mcp__sentry__[", check=False)
        empty = home.hubctl("set", PATTERN, "", check=False)
    with then("hubctl refuses both, says why, and the settings stay as they were"):
        assert broken.returncode == 1 and "not a valid pattern" in broken.stderr
        assert empty.returncode == 1 and "empty" in empty.stderr
        assert home.settings() == before

    for pattern in ("mcp__sentry__[", ""):
        with when(f"the settings file is edited by hand to the pattern {json.dumps(pattern)}"):
            hand_edit_pattern(home, pattern)
            local = tool_call(home, "mcp__sentry__update_issue", {"status": "resolved"})
            hosted = tool_call(home, "mcp__claude_ai_Sentry__update_issue", {"status": "resolved"})
        with then("Sentry writes on either server name still ask, and reads still run quietly"):
            assert decision(local) == "ask" and decision(hosted) == "ask"
            assert decision(tool_call(home, "mcp__sentry__search_issues", {"query": "x"})) is None
        with and_("a tool from another server is not touched, and the doctor names the broken pattern"):
            assert decision(tool_call(home, "mcp__docs__search", {"query": "x"})) is None
            doctor = home.hubctl("doctor").stdout
            assert "warning: sentry_write_gate:" in doctor and "server_pattern" in doctor
