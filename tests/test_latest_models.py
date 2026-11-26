"""hub runs the newest Claude models unless the user pinned one.

Claude Code agents name a model alias (opus, sonnet, haiku), which Claude
Code moves to the newest release by itself. Cursor needs exact model ids, so
hub keeps one table of them, cursor_link.agent_copies.effort_map. The
scenarios run the real hubctl in a temporary HOME and read the agent files
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
        home.hubctl("set", "scout.model", "sonnet")
    with when("a hub release ships newer Cursor ids and applies"):
        set_map_default(home, ["opus=claude-opus-6", "sonnet=claude-sonnet-6", "haiku=claude-haiku-6"])
        home.hubctl("apply")
    with then("Cursor's copies follow the release, and scout keeps the user's sonnet"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-6[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-sonnet-6")
        assert front_matter(agent_link(home, "scout"))["model"] == "sonnet"
        assert MAP not in home.settings()["flags"]
    with when("the user pins Opus 5.5 for Cursor"):
        pinned = ["opus=claude-opus-5-5", "sonnet=claude-sonnet-5-5", "haiku=claude-haiku-5-5"]
        home.hubctl("set", MAP, *pinned)
    with then("the tuner can not set the table back"):
        home.set_by("tuner", MAP, ["opus=claude-opus-6", "sonnet=claude-sonnet-6", "haiku=claude-haiku-6"],
                    check=False)
        assert home.settings()["flags"][MAP] == {"by": "user", "value": pinned}
    with and_("the next release ships even newer ids"):
        set_map_default(home, ["opus=claude-opus-7", "sonnet=claude-sonnet-7", "haiku=claude-haiku-7"])
        home.hubctl("apply")
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
        home.hubctl("set", MAP, "opus=claude-opus-5")
        home.hubctl("apply")
    with then("builder takes the pinned Opus, and scout takes the newest Haiku, not a bare alias"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-5[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["model"].startswith("claude-haiku-5-5")
        assert home.settings()["flags"][MAP] == {"by": "user", "value": ["opus=claude-opus-5"]}
