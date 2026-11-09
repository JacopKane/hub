"""Slice 10: the coordinator chat, Claude Code only.

The coordinator is named LOBBY in these scenarios, which proves the name is a
setting. Each chat is the hook input it would send, fed to the real
dispatcher in a temporary HOME. The chat listing is a fake `claude agents
--json` with the documented fields, and an inbox is a real socket.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from chats import SESSIONS, Chats
from steps import and_, given, then, when

DASH = chr(0x2014)


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def coordinator_on(home, name="LOBBY"):
    home.install()
    home.hubctl("on", "coordinator")
    if name is not None:
        home.hubctl("set", "coordinator.name", name)


def part_text(home, name):
    with open(os.path.join(home.plugin, "parts", "coordinator", "text", name)) as handle:
        return handle.read().strip()


def notes_path(home, name):
    return os.path.join(home.root, ".claude", "projects", "demo", SESSIONS[name], "live_notes.md")


QUESTION = {"questions": [{"question": "Which colour?", "header": "Colour", "multiSelect": False,
                           "options": [{"label": "Blue", "description": "Calm"},
                                       {"label": "Red", "description": "Loud"}]}]}


def test_a_lobby_chat_is_refused_topic_work_and_a_web_chat_is_not(home, chats):
    source = os.path.join(home.work, "app.py")
    with given("the coordinator is on and named LOBBY, and LOBBY and WEB run"):
        coordinator_on(home)
        chats.start("LOBBY", title="LOBBY")
        chats.start("WEB", title="WEB")
    with when("LOBBY edits a file in the repo, runs the tests or makes a commit"):
        calls = [chats.tool("LOBBY", "Edit", {"file_path": source, "old_string": "a", "new_string": "b"}),
                 chats.tool("LOBBY", "Write", {"file_path": source, "content": "print(1)\n"}),
                 chats.tool("LOBBY", "Bash", {"command": "cd work && python3 -m pytest -q tests"}),
                 chats.tool("LOBBY", "Bash", {"command": "npm test"}),
                 chats.tool("LOBBY", "Bash", {"command": "git -C work/demo commit -m 'Fix it'"})]
    with then("each call is refused, and the reason says to send the work to the chat that owns it"):
        assert [call.refused for call in calls] == [True] * 5
        for call in calls:
            assert "LOBBY" in call.reason and "owns the work" in call.reason
        assert calls[0].reason == part_text(home, "work_guard.md").format(name="LOBBY")
    with and_("a command that only reads, and a git step that is not a commit, run"):
        assert chats.tool("LOBBY", "Bash", {"command": "grep -n pytest README.md"}).ran
        assert chats.tool("LOBBY", "Bash", {"command": "git log --oneline -3"}).ran
    with when("LOBBY writes its live notes, a subagent of LOBBY edits the file, and WEB edits it"):
        notes = chats.tool("LOBBY", "Write", {"file_path": notes_path(home, "LOBBY"), "content": "- a fact\n"})
        subagent = chats.tool("LOBBY", "Edit", {"file_path": source, "old_string": "a", "new_string": "b"},
                              agent_id="agent-1", agent_type="builder")
        web = chats.tool("WEB", "Edit", {"file_path": source, "old_string": "a", "new_string": "b"})
    with then("all three run"):
        assert [notes.ran, subagent.ran, web.ran] == [True, True, True]
    with and_("LOBBY may also write its memory folder"):
        memory = os.path.join(home.root, ".claude", "projects", "demo", "memory", "fact.md")
        assert chats.tool("LOBBY", "Write", {"file_path": memory, "content": "x"}).ran
    with and_("a write to hub's state folder is refused by self-protection, not by the work guard"):
        state_note = {"file_path": os.path.join(home.state, "note.md"), "content": "x"}
        guarded = chats.tool("LOBBY", "Write", state_note)
        assert guarded.refused and "owns the work" not in guarded.reason
    with when("the user turns self_protection.settings_files off in /hub"):
        home.hubctl_from_mod("off", "self_protection.settings_files")
    with then("the work guard lets LOBBY write hub's state folder"):
        assert chats.tool("LOBBY", "Write", state_note).ran


def test_the_coordinator_is_off_until_switched_on_and_its_name_defaults_to_hub(home, chats):
    source = os.path.join(home.work, "app.py")
    edit = {"file_path": source, "old_string": "a", "new_string": "b"}
    with given("hub is installed with the coordinator at its default, and a chat named hub runs and is listed"):
        home.install()
        chats.start("HUB", title="hub")
        chats.set_listing([chats.row("hub", pid=os.getpid(), session=SESSIONS["HUB"])])
    with then("the chat edits freely"):
        assert chats.tool("HUB", "Edit", edit).ran
    with when("the user switches the coordinator on and leaves its name alone"):
        home.hubctl("on", "coordinator")
    with then("the chat named hub is refused, whatever the case of its name"):
        assert chats.tool("HUB", "Edit", edit).refused
    with when("WEB is renamed to HUB in the middle of its session"):
        chats.start("WEB", title="WEB")
        before = chats.tool("WEB", "Edit", edit)
        chats.send("WEB", "UserPromptSubmit", prompt="carry on", session_title="HUB")
    with then("its next edit is refused, and the one before it ran"):
        assert before.ran and chats.tool("WEB", "Edit", edit).refused
    with when("a chat that started before hub has no record, and the listing names it HUB"):
        chats.set_listing([chats.row("hub", pid=os.getpid(), session=SESSIONS["HUB"]),
                           chats.row("HUB", pid=os.getpid(), session=SESSIONS["API"])])
        api = chats.tool("API", "Edit", edit)
    with then("it is refused too, from the listing"):
        assert api.refused
    with when("the user switches coordinator.work_guard off in /hub"):
        home.hubctl_from_mod("off", "coordinator.work_guard")
    with then("the chat named hub edits freely again"):
        assert chats.tool("HUB", "Edit", edit).ran


def test_with_a_live_lobby_a_question_in_web_goes_to_lobby(home, chats):
    with given("LOBBY is live on this machine, with an inbox, and the listing shows LOBBY and WEB"):
        coordinator_on(home)
        lobby = chats.start("LOBBY", title="LOBBY", live=True, inbox=True)
        chats.start("WEB", title="WEB")
        chats.set_listing([chats.row("LOBBY", pid=lobby.pid), chats.row("WEB", pid=os.getpid())])
    with when("WEB opens a question dialog"):
        asked = chats.tool("WEB", "AskUserQuestion", QUESTION)
    with then("it is refused, and the reason names LOBBY's address and asks for the whole question"):
        assert asked.refused
        assert "uds:" + lobby.address in asked.reason
        assert "whole question" in asked.reason
        assert asked.reason == part_text(home, "question_guard.md").format(
            name="LOBBY", address="uds:" + lobby.address, urgent="[urgent]")
    with and_("LOBBY itself opens its dialog, and a subagent's dialog is left alone"):
        assert chats.tool("LOBBY", "AskUserQuestion", QUESTION).ran
        assert chats.tool("WEB", "AskUserQuestion", QUESTION, agent_id="agent-2").ran
    with when("LOBBY's process ends"):
        lobby.end()
    with then("WEB's next dialog opens, though the listing still shows LOBBY"):
        assert chats.tool("WEB", "AskUserQuestion", QUESTION).ran
    with when("another process takes LOBBY's old pid in hub's record"):
        stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            record_path = os.path.join(home.state, "sessions", SESSIONS["LOBBY"] + ".json")
            with open(record_path) as handle:
                record = json.load(handle)
            record["pid"] = stranger.pid
            with open(record_path, "w") as handle:
                json.dump(record, handle)
            chats.set_listing([chats.row("LOBBY", pid=stranger.pid), chats.row("WEB", pid=os.getpid())])
            reused = chats.tool("WEB", "AskUserQuestion", QUESTION)
        finally:
            stranger.kill()
            stranger.wait()
    with then("the reused pid does not count as LOBBY, and the dialog opens"):
        assert reused.ran


def test_a_lobby_with_no_hub_record_gets_questions_by_its_name(home, chats):
    with given("LOBBY started before hub was on, so only the listing knows it"):
        coordinator_on(home)
        chats.start("WEB", title="WEB")
        chats.set_listing([chats.row("LOBBY", pid=os.getpid()), chats.row("WEB", pid=os.getpid())])
    with when("WEB opens a question dialog"):
        asked = chats.tool("WEB", "AskUserQuestion", QUESTION)
    with then("it is refused, and the address is LOBBY's name"):
        assert asked.refused and "at address LOBBY." in asked.reason
    with when("the listing shows LOBBY with no process, as a chat that has ended"):
        chats.set_listing([chats.row("LOBBY"), chats.row("WEB", pid=os.getpid())])
    with then("the dialog opens"):
        assert chats.tool("WEB", "AskUserQuestion", QUESTION).ran


@pytest.mark.parametrize("failure", ["slow", "error"])
def test_a_slow_or_failing_listing_allows_the_dialog_with_one_line(home, chats, failure):
    with given(f"the listing is {failure}, and LOBBY is live"):
        coordinator_on(home)
        lobby = chats.start("LOBBY", title="LOBBY", live=True, inbox=True)
        rows = [chats.row("LOBBY", pid=lobby.pid)]
        chats.set_listing(rows, delay=5 if failure == "slow" else 0, exit_code=0 if failure == "slow" else 3)
    with when("WEB opens a question dialog twice"):
        started = time.monotonic()
        first = chats.tool("WEB", "AskUserQuestion", QUESTION)
        took = time.monotonic() - started
        second = chats.tool("WEB", "AskUserQuestion", QUESTION)
    with then("both dialogs open, the first within the listing's time limit"):
        assert first.ran and second.ran
        assert took < 4
    with and_("one line in the doctor log names the failed listing"):
        lines = chats.doctor_log()
        assert len(lines) == 1 and "claude agents --json" in lines[0]
        assert chats.listing_calls() == 1


def test_the_coordinator_loads_its_skill_and_is_reminded_to_delegate(home, chats):
    nudge = None
    with given("the coordinator is on and named LOBBY"):
        coordinator_on(home)
        nudge = part_text(home, "start_nudge.md").format(name="LOBBY")
    with when("LOBBY starts, and its skill was not loaded since the last compaction"):
        started = chats.start("LOBBY", title="LOBBY")
    with then("LOBBY's start text tells it to load hub-coordinator"):
        assert nudge in started.context and "hub-coordinator" in nudge
    with and_("its prompts carry the same line until it loads the skill"):
        assert nudge in chats.send("LOBBY", "UserPromptSubmit", prompt="hello").context
        chats.send("LOBBY", "PostToolUse", "Skill", {"skill": "hub-coordinator"},
                   tool_response={"success": True})
        assert nudge not in chats.send("LOBBY", "UserPromptSubmit", prompt="next").context
    with when("LOBBY is compacted"):
        compacted = chats.send("LOBBY", "SessionStart", source="compact")
    with then("the line comes back, and a typed /hub-coordinator counts as loading it"):
        assert nudge in compacted.context
        assert nudge not in chats.send("LOBBY", "UserPromptSubmit", prompt="/hub-coordinator").context
    with and_("WEB never gets the line"):
        assert nudge not in chats.start("WEB", title="WEB").context
    with when("LOBBY makes three Read or Bash calls on one prompt, outside its bookkeeping"):
        chats.send("LOBBY", "UserPromptSubmit", prompt="look at the repo")
        bookkeeping = chats.tool("LOBBY", "Read", {"file_path": notes_path(home, "LOBBY")})
        calls = [chats.tool("LOBBY", "Read", {"file_path": os.path.join(home.work, "a.py")}),
                 chats.tool("LOBBY", "Bash", {"command": "ls"}),
                 chats.tool("LOBBY", "Read", {"file_path": os.path.join(home.work, "b.py")})]
    with then("the third call carries one line that asks whether to delegate"):
        line = part_text(home, "delegate_nudge.md").format(name="LOBBY")
        assert [line in call.context for call in calls] == [False, False, True]
        assert line not in bookkeeping.context
    with and_("a new prompt starts the count again, and WEB is never counted"):
        chats.send("LOBBY", "UserPromptSubmit", prompt="again")
        assert line not in chats.tool("LOBBY", "Read", {"file_path": os.path.join(home.work, "c.py")}).context
        assert all(line not in chats.tool("WEB", "Bash", {"command": "ls"}).context for _ in range(3))
    with when("coordinator.delegate_nudge.every is set to 1"):
        home.hubctl("set", "coordinator.delegate_nudge.every", "1")
        chats.send("LOBBY", "UserPromptSubmit", prompt="once more")
    with then("every call carries the line"):
        assert line in chats.tool("LOBBY", "Bash", {"command": "ls"}).context


def test_the_coordinator_skill_has_one_section_per_instruction_and_each_turns_off_alone(home):
    with given("the coordinator is on, and chat_board too, so every section of the skill runs"):
        coordinator_on(home)
        home.hubctl("on", "chat_board")
        link = os.path.join(home.root, ".claude", "skills", "hub-coordinator")
        shipped = os.path.join(home.plugin, "parts", "coordinator", "skill")
        with open(os.path.join(home.plugin, "parts", "coordinator", "part.json")) as handle:
            manifest = json.load(handle)
        group = next(child for child in manifest["children"] if child["id"] == "instructions")
        instructions = [f"coordinator.instructions.{child['id']}" for child in group["children"]]
    with then("~/.claude/skills/hub-coordinator links to the shipped skill, named hub-coordinator"):
        assert os.readlink(link) == os.path.join(home.state, "built", "skills", "hub-coordinator")
        with open(os.path.join(shipped, "SKILL.md")) as handle:
            text = handle.read()
        assert text.startswith("---\nname: hub-coordinator\n")
    with and_("each instruction flag has exactly one section, and no other section is there"):
        found = [line[len("<!-- hub:section "):-len(" -->")] for line in text.splitlines()
                 if line.startswith("<!-- hub:section ")]
        assert sorted(found) == sorted(instructions) and len(found) == 5
        assert DASH not in text
    with when("the user turns off coordinator.instructions.relay"):
        home.hubctl("off", "coordinator.instructions.relay")
    with then("the link points at a built copy without the relay section and with the forward section"):
        with open(os.path.join(link, "SKILL.md")) as handle:
            copy = handle.read()
        def section(flag_id):
            return text.split(f"<!-- hub:section {flag_id} -->\n")[1].split("<!-- hub:end -->")[0].strip()
        assert os.readlink(link) != shipped
        assert section("coordinator.instructions.relay") not in copy
        assert section("coordinator.instructions.forward") in copy
        assert "<!-- hub:" not in copy
    with when("the user turns it on again"):
        home.hubctl("on", "coordinator.instructions.relay")
    with then("the link points at the shipped skill again"):
        assert os.readlink(link) == os.path.join(home.state, "built", "skills", "hub-coordinator")
