"""hub runs the newest Claude models unless the user pinned one.

Claude Code agents name a model alias (opus, sonnet, haiku), which Claude
Code moves to the newest release by itself. Cursor needs exact model ids, so
hub keeps one table of them, cursor_link.agent_copies.effort_map. The
scenarios run the real step in a temporary HOME and read the agent files
it writes to ~/.claude and ~/.cursor.
"""
import glob
import json
import os
import re
import sys

from conftest import PLUGIN
from steps import and_, given, then, when
from test_start_text_and_links import agent_link, cursor_agent, front_matter, use_cursor_beta

ALIASES = {"haiku", "sonnet", "opus"}
FIXED_ID = re.compile(r"claude-(?:opus|sonnet|haiku|fable)-\d")
MAP = "cursor_link.agent_copies.effort_map"


def shipped_manifests():
    for path in sorted(glob.glob(os.path.join(PLUGIN, "parts", "*", "part.json"))):
        with open(path) as handle:
            yield path, json.load(handle)


def nodes(node):
    yield node
    for child in node.get("children", []):
        yield from nodes(child)


def shipped_map():
    with open(os.path.join(PLUGIN, "parts", "cursor_link", "part.json")) as handle:
        part = json.load(handle)
    node = next(node for node in nodes(part) if node.get("id") == "effort_map")
    return dict(item.split("=", 1) for item in node["default"])


def newest_in_price_table():
    """The newest version of each family that the task list's price table knows, as a Claude model id."""
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    from hub import tasks
    newest = {alias: max(version for family, version, _price in tasks.PRICES if family == alias) for alias in ALIASES}
    return {alias: "-".join(["claude", alias, *map(str, version)]) for alias, version in newest.items()}


def set_map_default(home, pairs):
    """What a hub release that ships new Cursor ids changes: the map's default in the installed copy."""
    def change(part):
        next(node for node in nodes(part) if node.get("id") == "effort_map")["default"] = pairs
    home.edit_manifest("cursor_link", change)


def test_shipped_agents_name_aliases_and_cursor_copies_name_the_newest_ids(home):
    with given("every part hub ships"):
        manifests = list(shipped_manifests())
        agent_files = sorted(glob.glob(os.path.join(PLUGIN, "parts", "*", "agent.md")))
        assert manifests and agent_files
    with then("each agent file and each model option names an alias, never a fixed model id"):
        for path in agent_files:
            assert front_matter(path)["model"] in ALIASES, path
        for path, part in manifests:
            for node in nodes(part):
                if node.get("id") == "model" and node.get("kind") == "option":
                    assert node["default"] in ALIASES and set(node["values"]) <= ALIASES, path
    with and_("the only fixed model ids hub ships are in Cursor's one table"):
        for path, part in manifests:
            if path.endswith(os.path.join("cursor_link", "part.json")):
                continue
            assert not FIXED_ID.search(json.dumps(part)), path
    with and_("that table names the newest model of each family the price table knows"):
        assert shipped_map() == newest_in_price_table()
        assert shipped_map() == {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
    with when("the user turns on the Cursor beta and hub installs"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.switch_on("builder")
        home.install()
    with then("Claude Code's agent names the alias and Cursor's copy names the newest id"):
        assert front_matter(agent_link(home, "builder"))["model"] == "opus"
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-5-5[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-haiku-5-5")


def test_a_model_the_user_pinned_survives_updates_and_the_rest_follows_the_new_release(home):
    with given("hub installed with the Cursor beta and builder on, and the user moved scout to sonnet"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.switch_on("builder")
        home.install()
        home.step("set", "scout.model", "sonnet")
    with when("a hub release ships newer Cursor ids and applies"):
        set_map_default(home, ["opus=claude-opus-6", "sonnet=claude-sonnet-6", "haiku=claude-haiku-6"])
        home.step("apply")
    with then("Cursor's copies follow the release, and scout keeps the user's sonnet"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-6[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-sonnet-6")
        assert front_matter(agent_link(home, "scout"))["model"] == "sonnet"
        assert MAP not in home.settings()["flags"]
    with when("the user pins Opus 5.5 for Cursor"):
        pinned = ["opus=claude-opus-5-5", "sonnet=claude-sonnet-5-5", "haiku=claude-haiku-5-5"]
        home.step("set", MAP, *pinned)
    with then("the tuner can not set the table back"):
        home.set_by("tuner", MAP, ["opus=claude-opus-6", "sonnet=claude-sonnet-6", "haiku=claude-haiku-6"],
                    check=False)
        assert home.settings()["flags"][MAP] == {"by": "user", "value": pinned}
    with and_("the next release ships even newer ids"):
        set_map_default(home, ["opus=claude-opus-7", "sonnet=claude-sonnet-7", "haiku=claude-haiku-7"])
        home.step("apply")
    with then("the user's pin stays in the settings file and in every Cursor copy"):
        assert home.settings()["flags"][MAP] == {"by": "user", "value": pinned}
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-5-5[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-sonnet-5-5")
        assert front_matter(agent_link(home, "scout"))["model"] == "sonnet"


def test_a_table_the_user_pinned_in_part_takes_the_newest_ids_for_what_it_leaves_out(home):
    with given("hub installed with the Cursor beta and builder on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.switch_on("builder")
        home.install()
    with when("the user pins only Opus for Cursor"):
        home.step("set", MAP, "opus=claude-opus-5")
        home.step("apply")
    with then("builder takes the pinned Opus, and scout takes the newest Haiku, not a bare alias"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-5[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-haiku-5-5")
        assert home.settings()["flags"][MAP] == {"by": "user", "value": ["opus=claude-opus-5"]}


def set_model_default(home, part, alias):
    """What a hub release that moves an agent to another model changes: that agent's model default."""
    def change(manifest):
        next(node for node in nodes(manifest) if node.get("id") == "model")["default"] = alias
    home.edit_manifest(part, change)


TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1"}


def as_claude(home):
    """The next step runs come from a command Claude runs in a tool call."""
    home.env = dict(home.env, **TOOL_CALL)


def as_user(home):
    """The next step runs come from the user's own terminal."""
    home.env = {name: value for name, value in home.env.items() if name not in TOOL_CALL}


def test_a_version_pinned_for_the_user_stays_when_it_is_todays_default(home):
    with given("hub installed with the Cursor beta and builder on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.switch_on("builder")
        home.install()
    with when("the user asks Claude to keep scout on haiku, which is its default, and Claude runs the step"):
        as_claude(home)
        home.step("set", "scout.model", "haiku")
    with then("the choice is in the settings file, though it equals the default"):
        assert home.settings()["flags"]["scout.model"] == {"by": "model", "value": "haiku"}
    with when("Claude tries to set Cursor's table, which belongs to a beta part"):
        refused = home.step("set", MAP, "opus=claude-opus-5-5", check=False)
    with then("it is refused, so only the user sets that table"):
        assert refused.returncode == 1 and "only the user" in refused.stderr
        assert MAP not in home.settings()["flags"]
    with when("the user pins Cursor's table to today's default from their own terminal"):
        as_user(home)
        today = ["opus=claude-opus-5-5", "sonnet=claude-sonnet-5-5", "haiku=claude-haiku-5-5"]
        home.step("set", MAP, *today)
    with then("the table is stored as the user's pin"):
        assert home.settings()["flags"][MAP] == {"by": "user", "value": today}
    with when("a release ships newer Cursor ids and moves scout to sonnet"):
        set_map_default(home, ["opus=claude-opus-6", "sonnet=claude-sonnet-6", "haiku=claude-haiku-6"])
        set_model_default(home, "scout", "sonnet")
        home.step("apply")
    with then("the user keeps the pinned ids, and scout keeps haiku in both tools"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-5-5[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-haiku-5-5")
        assert front_matter(agent_link(home, "scout"))["model"] == "haiku"
    with when("Claude moves builder to sonnet, which is not its default, and the user pins only Opus in the table"):
        as_claude(home)
        home.step("set", "builder.model", "sonnet")
        as_user(home)
        home.step("set", MAP, "opus=claude-opus-5-5")
    with then("builder runs sonnet, and the families the table leaves out take the release's ids"):
        assert home.settings()["flags"]["builder.model"] == {"by": "model", "value": "sonnet"}
        assert front_matter(agent_link(home, "builder"))["model"] == "sonnet"
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-sonnet-6[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-haiku-6")
    with when("the user clears the pins from the settings file and the next release ships"):
        data = home.settings()
        for flag in (MAP, "scout.model", "builder.model"):
            data["flags"].pop(flag)
        home.write_json(home.user_file, data)
        set_map_default(home, ["opus=claude-opus-7", "sonnet=claude-sonnet-7", "haiku=claude-haiku-7"])
        home.step("apply")
    with then("every agent follows the newest release again"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-7[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-sonnet-7")
        assert front_matter(agent_link(home, "scout"))["model"] == "sonnet"


def test_setup_and_the_tuner_still_leave_a_default_out_of_the_file(home):
    with given("hub is installed"):
        home.install()
    with when("setup and the tuner write scout's default model, and Claude writes another flag's default"):
        home.set_by("setup", "scout.model", "haiku")
        home.set_by("tuner", "builder.model", "opus")
        as_claude(home)
        home.step("on", "demo_gate.unreadable")
    with then("nothing is stored, so each follows the next release"):
        flags = home.settings().get("flags", {})
        assert not {"scout.model", "builder.model", "demo_gate.unreadable"} & set(flags)
