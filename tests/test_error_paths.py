"""When hub itself is broken, public steps stop and ordinary calls run.

A settings file that does not parse, as after a merge with conflict markers,
must never let private text out, and must never let a builder or reviewer
start while a review loop may be stopped. These scenarios break hub on
purpose and check that only those calls are refused.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

import test_review_loop as loop
from chats import Chats
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


def plant(home, change, name=os.path.join("parts", "public_text_gate", "handler.py")):
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


def test_after_a_part_crashes_hub_names_a_push_as_public_without_reading_its_commits(home, tool):
    with given("hub is installed with the PR test gate off, the repo has a local bare remote and a new commit, the "
               "gate's handler raises as soon as it runs, and a git first on PATH logs each call"):
        home.install()
        home.hubctl_from_mod("off", "pr_test_gate")
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


# a review loop stop while hub fails

CANNOT_TELL = "can not tell whether a loop is stopped"
STOPPED = "export-button failed 2 reviews in a row"
RAISE_AT_ONCE = ("def handle(event, hook_input, flags, answer):\n",
                 "def handle(event, hook_input, flags, answer):\n    raise RuntimeError('planted failure')\n")
# A second process that holds a lock file until its input closes.
HOLD_LOCK = ("import fcntl, sys\nhandle = open(sys.argv[1], 'a')\nfcntl.flock(handle, fcntl.LOCK_EX)\n"
             "print('held', flush=True)\nsys.stdin.read()\n")


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def stopped_loop(home, chats):
    """hub as it ships, with the loop on the export button stopped after two failed reviews. Returns the list."""
    loop.installed(home, chats)
    export = loop.acceptance(home, "export-button")
    for task in ("Build it.", "Fix A2."):
        assert loop.start(chats, loop.LOOP, "builder", f"{task}\nAcceptance: {export}").ran
        assert loop.review(home, chats, loop.LOOP, loop.brief(export), loop.NO_GO)[1].ran
    assert STOPPED in loop.start(chats, loop.LOOP, "builder", f"Fix A2 again.\nAcceptance: {export}").reason
    return export


def agent_call(home, chats, agent, prompt, chat=loop.LOOP):
    """An Agent call, sent the way Claude Code sends it, while hub may log an error."""
    tool_input = {"description": "Run it", "prompt": prompt, "subagent_type": agent}
    return home.send("claude_code", "PreToolUse", chats.input(chat, "PreToolUse", "Agent", tool_input),
                     errors_expected=True)


def test_a_review_loop_stop_holds_builder_and_reviewer_calls_while_hub_can_not_read_its_settings(home, chats):
    with given("the loop on the export button is stopped, and the user settings file then holds merge conflict "
               "markers"):
        export = stopped_loop(home, chats)
        write_text(home.user_file, CONFLICT)
    with when("the loop chat starts a builder, another chat starts a critic on the list, and the loop chat starts "
              "a scout"):
        builder = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
        critic = agent_call(home, chats, "critic", loop.brief(export), chat=loop.OTHER)
        scout = agent_call(home, chats, "scout", "Count the files.")
    with then("the builder and the critic are refused with the error, and the scout runs"):
        for refused in (builder, critic):
            assert refused.refused and CANNOT_TELL in refused.reason and "hub.json" in refused.reason
        assert scout.ran and scout.output == {}
    with when("the review loop's own handler no longer loads either"):
        handler = os.path.join("parts", "review_loop", "handler.py")
        code = plant(home, lambda code: code + "\nthis is not python\n", handler)
        rough = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
    with then("the builder is still refused"):
        assert rough.refused and CANNOT_TELL in rough.reason
    with when("the handler loads again and the loop's file is cut off half way"):
        plant(home, lambda _: code, handler)
        state = os.path.join(home.state, "review_loop", "export-button.json")
        with open(state) as handle:
            whole = handle.read()
        write_text(state, whole[:len(whole) // 2])
        cut = agent_call(home, chats, "worker", "Tidy the export code.")
    with then("a worker is refused, and the reason names the file"):
        assert cut.refused and CANNOT_TELL in cut.reason and state in cut.reason
    with when("no review loop file is left"):
        os.remove(state)
        free = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
    with then("the builder runs, since no loop can be stopped"):
        assert free.ran and free.output == {}


def test_a_reviewer_the_user_added_is_held_while_hub_can_not_read_its_settings(home, chats):
    with given("the loop on the export button is stopped, the user adds an auditor to the reviewers, and the user "
               "settings file then holds merge conflict markers"):
        stopped_loop(home, chats)
        home.hubctl("set", "review_loop.reviewers", "critic", "reviewer", "auditor")
        write_text(home.user_file, CONFLICT)
    with when("the loop chat starts an auditor and a scout"):
        auditor = agent_call(home, chats, "auditor", "Audit the export button.")
        scout = agent_call(home, chats, "scout", "Count the files.")
    with then("the auditor is refused with the error, and the scout runs"):
        assert auditor.refused and CANNOT_TELL in auditor.reason and "hub.json" in auditor.reason
        assert scout.ran and scout.output == {}
    with when("hub's resolved settings no longer parse either"):
        write_text(os.path.join(home.state, "resolved.json"), "{cut off")
        critic = agent_call(home, chats, "critic", "Review the export button.")
        builder = agent_call(home, chats, "builder", "Fix A2 again.")
    with then("the default critic and builder are still refused, and the scout still runs"):
        for refused in (critic, builder):
            assert refused.refused and CANNOT_TELL in refused.reason
        assert agent_call(home, chats, "scout", "Count the files.").ran


def agent_ends(home, chats, agent_id, agent_type, event, **extra):
    """An agent's hand-back or end in the loop chat, sent the way Claude Code sends it, while hub may log an error."""
    if event == "SubagentStop":
        hook_input = chats.input(loop.LOOP, event, agent_id=agent_id, agent_type=agent_type, **extra)
    else:
        hook_input = chats.input(loop.LOOP, event, "SubagentHandback", {"message": loop.NO_GO}, agent_id=agent_id,
                                 agent_type=agent_type)
    return home.send("claude_code", event, hook_input, errors_expected=True)


def test_a_reviewers_hand_back_and_end_hold_while_hub_can_not_read_its_settings(home, chats):
    with given("the loop on the export button has one failed review, a second critic on the list and a critic "
               "with no list have started, and the user settings file then holds merge conflict markers"):
        loop.installed(home, chats)
        export = loop.acceptance(home, "export-button")
        assert loop.start(chats, loop.LOOP, "builder", f"Build it.\nAcceptance: {export}").ran
        assert loop.review(home, chats, loop.LOOP, loop.brief(export), loop.NO_GO)[1].ran
        assert loop.start(chats, loop.LOOP, "builder", f"Fix A2.\nAcceptance: {export}").ran
        assert loop.start(chats, loop.LOOP, "critic", loop.brief(export)).ran
        loop.agent_brief(home, loop.LOOP, "a-critic-on-the-list", loop.brief(export))
        no_list = "Review it.\nAcceptance: none, a one-line typo fix"
        assert loop.start(chats, loop.LOOP, "critic", no_list).ran
        loop.agent_brief(home, loop.LOOP, "a-critic-with-no-list", no_list)
        write_text(home.user_file, CONFLICT)
    with when("the critic on the list hands back NO-GO"):
        held = agent_ends(home, chats, "a-critic-on-the-list", "critic", "PreToolUse")
    with then("the hand-back is refused with the error, since hub can not record the review"):
        assert held.refused and "could not record this review" in held.reason and "hub.json" in held.reason
    with when("that critic ends once, and then again after a stop hook kept it going"):
        first = agent_ends(home, chats, "a-critic-on-the-list", "critic", "SubagentStop", stop_hook_active=False,
                           last_assistant_message=loop.NO_GO)
        again = agent_ends(home, chats, "a-critic-on-the-list", "critic", "SubagentStop", stop_hook_active=True,
                           last_assistant_message=loop.NO_GO)
    with then("the first end is blocked once with the error, and the second end goes through"):
        assert first.output.get("decision") == "block"
        assert "could not record this review" in first.output["reason"] and "hub.json" in first.output["reason"]
        assert "decision" not in again.output
    with and_("a scout's hand-back and the hand-back of the critic with no list run"):
        assert agent_ends(home, chats, "a-scout", "scout", "PreToolUse").ran
        assert agent_ends(home, chats, "a-critic-with-no-list", "critic", "PreToolUse").ran
    with when("the user file is fixed and the critic on the list hands back NO-GO again"):
        os.remove(home.user_file)
        counted = agent_ends(home, chats, "a-critic-on-the-list", "critic", "PreToolUse")
    with then("the hand-back runs, and the next builder call on the list is stopped with the rounds"):
        assert counted.ran, counted.reason
        stopped = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
        assert stopped.refused and STOPPED in stopped.reason


def repo_settings(home, name, lists):
    """A settings file in the repo's .hub folder that adds agent names to the review loop's lists. Returns its path."""
    path = os.path.join(home.work, ".hub", name)
    write_text(path, json.dumps({"version": 1, "flags": {
        f"review_loop.{list_id}": {"value": names, "by": "user"} for list_id, names in lists.items()}}))
    return path


def test_agent_names_the_repo_adds_are_held_while_hub_can_not_read_its_settings(home, chats):
    with given("the repo's committed settings add a fixer to the builders, its local settings add an inspector to "
               "the reviewers, the loop on the export button has one failed review, an inspector on the list has "
               "started, and the user settings file then holds merge conflict markers"):
        loop.installed(home, chats)
        repo_settings(home, "hub.json", {"builders": ["fixer"]})
        local = repo_settings(home, "hub.local.json", {"reviewers": ["inspector"]})
        export = loop.acceptance(home, "export-button")
        assert loop.start(chats, loop.LOOP, "builder", f"Build it.\nAcceptance: {export}").ran
        assert loop.review(home, chats, loop.LOOP, loop.brief(export), loop.NO_GO)[1].ran
        assert loop.start(chats, loop.LOOP, "fixer", f"Fix A2.\nAcceptance: {export}").ran
        assert loop.start(chats, loop.LOOP, "inspector", loop.brief(export)).ran
        loop.agent_brief(home, loop.LOOP, "an-inspector-on-the-list", loop.brief(export))
        write_text(home.user_file, CONFLICT)
    with when("the inspector hands back NO-GO and then ends"):
        held = agent_ends(home, chats, "an-inspector-on-the-list", "inspector", "PreToolUse")
        ended = agent_ends(home, chats, "an-inspector-on-the-list", "inspector", "SubagentStop",
                           stop_hook_active=False, last_assistant_message=loop.NO_GO)
    with then("the hand-back is refused and the end is blocked once, since hub can not record the review"):
        assert held.refused and "could not record this review" in held.reason and "hub.json" in held.reason
        assert ended.output.get("decision") == "block" and "could not record this review" in ended.output["reason"]
    with when("the user file is fixed, the inspector hands back NO-GO again, and the user file then holds merge "
              "conflict markers again"):
        os.remove(home.user_file)
        counted = agent_ends(home, chats, "an-inspector-on-the-list", "inspector", "PreToolUse")
        write_text(home.user_file, CONFLICT)
        fixer = agent_call(home, chats, "fixer", f"Fix A2 again.\nAcceptance: {export}")
        scout = agent_call(home, chats, "scout", "Count the files.")
    with then("the review counts, the fixer is refused with the error, and the scout runs"):
        assert counted.ran, counted.reason
        assert fixer.refused and CANNOT_TELL in fixer.reason and "hub.json" in fixer.reason
        assert scout.ran and scout.output == {}
    with when("the repo's local settings file no longer parses either"):
        write_text(local, "{cut off")
        unsure = agent_call(home, chats, "scout", "Count the files.")
    with then("even the scout is refused, since hub can not tell which agents the repo adds"):
        assert unsure.refused and CANNOT_TELL in unsure.reason


def test_a_stop_hub_has_already_answered_stays_when_hub_can_not_store_the_answer(home, chats):
    with given("the loop on the export button is stopped, and hub can not write the stored answers of its calls"):
        export = stopped_loop(home, chats)
        plant(home, lambda code: code.replace(
            "def write_json(path, data):\n",
            "def write_json(path, data):\n    if f'{os.sep}calls{os.sep}' in path:\n"
            "        raise OSError('planted write failure')\n"), os.path.join("lib", "hub", "files.py"))
    with when("the loop chat starts a builder on the list and a scout"):
        builder = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
        scout = agent_call(home, chats, "scout", "Count the files.")
    with then("the builder gets the stop's own refusal with the rounds, and the scout runs"):
        assert builder.refused and STOPPED in builder.reason
        assert scout.ran and scout.output == {}
    with and_("errors.log holds the failed write"):
        assert "planted write failure" in home.errors()


def test_a_review_loop_stop_holds_when_another_part_that_checks_agent_calls_raises(home, chats):
    with given("the loop on the export button is stopped, the live notes part is on, and its handler raises as "
               "soon as it runs"):
        export = stopped_loop(home, chats)
        home.hubctl("on", "live_notes")
        plant(home, lambda code: code.replace(*RAISE_AT_ONCE), os.path.join("parts", "live_notes", "handler.py"))
    with when("the loop chat starts a builder on the list and a scout"):
        builder = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
        scout = agent_call(home, chats, "scout", "Count the files.")
    with then("the builder is refused with the planted error, and the scout runs"):
        assert builder.refused and CANNOT_TELL in builder.reason and "planted failure" in builder.reason
        assert scout.ran and scout.output == {}


def test_a_review_loop_lock_another_process_holds_refuses_the_call_within_the_hook_time(home, chats):
    holder = None
    try:
        with given("the loop on the export button is stopped, and a second process holds the area's lock"):
            export = stopped_loop(home, chats)
            lock = os.path.join(home.state, "review_loop", "export-button.lock")
            holder = subprocess.Popen([sys.executable, "-I", "-c", HOLD_LOCK, lock], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, text=True)
            assert holder.stdout.readline().strip() == "held"
        with when("the loop chat starts a builder on the list"):
            started = time.monotonic()
            builder = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
            took = time.monotonic() - started
        with then("the builder is refused well inside the 15 s hook time, and the reason names the loop's folder"):
            assert builder.refused and CANNOT_TELL in builder.reason
            assert os.path.join(home.state, "review_loop") in builder.reason
            assert took < 10, took
        with when("the second process ends and the loop chat starts the builder again"):
            holder.stdin.close()
            holder.wait(timeout=10)
            again = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
        with then("the builder gets the stop's own refusal with the rounds"):
            assert again.refused and STOPPED in again.reason
    finally:
        if holder is not None:
            holder.kill()
            holder.wait(timeout=10)


def call_lock(home, hook_input):
    """The lock file the dispatcher takes for one tool call, named by its event, session and call id."""
    raw = "\0".join(["PreToolUse", hook_input["session_id"], hook_input["tool_use_id"]])
    return os.path.join(home.state, "calls", hashlib.sha256(raw.encode()).hexdigest()[:32] + ".lock")


def test_a_call_lock_another_process_holds_refuses_a_builder_while_a_stop_is_open_within_the_hook_time(home, chats):
    holders = []
    try:
        with given("the loop on the export button is stopped, and two other processes hold the locks of the loop "
                   "chat's next builder call and its next scout call"):
            export = stopped_loop(home, chats)
            builder_input = chats.input(loop.LOOP, "PreToolUse", "Agent", {
                "description": "Run it", "prompt": f"Fix A2 again.\nAcceptance: {export}", "subagent_type": "builder"})
            scout_input = chats.input(loop.LOOP, "PreToolUse", "Agent", {
                "description": "Run it", "prompt": "Count the files.", "subagent_type": "scout"})
            for hook_input in (builder_input, scout_input):
                holders.append(subprocess.Popen([sys.executable, "-I", "-c", HOLD_LOCK, call_lock(home, hook_input)],
                                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True))
                assert holders[-1].stdout.readline().strip() == "held"
        with when("the loop chat sends the builder call"):
            started = time.monotonic()
            builder = home.send("claude_code", "PreToolUse", builder_input, errors_expected=True)
            took = time.monotonic() - started
        with then("the builder is refused well inside the 15 s hook time, the reason names the stopped loop's file "
                  "and the held lock"):
            assert builder.refused and CANNOT_TELL in builder.reason
            assert os.path.join(home.state, "review_loop", "export-button.json") in builder.reason
            assert "stayed locked" in builder.reason
            assert took < 10, took
        with when("the loop chat sends the scout call"):
            scout = home.send("claude_code", "PreToolUse", scout_input, errors_expected=True)
        with then("the scout runs, since the review loop does not hold it"):
            assert scout.ran and scout.output == {}
        with when("both processes end and the loop chat starts the builder again"):
            for holder in holders:
                holder.stdin.close()
                holder.wait(timeout=10)
            again = agent_call(home, chats, "builder", f"Fix A2 again.\nAcceptance: {export}")
        with then("the builder gets the stop's own refusal with the rounds"):
            assert again.refused and STOPPED in again.reason
    finally:
        for holder in holders:
            holder.kill()
            holder.wait(timeout=10)
