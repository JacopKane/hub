"""Slice 10: the live notes, Claude Code only.

Every agent a chat starts gets the chat's live notes file, so rules and facts
learned mid-work reach agents the chat forgets. Each chat is the hook input it
would send, fed to the real dispatcher in a temporary HOME.
"""
import os

import pytest

from chats import SESSIONS, Chats
from steps import and_, given, then, when


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def notes_path(home, name):
    return os.path.join(home.root, ".claude", "projects", "demo", SESSIONS[name], "live_notes.md")


def part_text(home, name, **values):
    with open(os.path.join(home.plugin, "parts", "live_notes", "text", name)) as handle:
        return handle.read().strip().format(**values)


def brief(prompt, **extra):
    return dict({"description": "[sonnet] Check the docs", "subagent_type": "researcher", "prompt": prompt}, **extra)


def message(text, to="researcher-1"):
    return {"to": to, "message": text, "summary": "A note"}


SENT = {"success": True, "message": "Message sent to researcher-1"}


def test_a_brief_without_the_live_notes_path_is_refused(home, chats):
    notes = notes_path(home, "WEB")
    with given("hub is installed with live_notes at its default"):
        home.install()
    with then("an agent starts with any brief, and no notes file is made"):
        assert chats.tool("WEB", "Agent", brief("Read the docs and report.")).ran
        assert not os.path.exists(notes)
    with given("live_notes is on, and the chat's notes file does not exist yet"):
        home.hubctl("on", "live_notes")
    with when("the chat starts an agent whose brief does not name the file"):
        refused = chats.tool("WEB", "Agent", brief("Read the docs and report."))
    with then("the call is refused, and the reason gives the path to add"):
        assert refused.refused
        assert refused.reason == part_text(home, "brief_hint.md", path=notes)
    with and_("the notes file now exists with its header"):
        with open(notes) as handle:
            assert handle.read() == part_text(home, "header.md") + "\n\n"
    with when("a brief names the file, or names it from the home folder with ~"):
        full = chats.tool("WEB", "Agent", brief(f"Live notes: {notes}. Read the docs and report."))
        short = notes.replace(home.root, "~", 1)
        tilde = chats.tool("WEB", "Agent", brief(f"Live notes: {short}. Read the docs and report."))
    with then("both run unchanged and quiet"):
        assert full.ran and tilde.ran
        assert "updatedInput" not in full.output.get("hookSpecificOutput", {}) and not full.context
    with when("a subagent starts its own agent without the file"):
        nested = chats.tool("WEB", "Agent", brief("Count the files."), agent_id="agent-1", agent_type="builder")
    with then("it runs, because the subagent's brief already carried the notes"):
        assert nested.ran
    with when("the notes of another chat are named instead of this chat's"):
        other = chats.tool("WEB", "Agent", brief(f"Live notes: {notes_path(home, 'DOCS')}. Report."))
    with then("the call is refused"):
        assert other.refused
    with when("the user turns live_notes.brief_names_file off"):
        home.hubctl("off", "live_notes.brief_names_file")
    with then("a brief without the file runs"):
        assert chats.tool("WEB", "Agent", brief("Read the docs and report.")).ran


def test_a_brief_that_starts_background_work_gets_the_stay_line(home, chats):
    notes = notes_path(home, "WEB")
    stay = told = None
    with given("live_notes is on"):
        home.install()
        home.hubctl("on", "live_notes")
        stay = part_text(home, "stay_line.md")
        told = part_text(home, "stay_line_told.md", line=stay)
    with when("a brief names the file and starts background work without saying to stay until it ends"):
        prompt = f"Live notes: {notes}.\nRun the suite with run_in_background, then fix what fails."
        added = chats.tool("WEB", "Agent", brief(prompt))
    with then("the call runs with the stay line added, and the sender is told"):
        assert added.ran
        updated = added.output["hookSpecificOutput"]["updatedInput"]
        assert updated["prompt"] == prompt + "\n\n" + stay
        assert updated["description"] == "[sonnet] Check the docs" and updated["subagent_type"] == "researcher"
        assert told in added.context
    with when("briefs only mention background work someone else runs, forbid it, or already say to stay"):
        quiet = [chats.tool("WEB", "Agent", brief(f"Live notes: {notes}. {text}")) for text in (
            "The other chat runs the tests in the background, so read the report only.",
            "Do not start background jobs.",
            "Start the build in the background and wait until it finishes, then report.",
            "If you start a background job, stay until it finishes, then hand back once.",
        )]
    with then("each runs unchanged"):
        for call in quiet:
            assert call.ran and "updatedInput" not in call.output.get("hookSpecificOutput", {})
    with when("the user turns live_notes.stay_line.tell_sender off"):
        home.hubctl("off", "live_notes.stay_line.tell_sender")
        silent = chats.tool("WEB", "Agent", brief(prompt))
    with then("the line is still added, and the sender is not told"):
        assert silent.output["hookSpecificOutput"]["updatedInput"]["prompt"].endswith(stay)
        assert told not in silent.context
    with when("the user turns live_notes.stay_line off"):
        home.hubctl("off", "live_notes.stay_line")
    with then("the brief runs as it was written"):
        assert "updatedInput" not in chats.tool("WEB", "Agent", brief(prompt)).output.get("hookSpecificOutput", {})


def test_a_message_that_carries_a_new_rule_reminds_the_sender_to_pass_it_on(home, chats):
    reminder = None
    with given("live_notes is on"):
        home.install()
        home.hubctl("on", "live_notes")
        reminder = part_text(home, "broadcast.md")
    with when("a message to an agent carries a new rule"):
        rule = chats.send("WEB", "PostToolUse", "SendMessage", message("From now on, never push to main."),
                          tool_response=SENT)
    with then("the sender is reminded to add it to the notes and pass it on"):
        assert reminder in rule.context
    with and_("a subagent that sends a rule to another agent is reminded too"):
        assert reminder in chats.send("WEB", "PostToolUse", "SendMessage", message("Never push to main."),
                                      tool_response=SENT, agent_id="agent-1", agent_type="builder").context
    with when("a message carries no rule, goes to another chat's inbox, or went to a Remote Control session"):
        plain = chats.send("WEB", "PostToolUse", "SendMessage", message("Thanks, that looks good."),
                           tool_response=SENT)
        inbox = chats.send("WEB", "PostToolUse", "SendMessage",
                           message("Never push to main.", to="uds:/tmp/hubchats/docs.sock"), tool_response=SENT)
        remote = chats.send("WEB", "PostToolUse", "SendMessage", message("Never push to main.", to="DOCS"),
                            tool_response={"success": True, "message": "Sent to the Remote Control session DOCS"})
    with then("none of them gets the reminder"):
        assert reminder not in plain.context
        assert reminder not in inbox.context
        assert reminder not in remote.context
    with when("the user turns live_notes.broadcast off"):
        home.hubctl("off", "live_notes.broadcast")
    with then("a rule goes out with no reminder"):
        assert reminder not in chats.send("WEB", "PostToolUse", "SendMessage", message("Never push to main."),
                                          tool_response=SENT).context


def test_the_notes_file_follows_its_path_setting_and_warns_once_as_it_grows(home, chats):
    custom = os.path.join(home.root, "notes", SESSIONS["WEB"] + ".md")
    with given("live_notes is on, with its path set to a folder of its own"):
        home.install()
        home.hubctl("on", "live_notes")
        home.hubctl("set", "live_notes.path", "~/notes/{session}.md")
    with when("the chat starts an agent without the file"):
        refused = chats.tool("WEB", "Agent", brief("Report."))
    with then("the reason gives the new path, and that file is made"):
        assert refused.refused and custom in refused.reason and os.path.exists(custom)
    with when("the notes file grows past 30 KB"):
        with open(custom, "a") as handle:
            handle.write("- a fact\n" * 3500)
        ok = f"Live notes: {custom}. Report."
        first = chats.tool("WEB", "Agent", brief(ok))
        second = chats.tool("WEB", "Agent", brief(ok))
    with then("the next brief carries one size warning, and the one after it does not"):
        warning = part_text(home, "size_warning.md", kb=30)
        assert first.ran and warning in first.context
        assert second.ran and warning not in second.context
    with when("it grows past the next 20 KB step"):
        with open(custom, "a") as handle:
            handle.write("- a fact\n" * 2300)
        third = chats.tool("WEB", "Agent", brief(ok))
    with then("one warning names 50 KB"):
        assert part_text(home, "size_warning.md", kb=50) in third.context
    with and_("the notes file itself was never changed by the warning"):
        assert os.path.getsize(custom) == len(part_text(home, "header.md") + "\n\n") + 9 * 5800
