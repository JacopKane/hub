"""Cursor support is a beta group, off by default.

Every part and flag that runs only for Cursor is marked beta. It stays off
until the user turns it on through a door, even when the profile lists
Cursor, and while it is off hub writes nothing for Cursor. Each door names the
group "Beta, untested".
"""
import json
import os
import sys

from conftest import PLUGIN
from steps import and_, given, then, when
from test_start_text_and_links import cursor_agent, session_start, write_profile

BETA_NOTE = "cursor_link is in beta and untested. Type /hub off cursor_link to turn it off."
# How Claude Code marks a command the model runs in a tool call.
TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1"}


# helpers

def cursor_folder(home):
    return os.path.join(home.root, ".cursor", "plugins", "local", "hub")


def cursor_slots(home):
    return os.path.join(home.state, "slots", "cursor")


def cursor_links(home):
    cursor = os.path.join(home.root, ".cursor") + os.sep
    return [entry for entry in home.state_file("links.json")["links"]
            if entry["path"].startswith(cursor) or entry.get("kind", "").startswith("cursor")]


def cursor_cost(home):
    return home.state_file("cost.json")["cursor"]


def nothing_for_cursor(home):
    assert not os.path.lexists(cursor_folder(home))
    assert not os.path.lexists(cursor_agent(home, "builder")) and not os.path.lexists(cursor_agent(home, "scout"))
    assert not os.path.lexists(cursor_slots(home))
    assert cursor_links(home) == []
    assert cursor_cost(home) == {"parts": {}, "total": {"start_text": 0}}
    assert session_start(home, "cursor")[0] == ""


def install_with_cursor_in_profile(home):
    os.makedirs(home.config, exist_ok=True)
    write_profile(home, {"tools": {"cursor": True}})
    home.install()


def load_tree():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    from hub import tree
    return tree, tree.load(os.path.join(PLUGIN, "parts"))


# scenarios

def test_cursor_stays_off_and_unlinked_when_the_profile_lists_cursor(home):
    with given("a profile that lists Cursor"):
        os.makedirs(home.config)
        write_profile(home, {"tools": {"cursor": True}})
    with when("hub is installed and apply runs"):
        home.install()
    with then("cursor_link is off by its default"):
        record = home.show("--all")["flags"]["cursor_link"]
        assert (record["value"], record["runs"], record["by"]) == ("off", False, "default")
    with and_("hub wrote no Cursor plugin, no agent copies, no Cursor slots and no Cursor links, at no cost"):
        nothing_for_cursor(home)


def test_every_flag_that_runs_only_in_cursor_is_beta_and_defaults_off():
    with given("the shipped tree"):
        tree, parts = load_tree()
        only_cursor = [node for node in parts.nodes.values() if node.runs_in == ["cursor"]]
        beta_parts = [part for part in parts.parts if part.stage == "beta"]
    with then("each flag that runs only in Cursor is in the beta group"):
        assert only_cursor and all(node.stage == "beta" for node in only_cursor)
    with and_("each beta part defaults off for a profile that lists Cursor, and is never offered"):
        sys.path.insert(0, os.path.join(PLUGIN, "lib"))
        from hub import resolve
        facts = resolve.facts_for("cursor", {"tools": {"cursor": True}})
        assert [part.id for part in beta_parts] == ["cursor_link"]
        for part in beta_parts:
            assert resolve.default_value(part, {"tools": {"cursor": True}}, facts) == "off"
            assert "offer_when" not in part.data
    with and_("the label names the group"):
        assert tree.BETA_LABEL == "Beta, untested"


def test_a_beta_flag_that_could_default_on_fails_the_tree_check(home):
    parts = os.path.join(home.plugin, "parts")
    with given("hub is installed, then five fixture parts are added"):
        home.install()
        home.write_json(os.path.join(parts, "beta_on", "part.json"), {
            "id": "beta_on", "family": "tools", "kind": "extra", "stage": "beta", "default": "on", "does": "x"})
        home.write_json(os.path.join(parts, "beta_maybe", "part.json"), {
            "id": "beta_maybe", "family": "tools", "kind": "extra", "stage": "beta",
            "default": "on if profile.tools.cursor else off", "does": "x"})
        home.write_json(os.path.join(parts, "beta_child", "part.json"), {
            "id": "beta_child", "family": "tools", "kind": "extra", "stage": "beta", "default": "off",
            "does": "x",
            "children": [{"id": "trial", "stage": "beta", "does": "x"},
                         {"id": "fine", "stage": "beta", "default": "off", "does": "x"}]})
        home.write_json(os.path.join(parts, "cursor_only", "part.json"), {
            "id": "cursor_only", "family": "tools", "kind": "extra", "runs_in": ["cursor"], "does": "x"})
        home.write_json(os.path.join(parts, "odd_stage", "part.json"), {
            "id": "odd_stage", "family": "tools", "kind": "extra", "stage": "alpha", "does": "x"})
    with when("the tree loads"):
        check = home.hubctl("apply", check=False)
    with then("each bad one fails with its file and id"):
        assert check.returncode == 1
        lines = check.stderr.splitlines()

        def named(flag_id, words):
            return [line for line in lines if f"{flag_id}:" in line and words in line]

        assert named("beta_on", "a beta flag must default off")
        assert named("beta_maybe", "a beta flag must default off")
        assert named("beta_child.trial", "a beta flag must default off")
        assert named("cursor_only", "runs only in Cursor, so it must be beta")
        assert named("odd_stage", "stage must be one of ['beta']")
    with and_("a beta child that defaults off passes"):
        assert not [line for line in lines if "beta_child.fine" in line]


def test_a_beta_part_that_is_offered_fails_the_tree_check(home):
    with given("hub is installed, and a beta part with an offer_when"):
        home.install()
        home.write_json(os.path.join(home.plugin, "parts", "beta_offer", "part.json"), {
            "id": "beta_offer", "family": "tools", "kind": "extra", "stage": "beta", "does": "x",
            "offer_when": "profile.tools.cursor"})
    with when("the tree loads"):
        check = home.hubctl("apply", check=False)
    with then("it fails, because setup and the tuner never push a beta part"):
        assert check.returncode == 1
        assert "beta_offer: a beta part is never offered" in check.stderr


def test_every_door_shows_the_beta_group_with_its_label(home):
    with given("hub is installed with Cursor in the profile"):
        install_with_cursor_in_profile(home)
    with when("hubctl screen --json runs"):
        model = json.loads(home.hubctl("screen", "--json").stdout)
    with then("the beta group comes last, with its label, and holds cursor_link only"):
        beta = model["families"][-1]
        assert {key: beta[key] for key in ("family", "label", "parts_on", "parts_total")} == {
            "family": "beta", "label": "Beta, untested", "parts_on": 0, "parts_total": 1}
        assert [row["id"] for row in beta["parts"]] == ["cursor_link"]
        assert beta["parts"][0]["stage"] == "beta"
    with and_("no other family holds cursor_link, and every other row has no stage"):
        others = [row for family in model["families"][:-1] for row in family["parts"]]
        assert others and "cursor_link" not in [row["id"] for row in others]
        assert all(row["stage"] is None for row in others)
    with when("hubctl show --all runs"):
        shown = home.hubctl("show", "--all").stdout
    with then("the text has the group's line and marks the part's row"):
        assert "\nbeta, untested, 0 of 1 on\n" in shown
        row = [line for line in shown.splitlines() if line.strip().startswith("cursor_link ")]
        assert len(row) == 1 and row[0].endswith(", beta, untested")
        child = [line for line in shown.splitlines() if line.strip().startswith("plugin ")]
        assert len(child) == 1 and "beta" not in child[0]
    with when("the doctor runs, as text and as JSON"):
        text = home.hubctl("doctor").stdout
        found = json.loads(home.hubctl("doctor", "--json").stdout)
    with then("both name the beta part and say it is off"):
        assert "Beta, untested: cursor_link is off.\n" in text
        assert found["beta"] == [{"part": "cursor_link", "runs": False}]
    with when("the user turns cursor_link on"):
        home.hubctl("on", "cursor_link")
    with then("the doctor and the screen say it is on"):
        assert "Beta, untested: cursor_link is on.\n" in home.hubctl("doctor").stdout
        assert json.loads(home.hubctl("doctor", "--json").stdout)["beta"] == [{"part": "cursor_link", "runs": True}]
        assert json.loads(home.hubctl("screen", "--json").stdout)["families"][-1]["parts_on"] == 1


def test_turning_cursor_link_on_notes_the_beta_and_writes_cursor_files_and_off_removes_them(home):
    with given("the user turned builder and the tier rule on, and hub is installed with Cursor in the profile, so "
               "nothing is written for Cursor"):
        home.switch_on("builder", "tier_rule")
        install_with_cursor_in_profile(home)
        nothing_for_cursor(home)
    with when("the user types /hub on cursor_link, which runs hubctl on cursor_link"):
        done = home.hubctl("on", "cursor_link")
    with then("hubctl prints the one-line note first, then the row"):
        lines = done.stdout.splitlines()
        assert lines[0] == BETA_NOTE and len(lines) == 2 and lines[1].strip().startswith("cursor_link ")
    with and_("the Cursor plugin, the agent copies and the Cursor slots are written and recorded"):
        assert os.path.isdir(cursor_folder(home))
        assert os.path.isfile(cursor_agent(home, "builder")) and os.path.isfile(cursor_agent(home, "scout"))
        assert os.path.isfile(os.path.join(cursor_slots(home), "1.md"))
        recorded = {entry["path"] for entry in cursor_links(home)}
        assert {cursor_folder(home), cursor_agent(home, "builder"), cursor_agent(home, "scout")} <= recorded
        assert cursor_cost(home)["total"]["start_text"] > 0
        assert session_start(home, "cursor")[0]
    with when("the user types /hub off cursor_link, which runs hubctl off cursor_link"):
        off = home.hubctl("off", "cursor_link")
    with then("there is no note, only the row, and every Cursor file, slot and link is gone"):
        lines = off.stdout.splitlines()
        assert len(lines) == 1 and lines[0].strip().startswith("cursor_link ") and BETA_NOTE not in off.stdout
        nothing_for_cursor(home)
    with when("the user turns it on again with --json"):
        again = json.loads(home.hubctl("on", "cursor_link", "--json").stdout)
    with then("the JSON notes hold the same line"):
        assert again["notes"] == [BETA_NOTE]
    with when("the user turns on a part that is not beta"):
        other = home.hubctl("on", "reviewer")
    with then("there is no beta note"):
        assert "beta" not in other.stdout


def test_an_update_never_offers_a_beta_part(home):
    with given("hub is installed, and the seen list forgets cursor_link and reviewer, as after an update"):
        home.install()
        seen = home.state_file("seen.json")
        seen["parts"] = [part for part in seen["parts"] if part not in ("cursor_link", "reviewer")]
        home.write_json(os.path.join(home.state, "seen.json"), seen)
    with when("hub applies again"):
        home.hubctl("apply")
    with then("the part that is not beta is offered, and the beta part is not"):
        offered = [offer["id"] for offer in home.state_file("offers.json")["offers"]]
        assert "reviewer" in offered
        assert "cursor_link" not in offered


def test_only_the_user_turns_a_beta_flag_on(home):
    with given("hub is installed with Cursor in the profile"):
        install_with_cursor_in_profile(home)
    with when("the setup agent's answer offers to turn the beta part on"):
        answer = os.path.join(home.root, "beta_answer.json")
        home.write_json(answer, {"profile": {}, "instructions_off": [], "offers": [], "questions": [
            {"id": "cursor", "text": "Should hub run in Cursor too?",
             "options": [{"label": "yes", "does": "Turns on the Cursor beta.", "flags": {"cursor_link": "on"}}]}]})
        dialog = json.loads(home.hubctl("setup", "--answer", answer).stdout)
    with then("setup drops that option, so the question has nothing left and is not asked"):
        assert dialog["questions"] == []
    with and_("nothing is written for Cursor"):
        nothing_for_cursor(home)
    with when("the model runs the command the user would type, from a tool call"):
        home.env.update(TOOL_CALL)
        refused = home.hubctl("set", "cursor_link", "on", check=False)
    with then("it is refused, and nothing is written for Cursor"):
        assert refused.returncode != 0 and "beta" in refused.stderr
        nothing_for_cursor(home)
    with when("the user turns it on with the same command"):
        for name in TOOL_CALL:
            home.env.pop(name)
        home.hubctl("set", "cursor_link", "on")
    with then("it works"):
        assert os.path.isdir(cursor_folder(home))
    with when("the model tries to turn it off from a tool call"):
        home.env.update(TOOL_CALL)
        kept = home.hubctl("set", "cursor_link", "off", check=False)
    with then("the user's choice stands, because the user set it by hand"):
        assert kept.returncode != 0 and "by hand" in kept.stderr
        assert os.path.isdir(cursor_folder(home))


def test_both_manifests_say_cursor_support_is_beta(home):
    sentence = "Cursor support is in beta and untested."
    with given("the Claude Code manifest, the marketplace and the generated Cursor manifest"):
        claude = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8"))
        market = json.load(open(os.path.join(os.path.dirname(os.path.dirname(PLUGIN)), ".claude-plugin",
                                             "marketplace.json"), encoding="utf-8"))
        sys.path.insert(0, os.path.join(PLUGIN, "parts", "cursor_link"))
        import handler
        cursor = json.loads(handler.plugin_files(PLUGIN, "./plugins/hub")[".cursor-plugin/plugin.json"])
    with then("each description names the beta once"):
        assert claude["description"].count(sentence) == 1
        assert market["plugins"][0]["description"].count(sentence) == 1
        assert cursor["description"].count(sentence) == 1
        assert cursor["description"] == claude["description"]


def test_a_beta_child_under_a_part_that_is_not_beta_fails_the_tree_check(home):
    with given("hub is installed, and a part that is not beta holds a beta child"):
        home.install()
        home.write_json(os.path.join(home.plugin, "parts", "mixed", "part.json"), {
            "id": "mixed", "family": "tools", "kind": "extra", "default": "on", "does": "x",
            "children": [{"id": "trial", "stage": "beta", "default": "off", "does": "x"}]})
    with when("the tree loads"):
        check = home.hubctl("apply", check=False)
    with then("it fails, because no door marks a beta flag under such a part"):
        assert check.returncode == 1
        assert "mixed.trial" in check.stderr and "beta" in check.stderr
