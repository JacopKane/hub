"""When hub itself is broken, public steps stop and ordinary calls run.

A settings file that does not parse, as after a merge with conflict markers,
must never let private text out. These scenarios break hub on purpose and
check that only those calls are refused.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time

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


def cursor_link_handler():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    spec = importlib.util.spec_from_file_location(
        "cursor_link_handler", os.path.join(PLUGIN, "parts", "cursor_link", "handler.py"))
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    return handler


def test_cursor_is_asked_to_fail_closed_when_the_adapter_itself_crashes(home):
    with given("the Cursor hooks file the plugin ships, and the one hub renders for a plugin copy with the made-up "
               "demo_refusal gate, which checks shell calls in Cursor"):
        home.install(demo_refusal=True)
        with open(os.path.join(REPO, "cursor", "hooks.json")) as handle:
            shipped = json.load(handle)["hooks"]
        rendered = json.loads(cursor_link_handler().plugin_files(home.plugin, "./plugins/hub")["cursor/hooks.json"])
    with then("the shipped file starts hub before no tool call, since no shipped part checks one in Cursor"):
        assert "preToolUse" not in shipped
    with and_("the rendered file starts hub before each shell call, and every such entry sets failClosed, so a "
              "crash refuses the call instead of letting it run"):
        entries = rendered["hooks"]["preToolUse"]
        assert [entry["matcher"] for entry in entries] == ["Shell"]
        assert all(entry.get("failClosed") is True for entry in entries)


def plant(home, change, name=os.path.join("parts", "demo_refusal", "handler.py")):
    """Change one file in the plugin copy the dispatcher runs. Returns the code it had before."""
    path = os.path.join(home.plugin, name)
    with open(path) as handle:
        code = handle.read()
    changed = change(code)
    assert changed != code, f"the change did not apply to {name}"
    with open(path, "w") as handle:
        handle.write(changed)
    return code


def test_a_part_that_crashes_or_will_not_load_stops_only_public_steps(home, tool):
    with given("hub is installed with the made-up demo_refusal gate, whose handler raises an error as soon as it runs"):
        home.install(demo_refusal=True)
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
    with when("hub's shell parser, which tells a public step from the others, no longer loads at all"):
        plant(home, lambda code: code + "\nthis is not python\n", os.path.join("lib", "hub", "command_steps.py"))
        public = home.hook(tool, "PreToolUse", commit("Fix the save button"), errors_expected=True)
        listing = home.hook(tool, "PreToolUse", "ls -la", errors_expected=True)
    with then("any git or gh call counts as public and is refused, and other calls run"):
        assert public.refused and "could not read its settings or parts" in public.reason
        assert home.hook(tool, "PreToolUse", "git status", errors_expected=True).refused
        assert listing.ran and listing.output == {}


def test_after_a_part_crashes_hub_names_a_push_as_public_without_reading_its_commits(home, tool):
    with given("hub is installed, the repo has a local bare remote and a new commit, the "
               "demo_refusal gate's handler raises as soon as it runs, and a git first on PATH logs each call"):
        home.install(demo_refusal=True)
        bare = os.path.join(home.root, "remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("commit", "-q", "--allow-empty", "-m", "Late change")
        plant(home, lambda code: code.replace(
            "def handle(event, hook_input, flags, answer):\n",
            "def handle(event, hook_input, flags, answer):\n    raise RuntimeError('planted failure')\n"))
        logging = os.path.join(home.root, "logging-git")
        calls = os.path.join(home.root, "git-calls.txt")
        real = home.run(["sh", "-c", "command -v git"]).stdout.strip()
        write_text(os.path.join(logging, "git"), f"#!/bin/sh\necho \"$*\" >> '{calls}'\nexec '{real}' \"$@\"\n")
        os.chmod(os.path.join(logging, "git"), 0o755)
        home.env["PATH"] = logging + os.pathsep + home.env["PATH"]
    with when("Claude pushes main"):
        push = home.hook(tool, "PreToolUse", "git push origin main", errors_expected=True)
    with then("the push is refused with the error, and hub never asked git for the commits it sends, so the "
              "refusal does not spend the hook's time on a second read"):
        assert push.refused and "planted failure" in push.reason
        asked = open(calls).read().split("\n") if os.path.exists(calls) else []
        assert not [line for line in asked if line.split(" ", 1)[0] in ("rev-list", "log")], asked


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
        home.hubctl("off", "demo_gate.dashes")
        before = home.settings()
        changes = len(home.history())
    with when("the user makes each common mistake"):
        mistakes = {
            "no flag named": ["off", "demo_gate.colons"],
            "takes one of": ["set", "demo_gate", "loud"],
            "takes one value": ["set", "demo_gate", "warn", "block"],
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


# a stuck call lock, and settings from a hub that still had the review loop

# A second process that holds a lock file until its input closes.
HOLD_LOCK = ("import fcntl, sys\nhandle = open(sys.argv[1], 'a')\nfcntl.flock(handle, fcntl.LOCK_EX)\n"
             "print('held', flush=True)\nsys.stdin.read()\n")


def call_lock(home, hook_input):
    """The lock file the dispatcher takes for one tool call, named by its event, session and call id."""
    raw = "\0".join(["PreToolUse", hook_input["session_id"], hook_input["tool_use_id"]])
    return os.path.join(home.state, "calls", hashlib.sha256(raw.encode()).hexdigest()[:32] + ".lock")


def test_a_call_lock_another_process_holds_stops_only_a_public_step_within_the_hook_time(home):
    holders = []
    try:
        with given("hub is installed with the made-up demo_refusal gate, so a shell call has a handler to run, and two "
                   "other processes hold the locks of the next commit call and the next status call"):
            home.install(demo_refusal=True)
            commit_input = home.hook_input("claude_code", "PreToolUse", commit("Fix the save button"))
            status_input = home.hook_input("claude_code", "PreToolUse", "git status --short")
            os.makedirs(os.path.join(home.state, "calls"), exist_ok=True)
            for hook_input in (commit_input, status_input):
                holders.append(subprocess.Popen([sys.executable, "-I", "-c", HOLD_LOCK, call_lock(home, hook_input)],
                                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True))
                assert holders[-1].stdout.readline().strip() == "held"
        with when("Claude sends the commit"):
            started = time.monotonic()
            public = home.send("claude_code", "PreToolUse", commit_input, errors_expected=True)
            took = time.monotonic() - started
        with then("the commit is refused well inside the 15 s hook time, and the reason names the held lock"):
            assert public.refused and "could not read its settings" in public.reason
            assert "stayed locked" in public.reason
            assert took < 10, took
        with when("Claude sends the status call"):
            status = home.send("claude_code", "PreToolUse", status_input, errors_expected=True)
        with then("it runs, since it publishes nothing"):
            assert status.ran and status.output == {}
    finally:
        for holder in holders:
            holder.kill()
            holder.wait(timeout=10)


def test_settings_and_state_from_a_hub_that_had_the_review_loop_load_with_no_error(home):
    with given("hub is installed, and the user file, the repo's file and hub's state still hold the review loop "
               "from an older hub: switched on with its lists and rounds, offered once, and stopped on one area"):
        home.install()
        old_flags = {"review_loop": {"value": "on", "by": "user"},
                     "review_loop.reviewers": {"value": ["critic", "reviewer", "auditor"], "by": "user"},
                     "review_loop.stop_after.rounds": {"value": 3, "by": "user"},
                     "review_loop.require_list": {"value": "on", "by": "user"}}
        write_text(home.user_file, json.dumps({"version": 1, "flags": old_flags}))
        write_text(os.path.join(home.work, ".hub", "hub.json"),
                   json.dumps({"version": 1, "flags": {"review_loop.builders": {"value": ["fixer"], "by": "user"}}}))
        write_text(os.path.join(home.state, "review_loop", "export-button.json"),
                   json.dumps({"area": "export-button", "stop": {"rounds": 2}}))
        offers = home.state_file("offers.json")
        write_text(os.path.join(home.state, "offers.json"), json.dumps(
            {"offers": [*offers["offers"], {"id": "review_loop",
                                            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            "does": "Keeps a build and review loop on a written list."}]}))
    with when("hub applies its settings, shows them and runs the doctor"):
        home.hubctl("apply")
        shown = home.hubctl("show", "--all", check=False)
        doctor = home.hubctl("doctor", check=False)
    with then("each step exits 0, no flag of the review loop is listed, and the doctor does not name it"):
        assert shown.returncode == 0, shown.stderr
        assert not any(flag_id.startswith("review_loop") for flag_id in home.show("--all")["flags"])
        assert doctor.returncode == 0, doctor.stdout + doctor.stderr
        assert "review_loop" not in doctor.stdout + doctor.stderr
    with and_("the screen offers no part that is gone"):
        screen = json.loads(home.hubctl("screen", "--json").stdout)
        assert "offer:review_loop" not in [line["id"] for line in screen["strip"]]
    with when("Claude starts a builder with no acceptance list and a critic hands back with no verdict lines"):
        agent = home.hook_input("claude_code", "PreToolUse", "")
        agent.update(tool_name="Agent", tool_input={"description": "Fix it", "prompt": "Fix the export button.",
                                                    "subagent_type": "builder"})
        builder = home.send("claude_code", "PreToolUse", agent)
        hand_back = home.hook_input("claude_code", "PreToolUse", "")
        hand_back.update(tool_name="SubagentHandback", tool_input={"message": "Looks fine."},
                         agent_id="a-critic", agent_type="critic")
        critic = home.send("claude_code", "PreToolUse", hand_back)
    with then("both run with no answer and no error, though the old stop file is still there"):
        assert builder.ran and builder.output == {}
        assert critic.ran and critic.output == {}
        assert not os.path.exists(os.path.join(home.state, "errors.log"))
