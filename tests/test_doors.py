"""Slice 4: the text doors.

hubctl and the /hub command print one screen model, built by screen.py and
printed by textview.py. Every scenario runs the real hubctl in a temporary
HOME, on the made-up day 9 fixture where it needs one.
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import time

from conftest import FIXTURES, PLUGIN
from steps import and_, given, then, when
from test_start_text_and_links import use_cursor_beta

DAY9 = os.path.join(FIXTURES, "day9")
DASHES = "public_text_gate.dashes"
DASH_LINE = "d9dash000001"
OLD_LINE = "d9old0000001"


def utc(hours_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_ago * 3600))


def read_day9(name):
    with open(os.path.join(DAY9, name)) as handle:
        return json.load(handle)


def day9(home):
    """Install hub and lay the day 9 settings, history and offer over it."""
    home.install()
    researcher = os.path.join(home.plugin, "parts", "researcher")
    if not os.path.exists(researcher):
        shutil.copytree(os.path.join(DAY9, "researcher"), researcher)
    offers = read_day9("offers.json")
    for offer in offers["offers"]:
        offer["since"] = utc(offer.pop("hours_ago"))
    home.write_json(os.path.join(home.state, "offers.json"), offers)
    home.write_json(home.user_file, read_day9("hub.json"))
    with open(os.path.join(home.state, "history.jsonl"), "w") as handle:
        for line in read_day9("history.json"):
            line["time"] = utc(line.pop("hours_ago"))
            handle.write(json.dumps(line) + "\n")
    home.hubctl("apply")


def screen(home, *args, cwd=None):
    return json.loads(home.hubctl("screen", "--json", *args, cwd=cwd).stdout)


def textview(home):
    """The copy's own text view, imported from its lib folder."""
    lib = os.path.join(home.plugin, "lib")
    sys.path.insert(0, lib)
    try:
        for name in [name for name in sys.modules if name == "hub" or name.startswith("hub.")]:
            del sys.modules[name]
        from hub import textview as module
        return module
    finally:
        sys.path.remove(lib)


def strip_line(model, line_id):
    found = [line for line in model["strip"] if line["id"] == line_id]
    assert found, f"{line_id} is not in the strip: {[line['id'] for line in model['strip']]}"
    return found[0]


def buttons(line):
    return {button["label"]: button["args"] for button in line["buttons"]}


def test_hubctl_show_and_the_screen_model_print_the_same_tree_and_strip_on_day_9(home):
    with given("the day 9 fixture, with dashes off by the tuner and researcher offered"):
        day9(home)
    with when("hubctl show and hubctl screen --json run"):
        shown = home.hubctl("show").stdout
        model = screen(home)
    with then("show prints the text the text view makes from the screen model"):
        assert shown == textview(home).render(model)
    with and_("the header counts the parts on out of every manifest, without core, and the start text"):
        manifests = [name for name in os.listdir(os.path.join(home.plugin, "parts"))
                     if os.path.isfile(os.path.join(home.plugin, "parts", name, "part.json")) and name != "core"]
        header = model["header"]
        assert header["parts_total"] == len(manifests)
        every = home.show("--all")["flags"]
        on = [name for name in manifests if every[name]["runs"]]
        assert header["parts_on"] == len(on) and "researcher" not in on
        cost = home.state_file("cost.json")["claude_code"]["total"]["start_text"]
        assert header["start_text"] == cost and f"{cost:,}" in shown
    with and_("core has no family row, and every family row has a part"):
        families = [family["family"] for family in model["families"]]
        assert "core" not in families and all(family["parts"] for family in model["families"])
    with and_("the strip shows the dash line with undo and the researcher line with on and no"):
        dash = strip_line(model, DASH_LINE)
        assert buttons(dash) == {"Undo": ["undo", DASH_LINE]}
        assert "4 of 5 dash warnings" in dash["reason"]
        offer = strip_line(model, "offer:researcher")
        assert buttons(offer) == {"Turn on": ["on", "researcher"], "No": ["no", "researcher"]}
        assert f"hubctl undo {DASH_LINE}" in shown and "hubctl no researcher" in shown
    with and_("the text has no buttons, and the dash row shows who set it"):
        assert "[" not in shown.replace("[undo]", "")
        assert DASHES in shown and "tuner" in shown
    with and_("a line older than status_band.notices.keep_hours stays out of the strip"):
        assert OLD_LINE not in [line["id"] for line in model["strip"]]
    with when("the user keeps notices for 48 hours"):
        home.hubctl("set", "status_band.notices.keep_hours", "48")
    with then("the older line is back in the strip"):
        assert OLD_LINE in [line["id"] for line in screen(home)["strip"]]


def test_an_undo_from_the_strip_restores_the_old_value_and_pins_it(home):
    with given("the day 9 fixture, and a later change by the user"):
        day9(home)
        home.hubctl("set", "scout.model", "sonnet")
    with when("the user runs hubctl undo with the dash line's id"):
        result = home.hubctl("undo", DASH_LINE)
    with then("public_text_gate.dashes is on by user, and scout keeps the later change"):
        flags = home.settings()["flags"]
        assert flags[DASHES] == {"by": "user", "value": "on"}
        assert flags["scout.model"]["value"] == "sonnet"
        assert DASHES in result.stdout
    with and_("the undo is the newest strip line, and the dash line is gone"):
        strip = screen(home)["strip"]
        assert strip[0]["undoes"] == DASH_LINE and DASH_LINE not in [line["id"] for line in strip]
    with when("the user undoes the same line again"):
        again = home.hubctl("undo", DASH_LINE, check=False)
    with then("hubctl says it is undone already and changes nothing"):
        assert again.returncode == 1 and "already" in again.stderr
        assert home.settings()["flags"][DASHES] == {"by": "user", "value": "on"}


def test_an_offer_goes_when_the_user_says_on_or_no_and_pin_keeps_a_value(home):
    with given("the day 9 fixture"):
        day9(home)
    with when("the user says no to the researcher offer"):
        home.hubctl("no", "researcher")
    with then("the offer leaves the strip and the part stays off"):
        model = screen(home)
        assert "offer:researcher" not in [line["id"] for line in model["strip"]]
        assert home.show("--all")["flags"]["researcher"]["runs"] is False
    with when("the user undoes the no"):
        home.hubctl("undo")
    with then("the offer is back"):
        assert "offer:researcher" in [line["id"] for line in screen(home)["strip"]]
    with when("the user turns researcher on"):
        home.hubctl("on", "researcher")
    with then("it runs and the offer is gone"):
        assert home.show("--all")["flags"]["researcher"]["runs"] is True
        assert "offer:researcher" not in [line["id"] for line in screen(home)["strip"]]
    with when("the user pins scout.model, which nobody set"):
        result = json.loads(home.hubctl("pin", "scout.model", "--json").stdout)
    with then("the file holds the default, set by the user, so the tuner leaves it"):
        assert home.settings()["flags"]["scout.model"] == {"by": "user", "value": "haiku"}
        assert result["change"]["flag"] == "scout.model"
        home.set_by("tuner", "scout.model", "opus")
        assert home.settings()["flags"]["scout.model"]["value"] == "haiku"


def test_project_and_local_write_their_own_files(home):
    with given("a git repo"):
        home.install()
        before = home.settings() if os.path.exists(home.user_file) else None
    with when("the user switches dashes off in the project and on in the local file"):
        home.hubctl("off", DASHES, "--project")
        home.hubctl("on", DASHES, "--local")
    with then(".hub/hub.json and .hub/hub.local.json each hold their entry, and the user file is unchanged"):
        def flags(name):
            with open(os.path.join(home.work, ".hub", name)) as handle:
                return json.load(handle)["flags"]
        assert flags("hub.json")[DASHES]["value"] == "off"
        assert flags("hub.local.json")[DASHES]["value"] == "on"
        after = home.settings() if os.path.exists(home.user_file) else None
        assert after == before
    with and_("hubctl show in that repo shows the local value and its scope"):
        row = [line for line in home.hubctl("show").stdout.splitlines() if line.strip().startswith("dashes")]
        assert row and "on" in row[0] and "local" in row[0]
    with when("the user asks for JSON from a change"):
        result = json.loads(home.hubctl("off", "scout", "--json").stdout)
    with then("it prints the history line and the row"):
        assert result["change"]["flag"] == "scout" and result["change"]["new"] == "off"
        assert result["row"]["id"] == "scout" and result["row"]["runs"] is False


def test_status_band_first_digit_takes_1_to_7(home):
    with given("hub is installed"):
        home.install()
    with when("the user sets the band's first digit to 4, and then to 8"):
        home.hubctl("set", "status_band.first_digit", "4")
        too_big = home.hubctl("set", "status_band.first_digit", "8", check=False)
    with then("4 is kept, and 8 is refused with the range"):
        assert home.settings()["flags"]["status_band.first_digit"]["value"] == 4
        assert too_big.returncode == 1 and "1 to 7" in too_big.stderr
    with and_("the band runs in Claude Code only, and the row carries the CC mark"):
        flags = home.show("--all", "--tool", "cursor")["flags"]
        assert flags["status_band"]["runs"] is False
        rows = {part["id"]: part for family in screen(home)["families"] for part in family["parts"]}
        assert rows["status_band"]["claude_code_only"] is True


def test_an_older_line_keeps_its_undo_only_while_its_flag_holds_the_value_it_set(home):
    with given("the day 9 fixture, where the tuner turned public_text_gate.dashes off"):
        day9(home)
    with and_("the dash line offers undo while dashes is still off"):
        assert buttons(strip_line(screen(home), DASH_LINE)) == {"Undo": ["undo", DASH_LINE]}
    with when("the user turns dashes on again by hand, a newer change to the same flag"):
        home.hubctl("on", DASHES)
    with then("the older dash line stays in the strip without its undo, in the model and the text"):
        assert strip_line(screen(home), DASH_LINE)["buttons"] == []
        assert f"hubctl undo {DASH_LINE}" not in home.hubctl("show").stdout
    with when("the user still runs hubctl undo with the older line's id"):
        refused = home.hubctl("undo", DASH_LINE, check=False)
    with then("hubctl refuses, says the flag changed since, and keeps the newer value"):
        assert refused.returncode == 1 and "changed since" in refused.stderr
        assert home.settings()["flags"][DASHES] == {"by": "user", "value": "on"}
    with when("the user turns dashes off again, so it holds the value the dash line set"):
        home.hubctl("off", DASHES)
    with then("the dash line has its undo again, and the undo goes through"):
        assert buttons(strip_line(screen(home), DASH_LINE)) == {"Undo": ["undo", DASH_LINE]}
        home.hubctl("undo", DASH_LINE)
        assert home.settings()["flags"][DASHES] == {"by": "user", "value": "on"}


def test_an_undo_of_a_project_value_checks_the_project_file_not_the_user_file(home):
    with given("the day 9 fixture, dashes on in this repo's project file, and later off in the user file"):
        day9(home)
        home.hubctl("on", DASHES, "--project")
        project_line = screen(home)["strip"][0]
        home.hubctl("off", DASHES)
    with then("the project line keeps its undo, since the project file still holds on"):
        assert buttons(strip_line(screen(home), project_line["id"])) == {"Undo": ["undo", project_line["id"]]}
    with when("the user undoes the project line"):
        home.hubctl("undo", project_line["id"])
    with then("the project file holds the old value again, pinned"):
        with open(os.path.join(home.work, ".hub", "hub.json")) as handle:
            assert json.load(handle)["flags"][DASHES] == {"by": "user", "value": "off"}


def test_the_band_reads_its_flags_and_each_line_mark_from_the_screen_model(home):
    with given("the day 9 fixture"):
        day9(home)
        model = screen(home)
    with then("the band section holds the status_band flags at their defaults"):
        assert model["band"] == {"runs": True, "state": True, "notices": True, "offers": True,
                                 "chats": False, "problems": True, "first_digit": 1, "keep_hours": 24}
    with and_("the dash line that turned a flag off is marked -, and the offer +"):
        assert strip_line(model, DASH_LINE)["mark"] == "-"
        assert strip_line(model, "offer:researcher")["mark"] == "+"
    with when("the user moves the digits to 4, keeps lines 48 hours and switches notices off"):
        home.hubctl("set", "status_band.first_digit", "4")
        home.hubctl("set", "status_band.notices.keep_hours", "48")
        home.hubctl("off", "status_band.notices")
        model = screen(home)
    with then("the band section follows, and the setting lines are marked ~ and -"):
        assert model["band"] == {"runs": True, "state": True, "notices": False, "offers": True,
                                 "chats": False, "problems": True, "first_digit": 4, "keep_hours": 48}
        marks = [line["mark"] for line in model["strip"] if line["kind"] == "change"]
        assert marks[:3] == ["-", "~", "~"]
    with when("the user turns scout off and on again, and undoes the dash line"):
        home.hubctl("off", "scout")
        home.hubctl("on", "scout")
        home.hubctl("undo", DASH_LINE)
        model = screen(home)
    with then("turning a part on is marked +, and the undo ~"):
        assert model["strip"][0]["kind"] == "undo" and model["strip"][0]["mark"] == "~"
        assert model["strip"][1]["text"].endswith("turned scout on") and model["strip"][1]["mark"] == "+"
    with when("the user switches the band off"):
        home.hubctl("off", "status_band")
    with then("the band section says it does not run"):
        assert screen(home)["band"]["runs"] is False


def mod_fixture():
    with open(os.path.join(PLUGIN, "mod", "tests", "day9.ts"), encoding="utf-8") as handle:
        text = handle.read()
    return json.loads(text.split("/* json */", 1)[1])


def test_the_mod_tests_draw_the_band_from_the_real_day_9_model(home):
    with given("the day 9 fixture"):
        day9(home)
    with when("hubctl screen --json runs, and the mod tests' copy of the model is read"):
        real, copy = screen(home), mod_fixture()
    with then("they are the same, but for the machine name, the times and the doors folder"):
        for model in (real, copy):
            model["header"]["machine"] = "laptop"
            model["doors"] = "/home/sam/.local/state/hubctl/doors"
            for line in model["strip"]:
                line["time"] = "-"
        assert copy == real


def hello_part(home, name="hello"):
    folder = os.path.join(home.plugin, "parts", name)
    os.makedirs(folder)
    home.write_json(os.path.join(folder, "part.json"), {
        "id": name, "family": "tools", "kind": "extra", "default": "on", "does": "Says hello.",
        "commands": {"hello": {"script": "hello.py", "does": "Prints hello and its words."}}})
    with open(os.path.join(folder, "hello.py"), "w") as handle:
        handle.write("import sys\nprint('hello', *sys.argv[1:])\n")


def snapshot(home):
    found = {}
    for folder in (home.config, home.state):
        for root, _dirs, names in os.walk(folder):
            for name in names:
                if name.endswith(".lock"):
                    continue
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    found[path] = handle.read()
    return found


def test_a_part_command_named_in_part_json_runs_through_hubctl(home):
    with given("a fixture part whose part.json names the command hello and its script"):
        home.install()
        hello_part(home)
        home.hubctl("apply")
        home.hubctl("on", "hello")
        before = snapshot(home)
    with when("the user runs hubctl hello"):
        result = home.hubctl("hello", "there")
    with then("the part's script runs, and no hubctl file changed"):
        assert result.stdout.strip() == "hello there"
        assert snapshot(home) == before
    with when("the part is off"):
        home.hubctl("off", "hello")
        stopped = home.hubctl("hello", check=False)
    with then("hubctl says how to switch it on"):
        assert stopped.returncode == 1 and "hubctl on hello" in stopped.stderr
    with when("a part names a command that is a core verb"):
        home.edit_manifest("hello", lambda data: data["commands"].update(
            {"undo": {"script": "hello.py", "does": "Clashes."}}))
        clash = home.hubctl("show", check=False)
    with then("the tree fails and names the clash"):
        assert clash.returncode == 1 and "undo" in clash.stderr and "core" in clash.stderr


def test_the_doctor_names_broken_links_the_cost_an_untested_version_and_disable_all_hooks(home):
    with given("a broken hub link, a claude --version missing from the tested list, and disableAllHooks"):
        home.install()
        link = os.path.join(home.root, ".claude", "agents", "hub-scout.md")
        assert os.path.islink(link)
        os.remove(link)
        os.symlink(os.path.join(home.root, "gone.md"), link)
        home.write_json(os.path.join(home.plugin, "tested-versions.json"),
                        {"claude_code": ["2.1.287"], "cursor": []})
        home.write_json(os.path.join(home.root, ".claude", "settings.json"), {"disableAllHooks": True})
    with when("the user runs hubctl doctor and hubctl doctor --json"):
        text = home.hubctl("doctor").stdout
        report = json.loads(home.hubctl("doctor", "--json").stdout)
    with then("each problem is one line that says what to do"):
        problems = report["problems"]
        kinds = {problem["kind"] for problem in problems}
        assert {"broken_link", "untested_version", "hooks_disabled"} <= kinds
        for problem in problems:
            assert "\n" not in problem["text"] and problem["text"] in text
        assert "2.1.319" in text and link in text
    with and_("the text says hooks, slots and the band are quiet, while links, hubctl and the web page still work"):
        line = [problem["text"] for problem in problems if problem["kind"] == "hooks_disabled"][0]
        for word in ("hooks", "slots", "band", "links", "hubctl", "web page"):
            assert word in line
    with and_("the doctor names what runs and the start text cost"):
        assert report["runs"] and "scout" in report["runs"]
        assert report["start_text"] == home.state_file("cost.json")["claude_code"]["total"]["start_text"]


def test_an_update_lists_new_behaviors_with_an_undo_and_new_parts_as_offers(home):
    with given("apply has seen a first plugin copy"):
        home.install()
        calls = home.record_claude()
    with when("a second copy with a new part and a new gate behavior is installed and hubctl update runs"):
        home.edit_manifest("public_text_gate", lambda data: data["children"].append(
            {"id": "shouting", "default": "on", "privacy": "none", "does": "Stops a title in capitals."}))
        hello_part(home, "greeter")
        result = home.hubctl("update")
    with then("hubctl update asked Claude Code to update the plugin, then applied"):
        assert ["plugin", "update", "hub@hub"] in home.claude_calls()
        assert os.path.exists(calls)
        assert result.returncode == 0
    with and_("hubctl show prints one update line with the new behavior and its undo"):
        shown = home.hubctl("show").stdout
        assert shown.count("public_text_gate.shouting") >= 1
        assert "hubctl off public_text_gate.shouting" in shown
    with and_("the strip lists the new part as an offer"):
        model = screen(home)
        update = strip_line(model, "update:public_text_gate.shouting")
        assert buttons(update) == {"Turn off": ["off", "public_text_gate.shouting"]}
        assert buttons(strip_line(model, "offer:greeter")) == {"Turn on": ["on", "greeter"], "No": ["no", "greeter"]}
    with when("the user turns the new behavior off"):
        home.hubctl("off", "public_text_gate.shouting")
    with then("its update line is gone"):
        assert "update:public_text_gate.shouting" not in [line["id"] for line in screen(home)["strip"]]


def test_the_core_verbs_and_the_part_commands_are_listed_in_help(home):
    with given("hub is installed, with a part that names a command"):
        home.install()
        hello_part(home)
        home.hubctl("apply")
    with when("the user runs hubctl --help"):
        listed = home.hubctl("--help").stdout
    with then("it lists the core verbs and the part's command with what it does"):
        for verb in ("show", "screen", "on", "off", "set", "pin", "no", "undo", "apply", "doctor", "update",
                     "uninstall"):
            assert verb in listed
        assert "hello" in listed and "Prints hello" in listed


def mod_command(home, record):
    return home.run([sys.executable, os.path.join(home.plugin, "bin", "hubctl"), "mod-command", "--json"],
                    stdin=json.dumps(record))


def clash_lines(home):
    return [problem["text"] for problem in json.loads(home.hubctl("doctor", "--json").stdout)["problems"]
            if problem["kind"] == "command_clash"]


def test_when_claude_code_refuses_hub_the_doctor_names_the_clash_and_the_name_the_mod_took(home):
    with given("hub is installed, and the mod found that the user has a /hub of their own"):
        home.install()
        mod_command(home, {"used": "hub-flags", "refused": {"hub": "it is the user's /hub"}})
    with when("the user runs hubctl doctor"):
        lines = clash_lines(home)
    with then("one line names the clash, Claude Code's reason, the folder and the name the mod took"):
        assert len(lines) == 1
        assert "/hub" in lines[0] and "it is the user's /hub" in lines[0] and "/hub-flags" in lines[0]
        assert home.work in lines[0]
    with and_("the screen model holds the clash, so the mod can clear it later"):
        assert screen(home)["command_clash"]["used"] == "hub-flags"
    with when("the fallback name is taken too"):
        mod_command(home, {"used": None, "refused": {"hub": "it is the user's /hub",
                                                    "hub-flags": "it is the user's /hub-flags"}})
    with then("the doctor line says hub has no slash command and points to /hub in Claude Code"):
        lines = clash_lines(home)
        assert len(lines) == 1 and "no slash command" in lines[0] and "then use /hub in Claude Code" in lines[0]
        assert "it is the user's /hub-flags" in lines[0]
    with when("a later session gets /hub again"):
        mod_command(home, {"used": "hub", "refused": {}})
    with then("the clash is gone from the doctor and the screen model"):
        assert clash_lines(home) == []
        assert screen(home)["command_clash"] is None


def test_mod_command_refuses_input_that_is_not_its_record(home):
    with given("hub is installed"):
        home.install()
    with when("mod-command gets text that is not the record"):
        bad = home.run([sys.executable, os.path.join(home.plugin, "bin", "hubctl"), "mod-command", "--json"],
                       stdin="[1, 2]", check=False)
    with then("it exits 1, says what it takes, and writes nothing"):
        assert bad.returncode == 1 and "used" in bad.stderr
        assert screen(home)["command_clash"] is None


def test_the_shipped_tested_versions_file_lists_the_recorded_claude_version():
    with open(os.path.join(PLUGIN, "tested-versions.json")) as handle:
        tested = json.load(handle)
    with open(os.path.join(FIXTURES, "claude_replay", "index.json")) as handle:
        index = json.load(handle)
    with open(os.path.join(FIXTURES, "claude_replay", index["--version"])) as handle:
        recorded = handle.read().split()[0]
    assert recorded in tested["claude_code"]


# The /hub skill. In Claude Code /hub is the mod's command, and a skill named hub would
# block it (slice 0 fact 14), so the plugin's skill is hubctl there. Cursor runs no mods,
# so cursor_link gives Cursor the same skill under the name hub.

CLAUDE_SKILL = os.path.join(PLUGIN, "skills", "hubctl", "SKILL.md")
CURSOR_SKILL = "cursor/skills/hub/SKILL.md"
COMMAND = re.compile(r"^python3 -I (\S+/bin/hubctl) <words>$", re.MULTILINE)


def skill_parts(text):
    """(front matter as a dict, body) of a SKILL.md."""
    assert text.startswith("---\n"), "a SKILL.md starts with front matter"
    head, _, body = text[4:].partition("\n---\n")
    return dict(line.split(": ", 1) for line in head.splitlines()), body


def cursor_link_handler():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    spec = importlib.util.spec_from_file_location(
        "cursor_link_handler", os.path.join(PLUGIN, "parts", "cursor_link", "handler.py"))
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    return handler


def run_skill_command(home, text, words):
    """Run the one command a skill's text names, with the user's words, as the model would."""
    found = COMMAND.findall(text)
    assert len(found) == 1, f"the skill names one hubctl command, found {found}"
    return home.run(["python3", "-I", found[0], *words])


def test_the_hub_skill_is_hubctl_in_claude_code_and_hub_in_cursor():
    with given("the plugin's skills folder"):
        names = sorted(os.listdir(os.path.join(PLUGIN, "skills")))
    with then("no skill there is named hub, so the mod's /hub can register"):
        assert "hub" not in names
        for name in names:
            with open(os.path.join(PLUGIN, "skills", name, "SKILL.md")) as handle:
                assert skill_parts(handle.read())[0]["name"] == name != "hub"
    with and_("the hubctl skill runs only when typed, and may run its one command without a prompt"):
        with open(CLAUDE_SKILL) as handle:
            head, body = skill_parts(handle.read())
        assert head["disable-model-invocation"] == "true"
        assert COMMAND.findall(body) == ["${CLAUDE_PLUGIN_ROOT}/bin/hubctl"]
        assert head["allowed-tools"] == "Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/bin/hubctl *)"
    with when("cursor_link renders the Cursor plugin for a copy in a folder with a space"):
        rendered = cursor_link_handler().plugin_files(PLUGIN, "/opt/my tools/hub")
        manifest = json.loads(rendered[".cursor-plugin/plugin.json"])
        cursor_head, cursor_body = skill_parts(rendered[CURSOR_SKILL])
    with then("Cursor gets the skill as hub, from the folder its manifest names"):
        assert manifest["skills"] == "./cursor/skills/"
        assert cursor_head["name"] == "hub"
        assert cursor_head["description"] == head["description"]
        assert cursor_head["disable-model-invocation"] == "true"
    with and_("without Claude Code's keys, and with the copy's hubctl in place of the plugin root"):
        assert set(cursor_head) == {"name", "description", "disable-model-invocation"}
        assert "${CLAUDE_" not in cursor_body
        assert cursor_body == body.replace("python3 -I ${CLAUDE_PLUGIN_ROOT}/bin/hubctl",
                                           "python3 -I '/opt/my tools/hub/bin/hubctl'")


def test_the_mod_guards_the_hubctl_skill_under_the_name_claude_code_runs_it_by():
    with given("the plugin's name and the hubctl skill's name"):
        with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")) as handle:
            plugin_name = json.load(handle)["name"]
        with open(CLAUDE_SKILL) as handle:
            skill_name = skill_parts(handle.read())[0]["name"]
        with open(os.path.join(PLUGIN, "mod", "register.tsx")) as handle:
            module = handle.read()
    with then("the mod's skill guard names <plugin>:<skill>, as Claude Code 2.1.319 runs /hubctl"):
        assert f"const SKILL = '{plugin_name}:{skill_name}'" in module
        assert "on('command.run', { command: SKILL }, guard)" in module


def test_the_command_each_hub_skill_names_changes_a_flag_and_prints_the_row(home):
    with given("hub installed with Cursor in the profile and the Cursor beta on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
        cursor_skill = os.path.join(home.root, ".cursor", "plugins", "local", "hub", *CURSOR_SKILL.split("/"))
        with open(CLAUDE_SKILL) as handle:
            claude_text = handle.read().replace("${CLAUDE_PLUGIN_ROOT}", home.plugin)
        with open(cursor_skill) as handle:
            cursor_text = handle.read()
    with when('Claude Code runs its skill\'s command with the words "off public_text_gate.dashes"'):
        done = run_skill_command(home, claude_text, ["off", DASHES])
    with then("hub.json holds the change, and hubctl printed the changed row"):
        assert home.settings()["flags"][DASHES] == {"by": "user", "value": "off"}
        assert DASHES in done.stdout and "off" in done.stdout.split(DASHES, 1)[1]
    with when('Cursor runs its copy\'s command with the words "on public_text_gate.dashes"'):
        done = run_skill_command(home, cursor_text, ["on", DASHES])
    with then("the same hub.json changes back, because both skills start the installed hubctl"):
        assert home.settings()["flags"][DASHES]["value"] == "on"
        assert DASHES in done.stdout
    with and_("apply put no skill named hub in ~/.claude/skills"):
        assert not os.path.lexists(os.path.join(home.root, ".claude", "skills", "hub"))
