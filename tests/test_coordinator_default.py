"""The coordinator is on by default in Claude Code, and its hooks sleep until a chat has its name.

Each call goes through Claude Code's own hook group (hooks.json, then
pre_check.sh), so a test sees whether Python starts at all. The chat listing
is the fake `claude agents --json`, the one outer seam, and the tests count
its calls. A live chat is a real process that waits, and an inbox is a real
socket.
"""
import os
import shutil

from chats import Chats
from conftest import FIXTURES, PLUGIN, Home
from steps import and_, given, then, when
from test_coordinator import QUESTION, part_text
from test_dispatch_order import EveryToolGroup

START_LINE = "To start it, type /rename HUB in the chat you want."
MARKS = "coordinator_chats"


class Groups:
    """Claude Code's hook groups for the calls a chat makes, each run as Claude Code runs it."""

    def __init__(self, home, chats):
        self.chats = chats
        self.bash = EveryToolGroup(home, "PreToolUse", 0)
        self.named = EveryToolGroup(home, "PreToolUse", 1)
        self.prompt = EveryToolGroup(home, "UserPromptSubmit", 0)
        self.after = EveryToolGroup(home, "PostToolUse", 0)

    def tool(self, name, tool_name, tool_input):
        group = self.bash if tool_name == "Bash" else self.named
        return group.send(self.chats.input(name, "PreToolUse", tool_name, tool_input))[0]

    def type(self, name, prompt, **extra):
        return self.prompt.send(self.chats.input(name, "UserPromptSubmit", prompt=prompt, **extra))[0]

    def skill(self, name, skill):
        return self.after.send(self.chats.input(name, "PostToolUse", "Skill", {"skill": skill}))[0]

    def python_starts(self):
        return sum(group.python_starts() for group in (self.bash, self.named, self.prompt, self.after))


def topic_work(home):
    source = os.path.join(home.work, "app.py")
    return [("Read", {"file_path": source}), ("Grep", {"pattern": "login"}), ("Glob", {"pattern": "*.py"}),
            ("Edit", {"file_path": source, "old_string": "a", "new_string": "b"}),
            ("Write", {"file_path": source, "content": "x\n"}),
            ("Bash", {"command": "ls"}), ("Bash", {"command": "python3 -m pytest -q"}),
            ("Bash", {"command": "git commit -m 'Fix login'"}), ("AskUserQuestion", QUESTION)]


def marks(home):
    folder = os.path.join(home.state, MARKS)
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def parts_marked(home, name):
    path = os.path.join(home.state, name)
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return handle.read().split()


def set_up(home):
    answers = os.path.join(FIXTURES, "agent_answers")
    home.step("setup", "--answer", os.path.join(answers, "nothing.json"))
    return home.step("setup", "--reply", "{}").stdout


def as_in_0_20_2(part):
    """The coordinator's manifest before it was on by default, off and offered, never on by itself."""
    part["default"] = "off"
    part.pop("never_offered", None)
    part["offer_when"] = "profile.chats_running >= 2"


def ship_this_version(home):
    shutil.copy(os.path.join(PLUGIN, "parts", "coordinator", "part.json"),
                os.path.join(home.plugin, "parts", "coordinator", "part.json"))


def test_the_coordinator_is_on_by_default_and_costs_nothing_until_a_chat_has_its_name(home, tmp_path):
    chats = Chats(home)
    try:
        with given("a fresh install, set up with nothing picked, and one chat, WEB, with no name"):
            home.install()
            summary = set_up(home)
            chats.start("WEB")
            groups = Groups(home, chats)
        with then("the coordinator runs, and setup's summary says how to start it"):
            assert home.show()["flags"]["coordinator"]["runs"]
            assert home.show()["flags"]["coordinator"]["by"] == "default"
            assert "- coordinator:" in summary and START_LINE in summary
        with when("WEB reads, greps, globs, edits, writes, runs tests, commits, asks, types and loads a skill"):
            calls = [groups.tool("WEB", tool_name, tool_input) for tool_name, tool_input in topic_work(home)]
            calls.append(groups.type("WEB", "carry on"))
            calls.append(groups.skill("WEB", "pr"))
        with then("every call runs with no refusal and no added line, Python never starts, and nobody "
                  "lists the chats"):
            assert [call.ran for call in calls] == [True] * len(calls)
            assert [call.context for call in calls] == [""] * len(calls)
            assert groups.python_starts() == 0
            assert chats.listing_calls() == 0

        with when("a second chat starts with no name, and the user types /rename HUB in it"):
            hub = chats.start("HUB", live=True, inbox=True)
            chats.set_listing([chats.row("HUB", pid=hub.pid), chats.row("WEB", pid=os.getpid())])
            renamed = groups.type("HUB", "you coordinate now", session_title="HUB")
        with then("that prompt reaches hub, which tells HUB to load the coordinator skill and marks it"):
            assert part_text(home, "start_nudge.md").format(name="HUB") in renamed.context
            assert marks(home) == [hub.session]
        with when("HUB edits a file and runs the tests"):
            edit = groups.tool("HUB", "Edit", topic_work(home)[3][1])
            test_run = groups.tool("HUB", "Bash", {"command": "python3 -m pytest -q"})
        with then("both are refused, and the reason says how to send the step on, rename the chat or turn "
                  "the part off"):
            assert edit.refused and test_run.refused
            assert edit.reason == part_text(home, "work_guard.md").format(name="HUB")
            assert "/rename" in edit.reason and "turn the coordinator off in /hub" in edit.reason
        with and_("HUB may still read"):
            assert groups.tool("HUB", "Read", topic_work(home)[0][1]).ran
        with when("WEB asks the user a question"):
            asked = groups.tool("WEB", "AskUserQuestion", QUESTION)
        with then("it is refused, and the reason gives HUB's address, so the question goes to HUB"):
            assert asked.refused and "uds:" + hub.address in asked.reason
        with and_("WEB still edits its own files"):
            assert groups.tool("WEB", "Edit", topic_work(home)[3][1]).ran

        with when("HUB's process ends without a SessionEnd, as a crash does"):
            hub.end()
            starts = groups.python_starts()
            after_hub = [groups.tool("WEB", tool_name, tool_input) for tool_name, tool_input in topic_work(home)]
        with then("WEB's calls run and sleep again, with no Python start"):
            assert [call.ran for call in after_hub] == [True] * len(after_hub)
            assert groups.python_starts() == starts
        with when("the next chat starts"):
            chats.start("DOCS")
        with then("its start drops HUB's old mark"):
            assert marks(home) == []

        with when("the user turns the coordinator off in /hub, the one place that may turn its guard off"):
            home.step_from_mod("off", "coordinator")
        with then("the skill link and the coordinator's hook markers are gone"):
            assert not os.path.lexists(os.path.join(home.root, ".claude", "skills", "hub-coordinator"))
            for marker in ("tool_parts/Read", "tool_parts/Edit", "tool_parts/Bash", "tool_parts/AskUserQuestion",
                           "post_tool_parts/Skill", "user_prompt_parts"):
                assert "coordinator" not in parts_marked(home, marker), marker
        with and_("a chat started as HUB edits freely"):
            chats.start("API", title="HUB")
            assert groups.tool("API", "Edit", topic_work(home)[3][1]).ran
    finally:
        chats.close()

    with given("an install of 0.20.2, where the coordinator was off by default, and a second one where the "
               "user had switched it off"):
        updated, pinned = Home(tmp_path / "updated"), Home(tmp_path / "pinned")
        for old in (updated, pinned):
            old.install()
            old.edit_manifest("coordinator", as_in_0_20_2)
            old.step("apply")
            assert not old.show()["flags"]["coordinator"]["runs"]
        pinned.step("on", "coordinator")
        pinned.step_from_mod("off", "coordinator")
        assert pinned.settings()["flags"]["coordinator"] == {"by": "user", "value": "off"}
    with when("both update to this version, and a chat starts in each"):
        for old in (updated, pinned):
            ship_this_version(old)
            other = Chats(old)
            try:
                other.start("WEB")
            finally:
                other.close()
    with then("the coordinator runs in the first install, by its default"):
        assert updated.show()["flags"]["coordinator"]["runs"]
        assert parts_marked(updated, "tool_parts/Edit") == ["coordinator"]
    with and_("it stays off for the user who switched it off, with no hook marker"):
        assert not pinned.show()["flags"]["coordinator"]["runs"]
        assert "coordinator" not in parts_marked(pinned, "tool_parts/Edit")
