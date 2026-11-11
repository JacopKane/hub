"""The task list, in Claude Code.

Each hook event that opens or closes a task changes the files under
<state>/task_list. The logic lives in lib/hub/tasks.py, which needs only the
standard library. The owner's name is the chat's title from the hook input or
from hub's record of the chat. The chat listing is never read here.
"""
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files, sessions, tasks  # noqa: E402

PART = "task_list"


def owner_name(hook_input):
    title = hook_input.get("session_title")
    if isinstance(title, str) and title.strip():
        return title
    try:
        record = sessions.record(hook_input.get("session_id"))
    except (OSError, ValueError):
        record = None
    title = record.get("title") if isinstance(record, dict) else None
    return title if isinstance(title, str) else ""


def handle(event, hook_input, flags, answer):
    folder = files.state_path(PART)
    if event == "PreToolUse":  # a dialog another part refuses never opens, so it is no question
        answer.after_all(lambda final: final.refusal is None
                         and tasks.on_event(folder, event, hook_input, owner_name(hook_input)))
        return
    tasks.on_event(folder, event, hook_input, owner_name(hook_input))
