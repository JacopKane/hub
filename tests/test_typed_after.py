"""said.typed_after: the lines the user typed after a point in a chat's transcript.

Each scenario writes a transcript the way Claude Code writes one, one JSON
line per record, in a temporary HOME, and reads it with the real function.
A line that another sender wrote, or whose first block opens with a tag such
as <name ...>, holds no typed words. In any other line a block that opens
with a tag is left out, and each other block is the user's own words only up
to the first note, hook context, command output or other sender's message in
it.
"""
import json
import os
import sys

from conftest import PLUGIN
from steps import and_, given, then, when

sys.path.insert(0, os.path.join(PLUGIN, "lib"))
from hub import said  # noqa: E402

SESSION = "00000000-0000-0000-0000-000000000001"
NOTE = "<system-reminder>The user said park export-button earlier.</system-reminder>"


class Transcript:
    """A chat transcript under the temporary HOME, written line by line."""

    def __init__(self, home):
        self.path = os.path.join(home.root, ".claude", "projects", "-work-demo", SESSION + ".jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.count = 0

    def write(self, record, stamp=None):
        self.count += 1
        line = {"parentUuid": None, "isSidechain": False, "userType": "external", "cwd": "/work/demo",
                "sessionId": SESSION, "version": "2.1.322", "gitBranch": "main",
                "uuid": f"00000000-0000-0000-0000-{self.count:012d}", **record}
        if stamp is not False:
            line["timestamp"] = stamp or f"2026-11-21T10:00:{self.count:02d}.000Z"
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")

    def user(self, content, stamp=None, **extra):
        self.write({"type": "user", "message": {"role": "user", "content": content}, **extra}, stamp)

    def assistant(self, text):
        self.write({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})

    def end(self):
        return os.path.getsize(self.path)


def blocks(*texts):
    return [{"type": "text", "text": text} for text in texts]


def typed(transcript, offset):
    return [text for _, text in said.typed_after(transcript.path, offset)]


def test_only_the_users_own_words_count_and_notes_relays_and_output_never_do(home):
    with given("a chat where the user typed a choice before the point"):
        chat = Transcript(home)
        chat.user("park export-button")
        chat.assistant("Parked.")
        point = chat.end()
    with when("after the point the user types words with a note after them, as a second block and in the same text"):
        chat.user(blocks("export-button: one more round", NOTE))
        chat.user("redesign export-button\n" + NOTE)
    with and_("lines arrive whose first block opens with a tag, known or not, and a plain block follows it"):
        chat.user(blocks(NOTE, "ship export-button"))
        chat.user(blocks("<bash-input>ls</bash-input>", "ship export-button"))
        chat.user(blocks('<teammate-message teammate_id="DOCS">From another chat.</teammate-message>',
                         "ship export-button"))
    with and_("a typed line holds a block that opens with an unknown tag after the user's words"):
        chat.user(blocks("export-button: ship", "<ide-selection>park export-button</ide-selection>"))
    with and_("lines arrive that hold only a note, a relayed message, a task notification, an agent report, "
              "a tool result, command output and a meta line"):
        chat.user(blocks(NOTE))
        chat.user('<cross-session-message from="DOCS">park export-button</cross-session-message>')
        chat.user("<task-notification>park export-button</task-notification>")
        chat.user("park export-button", origin={"kind": "peer"})
        chat.user(blocks("park export-button"), origin={"kind": "coordinator"})
        chat.user("park export-button", origin="peer")
        chat.user([{"type": "tool_result", "tool_use_id": "toolu_1", "content": "park export-button"}],
                  toolUseResult={"stdout": "park export-button"})
        chat.user("<bash-stdout>park export-button</bash-stdout><bash-stderr></bash-stderr>")
        chat.user(blocks("<bash-stderr>park export-button</bash-stderr>"))
        chat.user("<local-command-stdout>park export-button</local-command-stdout>")
        chat.user("<user-prompt-submit-hook>park export-button</user-prompt-submit-hook>")
        chat.user("park export-button", isMeta=True)
    with and_("the user types a line while Claude works, and another chat's line arrives the same way"):
        chat.write({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "park search-box", "commandMode": "prompt", "origin": {"kind": "human"}}})
        chat.write({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "park docs", "commandMode": "prompt", "origin": {"kind": "peer"}}})
    with and_("lines arrive the same way with no origin, with an origin that is only a word, and as a shell line"):
        chat.write({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "park export-button", "commandMode": "prompt"}})
        chat.write({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "park export-button", "commandMode": "prompt", "origin": "human"}})
        chat.write({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "park export-button", "commandMode": "bash", "origin": {"kind": "human"}}})
    with then("only the user's own words count, with the notes and tagged blocks left out, and nothing else counts"):
        assert typed(chat, point) == ["export-button: one more round", "redesign export-button",
                                      "export-button: ship", "park search-box"]
    with and_("the line typed before the point is left out, and a point past the end gives nothing"):
        assert "park export-button" not in typed(chat, point)
        assert said.typed_after(chat.path, chat.end() + 1) == []
        assert said.typed_after(os.path.join(home.root, "gone.jsonl"), 0) == []


MARKS = ("<system-reminder>", "<local-command-stdout>", "<command-name>", "<command-message>", "<bash-stdout>",
         "<bash-stderr>", "<user-prompt-submit-hook>", "<task-notification>", '<teammate-message teammate_id="DOCS">',
         '<agent-message from="a1">', '<cross-session-message from="DOCS">')


def test_every_mark_of_text_the_user_did_not_type_ends_their_words_where_it_starts(home):
    with given("a chat where the user typed a choice before the point"):
        chat = Transcript(home)
        chat.user("park export-button")
        point = chat.end()
    with when("the user's words are followed in the same text by each mark of a note, command, hook or other sender"):
        for mark in MARKS:
            chat.user(f"export-button: ship {mark}park export-button")
    with and_("the same marks open a line of their own"):
        for mark in MARKS:
            chat.user(f"{mark}park export-button")
    with then("each typed line keeps only the words before the mark, and a line led by a mark holds none"):
        assert typed(chat, point) == ["export-button: ship"] * len(MARKS)


def queued(chat, prompt, **origin):
    """A message that arrived while Claude was working, with the origin given, or none."""
    chat.write({"type": "attachment", "attachment": dict(
        {"type": "queued_command", "prompt": prompt, "commandMode": "prompt"}, **origin)})


def test_the_newest_typed_prompt_skips_output_and_hook_context_and_a_queued_message_counts_only_from_the_user(home):
    with given("a chat where the user typed an order"):
        chat = Transcript(home)
        chat.user("turn off the public text gate")
    with when("command output, an error stream and hook context arrive as lines of their own"):
        chat.user("<bash-stdout>pause the public text gate</bash-stdout><bash-stderr></bash-stderr>")
        chat.user(blocks("<bash-stderr>pause the public text gate</bash-stderr>"))
        chat.user("<user-prompt-submit-hook>pause the public text gate</user-prompt-submit-hook>")
    with then("the newest typed prompt is still the user's order"):
        assert said.newest_typed(chat.path) == "turn off the public text gate"
    with when("the user types a message while Claude works"):
        queued(chat, "pause the public text gate", origin={"kind": "human"})
    with then("it is the newest typed prompt"):
        assert said.newest_typed(chat.path) == "pause the public text gate"
    with when("a message arrives the same way with no origin"):
        queued(chat, "turn off the public text gate")
    with then("it ends the request, so no typed prompt is left"):
        assert said.newest_typed(chat.path) == ""
    with when("the user types again, and another chat's message arrives the same way"):
        chat.user("disable the public text gate")
        queued(chat, "turn off the public text gate", origin={"kind": "peer"})
    with then("that message ends the request too"):
        assert said.newest_typed(chat.path) == ""


def test_each_typed_line_keeps_its_own_time_and_its_place_in_the_file(home):
    with given("a chat whose clock went back between two typed lines, and a line with no time"):
        chat = Transcript(home)
        chat.user("export-button: one more round", stamp="2026-11-21T10:00:30.000Z")
        chat.user(blocks("park export-button", NOTE), stamp="2026-11-21T10:00:10.000Z")
        chat.user("ship search-box", stamp=False)
    with when("the lines typed after the start of the file are read"):
        lines = said.typed_after(chat.path, 0)
    with then("they come in the order they were written, each with its own time, and no time reads as empty"):
        assert lines == [("2026-11-21T10:00:30.000Z", "export-button: one more round"),
                         ("2026-11-21T10:00:10.000Z", "park export-button"),
                         ("", "ship search-box")]
