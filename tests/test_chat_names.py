"""Slice 10: chat names, Claude Code only.

A send by a plain chat name goes to the one live chat of that name on this
machine, by its own inbox address, so a dead row of the same name never
takes the message. A send that met an offline row or a stopped chat gets one
note. Each chat is the hook input it would send, fed to the real dispatcher
in a temporary HOME, with a fake `claude agents --json` and real sockets.
"""
import os
import subprocess
import sys

import pytest

from chats import SESSIONS, Chats
from steps import and_, given, then, when

OFFLINE = {"success": True, "message": "API is offline right now. The message is delivered when it reconnects."}


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def chat_names_on(home):
    home.install()
    home.hubctl("on", "chat_names")


def part_text(home, name):
    with open(os.path.join(home.plugin, "parts", "chat_names", "text", name)) as handle:
        return handle.read().strip()


def send(chats, sender, to, **extra):
    return chats.tool(sender, "SendMessage", {"to": to, "message": "The tests pass."}, **extra)


def rewritten_to(result):
    return result.output.get("hookSpecificOutput", {}).get("updatedInput", {}).get("to")


def test_a_plain_chat_name_reaches_the_live_chat_and_never_a_dead_row(home, chats):
    with given("two rows are named DOCS, and one belongs to a chat whose process ended"):
        chat_names_on(home)
        old = chats.start("DOCS-OLD", title="DOCS", live=True, inbox=True)
        old.end()
        docs = chats.start("DOCS", title="DOCS", live=True, inbox=True)
    with and_("a third DOCS row has no hub record and a process that ended"):
        gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True).stdout.strip()
        rows = [chats.row("DOCS", pid=old.pid, session="DOCS-OLD"), chats.row("DOCS", pid=int(gone), session="DOCS-GONE"),
                chats.row("DOCS", pid=docs.pid), chats.row("WEB", pid=os.getpid())]
        chats.set_listing(rows)
    with when("WEB sends a message to \"DOCS\""):
        sent = send(chats, "WEB", "DOCS")
    with then("the send goes to the live chat's own address, with the message unchanged"):
        assert sent.ran
        assert rewritten_to(sent) == "uds:" + docs.address
        assert sent.output["hookSpecificOutput"]["updatedInput"]["message"] == "The tests pass."
    with and_("the client's own name match holds, so \" docs \" reaches it too"):
        assert rewritten_to(send(chats, "WEB", " docs ")) == "uds:" + docs.address
    with and_("an address, a name with a ref, a subagent's send and a send to itself are left alone"):
        assert rewritten_to(send(chats, "WEB", "uds:" + docs.address)) is None
        assert rewritten_to(send(chats, "WEB", "DOCS [a1b2]")) is None
        assert rewritten_to(send(chats, "WEB", "DOCS", agent_id="agent-7")) is None
        assert rewritten_to(send(chats, "DOCS", "DOCS")) is None
    with when("a second live chat takes the name DOCS"):
        second = chats.start("DOCS-TWO", title="DOCS", live=True, inbox=True)
        chats.set_listing(rows + [chats.row("DOCS", pid=second.pid, session="DOCS-TWO")])
    with then("the name means two chats, so the native send decides"):
        assert rewritten_to(send(chats, "WEB", "DOCS")) is None
    with when("chat_names.resolve is off and only one DOCS runs"):
        second.end()
        chats.set_listing(rows)
        home.hubctl("off", "chat_names.resolve")
    with then("the send is left alone"):
        assert rewritten_to(send(chats, "WEB", "DOCS")) is None


def test_a_send_that_met_an_offline_row_or_a_stopped_chat_gets_one_note(home, chats):
    with given("WEB and a live DOCS with an inbox"):
        chat_names_on(home)
        docs = chats.start("DOCS", title="DOCS", live=True, inbox=True)
        chats.set_listing([chats.row("DOCS", pid=docs.pid), chats.row("WEB", pid=os.getpid())])
    with when("a send by plain name meets an offline row"):
        offline = chats.send("WEB", "PostToolUse", "SendMessage", {"to": "API", "message": "Hi."},
                             tool_response=OFFLINE)
    with then("one note tells WEB that the message did not arrive"):
        assert offline.context == part_text(home, "offline_note.md").format(name="API")
    with when("a send to DOCS's address fails because DOCS stopped"):
        stopped = chats.send("WEB", "PostToolUse", "SendMessage", {"to": "uds:" + docs.address, "message": "Hi."},
                             tool_response={"success": False, "message": "connect failed"})
    with then("the note names DOCS"):
        assert stopped.context == part_text(home, "stopped_note.md").format(name="DOCS")
    with and_("a send that arrived adds nothing, and neither does a subagent's"):
        arrived = chats.send("WEB", "PostToolUse", "SendMessage", {"to": "DOCS", "message": "Hi."},
                             tool_response={"success": True, "message": "Message sent to DOCS"})
        assert arrived.context == ""
        subagent = chats.send("WEB", "PostToolUse", "SendMessage", {"to": "API", "message": "Hi."},
                              tool_response=OFFLINE, agent_id="agent-7")
        assert subagent.context == ""
    with when("chat_names.offline_note is off"):
        home.hubctl("off", "chat_names.offline_note")
    with then("neither note comes"):
        assert chats.send("WEB", "PostToolUse", "SendMessage", {"to": "API", "message": "Hi."},
                          tool_response=OFFLINE).context == ""
        assert chats.send("WEB", "PostToolUse", "SendMessage", {"to": "uds:" + docs.address, "message": "Hi."},
                          tool_response={"success": False, "message": "connect failed"}).context == ""
