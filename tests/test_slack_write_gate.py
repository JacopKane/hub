"""slack_write_gate: Slack tools can not write anything other people see."""
import json
import os

from dialogs import decision, tool_call
from steps import and_, given, then, when

CONFLICT = '<<<<<<< HEAD\n{"version": 1, "flags": {}}\n=======\n{"version": 1}\n>>>>>>> other\n'
PATTERN = "slack_write_gate.server_pattern"


def hand_edit_pattern(home, pattern):
    """Write the pattern into the user file as a person editing it would, past hubctl's check."""
    data = home.settings()
    data.setdefault("flags", {})[PATTERN] = {"by": "user", "value": pattern}
    home.write_json(home.user_file, data)


def test_the_slack_gate_lets_only_read_tools_pass(home):
    with given("slack_write_gate is on, with the read list of the tested version"):
        home.install()
        home.hubctl("on", "slack_write_gate")

    with when("a chat reads a channel's history"):
        result = tool_call(home, "mcp__slack__conversations_history", {"channel_id": "C0000LOBBY"})
    with then("the call runs"):
        assert result.ran and decision(result) is None

    with when("a chat posts, reacts, calls a tool that is not on the list, or sends broken input"):
        calls = {"post": tool_call(home, "mcp__slack__conversations_add_message", {"channel_id": "C0000LOBBY"}),
                 "react": tool_call(home, "mcp__slack__reactions_add", {"name": "eyes"}),
                 "unknown": tool_call(home, "mcp__slack__channels_archive", {}),
                 "broken": tool_call(home, "mcp__slack__", "not an object")}
    with then("each call is denied, and the reason names the tool and the reads that pass"):
        for name, result in calls.items():
            assert result.refused, name
            assert "read-only" in result.reason and "conversations_history" in result.reason
        assert "conversations_add_message" in calls["post"].reason
        assert "slack_write_gate" in calls["post"].reason
    with and_("a tool from another server is not touched"):
        assert tool_call(home, "mcp__docs__search", {"query": "x"}).ran

    with when("the user moves the server pattern to the name their Slack server has"):
        home.hubctl("set", "slack_write_gate.server_pattern", "mcp__claude_ai_Slack__.*")
        renamed = tool_call(home, "mcp__claude_ai_Slack__slack_send_message", {})
    with then("that server is gated too, and the old name is not"):
        assert renamed.refused
        assert tool_call(home, "mcp__slack__reactions_add", {}).ran


def test_the_slack_gate_refuses_writes_when_hub_can_not_read_its_settings(home):
    with given("slack_write_gate is on, and then the user settings file gets merge conflict markers"):
        home.install()
        home.hubctl("on", "slack_write_gate")
        with open(home.user_file, "w") as handle:
            handle.write(CONFLICT)

    with when("a chat posts to a channel"):
        post = tool_call(home, "mcp__slack__conversations_add_message", {"channel_id": "C0000LOBBY"},
                         errors_expected=True)
    with then("the post is refused, and the reason names the settings file"):
        assert post.refused
        assert "could not read its settings" in post.reason and "hub.json" in post.reason
    with and_("a read of the tested list and a tool from another server still run"):
        assert tool_call(home, "mcp__slack__conversations_history", {"channel_id": "C0000LOBBY"},
                         errors_expected=True).ran
        assert decision(tool_call(home, "mcp__docs__search", {"query": "x"}, errors_expected=True)) is None


def test_the_slack_gate_refuses_every_call_when_its_read_list_is_missing_or_broken(home):
    read_list = os.path.join(home.plugin, "parts", "slack_write_gate", "read_tools.json")
    with given("slack_write_gate is on, and its read_tools.json is gone"):
        home.install()
        home.hubctl("on", "slack_write_gate")
        os.remove(read_list)

    with when("a chat reads a channel's history and posts to it"):
        read = tool_call(home, "mcp__slack__conversations_history", {"channel_id": "C0000LOBBY"})
        post = tool_call(home, "mcp__slack__conversations_add_message", {"channel_id": "C0000LOBBY"})
    with then("both are refused, because hub can not tell a read from a write, and the reason says so"):
        for result in (read, post):
            assert result.refused
            assert "read_tools.json" in result.reason
    with and_("the doctor names the missing list"):
        doctor = home.hubctl("doctor").stdout
        assert "warning: slack_write_gate:" in doctor and "read_tools.json" in doctor

    with when("the file is back but holds broken JSON"):
        with open(read_list, "w") as handle:
            handle.write("{broken")
        read = tool_call(home, "mcp__slack__conversations_history", {"channel_id": "C0000LOBBY"})
    with then("the read is refused too"):
        assert read.refused and "read_tools.json" in read.reason


def test_hubctl_refuses_a_broken_slack_pattern_and_a_hand_edited_one_still_gates_slack(home):
    with given("slack_write_gate is on with its default pattern"):
        home.install()
        home.hubctl("on", "slack_write_gate")
        before = home.settings()

    with when("the user sets a pattern that is not valid, and an empty one"):
        broken = home.hubctl("set", PATTERN, "mcp__slack__(", check=False)
        empty = home.hubctl("set", PATTERN, "", check=False)
    with then("hubctl refuses both, says why, and the settings stay as they were"):
        assert broken.returncode == 1 and "not a valid pattern" in broken.stderr
        assert empty.returncode == 1 and "empty" in empty.stderr
        assert home.settings() == before

    for pattern in ("mcp__slack__(", ""):
        with when(f"the settings file is edited by hand to the pattern {json.dumps(pattern)}"):
            hand_edit_pattern(home, pattern)
            post = tool_call(home, "mcp__slack__conversations_add_message", {"channel_id": "C0000LOBBY"})
            hosted = tool_call(home, "mcp__claude_ai_Slack__slack_send_message", {})
        with then("Slack posts on either server name are still refused, and reads still run"):
            assert post.refused and hosted.refused
            assert tool_call(home, "mcp__slack__conversations_history", {"channel_id": "C0000LOBBY"}).ran
        with and_("a tool from another server is not touched, and the doctor names the broken pattern"):
            assert tool_call(home, "mcp__docs__search", {"query": "x"}).ran
            doctor = home.hubctl("doctor").stdout
            assert "warning: slack_write_gate:" in doctor and "server_pattern" in doctor


def test_a_manifest_that_misuses_the_pattern_check_is_named_before_anything_runs(home):
    def child(data):
        return next(node for node in data["children"] if node["id"] == "server_pattern")

    with given("hub is installed"):
        home.install()
    with when("the Slack gate's manifest ships a default pattern that is not valid"):
        home.edit_manifest("slack_write_gate", lambda data: child(data).update(default="mcp__slack__("))
        broken = home.hubctl("apply", check=False)
    with then("apply exits 1 and names the option and the reason"):
        assert broken.returncode == 1
        assert "slack_write_gate.server_pattern" in broken.stderr and "not a valid pattern" in broken.stderr
    with when("the manifest puts the pattern check on an option that is not a text"):
        home.edit_manifest("slack_write_gate", lambda data: child(data).update(type="number", default=1))
        misused = home.hubctl("apply", check=False)
    with then("apply exits 1 and says the check needs a text"):
        assert misused.returncode == 1 and "check must be" in misused.stderr
