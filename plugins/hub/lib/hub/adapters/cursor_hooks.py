#!/usr/bin/env python3
"""Cursor's hooks, through the same dispatcher.

    python3 -I cursor_hooks.py <Cursor event name>   reads Cursor's hook input on stdin

It maps Cursor's event names, input fields and tool names to Claude Code's,
runs the dispatcher, and puts the answer in Cursor's own output keys.
Handlers only ever see Claude Code's names.
"""
import json
import os
import sys

LIB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import dispatch, tree  # noqa: E402

EVENTS = {cursor: claude for claude, cursor in tree.CURSOR_EVENT_NAMES.items()}
TOOL_NAMES = {"Shell": "Bash", "Read": "Read", "Write": "Write", "Edit": "Edit", "Grep": "Grep",
              "Glob": "Glob", "Task": "Agent", "Delete": "Delete"}


def claude_tool_input(tool_name, tool_input):
    tool_input = dict(tool_input or {})
    if tool_name == "Shell":
        tool_input.pop("working_directory", None)
    return tool_input


def to_claude(event, data):
    """Cursor's hook input in Claude Code's shape."""
    tool_input = data.get("tool_input") or {}
    roots = data.get("workspace_roots") or []
    cwd = (tool_input.get("working_directory") if isinstance(tool_input, dict) else None) \
        or data.get("cwd") or (roots[0] if roots else None) or os.getcwd()
    tool_name = data.get("tool_name")
    mapped = {
        "session_id": data.get("conversation_id") or data.get("session_id") or "",
        "transcript_path": data.get("transcript_path"),
        "cwd": cwd,
        "hook_event_name": EVENTS[event],
        "permission_mode": "default",
    }
    if tool_name is not None:
        mapped["tool_name"] = TOOL_NAMES.get(tool_name, tool_name)
        mapped["tool_input"] = claude_tool_input(tool_name, tool_input)
    if data.get("tool_use_id"):
        mapped["tool_use_id"] = data["tool_use_id"]
    if event == "stop":
        # Not checked in Cursor yet. A stop that follows a followup_message should have a loop_count above 0.
        mapped["stop_hook_active"] = bool(data.get("loop_count"))
        if isinstance(data.get("last_assistant_message"), str):
            mapped["last_assistant_message"] = data["last_assistant_message"]
    return mapped


def cursor_output(event, result, data):
    """Put a neutral answer in Cursor's keys. An empty answer prints nothing."""
    if not result:
        return None
    output = {}
    if "refusal" in result and event == "stop":
        output["followup_message"] = result["refusal"]  # unverified in Cursor
    elif "refusal" in result:
        output.update(permission="deny", user_message=result["refusal"], agent_message=result["refusal"])
    elif "context" in result or "notice" in result:
        # Unverified in Cursor: whether an answer with no permission key keeps its message.
        # Cursor has no line for the user alone, so a notice leads the context.
        key = "agent_message" if event == "preToolUse" else "additional_context"
        output[key] = "\n\n".join(text for text in (result.get("notice"), result.get("context")) if text)
    if "updated_input" in result and "refusal" not in result:
        updated = dict(result["updated_input"])
        if data.get("tool_name") == "Shell" and "working_directory" in (data.get("tool_input") or {}):
            updated.setdefault("working_directory", data["tool_input"]["working_directory"])
        output["updated_input"] = updated  # unverified in Cursor
    return output or None


def main(argv):
    if len(argv) != 1 or argv[0] not in EVENTS:
        print(f"usage: cursor_hooks.py <one of {', '.join(sorted(EVENTS))}>", file=sys.stderr)
        return 2
    event = argv[0]
    raw, data, mapped = "", None, None
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        mapped = to_claude(event, data)
        output = cursor_output(event, dispatch.run(EVENTS[event], mapped, tool="cursor"), data)
    except Exception as error:  # a broken hub stops only public steps
        dispatch.log_error()
        output = cursor_output(event, dispatch.failure(EVENTS[event], mapped, raw, error, tool="cursor"), data or {})
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
