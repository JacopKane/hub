"""The gates see a call as it will run, and remember warnings under load.

A part that rewrites a call (its tool input) must never slip text past a
gate, so the gates run after every other part. The warn-once memory is read
and written under a lock, so two calls at the same time both count. The
hooks files start the dispatcher only for the tools a part handles, and the
group for every other tool starts Python only when a part may need it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from conftest import DASH, PLUGIN, REPO, HookResult, commit, load_fixture
from steps import and_, given, then, when
from test_other_tiers import agent_transcript, agent_transcript_path, main_transcript

GATE = "public_text_gate"
EVERY_TOOL_PARTS = "every_tool_parts"
EVENT_MARKERS = {"PostToolUse": "post_tool_use_parts", "Stop": "stop_parts"}
USER_PROMPT_PARTS = "user_prompt_parts"
TOOL_PARTS, MARKERS_DONE = "tool_parts", "markers_done"
REWRITER = {
    "$schema": "../../../../schema/part.schema.json",
    "id": "zz_rewriter",
    "family": "rules",
    "kind": "hook",
    "default": "on",
    "privacy": "none",
    "runs_in": ["claude_code", "cursor"],
    "handlers": {"PreToolUse": {"tools": ["Bash"]}},
    "does": "A test part that rewrites the word CLEAN in a shell command.",
}
REWRITER_CODE = '''
def handle(event, hook_input, flags, answer):
    tool_input = dict(hook_input.get("tool_input") or {})
    command = tool_input.get("command") or ""
    if "CLEAN" in command:
        tool_input["command"] = command.replace("CLEAN", "/home/sam/plan.md")
        answer.rewrite(tool_input)
'''


def test_a_part_that_rewrites_a_call_after_the_gate_can_not_slip_text_past_it(home, tool, tmp_path):
    source = os.path.join(str(tmp_path), "plugin-with-rewriter")
    with given("a plugin with one more part, in a family after the gates, that writes a home path into commands"):
        shutil.copytree(PLUGIN, source, ignore=shutil.ignore_patterns("__pycache__"))
        part = os.path.join(source, "parts", REWRITER["id"])
        os.makedirs(part)
        with open(os.path.join(part, "part.json"), "w") as handle:
            json.dump(REWRITER, handle)
        with open(os.path.join(part, "handler.py"), "w") as handle:
            handle.write(REWRITER_CODE)
        home.install(source)
    with when("Claude commits a message that is clean until that part rewrites it"):
        result = home.hook(tool, "PreToolUse", commit("CLEAN"))
    with then("the gate checks the rewritten command and refuses it for the home path"):
        assert result.refused and result.findings == ["private_paths"]
    with and_("a command the part leaves alone still runs"):
        assert home.hook(tool, "PreToolUse", commit("Fix the save button")).ran


def test_warnings_that_arrive_at_the_same_time_are_all_remembered(home):
    count = 16
    session = "session-parallel"
    with given("hub is installed with the gate at warn and the em dash check on"):
        home.install()
        home.hubctl("on", "public_text_gate.dashes")
    with when(f"{count} commits, each with its own dash text, reach the gate at the same moment in one session"):
        processes = []
        for number in range(count):
            data = home.hook_input("claude_code", "PreToolUse", commit(f"Change {number} {DASH} done"), session=session)
            process = subprocess.Popen(
                [sys.executable, "-I", os.path.join(home.plugin, "lib", "hub", "dispatch.py"), "PreToolUse"],
                cwd=home.work, env=home.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            processes.append((process, json.dumps(data)))
        for process, data in processes:
            process.stdin.write(data)
            process.stdin.close()
        first = [json.loads(process.stdout.read() or "{}") for process, _data in processes]
        for process, _data in processes:
            process.wait(timeout=60)
    with then("each is warned once"):
        assert all(answer.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" for answer in first)
    with when("each text comes again"):
        again = [home.hook("claude_code", "PreToolUse", commit(f"Change {number} {DASH} done"), session=session)
                 for number in range(count)]
    with then("every one goes through, because no warning was lost to another write"):
        assert [result.ran for result in again] == [True] * count


def test_the_hooks_files_start_the_dispatcher_only_for_the_tools_parts_handle():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    from hub import tree
    from hub.adapters import cursor_hooks

    with given("the shipped parts and both hooks files"):
        parts = tree.load(os.path.join(PLUGIN, "parts"))
        with open(os.path.join(PLUGIN, "hooks", "hooks.json")) as handle:
            claude = json.load(handle)["hooks"]
        with open(os.path.join(REPO, "cursor", "hooks.json")) as handle:
            cursor = json.load(handle)["hooks"]
        cursor_names = {claude_name: cursor_name for cursor_name, claude_name in cursor_hooks.TOOL_NAMES.items()}

    def tools_by_event(tool):
        found = {}
        for part in parts.parts:
            if tool in part.runs_in:
                for event, spec in part.handlers.items():
                    if tree.handler_runs_in(spec, tool):
                        found.setdefault(event, set()).update(spec.get("tools", []))
        return {event: sorted(names) for event, names in found.items()}

    def every_tool_events(tool):
        """The events where a part that runs in the tool names some tools and another handles every tool."""
        found = set()
        for part in parts.parts:
            if tool in part.runs_in:
                for event, spec in part.handlers.items():
                    if tree.handler_runs_in(spec, tool) and not spec.get("tools"):
                        found.add(event)
        return found

    claude_tools, cursor_tools = tools_by_event("claude_code"), tools_by_event("cursor")
    claude_every = every_tool_events("claude_code")
    with then("each hooks file has an entry only for the events its tool's parts handle"):
        assert set(claude) == set(claude_tools)
        assert set(cursor) == {tree.CURSOR_EVENT_NAMES[event] for event in cursor_tools}
    for event, tools in sorted(claude_tools.items()):
        if event == "PreToolUse":
            named = [name for name in tools if name != "Bash"]
            with then("PreToolUse has the Bash entry, one entry for the other tools a part names, "
                      "and one for every other tool except Monitor"):
                matchers = [entry.get("matcher") for entry in claude[event]]
                assert matchers[:2] == ["Bash", "|".join(named)] and len(matchers) == 3
                every_other = re.compile(matchers[2])
                for name in tools + ["Read", "Edit", "Monitor", "WebFetch"]:
                    hits = [name == "Bash", name in named, bool(every_other.search(name))]
                    assert sum(hits) == (0 if name == "Monitor" else 1), name
        elif event == "PostToolUse" and tools and event in claude_every:
            with then("PostToolUse has one entry for the tools a part names and one for every other tool except "
                      "Monitor, for the part that handles every tool"):
                matchers = [entry.get("matcher") for entry in claude[event]]
                assert matchers[0] == "|".join(tools) and len(matchers) == 2
                every_other = re.compile(matchers[1])
                for name in tools + ["Bash", "Agent", "Monitor", "WebFetch", "mcp__search__query"]:
                    hits = [name in tools, bool(every_other.search(name))]
                    assert sum(hits) == (0 if name == "Monitor" else 1), name
                commands = [hook["command"] for entry in claude[event] for hook in entry["hooks"]]
                assert commands[0].endswith("pre_check.sh\" PostToolUse")
                assert commands[1].endswith("pre_check.sh\" PostToolUse every")
        else:
            with then(f"Claude Code's {event} entry matches exactly {', '.join(tools) or 'every call'}"):
                assert [entry.get("matcher") for entry in claude[event]] == ["|".join(tools)]
    for event, tools in sorted(cursor_tools.items()):
        with and_(f"Cursor's {tree.CURSOR_EVENT_NAMES[event]} entry matches the Cursor names of "
                  f"{', '.join(tools) or 'every call'}, the tools of the handlers that run in Cursor"):
            assert [entry.get("matcher") for entry in cursor[tree.CURSOR_EVENT_NAMES[event]]] == [
                "|".join(sorted(cursor_names[tool] for tool in tools))]


class EveryToolGroup:
    """One of Claude Code's hook groups, run as Claude Code runs it.

    By default the PreToolUse group for every other tool. PATH holds only
    shims for sh, cat and python3, which log each start and run the real
    program. The python3 shim gives the dispatcher the full PATH back. Each group
    has its own log, since one event can have two groups.
    """

    def __init__(self, home, event="PreToolUse", index=2):
        self.home = home
        with open(os.path.join(home.plugin, "hooks", "hooks.json")) as handle:
            groups = json.load(handle)["hooks"][event]
        [self.command] = [hook["command"] for hook in groups[index]["hooks"]]
        folder = os.path.join(home.root, f"probe-bin-{event}-{index}")
        os.makedirs(folder)
        self.starts = os.path.join(home.root, f"starts-{event}-{index}.log")
        shims = {"sh": "exec /bin/sh \"$@\"", "cat": "exec /bin/cat \"$@\"",
                 "python3": f'PATH="{home.env["PATH"]}" exec "{sys.executable}" "$@"'}
        for name, body in shims.items():
            with open(os.path.join(folder, name), "w") as handle:
                handle.write(f'#!/bin/sh\necho "{name} $*" >> "{self.starts}"\n{body}\n')
            os.chmod(os.path.join(folder, name), 0o755)
        # Only the shims are on PATH, so the check can start nothing else unseen.
        self.env = dict(home.env, CLAUDE_PLUGIN_ROOT=home.plugin, PATH=folder)
        self.calls = 0
        self.errors = []

    def read(self, agent_id, cwd=None):
        """One Read call from an agent. Returns (result, seconds)."""
        self.calls += 1
        data = load_fixture("claude_code", "PreToolUse.SubagentHandback.json")
        data.update(cwd=cwd or self.home.work, transcript_path=main_transcript(self.home), tool_name="Read",
                    tool_input={"file_path": os.path.join(self.home.work, "notes.md")},
                    tool_use_id=f"toolu_every_{self.calls:06d}", agent_id=agent_id,
                    agent_transcript_path=agent_transcript_path(self.home, agent_id))
        return self.send(data)

    def send(self, data):
        """Run the group with one hook input. Returns (result, seconds)."""
        started = time.perf_counter()
        process = subprocess.run(["/bin/sh", "-c", self.command], cwd=data["cwd"], env=self.env,
                                 input=json.dumps(data), capture_output=True, text=True, timeout=60)
        seconds = time.perf_counter() - started
        assert process.returncode == 0, process.stderr
        self.errors.append(process.stderr)
        return HookResult("claude_code", process), seconds

    def programs(self):
        """The names of the programs the group started, in order."""
        if not os.path.exists(self.starts):
            return []
        with open(self.starts) as handle:
            return [line.split(" ", 1)[0] for line in handle]

    def fast_path(self):
        """Whether every call so far started only the shell and cat, and wrote no error."""
        return set(self.programs()) <= {"sh", "cat"} and not any(self.errors)

    def python_starts(self):
        if not os.path.exists(self.starts):
            return 0
        with open(self.starts) as handle:  # the dispatcher's starts, not the Python a part runs in turn
            return sum(1 for line in handle if line.startswith("python3 ") and "/lib/hub/dispatch.py" in line)


def agent_at(home, agent_id, context):
    transcript = agent_transcript(home, agent_id=agent_id)
    transcript.step(context)
    return transcript


def marker(home, name=EVERY_TOOL_PARTS):
    path = os.path.join(home.state, name)
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return handle.read().split()


def test_the_group_for_every_other_tool_starts_python_only_when_a_part_that_handles_every_tool_may_run(home):
    with given("hub is installed as it ships, where no part that handles every tool runs, "
               "and an agent is at 135k context"):
        home.install()
        group = EveryToolGroup(home)
        agent_at(home, "a0000000000000021", 135000)
    with then("apply leaves no marker in the state folder"):
        assert marker(home) is None
    with when("the agent reads a file nine times through the group"):
        runs = [group.read("a0000000000000021") for _ in range(9)]
    with then("each call ends with no answer, and only the shell and cat start, with no error"):
        assert [result.output for result, _seconds in runs] == [{}] * 9
        assert group.python_starts() == 0 and group.fast_path()

    with when("the user switches the context budget on"):
        home.hubctl("on", "context_budget")
        result, _seconds = group.read("a0000000000000021")
    with then("apply marks it, the group starts the dispatcher, and the agent gets its reminder"):
        assert marker(home) == ["context_budget"]
        assert group.python_starts() == 1 and "135k context" in result.context

    with when("the user switches it off again"):
        home.hubctl("off", "context_budget")
        agent_at(home, "a0000000000000022", 135000)
        result, _seconds = group.read("a0000000000000022")
    with then("the marker is gone and Python does not start"):
        assert marker(home) is None and not result.context and group.python_starts() == 1

    with when("the user turns the budget on by editing hub.json by hand, so no apply ran yet"):
        settings = home.settings()
        settings["flags"]["context_budget"] = {"by": "user", "value": "on"}
        home.write_json(home.user_file, settings)
        later = os.stat(os.path.join(home.state, "resolved.json")).st_mtime + 2
        os.utime(home.user_file, (later, later))
        result, _seconds = group.read("a0000000000000022")
    with then("the group sees settings newer than the last apply and starts the dispatcher, which applies them"):
        assert group.python_starts() == 2 and "135k context" in result.context
        assert marker(home) == ["context_budget"]

    with when("the user's own setting is off, and the repo turns the budget on in its own .hub/hub.json"):
        home.hubctl("off", "context_budget")
        home.write_json(os.path.join(home.work, ".hub", "hub.json"),
                        {"version": 1, "flags": {"context_budget": {"by": "user", "value": "on"}}})
        agent_at(home, "a0000000000000023", 135000)
        nested = os.path.join(home.work, "src")
        os.makedirs(nested)
        result, _seconds = group.read("a0000000000000023", cwd=nested)
    with then("a call from a folder in that repo starts the dispatcher, and the repo's setting reminds the agent"):
        assert marker(home) is None
        assert group.python_starts() == 3 and "135k context" in result.context
    with and_("a call from a folder outside any repo still ends at once"):
        outside = os.path.join(home.root, "loose")
        os.makedirs(outside)
        agent_at(home, "a0000000000000024", 135000)
        result, _seconds = group.read("a0000000000000024", cwd=outside)
        assert not result.context and group.python_starts() == 3


def test_the_post_tool_use_and_stop_groups_start_python_only_when_one_of_their_parts_may_run(home):
    with given("hub is installed as it ships, with path_rules and link_check off"):
        home.install()
        groups = {event: EveryToolGroup(home, event, 0) for event in EVENT_MARKERS}
        read_input = load_fixture("claude_code", "PostToolUse.Read.json")
        read_input.update(cwd=home.work, tool_input={"file_path": os.path.join(home.work, "notes.md")})
        stop_input = load_fixture("claude_code", "Stop.json")
        stop_input.update(cwd=home.work, last_assistant_message="Done, see #123.", stop_hook_active=False)
        inputs = {"PostToolUse": read_input, "Stop": stop_input}
    with then("apply leaves no marker for either group"):
        assert [marker(home, name) for name in EVENT_MARKERS.values()] == [None, None]
    with when("a file is read and a reply ends, through each group"):
        results = {event: group.send(inputs[event])[0] for event, group in groups.items()}
    with then("each group ends with no answer, and Python never starts"):
        assert [result.output for result in results.values()] == [{}, {}]
        assert [group.python_starts() for group in groups.values()] == [0, 0]

    with when("the user switches path_rules and link_check on"):
        home.hubctl("on", "path_rules")
        home.hubctl("on", "link_check")
        stopped, _seconds = groups["Stop"].send(inputs["Stop"])
        groups["PostToolUse"].send(inputs["PostToolUse"])
    with then("apply marks each part for its group, each group starts the dispatcher, "
              "and link_check sends the bare PR number back"):
        assert marker(home, EVENT_MARKERS["PostToolUse"]) == ["path_rules"]
        assert marker(home, EVENT_MARKERS["Stop"]) == ["link_check"]
        assert [group.python_starts() for group in groups.values()] == [1, 1]
        assert stopped.output.get("decision") == "block"

    with when("the user switches both off again"):
        home.hubctl("off", "path_rules")
        home.hubctl("off", "link_check")
        results = {event: group.send(inputs[event])[0] for event, group in groups.items()}
    with then("both markers are gone, and Python does not start again"):
        assert [marker(home, name) for name in EVENT_MARKERS.values()] == [None, None]
        assert [result.output for result in results.values()] == [{}, {}]
        assert [group.python_starts() for group in groups.values()] == [1, 1]


def test_the_prompt_group_starts_python_for_one_of_its_parts_or_a_notice_that_waits_for_a_chat(home):
    with given("hub is installed as it ships, where no part that handles a typed prompt runs"):
        home.install()
        group = EveryToolGroup(home, "UserPromptSubmit", 0)
        typed = load_fixture("claude_code", "UserPromptSubmit.json")
        typed.update(cwd=home.work, prompt="Add the layout test.")
        notices = os.path.join(home.state, "chat_notices")
    with then("apply leaves no marker for the group"):
        assert marker(home, USER_PROMPT_PARTS) is None
    with when("the user types a prompt"):
        result, _seconds = group.send(typed)
    with then("it ends with no answer, and Python never starts"):
        assert result.output == {} and group.python_starts() == 0

    with when("a part that has since been turned off left a notice for this chat"):
        os.makedirs(notices, exist_ok=True)
        with open(os.path.join(notices, typed["session_id"] + ".jsonl"), "w") as handle:
            handle.write(json.dumps({"at": time.time(), "text": "WEB did not get your message."}) + "\n")
        result, _seconds = group.send(typed)
    with then("the group starts the dispatcher, and the chat gets the notice with its prompt"):
        assert group.python_starts() == 1 and "WEB did not get your message." in result.context
    with and_("the next prompt finds no notice, so Python does not start"):
        result, _seconds = group.send(typed)
        assert not result.context and group.python_starts() == 1

    with when("the user switches knowledge_only on"):
        home.hubctl("on", "knowledge_only")
        group.send(typed)
    with then("apply marks it, and the group starts the dispatcher"):
        assert marker(home, USER_PROMPT_PARTS) == ["knowledge_only"]
        assert group.python_starts() == 2


def named_call(group, tool_name, tool_input=None, **fields):
    """One call from the main chat through a PreToolUse group. Returns (result, seconds)."""
    group.calls += 1
    data = load_fixture("claude_code", "PreToolUse.SubagentHandback.json")
    for key in ("agent_id", "agent_type", "agent_transcript_path"):
        data.pop(key, None)
    data.update(cwd=group.home.work, tool_name=tool_name, tool_use_id=f"toolu_named_{group.calls:06d}",
                tool_input={"file_path": os.path.join(group.home.work, "notes.md")} if tool_input is None
                else tool_input, **fields)
    return group.send(data)


def tool_marker(home, tool):
    return marker(home, os.path.join(TOOL_PARTS, tool))


def test_the_group_for_named_tools_starts_python_only_for_a_tool_that_a_running_part_names(home):
    with given("hub is installed as it ships, where the gate names Bash, self_protection names Bash, Delete, "
               "Edit and Write, and tier_rule, which names Agent, is off"):
        home.install()
        group = EveryToolGroup(home, "PreToolUse", 1)
    with then("apply marks those four tools, and no other"):
        assert sorted(os.listdir(os.path.join(home.state, TOOL_PARTS))) == ["Bash", "Delete", "Edit", "Write"]
        assert tool_marker(home, "Agent") is None
        assert [tool_marker(home, tool) for tool in ("Delete", "Edit", "Write")] == [["self_protection"]] * 3
    with when("the main chat reads, greps, globs, edits a notebook, asks and sends seven times through the group"):
        calls = [("Read", None), ("Grep", {"pattern": "x"}), ("Glob", {"pattern": "*"}), ("NotebookEdit", None),
                 ("AskUserQuestion", {"questions": []}), ("SendMessage", {"to": "WEB"}), ("Read", None)]
        runs = [named_call(group, name, tool_input) for name, tool_input in calls]
    with then("each call ends with no answer, and only the shell and cat start, with no error"):
        assert [result.output for result, _seconds in runs] == [{}] * 7
        assert group.python_starts() == 0 and group.fast_path()
    with when("the main chat edits and writes a file in the repo"):
        edits = [named_call(group, "Edit"), named_call(group, "Write")]
    with then("the group starts the dispatcher for self_protection each time, and both calls run"):
        assert [result.output for result, _seconds in edits] == [{}] * 2
        assert group.python_starts() == 2
    with when("the main chat starts an agent"):
        named_call(group, "Agent", {"description": "Read the docs", "prompt": "Read them", "subagent_type": "worker"})
    with then("Python does not start, because no running part names Agent"):
        assert group.python_starts() == 2
    with when("the user switches tier_rule on, which names Agent, and the main chat starts an agent"):
        home.hubctl("on", "tier_rule")
        named_call(group, "Agent", {"description": "Read the docs", "prompt": "Read them", "subagent_type": "worker"})
    with then("apply marks Agent for tier_rule, and the group starts the dispatcher for it"):
        assert tool_marker(home, "Agent") == ["tier_rule"]
        assert group.python_starts() == 3

    with when("the user switches the coordinator on, which names Read, and a large Write comes"):
        home.hubctl("on", "coordinator")
        named_call(group, "Read")
        big = {"file_path": os.path.join(home.work, "big.md"), "content": "line \"quoted\" 100%s\n" * 50000}
        named_call(group, "Write", big)
    with then("apply marks Read for it, the group starts the dispatcher for both, "
              "and the dispatcher read the whole input"):
        assert tool_marker(home, "Read") == ["coordinator"]
        assert group.python_starts() == 5
        assert not os.path.exists(os.path.join(home.state, "errors.log"))
    with when("the user switches it off again in /hub"):
        home.hubctl_from_mod("off", "coordinator")
        named_call(group, "Read")
    with then("the marker is gone, and Python does not start"):
        assert tool_marker(home, "Read") is None and group.python_starts() == 5


BROKEN_MARKS = [
    "the done mark is missing, as after an apply from before this check",
    "the done mark is missing, as after an apply that stopped midway",
    "the done mark can not be read",
    "the done mark is a folder",
    "the done mark is older than resolved.json, as after a write that skipped the markers",
    "the marker folder is missing",
    "the marker folder can not be read",
]


def break_markers(home, how):
    folder, done = os.path.join(home.state, TOOL_PARTS), os.path.join(home.state, MARKERS_DONE)
    if how == "the done mark is missing, as after an apply from before this check":
        os.unlink(done)
        shutil.rmtree(folder)
    elif how == "the done mark is missing, as after an apply that stopped midway":
        os.unlink(done)
    elif how == "the done mark can not be read":
        os.chmod(done, 0)
    elif how == "the done mark is a folder":
        os.unlink(done)
        os.mkdir(done)
    elif how.startswith("the done mark is older"):
        resolved = os.stat(os.path.join(home.state, "resolved.json")).st_mtime
        os.utime(done, (resolved - 10, resolved - 10))
    elif how == "the marker folder is missing":
        shutil.rmtree(folder)
    elif how == "the marker folder can not be read":
        os.chmod(folder, 0)
    return folder


@pytest.mark.parametrize("how", BROKEN_MARKS)
def test_markers_the_check_can_not_trust_start_the_dispatcher(home, how):
    with given(f"hub is installed as it ships, and {how}"):
        home.install()
        group = EveryToolGroup(home, "PreToolUse", 1)
        folder = break_markers(home, how)
    try:
        with when("the main chat reads a file"):
            result, _seconds = named_call(group, "Read")
        with then("the group starts the dispatcher, which answers as usual"):
            assert group.python_starts() == 1 and result.output == {}
    finally:
        if os.path.isdir(folder):
            os.chmod(folder, 0o700)
    with when("the user runs apply"):
        home.hubctl("apply")
        result, _seconds = named_call(group, "Read")
    with then("the markers are whole again, and the next read starts no Python"):
        assert os.path.isfile(os.path.join(home.state, MARKERS_DONE))
        assert group.python_starts() == 1


@pytest.mark.parametrize("event", ["PostToolUse", "UserPromptSubmit", "Stop"])
def test_without_a_done_mark_the_other_groups_start_the_dispatcher_too(home, event):
    with given(f"hub is installed as it ships, with no {event} part on, and the done mark is missing"):
        home.install()
        group = EveryToolGroup(home, event, 0)
        data = load_fixture("claude_code", {"PostToolUse": "PostToolUse.Read.json",
                                            "UserPromptSubmit": "UserPromptSubmit.json", "Stop": "Stop.json"}[event])
        data.update(cwd=home.work)
        group.send(data)
        assert group.python_starts() == 0 and group.fast_path()
        os.unlink(os.path.join(home.state, MARKERS_DONE))
    with when(f"the {event} hook runs"):
        group.send(data)
    with then("its group no longer trusts its missing marker, and starts the dispatcher"):
        assert group.python_starts() == 1


def test_the_bash_group_starts_python_only_while_a_part_that_names_bash_runs(home):
    with given("hub is installed as it ships, where self_protection and the public text gate name Bash"):
        home.install()
        group = EveryToolGroup(home, "PreToolUse", 0)
        listing = home.hook_input("claude_code", "PreToolUse", "ls")
        listing.update(cwd=home.work)
    with then("apply marks Bash for both, and a command starts the dispatcher"):
        assert tool_marker(home, "Bash") == ["self_protection", GATE]
        group.send(listing)
        assert group.python_starts() == 1
    with when("the user switches the gate and self_protection off in /hub"):
        home.hubctl_from_mod("off", GATE)
        home.hubctl_from_mod("off", "self_protection")
        result, _seconds = group.send(listing)
    with then("the Bash marker is gone, and the command starts only the shell and cat"):
        assert tool_marker(home, "Bash") is None
        assert result.output == {} and group.python_starts() == 1 and not any(group.errors)
    with when("the user switches the coordinator on, which also names Bash"):
        home.hubctl("on", "coordinator")
        group.send(listing)
    with then("apply marks Bash for it, and the command starts the dispatcher"):
        assert tool_marker(home, "Bash") == ["coordinator"] and group.python_starts() == 2
    with when("the user switches it off again in /hub"):
        home.hubctl_from_mod("off", "coordinator")
        group.send(listing)
    with then("no marker is left, and Python does not start"):
        assert tool_marker(home, "Bash") is None and group.python_starts() == 2


def test_a_marker_that_can_not_be_read_or_is_empty_still_starts_the_dispatcher(home):
    with given("the coordinator is on, so Read and Edit have markers"):
        home.install()
        home.hubctl("on", "coordinator")
        group = EveryToolGroup(home, "PreToolUse", 1)
        os.chmod(os.path.join(home.state, TOOL_PARTS, "Read"), 0)
        with open(os.path.join(home.state, TOOL_PARTS, "Edit"), "w"):
            pass
    with when("the main chat reads and edits a file"):
        named_call(group, "Read")
        named_call(group, "Edit")
    with then("both calls start the dispatcher"):
        assert group.python_starts() == 2


@pytest.mark.parametrize("case", ["the name appears twice", "the name is not text", "the name has odd characters",
                                  "the name is missing", "the name is escaped", "the input is not JSON"])
def test_a_tool_name_the_check_can_not_read_starts_the_dispatcher(home, case):
    with given("hub is installed as it ships, where no running part names Read"):
        home.install()
        group = EveryToolGroup(home, "PreToolUse", 1)
        data = load_fixture("claude_code", "PreToolUse.SubagentHandback.json")
        data.update(cwd=home.work, tool_use_id="toolu_odd_000001", tool_input={"file_path": "notes.md"})
        for key in ("agent_id", "agent_type", "agent_transcript_path"):
            data.pop(key, None)
        data.pop("tool_name", None)
        text = {
            "the name appears twice": json.dumps(dict({"tool_input": {"tool_name": "Read"}},
                                                      **{key: value for key, value in data.items()
                                                         if key != "tool_input"}, tool_name="Agent")),
            "the name is not text": json.dumps(dict(data, tool_name=None)),
            "the name has odd characters": json.dumps(dict(data, tool_name="Read/../Agent")),
            "the name is missing": json.dumps(data),
            "the name is escaped": json.dumps(dict(data, tool_name="Re\nad")),
            "the input is not JSON": "not json",
        }[case]
    with when(f"a call comes where {case}"):
        process = subprocess.run(["/bin/sh", "-c", group.command], cwd=home.work, env=group.env,
                                 input=text, capture_output=True, text=True, timeout=30)
    with then("the group starts the dispatcher, and the hook still ends cleanly"):
        assert process.returncode == 0, process.stderr
        assert group.python_starts() == 1


def test_the_post_tool_use_group_for_every_other_tool_starts_python_only_while_a_part_handles_every_tool(home):
    every_marker = "post_tool_use_every_parts"
    with given("hub is installed as it ships, with the permission relay off"):
        home.install()
        named, every = EveryToolGroup(home, "PostToolUse", 0), EveryToolGroup(home, "PostToolUse", 1)
        bash_input = load_fixture("claude_code", "PostToolUse.Read.json")
        bash_input.update(cwd=home.work, tool_name="Bash", tool_input={"command": "make"},
                          tool_response={"stdout": "", "stderr": ""})
        read_input = load_fixture("claude_code", "PostToolUse.Read.json")
        read_input.update(cwd=home.work, tool_input={"file_path": os.path.join(home.work, "notes.md")})
    with then("apply leaves no marker for the every-tool group"):
        assert marker(home, every_marker) is None
    with when("a shell call ends, through the every-tool group"):
        result, _seconds = every.send(bash_input)
    with then("the group ends with no answer, and Python never starts"):
        assert result.output == {} and every.python_starts() == 0
    with when("the user switches the coordinator and the relay on, and a shell call and a read end"):
        home.hubctl("on", "coordinator")
        home.hubctl("on", "permission_relay")
        every.send(bash_input)
        named.send(read_input)
    with then("apply marks the relay for both groups, and each group starts the dispatcher once"):
        assert marker(home, every_marker) == ["permission_relay"]
        assert "permission_relay" in marker(home, EVENT_MARKERS["PostToolUse"])
        assert every.python_starts() == 1 and named.python_starts() == 1
    with when("the user switches the relay off again in /hub"):
        home.hubctl_from_mod("off", "permission_relay")
        every.send(bash_input)
    with then("the marker is gone, and Python does not start again"):
        assert marker(home, every_marker) is None and every.python_starts() == 1


def test_a_push_with_a_private_path_is_still_refused_while_the_context_budget_runs(home):
    with given("a repo with a bare remote, a commit message that holds a home path, "
               "the context budget on, and an agent at 135k context"):
        home.install()
        home.hubctl("on", "context_budget")
        bare = os.path.join(home.root, "budget-remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("commit", "-q", "--allow-empty", "-m", "Keep the plan in /home/sam/plan.md")
        agent_at(home, "a0000000000000031", 135000)
    with when("that agent sends git push"):
        data = home.hook_input("claude_code", "PreToolUse", "git push origin main")
        data.update(transcript_path=main_transcript(home), agent_id="a0000000000000031",
                    agent_transcript_path=agent_transcript_path(home, "a0000000000000031"))
        result = home.send("claude_code", "PreToolUse", data)
    with then("the gate still refuses it for the private path"):
        assert result.refused and result.findings == ["private_paths"]
