"""slack_write_gate: Slack tools can not write anything other people see.

Every post, reaction, join or mark in Slack is seen by others, so only the read
tools in read_tools.json pass, the list of the server version it was tested
with. Every other tool is refused, and so are an unknown tool and broken input,
so a new server version can not add a silent write. When the gate can not
decide, it refuses. Without the read list every Slack call is refused, and
with a server pattern that can not be used, every MCP server with slack in
its name is gated. Experimental, Claude Code only.
"""
import json
import os
import re

from hub.tree import pattern_problem

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "slack_write_gate"
SERVER_WORD = "slack"  # with a pattern that can not be used, every MCP server with this word in its name is gated


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


def is_read(hook_input):
    """Whether a call is on the tested read list. The dispatcher asks this when the settings can not be read."""
    found = load_list()
    return (found is not None and isinstance(hook_input.get("tool_input"), dict)
            and hook_input["tool_name"].rsplit("__", 1)[-1] in found["read_tools"])


def handle(event, hook_input, flags, answer):
    tool = hook_input.get("tool_name")
    if event != "PreToolUse" or not isinstance(tool, str) or not gated(tool, flags):
        return
    if not flags.runs(f"{PART}.read_tools_only"):
        return
    found = load_list()
    if found is None:
        answer.signal(f"{PART}.read_tools_only", "refused")
        answer.refuse("hub can not read the Slack gate's read_tools.json, so it can not tell a read from a write. "
                      "Every Slack call is refused until the file is back, for example after hub is "
                      f"reinstalled. ({PART})")
        return
    reads = found["read_tools"]
    name = tool.rsplit("__", 1)[-1]
    if name in reads and isinstance(hook_input.get("tool_input"), dict):
        return
    shown = name if name and isinstance(hook_input.get("tool_input"), dict) else "an unreadable call"
    answer.signal(f"{PART}.read_tools_only", "refused")
    answer.refuse(f"Slack is read-only here, so {shown} is refused. Only these read tools pass: {', '.join(reads)}. "
                  f"Any change in Slack, such as a post or a reaction, is for the user to make. ({PART})")


def doctor(flags):
    lines = []
    problem = pattern_problem(flags.value(f"{PART}.server_pattern") or "")
    if problem:
        lines.append(("warning", f"server_pattern can not be used, because {problem}. Until it is fixed, every MCP "
                                 f"server with {SERVER_WORD} in its name is gated. Fix it by saying "
                                 f"\"set {PART}.server_pattern to <pattern>\"."))
    found = load_list()
    if found is None:
        lines.append(("warning", "read_tools.json is missing or broken, so every Slack call is refused. "
                                 "A reinstall of hub brings the file back."))
    else:
        lines.append(("note", f"the read list comes from {found['server']} {found['version']}."))
    return lines
