"""Steps for the question and gate parts: dialogs, prompts and tool calls as Claude Code sends them.

Each call gets its own tool call id, so the dispatcher never answers from a
stored call. The clock is HUB_TEST_NOW, an ISO UTC time, set on the home.
"""
import itertools
import json
import os

from conftest import load_fixture

CALL_IDS = itertools.count(1)
SESSION_WEB = "00000000-0000-4000-8000-0000000000a1"
SESSION_DOCS = "00000000-0000-4000-8000-0000000000b2"
SESSION_LOBBY = "00000000-0000-4000-8000-0000000000c3"
# A question with every part question_check looks for: length, a path, an example and a pick.
FULL_QUESTION = (
    "The settings page lists every part as a tree today, in plugins/web/settings.py, and the new change strip "
    "needs a place on it. For example, after a switch the strip shows one line with an undo button above the "
    "tree, so the user sees what changed without scrolling. Which layout should the page use from now on?"
)
FULL_OPTIONS = [
    {"label": "Strip above the tree (Recommended)",
     "description": "The strip sits on top and the tree keeps its place, so nothing the user knows moves."},
    {"label": "Strip in a side panel",
     "description": "The tree gets narrower, and the strip has room for ten lines instead of three."},
]


def question(text=FULL_QUESTION, options=None, header="Layout"):
    return {"question": text, "header": header, "multiSelect": False,
            "options": [dict(option) for option in (FULL_OPTIONS if options is None else options)]}


def set_clock(home, stamp):
    home.env["HUB_TEST_NOW"] = stamp


def _send(home, event, fixture, session=None, errors_expected=False, **fields):
    data = load_fixture("claude_code", fixture)
    data.pop("_source", None)
    data.update(cwd=home.work, **fields)
    if "tool_name" in data:
        data["tool_use_id"] = f"toolu_s11_{next(CALL_IDS):06d}"
    if session:
        data["session_id"] = session
    return home.send("claude_code", event, data, errors_expected=errors_expected)


def ask(home, questions, session=None, metadata=None):
    tool_input = {"questions": questions}
    if metadata is not None:
        tool_input["metadata"] = metadata
    return _send(home, "PreToolUse", "PreToolUse.AskUserQuestion.json", session, tool_input=tool_input)


def answered(home, session=None):
    return _send(home, "PostToolUse", "PostToolUse.AskUserQuestion.json", session)


def prompt(home, text, session=None):
    return _send(home, "UserPromptSubmit", "UserPromptSubmit.json", session, prompt=text)


def tool_call(home, name, tool_input=None, session=None, agent_id=None, errors_expected=False):
    fields = {"tool_name": name, "tool_input": {} if tool_input is None else tool_input}
    if agent_id:
        fields.update(agent_id=agent_id, agent_type="worker")
    return _send(home, "PreToolUse", "PreToolUse.mcp.json", session, errors_expected, **fields)


def bash(home, command, session=None):
    return _send(home, "PreToolUse", "PreToolUse.Bash.json", session,
                 tool_input={"command": command, "description": "Run it"})


def decision(result):
    """deny, ask, or None, from Claude Code's keys."""
    return result.output.get("hookSpecificOutput", {}).get("permissionDecision")


def state_lines(home, name):
    path = os.path.join(home.state, name)
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def cursor_send(home, cursor_event, fixture, **fields):
    """Cursor's hook input through Cursor's adapter, as Cursor runs it."""
    import sys

    from conftest import HookResult

    data = load_fixture("cursor", fixture)
    for key in ("docs_based", "_source"):
        data.pop(key, None)
    data.update(cwd=home.work, workspace_roots=[home.work], **fields)
    if "tool_name" in data:
        data["tool_use_id"] = f"call_s11_{next(CALL_IDS):06d}"
    script = os.path.join(home.plugin, "lib", "hub", "adapters", "cursor_hooks.py")
    process = home.run([sys.executable, "-I", script, cursor_event], stdin=json.dumps(data))
    assert not home.errors(), home.errors()
    return HookResult("cursor", process)
