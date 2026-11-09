"""The message queue, in Claude Code. The work is in queue.py, beside this file.

- SessionStart keeps hub's session record of the chat (sessions.note), and,
  while the version guard runs, adds the Claude Code version to it.
- UserPromptSubmit: the chat woke for another reason, so what waits for it
  goes now.
- SessionEnd removes the record.
- PreToolUse SendMessage holds the message with a refusal that says it is
  queued, or leaves the send alone. It runs after chat_names.resolve, so it
  sees the address that part chose.

Any error here leaves the send alone, which is the native path, and never
touches another part's answer. A bug may cost a wake, never a message.
"""
import importlib.util
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import sessions  # noqa: E402

PART = "queue"


def load_queue():
    """queue.py by its path, because the name queue alone is the standard library's module."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.py")
    spec = importlib.util.spec_from_file_location("hub_part_queue_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def handle(event, hook_input, flags, answer):
    if hook_input.get("agent_id") and event != "PreToolUse":
        return
    try:
        queue = load_queue()
        settings = queue.settings_from(flags)
        if event in ("SessionStart", "UserPromptSubmit", "SessionEnd"):
            data = sessions.note(event, hook_input)
            if event == "SessionStart" and data is not None and settings["versions"] is not None:
                sessions.update(hook_input.get("session_id"), version=queue.claude_version())
        queue.on_any_hook(settings)
        if event == "UserPromptSubmit":
            queue.on_prompt(hook_input)
        elif event == "PreToolUse" and hook_input.get("tool_name") == "SendMessage":
            refusal = queue.on_send(hook_input, settings)
            if refusal:
                answer.refuse(refusal)
    except Exception:  # the failure rule: the native send
        return
