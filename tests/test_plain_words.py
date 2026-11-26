"""Switching parts by plain words, through the hub skill and hub's internal step.

The user says "turn off the scout agent" and Claude runs the step the switch
skill names. A normal part changes on any request, and so does uninstall. A
protection, such as a gate's mode, is never lowered from a tool call. The
step refuses and points the user to /hub.
"""
import glob
import json
import os
import re
import subprocess
import sys

from conftest import PLUGIN, REPO
from steps import and_, given, then, when

STEP = os.path.join("lib", "hub_switch.py")
SKILL = os.path.join(PLUGIN, "skills", "switch", "SKILL.md")
GATE = "demo_gate"


# The chat the hook input fixture names, which Claude Code also gives the commands a tool call runs.
CHAT = "00000000-0000-4000-8000-000000000001"
TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": CHAT}


def step(home, *args, check=True):
    """The internal step as Claude runs it from a tool call."""
    process = subprocess.run([sys.executable, "-I", os.path.join(home.plugin, STEP), *args], cwd=home.work,
                             env={**home.env, **TOOL_CALL}, capture_output=True, text=True, timeout=60)
    if check and process.returncode != 0:
        raise AssertionError(f"{args} exited {process.returncode}\n{process.stdout}\n{process.stderr}")
    return process


def test_plain_words_turn_a_normal_part_off_and_undo_brings_it_back(home):
    home.install()
    assert home.show()["flags"]["scout"]["effective"] == "on"

    turned = step(home, "off", "scout")

    assert home.show()["flags"]["scout"]["effective"] == "off"
    assert re.search(r"scout .*off", turned.stdout)
    assert 'Say "undo that" to take it back.' in turned.stdout
    undone = step(home, "undo")
    assert home.show()["flags"]["scout"]["effective"] == "on"
    assert "scout" in undone.stdout


def test_the_switch_skill_tells_claude_to_change_parts_from_plain_words():
    with open(SKILL) as handle:
        text = handle.read()

    assert "disable-model-invocation" not in text
    assert "turn off" in text and "undo that" in text
    assert "python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py" in text


def test_a_tool_call_raises_a_protection_but_never_lowers_one_and_is_pointed_to_hub(home):
    with given("hub is installed with the demo gate at warn"):
        home.install()
        assert home.show()["flags"][GATE]["effective"] == "warn"
    with when("Claude runs the step to switch the gate off from a tool call"):
        lowered = step(home, "off", GATE, check=False)
    with then("the step refuses, says only the user can make the change, points to /hub, and the gate stays"):
        assert lowered.returncode == 1, lowered.stdout
        assert "only the user" in lowered.stderr and "/hub" in lowered.stderr, lowered.stderr
        assert home.show()["flags"][GATE]["effective"] == "warn"
    with when("Claude raises the gate to block from a tool call"):
        step(home, "set", GATE, "block")
    with then("the raise works"):
        assert home.show()["flags"][GATE]["effective"] == "block"
    with when("Claude tries to put it back to warn, which is lower than block"):
        back = step(home, "set", GATE, "warn", check=False)
    with then("that is refused too, and the gate still blocks"):
        assert back.returncode == 1 and "/hub" in back.stderr, back.stderr
        assert home.show()["flags"][GATE]["effective"] == "block"


def installed_with_a_key(home):
    """hub installed, with its links and one Claude Code value it wrote over the user's own."""
    home.record_claude()
    home.install()
    settings_file = os.path.join(home.root, ".claude", "settings.json")
    home.write_json(settings_file, {"outputStyle": "hub", "other": 1})
    home.write_json(os.path.join(home.state, "keys.json"), {"keys": [
        {"file": settings_file, "key": "outputStyle", "hub_value": "hub", "earlier": "Explanatory",
         "had_earlier": True}]})
    recorded = [entry["path"] for entry in home.state_file("links.json")["links"]]
    assert recorded and all(os.path.lexists(path) for path in recorded)
    return settings_file, recorded


def test_uninstall_from_a_tool_call_takes_hub_out_and_keeps_the_users_settings_file(home):
    with given("hub is installed with its links, one Claude Code value it wrote, and a choice the user made"):
        settings_file, recorded = installed_with_a_key(home)
        step(home, "off", "scout")
        choices = home.settings()
    with when("Claude runs the step's uninstall from a tool call, with no typed message to check"):
        done = step(home, "uninstall")
    with then("hub's links are gone and the Claude Code value is back to the user's own"):
        assert not [path for path in recorded if os.path.lexists(path)]
        with open(settings_file) as handle:
            assert json.load(handle) == {"outputStyle": "Explanatory", "other": 1}
        assert os.path.exists(os.path.join(home.state, "uninstalled.json"))
        assert "hub is out of Claude Code" in done.stdout
    with and_("the user's own settings file stays as it was"):
        assert home.settings() == choices


def test_the_switch_skill_covers_undo_by_id_and_uninstall():
    with open(SKILL) as handle:
        text = handle.read()

    assert "undo <id>" in text
    assert "uninstall hub" in text and "`uninstall`" in text


def test_the_settings_folder_moves_to_its_new_name_and_keeps_every_choice(home):
    old = os.path.join(home.root, ".config", "hubctl")
    choices = {"version": 1, "flags": {"humanize": {"by": "user", "value": "on"}}}
    home.write_json(os.path.join(old, "hub.json"), choices)
    home.write_json(os.path.join(old, "profile.json"), {"facts": {"use": {"value": "work", "by": "user"}}})

    home.install()

    new = home.config
    assert os.path.basename(new) == "claude-hub"
    assert not os.path.exists(old)
    with open(os.path.join(new, "hub.json")) as handle:
        moved = json.load(handle)["flags"]
    assert moved["humanize"] == {"by": "user", "value": "on"}
    with open(os.path.join(new, "profile.json")) as handle:
        assert json.load(handle)["facts"]["use"]["value"] == "work"
    assert home.show()["flags"]["humanize"]["effective"] == "on"


def test_an_old_settings_file_that_still_names_the_retired_text_gate_loads_and_the_check_finds_nothing(home):
    with given("hub installed, and a settings file from before the public text gate was retired"):
        home.install()
        baseline = json.loads(home.step("doctor", "--json").stdout)
        home.write_json(home.user_file, {"version": 1, "private_words": ["quillbrook"], "flags": {
            "public_text_gate": {"by": "user", "value": "block"},
            "public_text_gate.dashes": {"by": "tuner", "value": "off"},
            "public_text_gate.private_paths": {"by": "user", "value": "on"},
            "humanize": {"by": "user", "value": "on"}}})
    with when("hub applies the file, lists its flags and checks the setup"):
        home.step("apply")
        flags = home.show()["flags"]
        doctor = home.step("doctor")
        found = json.loads(home.step("doctor", "--json").stdout)
    with then("the other choices in the file still hold"):
        assert flags["humanize"]["effective"] == "on"
    with and_("no flag of the retired gate is listed"):
        assert not [flag for flag in flags if flag.split(".")[0] == "public_text_gate"]
    with and_("the setup check names the gate nowhere and finds no more problems than before"):
        assert "public_text_gate" not in doctor.stdout + doctor.stderr
        assert "public_text_gate" not in json.dumps(found)
        assert found["problems"] == baseline["problems"]


# Flags of the retired self-protection part that an older hub wrote into the user's settings file.
RETIRED_FLAGS = {
    "self_protection": {"by": "user", "value": "off"},
    "self_protection.lowering": {"by": "user", "value": "on"},
    "self_protection.agent_marker": {"by": "user", "value": "off"},
    "self_protection.settings_files": {"by": "user", "value": "on"},
    "self_protection.ask_the_user": {"by": "user", "value": "off"},
    "self_protection.hubctl": {"by": "user", "value": "on"},
}


def doctor_findings(home):
    found = json.loads(home.step("doctor", "--json").stdout)
    return found["problems"], found["part_lines"]


def test_a_settings_file_with_the_retired_self_protection_flags_loads_with_no_error(home):
    with given("hub is installed, and doctor's findings on the clean install are noted"):
        home.install()
        clean = doctor_findings(home)
    with when("the user's settings file still holds the self-protection flags an older hub wrote, on and off"):
        home.write_json(home.user_file, {"version": 1, "flags": {**RETIRED_FLAGS,
                                                                 "humanize": {"by": "user", "value": "on"}}})
    with then("show reads the file with no error, lists none of the retired flags, and keeps the user's choice"):
        shown = home.step("show", "--json")
        assert shown.stderr == "", shown.stderr
        flags = json.loads(shown.stdout)["flags"]
        assert not [flag for flag in flags if flag.startswith("self_protection")]
        assert flags["humanize"]["effective"] == "on"
    with and_("doctor finds nothing it did not find on the clean install"):
        assert doctor_findings(home) == clean
    with when("Claude switches a normal part off and back on, and hub applies its settings again"):
        step(home, "off", "scout")
        step(home, "undo")
        home.step("apply")
    with then("each step works, and the retired flags stay in the file as they were"):
        assert home.show()["flags"]["scout"]["effective"] == "on"
        kept = home.settings()["flags"]
        assert {flag: kept.get(flag) for flag in RETIRED_FLAGS} == RETIRED_FLAGS


def test_no_file_under_bin_ships():
    assert glob.glob(os.path.join(PLUGIN, "bin", "*")) == []


def user_facing_files():
    found = [os.path.join(REPO, "README.md"), *glob.glob(os.path.join(REPO, "cursor", "skills", "*", "SKILL.md")),
             *glob.glob(os.path.join(PLUGIN, "skills", "*", "SKILL.md")),
             *glob.glob(os.path.join(PLUGIN, "parts", "*", "text", "*.md")),
             *glob.glob(os.path.join(PLUGIN, "parts", "*", "skill", "SKILL.md"))]
    return sorted(found)


def does_texts(node):
    yield node.get("does", "")
    for child in node.get("children", []):
        yield from does_texts(child)


def test_no_user_facing_text_names_hubctl(home):
    for path in user_facing_files():
        with open(path) as handle:
            assert "hubctl" not in handle.read().casefold(), path
    for path in glob.glob(os.path.join(PLUGIN, "parts", "*", "part.json")):
        with open(path) as handle:
            for text in does_texts(json.load(handle)):
                assert "hubctl" not in text, path
    home.install()
    printed = [step(home, "show").stdout, step(home, "doctor").stdout, step(home, "off", "scout").stdout,
               step(home, "undo").stdout, step(home, "off", GATE, check=False).stderr]
    for text in printed:
        assert "hubctl" not in text, text
