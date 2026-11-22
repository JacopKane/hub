"""hub's switch step run while the shell sits inside one of hub's own folders.

hub counts its plugin folder, its settings folder and its state folder as its
settings, so a relative word there names one of hub's files. The step's own
fixed words, such as show, --json or a flag id, name no file, so a read runs
from there and a change is judged by the lowering check as anywhere else.
Every other word is still judged as a path, so every write stays refused, and
the refusal names the folder and says to leave it.
"""
import json
import os
import subprocess
import sys

import pytest

from steps import and_, given, then, when
from test_plain_words import STEP, TOOL_CALL, typed

PART = "self_protection"


def step_path(home):
    return os.path.join(home.plugin, STEP)


def step_hook(home, tool, words, cwd, lines=None, program=None, errors_expected=False):
    """The PreToolUse hook for the step run with these words from this folder, as Claude writes the call."""
    command = f"{program or 'python3 -I ' + step_path(home)} {words}"
    data = home.hook_input(tool, "PreToolUse", command, cwd=cwd)
    if lines is not None:
        transcript = os.path.join(home.root, "transcript.jsonl")
        with open(transcript, "w") as handle:
            handle.writelines(json.dumps(line) + "\n" for line in lines)
        data["transcript_path"] = transcript
    return home.send(tool, "PreToolUse", data, errors_expected=errors_expected)


def command_hook(home, tool, command, cwd, errors_expected=False):
    return home.send(tool, "PreToolUse", home.hook_input(tool, "PreToolUse", command, cwd=cwd),
                     errors_expected=errors_expected)


def run_step_from(home, folder, *args):
    """The step as Claude's tool call runs it, with the shell in this folder."""
    return subprocess.run([sys.executable, "-I", step_path(home), *args], cwd=folder,
                          env={**home.env, **TOOL_CALL}, capture_output=True, text=True, timeout=60)


def installed_with_settings_folder(home):
    """hub installed, and its settings folder made, as it is once any change was saved. Nothing is pinned."""
    home.install()
    os.makedirs(home.config, exist_ok=True)


def settings_now(home):
    """The user's settings file as it reads now, or None while hub has written none."""
    return home.settings() if os.path.exists(home.user_file) else None


def refused_by(result, check):
    return result.refused and f"({PART}.{check})" in result.reason


def names_the_folder(result, folder):
    return os.path.realpath(folder) in result.reason and "Change to another folder first" in result.reason


def hub_folders(home):
    """hub's own folders, a folder under the plugin, and a link to the plugin, as the shell's folder."""
    link = os.path.join(home.root, "hub-link")
    os.symlink(home.plugin, link)
    return {"the plugin folder": home.plugin, "a folder under the plugin": os.path.join(home.plugin, "lib"),
            "the state folder": home.state, "the settings folder": home.config, "a link to the plugin folder": link}


READS = ["show", "show --json", "show --js --all", "screen --json", "screen --tool cursor --json", "doctor",
         "doctor --json", "offers"]


def test_reading_hubs_switches_from_inside_hubs_folder_runs_and_a_change_is_judged_as_anywhere_else(home, tool):
    with given("hub is installed, and hub's setup switched scout off once, so scout is not pinned"):
        home.install()
        home.set_by("setup", "scout", "off")
        assert home.history()[-1]["by"] == "setup"
        known_id = home.history()[-1]["id"]
        folders = hub_folders(home)
        before = home.settings()
    for name, folder in folders.items():
        with when(f"Claude runs each of the step's reads with the shell in {name}"):
            results = {words: step_hook(home, tool, words, folder) for words in READS}
        with then(f"every read runs from {name}"):
            assert not [(words, result.reason) for words, result in results.items() if result.refused]
    with when("Claude runs the step by its own path, with no interpreter word, from the plugin folder"):
        direct = step_hook(home, tool, "show", home.plugin, program=step_path(home))
        relative = step_hook(home, tool, "show --json", home.plugin, program="./lib/hub_switch.py")
    with then("it runs too"):
        assert direct.ran, direct.reason
        assert relative.ran, relative.reason
    with when("Claude undoes a change by an id hub's history knows, from the plugin folder"):
        undo = step_hook(home, tool, f"undo {known_id}", home.plugin)
    with then("it runs, since scout is no protection"):
        assert undo.ran, undo.reason
    with when("the step really runs show from the plugin folder and from the settings folder"):
        shown = [run_step_from(home, folder, "show", "--json") for folder in (home.plugin, home.config)]
    with then("it prints the switches and changes nothing"):
        assert all(process.returncode == 0 for process in shown), [process.stderr for process in shown]
        assert all("public_text_gate" in json.loads(process.stdout)["flags"] for process in shown)
        assert home.settings() == before
    with when("Claude lowers the public text gate from the plugin folder, with no typed order"):
        lowered = step_hook(home, tool, "off public_text_gate", home.plugin, lines=[typed("fix the tests")])
        band = step_hook(home, tool, "screen --json --band", home.plugin)
    with then("the lowering check refuses it as it would anywhere, and the gate stays as it was"):
        assert refused_by(lowered, "lowering"), lowered.reason
        assert refused_by(band, "lowering"), band.reason
        assert home.settings() == before


def test_the_users_typed_order_lowers_a_protection_from_inside_hubs_folder(home):
    with given("hub is installed with the public text gate at warn"):
        home.install()
        assert home.show()["flags"]["public_text_gate"]["effective"] == "warn"
    with when("the user types the order and Claude runs the step from the plugin folder"):
        hook = step_hook(home, "claude_code", "off public_text_gate", home.plugin,
                         lines=[typed("turn off the public text gate")])
        changed = run_step_from(home, home.plugin, "off", "public_text_gate")
    with then("the hook lets it through and the step records the change as the user's"):
        assert hook.ran, hook.reason
        assert changed.returncode == 0, changed.stderr
        assert home.show()["flags"]["public_text_gate"]["effective"] == "off"
        assert home.history()[-1]["by"] == "user"


@pytest.mark.parametrize("words, folder", [
    ("show > hub.json", "config"),
    ("show >> hub.json", "config"),
    ("set scout hub.json", "config"),
    ("profile set a.b hub.json", "config"),
    ("tuner decide --answer=hub.json", "config"),
    ("tuner decide --answer hub.json", "config"),
    ("setup --reply hub.json", "config"),
    ("show --tool=claude_code", "plugin"),
    ("show extra-word", "plugin"),
    ("undo 000000000000", "plugin"),
], ids=["the step's output into hub.json", "appended to hub.json", "a value that names hub.json",
        "a profile word that names hub.json", "an option value after =", "an option's value", "a --reply value",
        "a word with =", "a word hub's parser does not take", "an undo id hub's history does not know"])
@pytest.mark.parametrize("form", ["through python3 -I", "by the step's own path"])
def test_a_step_word_that_could_name_a_file_inside_hubs_folder_is_still_refused(home, tool, words, folder, form):
    with given("hub is installed with its settings folder"):
        installed_with_settings_folder(home)
        place = {"config": home.config, "plugin": home.plugin}[folder]
        program = None if form == "through python3 -I" else step_path(home)
        before = settings_now(home)
    with when(f"Claude runs the step {form} with {words!r} from hub's {folder} folder"):
        result = step_hook(home, tool, words, place, program=program)
    with then("the settings files check refuses it, names the folder and says to leave it"):
        assert refused_by(result, "settings_files"), result.reason
        assert names_the_folder(result, place), result.reason
    with and_("nothing changed"):
        assert settings_now(home) == before


@pytest.mark.parametrize("before_path, folder", [
    ("python3 -X=hub.json", "config"),
    ("python3 --check-hash-based-pycs=hub.json", "config"),
    ("python3 -Ohub.json", "config"),
    ("python3 -I -Whub.json", "config"),
    ("python3 -cpass", "plugin"),
    ("python3 -I -cpass", "plugin"),
    ("python3 -X hub.json", "config"),
    ("python3 -mthis", "plugin"),
    ("python3 -IBc1", "plugin"),
    ("perl -e1", "plugin"),
    ("perl -Ilib", "plugin"),
    ("ruby -e1", "plugin"),
], ids=["a switch with = and a value", "a long switch with = and a value", "a switch with a value joined to it",
        "a joined value after an allowed switch", "python code joined to its switch",
        "python code joined to its switch after an allowed one", "an unlisted switch whose value is the next word",
        "a python module joined to its switch",
        "python code joined to a group of switches", "perl code joined to its switch",
        "a perl library folder joined to its switch", "ruby code joined to its switch"])
def test_an_interpreter_word_before_the_steps_path_is_judged_unless_it_is_a_listed_python_switch(
        home, tool, before_path, folder):
    with given("hub is installed with its settings folder"):
        installed_with_settings_folder(home)
        place = {"config": home.config, "plugin": home.plugin}[folder]
        before = settings_now(home)
    with when(f"Claude runs the step's show through {before_path!r} from hub's {folder} folder"):
        result = step_hook(home, tool, "show", place, program=f"{before_path} {step_path(home)}")
    with then("the settings files check judges that word as a path, refuses it and names the folder"):
        assert refused_by(result, "settings_files"), result.reason
        assert names_the_folder(result, place), result.reason
    with and_("nothing changed"):
        assert settings_now(home) == before


def test_the_listed_python_switches_before_the_steps_path_still_let_a_read_run_from_hubs_folder(home, tool):
    with given("hub is installed"):
        installed_with_settings_folder(home)
    with when("Claude runs show with -I, -B, -s, -S, -u and -q before the step's path, from the plugin folder"):
        result = step_hook(home, tool, "show", home.plugin,
                           program=f"python3 -I -B -s -S -u -q {step_path(home)}")
    with then("it runs"):
        assert result.ran, result.reason


@pytest.mark.parametrize("command, folder", [
    ("rm hub.json", "config"),
    ("cp /tmp/x.json hub.json", "state"),
    ("echo '{}' > history.jsonl", "state"),
    ("sed -i s/on/off/ parts/scout/part.json", "plugin"),
    ("python3 -I lib/hub_switch.py show; rm hub.json", "config"),
], ids=["rm in the settings folder", "cp in the state folder", "a redirect in the state folder",
        "sed -i in the plugin folder", "a read and then a write"])
def test_a_write_from_inside_hubs_folder_is_refused_and_the_refusal_names_the_folder(home, tool, command, folder):
    with given("hub is installed with its settings folder"):
        installed_with_settings_folder(home)
        place = {"config": home.config, "state": home.state, "plugin": home.plugin}[folder]
        before = settings_now(home)
    with when(f"Claude runs {command!r} from hub's {folder} folder"):
        result = command_hook(home, tool, command, place)
    with then("the settings files check refuses it, names the folder and says to leave it"):
        assert refused_by(result, "settings_files"), result.reason
        assert names_the_folder(result, place), result.reason
    with and_("nothing changed"):
        assert settings_now(home) == before


def test_an_edit_of_hubs_settings_gets_no_folder_hint_since_edit_takes_a_full_path(home):
    with given("hub is installed with its settings folder"):
        installed_with_settings_folder(home)
        before = settings_now(home)
    with when("Claude's Edit tool changes hub's settings file by its full path, with the shell in the settings folder"):
        data = home.hook_input("claude_code", "PreToolUse", "unused", cwd=home.config)
        data["tool_name"] = "Edit"
        data["tool_input"] = {"file_path": home.user_file, "old_string": "on", "new_string": "off"}
        result = home.send("claude_code", "PreToolUse", data)
    with then("it is refused, and the refusal does not tell Claude to change folder"):
        assert refused_by(result, "settings_files"), result.reason
        assert "Change to another folder first" not in result.reason
    with and_("nothing changed"):
        assert settings_now(home) == before


def test_with_hubs_tree_unreadable_show_from_hubs_folder_is_refused(home):
    with given("hub is installed, and one of its parts' manifests no longer reads as JSON"):
        installed_with_settings_folder(home)
        with open(os.path.join(home.plugin, "parts", "scout", "part.json"), "w") as handle:
            handle.write("{not json")
    with when("Claude runs show from the plugin folder"):
        result = step_hook(home, "claude_code", "show", home.plugin, errors_expected=True)
    with then("the settings files check judges every word, so it is refused"):
        assert refused_by(result, "settings_files"), result.reason


def test_a_write_outside_hubs_folder_gets_no_folder_hint(home):
    with given("hub is installed"):
        home.install()
    with when("Claude writes hub's settings file by its full path from the work folder"):
        result = command_hook(home, "claude_code", f"rm {home.user_file}", home.work)
    with then("it is refused, and the refusal does not tell Claude to leave a folder it is not in"):
        assert refused_by(result, "settings_files"), result.reason
        assert "Change to another folder first" not in result.reason


def test_with_hubs_settings_unreadable_show_runs_from_hubs_folder_and_every_change_and_write_is_refused(home):
    with given("hub is installed and its settings file no longer reads as JSON"):
        installed_with_settings_folder(home)
        with open(home.user_file, "w") as handle:
            handle.write("{not json")
    with when("Claude runs show from the plugin folder"):
        shown = step_hook(home, "claude_code", "show", home.plugin, errors_expected=True)
    with then("it runs, since it only reads"):
        assert shown.ran, shown.reason
    with when("Claude turns scout off from the plugin folder, and removes the settings file from its folder"):
        changed = step_hook(home, "claude_code", "off scout", home.plugin, errors_expected=True)
        removed = command_hook(home, "claude_code", "rm hub.json", home.config, errors_expected=True)
    with then("both are refused, and the write's refusal names the folder"):
        assert refused_by(changed, "lowering"), changed.reason
        assert refused_by(removed, "settings_files"), removed.reason
        assert names_the_folder(removed, home.config), removed.reason
    with and_("the settings file is as it was"):
        with open(home.user_file) as handle:
            assert handle.read() == "{not json"
