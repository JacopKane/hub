"""Slice 10: what hubctl doctor says about the chat parts, and how they stay quiet in Cursor.

The coordinator, chat_names, live_notes and restart run only in Claude
Code. The doctor says for each one that is on whether the chat listing it
reads works, and, when Cursor is linked, why the part does not run there.
When the restart looks for tmux panes, it asks a private tmux server that
does not run, so no test reads the user's own panes.
"""
import json
import os

import pytest

from chats import SESSIONS, Chats
from steps import and_, given, then, when

PARTS = ["chat_names", "coordinator", "live_notes", "restart"]
CURSOR_CHAT = "00000000-0000-4000-8000-000000000011"  # the conversation id of the Cursor fixtures


@pytest.fixture
def chats(home):
    home.env["HUB_TMUX_SERVER"] = f"zztest{os.getpid()}none"
    chats = Chats(home)
    yield chats
    chats.close()


def doctor(home):
    return home.hubctl("doctor").stdout.splitlines()


def lines_about(lines, part):
    return [line for line in lines if line.split(": ", 2)[1:2] == [part]]


def reason_in_cursor(home, part):
    with open(os.path.join(home.plugin, "parts", part, "part.json")) as handle:
        return json.load(handle)["reason_not_in"]["cursor"]


def every_part_on(home):
    for part in PARTS:
        home.hubctl("on", part)
    home.hubctl("set", "coordinator.name", "LOBBY")


def test_the_doctor_names_each_chat_part_and_whether_the_listing_works(home, chats):
    with given("hub is installed with the chat parts at their defaults"):
        home.install()
    with then("the doctor says nothing about them"):
        lines = doctor(home)
        assert all(not lines_about(lines, part) for part in PARTS)
    with given("the coordinator, chat_names, live_notes and restart are on, and the listing shows a live LOBBY"):
        every_part_on(home)
        chats.set_listing([chats.row("LOBBY", pid=os.getpid()), chats.row("WEB", pid=os.getpid())])
    with when("the user runs hubctl doctor"):
        lines = doctor(home)
    with then("each part has one note, and the coordinator's names LOBBY as running"):
        for part in PARTS:
            assert len(lines_about(lines, part)) == 1, (part, lines)
            assert lines_about(lines, part)[0].startswith("note: ")
        assert "LOBBY" in lines_about(lines, "coordinator")[0]
        assert "runs now" in lines_about(lines, "coordinator")[0]
        assert "listing works" in lines_about(lines, "chat_names")[0]
        assert "{transcript_folder}/{session}/live_notes.md" in lines_about(lines, "live_notes")[0]
        assert "listing works" in lines_about(lines, "restart")[0]
    with when("no chat named LOBBY runs"):
        chats.set_listing([chats.row("WEB", pid=os.getpid())])
        coordinator = lines_about(doctor(home), "coordinator")[0]
    with then("the coordinator's note says so"):
        assert "no chat named LOBBY runs now" in coordinator
    with when("the listing exits with an error"):
        chats.set_listing([], exit_code=3)
        lines = doctor(home)
    with then("the coordinator, chat_names and restart lines are warnings that name the failed listing and its exit code"):
        for part in ("coordinator", "chat_names", "restart"):
            line = lines_about(lines, part)[0]
            assert line.startswith("warning: ") and "claude agents --json" in line and "exited with 3" in line
    with and_("the doctor's own check leaves the hooks' doctor log alone"):
        assert chats.doctor_log() == []
    with when("a hook logged a failed listing"):
        chats.set_listing([], exit_code=3)
        chats.tool("WEB", "AskUserQuestion", {"questions": []})
        logged = chats.doctor_log()
        lines = doctor(home)
    with then("the doctor names the log, how many lines it has, and the last one"):
        assert len(logged) == 1
        log_lines = [line for line in lines if "doctor.log" in line]
        assert len(log_lines) == 1 and "1 line" in log_lines[0] and logged[0] in log_lines[0]


def test_in_cursor_none_of_these_parts_run_and_the_doctor_says_why(home, chats):
    notes = os.path.join(home.root, ".claude", "projects", "demo", CURSOR_CHAT, "live_notes.md")
    transcript = os.path.join(home.root, ".claude", "projects", "demo", CURSOR_CHAT + ".jsonl")
    commit = {"command": "git commit -m 'Fix it'"}
    task = {"description": "Check the docs", "prompt": "Read the docs and report."}
    with given("every part of this slice is on, Cursor is linked, and the listing names the Cursor chat LOBBY"):
        home.install()
        every_part_on(home)
        home.hubctl("on", "cursor_link")
        chats.set_listing([chats.row("LOBBY", pid=os.getpid(), session=CURSOR_CHAT)])
    with and_("the same calls from a Claude Code chat with that id are refused"):
        assert chats.tool(CURSOR_CHAT, "Bash", commit).refused
        assert chats.tool(CURSOR_CHAT, "Agent", task).refused
        assert os.path.exists(notes)
        os.unlink(notes)
    with when("Cursor sends its hook input for the same events"):
        started = chats.cursor("sessionStart", conversation_id=CURSOR_CHAT, session_id=CURSOR_CHAT)
        shell = chats.cursor("preToolUse", "Shell", dict(commit, working_directory=home.work),
                             conversation_id=CURSOR_CHAT, transcript_path=transcript)
        agent = chats.cursor("preToolUse", "Task", task, conversation_id=CURSOR_CHAT, transcript_path=transcript)
    with then("nothing is refused, rewritten or nudged"):
        assert shell.ran and agent.ran
        for result in (shell, agent):
            assert "updated_input" not in result.output and not result.context
        assert "hub-coordinator" not in json.dumps(started.output)
        assert not os.path.exists(notes)
    with and_("hubctl doctor names each part and what Cursor lacks for it"):
        lines = doctor(home)
        for part in PARTS:
            cursor_lines = [line for line in lines_about(lines, part) if "Cursor" in line]
            assert len(cursor_lines) == 1, (part, lines)
            assert cursor_lines[0].startswith("note: ")
            assert reason_in_cursor(home, part) in cursor_lines[0]
        assert any("ListAgents" in line for line in lines_about(lines, "coordinator"))
    with when("the user turns the Cursor link off"):
        home.hubctl("off", "cursor_link")
    with then("the doctor no longer talks about Cursor for these parts"):
        lines = doctor(home)
        assert all("Cursor" not in line for part in PARTS for line in lines_about(lines, part))
