"""sentry_write_gate: the user is asked before a Sentry tool changes anything.

A resolve, an assign, a note or a project change is seen by the whole team.
The read tools in read_tools.json, the list of the server version it was
tested with, run quietly. Every other tool asks the user first. So do an
unknown tool and broken input, so a new server version can not add a silent
write. execute_sentry_tool can run any tool of the server, so the tool named
inside it is the one checked. When the gate can not decide, it asks. Without
the read list every Sentry call asks first, and with a server pattern that
can not be used, every MCP server with sentry in its name is gated.
Experimental, Claude Code only.
"""
import json
import os
import re

from hub.tree import pattern_problem

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "sentry_write_gate"
WRAPPER = "execute_sentry_tool"
SERVER_WORD = "sentry"  # with a pattern that can not be used, every MCP server with this word in its name is gated


def load_list():
    """read_tools.json as a dict, or None when it is missing or broken."""
    try:
        with open(os.path.join(HERE, "read_tools.json"), encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and isinstance(found.get("read_tools"), list) else None


def server_named(tool):
    server = tool.rsplit("__", 1)[0]
    return server.startswith("mcp__") and SERVER_WORD in server.lower()


def gated(tool, flags):
    pattern = flags.value(f"{PART}.server_pattern") or ""
    if pattern_problem(pattern):
        return server_named(tool)
    return re.fullmatch(pattern, tool) is not None


def inner_tool(hook_input):
    """The tool the call runs, or None when the input can not be read."""
    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    name = hook_input["tool_name"].rsplit("__", 1)[-1]
    if name == WRAPPER:
        inner = tool_input.get("name")
        return inner if isinstance(inner, str) and inner else None
    return name or None


def is_read(hook_input):
    """Whether a call is on the tested read list. The dispatcher asks this when the settings can not be read."""
    found, name = load_list(), inner_tool(hook_input)
    return found is not None and name is not None and name in found["read_tools"]


def handle(event, hook_input, flags, answer):
    tool = hook_input.get("tool_name")
    if event != "PreToolUse" or not isinstance(tool, str) or not gated(tool, flags):
        return
    found = load_list()
    reads = found["read_tools"] if found is not None else []
    name = inner_tool(hook_input)
    if name and flags.runs(f"{PART}.read_tools") and name in reads:
        return
    if not flags.runs(f"{PART}.ask_before_write") and not (name and name in reads):
        return
    if name is None:
        shown = f"{WRAPPER} without a tool name" if tool.endswith("__" + WRAPPER) else "an unreadable call"
    else:
        shown = name
    unsure = ("hub can not read the Sentry gate's read_tools.json, so it can not tell a read from a write and asks "
              "before every Sentry call. " if found is None else "")
    answer.signal(f"{PART}.ask_before_write", "asked")
    answer.ask(f"{unsure}This Sentry call ({shown}) may change what the team sees. Run it only if the user asked for "
               f"this exact action in this chat. A subagent never runs it, and sends the exact call to its main chat "
               f"instead. ({PART})")


def doctor(flags):
    lines = []
    problem = pattern_problem(flags.value(f"{PART}.server_pattern") or "")
    if problem:
        lines.append(("warning", f"server_pattern can not be used, because {problem}. Until it is fixed, every MCP "
                                 f"server with {SERVER_WORD} in its name is gated. Fix it with "
                                 f"hubctl set {PART}.server_pattern <pattern>."))
    found = load_list()
    if found is None:
        lines.append(("warning", "read_tools.json is missing or broken, so every Sentry call asks first. "
                                 "A reinstall of hub brings the file back."))
    else:
        lines.append(("note", f"the read list comes from {found['server']} {found['version']}."))
    return lines
