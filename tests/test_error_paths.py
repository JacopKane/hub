"""When hub itself is broken, public steps stop and every other call runs.

A settings file that does not parse, as after a merge with conflict markers,
must never let private text out. These scenarios break hub on purpose and
check that only the public steps are refused.
"""
import json
import os
import sys

from conftest import PLUGIN, REPO, commit
from steps import and_, given, then, when

CONFLICT = '<<<<<<< HEAD\n{"version": 1, "flags": {}}\n=======\n{"version": 1}\n>>>>>>> other\n'


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def test_settings_that_do_not_parse_stop_public_steps_and_let_other_calls_run(home, tool):
    with given("hub is installed and the user settings file holds merge conflict markers"):
        home.install()
        write_text(home.user_file, CONFLICT)
    with when("Claude sends a commit with a clean message"):
        public = home.hook(tool, "PreToolUse", commit("Fix the save button"), errors_expected=True)
    with then("the commit is refused, and the reason names the settings file"):
        assert public.refused
        assert "could not read its settings" in public.reason and "hub.json" in public.reason
    with and_("a call that publishes nothing runs, and the error is in errors.log"):
        assert home.hook(tool, "PreToolUse", "git status --short", errors_expected=True).ran
        assert "hub.json" in home.errors()
    with when("the user file is fixed and the repo's committed .hub/hub.json holds conflict markers"):
        os.remove(home.user_file)
        write_text(os.path.join(home.work, ".hub", "hub.json"), CONFLICT)
        inside = home.hook(tool, "PreToolUse", commit("Fix the save button"), errors_expected=True)
        outside_folder = os.path.join(home.root, "elsewhere")
        os.makedirs(outside_folder)
        outside = home.hook(tool, "PreToolUse", commit("Fix the save button"), cwd=outside_folder,
                            errors_expected=True)
    with then("a commit in that repo is refused, and the same commit outside it runs"):
        assert inside.refused and "could not read its settings" in inside.reason
        assert outside.ran and outside.output == {}


def test_cursor_is_asked_to_fail_closed_when_the_adapter_itself_crashes():
    with given("the Cursor hooks file the plugin ships"):
        with open(os.path.join(REPO, "cursor", "hooks.json")) as handle:
            entries = json.load(handle)["hooks"]["preToolUse"]
    with then("every entry sets failClosed, so a crash refuses the call instead of letting it run"):
        assert entries and all(entry.get("failClosed") is True for entry in entries)


def plant(home, change):
    path = os.path.join(home.plugin, "parts", "public_text_gate", "handler.py")
    with open(path) as handle:
        code = handle.read()
    with open(path, "w") as handle:
        handle.write(change(code))


def test_a_part_that_crashes_or_will_not_load_stops_only_public_steps(home, tool):
    with given("hub is installed and the gate's handler raises an error as soon as it runs"):
        home.install()
        plant(home, lambda code: code.replace(
            "def handle(event, hook_input, flags, answer):\n",
            "def handle(event, hook_input, flags, answer):\n    raise RuntimeError('planted failure')\n"))
    with when("Claude sends a clean commit and a plain status call"):
        public = home.hook(tool, "PreToolUse", commit("Fix the save button"), errors_expected=True)
        status = home.hook(tool, "PreToolUse", "git status --short", errors_expected=True)
    with then("the commit is refused with the error, the status call runs, and errors.log holds the error"):
        assert public.refused and "planted failure" in public.reason
        assert status.ran and status.output == {}
        assert "planted failure" in home.errors()
    with when("the handler file no longer loads at all"):
        plant(home, lambda code: code + "\nthis is not python\n")
        public = home.hook(tool, "PreToolUse", commit("Fix the save button"), errors_expected=True)
        listing = home.hook(tool, "PreToolUse", "ls -la", errors_expected=True)
    with then("any git or gh call counts as public and is refused, and other calls run"):
        assert public.refused and "could not read its settings or parts" in public.reason
        assert home.hook(tool, "PreToolUse", "git status", errors_expected=True).refused
        assert listing.ran and listing.output == {}


def test_hook_input_that_is_not_json_or_an_unknown_event_never_lets_a_public_step_through(home):
    dispatcher = [sys.executable, "-I", os.path.join(PLUGIN, "lib", "hub", "dispatch.py")]
    adapter = [sys.executable, "-I", os.path.join(PLUGIN, "lib", "hub", "adapters", "cursor_hooks.py")]
    with given("hub is installed"):
        home.install()
        dispatcher[-1] = os.path.join(home.plugin, "lib", "hub", "dispatch.py")
        adapter[-1] = os.path.join(home.plugin, "lib", "hub", "adapters", "cursor_hooks.py")
    with when("Claude Code sends hook input that is not JSON, once naming a commit and once not"):
        public = home.run(dispatcher + ["PreToolUse"], stdin='{"tool_input": git commit -m "x"')
        plain = home.run(dispatcher + ["PreToolUse"], stdin="{broken ls -la")
    with then("the one that names a commit is refused in Claude Code's keys, and the other gets no answer"):
        assert json.loads(public.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert plain.stdout == ""
    with when("Cursor sends broken input that names a push"):
        cursor = home.run(adapter + ["preToolUse"], stdin="{oops git push origin main")
    with then("the push is refused in Cursor's keys"):
        assert json.loads(cursor.stdout)["permission"] == "deny"
    with when("either hook is started with no event or an event it does not know"):
        no_event = home.run(dispatcher, stdin="{}", check=False)
        unknown = home.run(adapter + ["afterLunch"], stdin="{}", check=False)
    with then("each exits 2 with its usage line and prints no answer"):
        assert no_event.returncode == 2 and "usage" in no_event.stderr and no_event.stdout == ""
        assert unknown.returncode == 2 and "usage" in unknown.stderr and unknown.stdout == ""


def test_hubctl_explains_each_mistake_and_changes_nothing(home):
    with given("hub is installed with one value set"):
        home.install()
        home.hubctl("off", "public_text_gate.dashes")
        before = home.settings()
        changes = len(home.history())
    with when("the user makes each common mistake"):
        mistakes = {
            "no flag named": ["off", "public_text_gate.colons"],
            "takes one of": ["set", "public_text_gate", "loud"],
            "takes one value": ["set", "public_text_gate", "warn", "block"],
            "set it with a value": ["on", "core.private_words"],
        }
        results = {message: home.hubctl(*args, check=False) for message, args in mistakes.items()}
    with then("each exits 1 and says what was wrong"):
        assert {message: (result.returncode, message in result.stderr) for message, result in results.items()} == {
            message: (1, True) for message in mistakes}
    with and_("the settings file and the history are as they were"):
        assert home.settings() == before and len(home.history()) == changes
    with when("the user file gets a key hub does not know"):
        write_text(home.user_file, json.dumps(dict(before, colours="loud")))
        shown = home.hubctl("show", check=False)
    with then("show exits 1 and names the file and the key"):
        assert shown.returncode == 1 and "hub.json" in shown.stderr and "colours" in shown.stderr
    with when("the history is empty and the user runs undo"):
        os.remove(os.path.join(home.state, "history.jsonl"))
        write_text(home.user_file, json.dumps(before))
        nothing = home.hubctl("undo", check=False)
    with then("undo exits 1 and says there is nothing to undo"):
        assert nothing.returncode == 1 and "nothing to undo" in nothing.stderr
