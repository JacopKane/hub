"""Chat names, in Claude Code.

- resolve: a SendMessage to a plain name that exactly one live chat on this
  machine has goes to that chat's own inbox address, uds:<socket>. That route
  skips the name lookup, so a dead row with the same name never takes the
  message. Every other send is left alone, so a chat of that name on another
  machine is still reached the native way.
- offline_note: after a send by plain name went to an offline row, one note
  says it did not arrive. Its child, stopped, says the same when a send to a
  chat's address failed because that chat stopped.

It keeps hub's session records (sessions.note), which resolve reads. A send
to the name of one of this chat's own subagents is left alone. The
part never decides a permission, so the user's permission rules still apply.
"""
import glob
import json
import os
import re
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import sessions  # noqa: E402

PART = "chat_names"
REF_SUFFIX = re.compile(r"\[\s*[0-9a-f]{4,16}\s*\]\s*$", re.I)
SCHEME = re.compile(r"^\s*[A-Za-z][A-Za-z0-9+.\-]*:")
OFFLINE_RESULT = "offline right now"  # Claude Code's words for a send to an offline row


def is_plain_name(to):
    return (isinstance(to, str) and bool(to.strip()) and not REF_SUFFIX.search(to) and not SCHEME.match(to)
            and "@" not in to and "/" not in to)


def own_subagent_named(name, transcript_path):
    """Whether this chat has a subagent of that name, from its transcript's subagents folder."""
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return False
    for path in glob.glob(os.path.join(glob.escape(transcript_path[:-6]), "subagents", "*.meta.json")):
        try:
            with open(path) as handle:
                if sessions.same_name(json.load(handle).get("name"), name):
                    return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def resolve(hook_input, flags, answer):
    tool_input = hook_input.get("tool_input")
    if not flags.runs(f"{PART}.resolve") or not isinstance(tool_input, dict):
        return
    to, message = tool_input.get("to"), tool_input.get("message")
    if not is_plain_name(to) or not isinstance(message, str) or not message.strip():
        return
    if own_subagent_named(to, hook_input.get("transcript_path")):
        return
    live = sessions.live_chats(to, sessions.listing())
    if any(chat["session"] == hook_input.get("session_id") for chat in live):  # the name is this chat's own
        return
    if len(live) == 1 and live[0]["address"]:
        own = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
        if not own or live[0]["address"] != "uds:" + own:
            answer.rewrite(dict(tool_input, to=live[0]["address"]))


def offline_note(hook_input, flags, answer):
    tool_input, response = hook_input.get("tool_input"), hook_input.get("tool_response")
    if not flags.runs(f"{PART}.offline_note") or not isinstance(tool_input, dict):
        return
    to = tool_input.get("to")
    if not isinstance(to, str):
        return
    if to.startswith("uds:") and isinstance(response, dict) and response.get("success") is False:
        if flags.runs(f"{PART}.offline_note.stopped"):
            name = sessions.chat_at(to) or to[4:]
            answer.add_context(flags.text(f"{PART}.offline_note.stopped").format(name=name))
    elif is_plain_name(to) and OFFLINE_RESULT in json.dumps(response):
        answer.add_context(flags.text(f"{PART}.offline_note").format(name=to.strip()))


def handle(event, hook_input, flags, answer):
    if hook_input.get("agent_id"):
        return
    if event in ("SessionStart", "UserPromptSubmit", "SessionEnd"):
        sessions.note(event, hook_input)
        return
    if hook_input.get("tool_name") != "SendMessage":
        return
    if event == "PreToolUse":
        resolve(hook_input, flags, answer)
    elif event == "PostToolUse":
        offline_note(hook_input, flags, answer)


def doctor(flags):
    """What hubctl doctor says: whether names can be resolved from the chat listing."""
    rows, problem = sessions.run_listing()
    if problem:
        return [("warning", f"the chat listing (claude agents --json) failed, {problem}, so a message sent to a "
                            f"name goes the usual way, and an offline row of that name may take it.")]
    return [("note", "the chat listing works, so a message sent to a chat's name reaches the live chat of "
                     "that name on this machine.")]
