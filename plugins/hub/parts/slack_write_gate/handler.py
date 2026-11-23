"""slack_write_gate: Slack tools can not write anything other people see.

Every post, reaction, join or mark in Slack is seen by others, so only the read
tools in read_tools.json pass, the list of the server version it was tested
with. A tool on a claude.ai connector passes only when its full name is on the
file's second list, which holds the claude.ai Slack sign-in tools, so a name
from one list never passes on the other server. Every other tool is refused,
and so are an unknown tool and broken input, so a new server version can not
add a silent write. When the gate can not decide, it refuses. Without the read
list every Slack call is refused, and with a server pattern that can not be
used, every MCP server with slack in its name is gated. Experimental, Claude
Code only.
"""
import json
import os
import re

from hub.tree import pattern_problem

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "slack_write_gate"
SERVER_WORD = "slack"  # with a pattern that can not be used, every MCP server with this word in its name is gated
CLAUDE_AI = "mcp__claude_ai_"  # claude.ai connectors start with this, and their tools match on the full name


def load_list():
    """read_tools.json as a dict, or None when it is missing or broken."""
    try:
        with open(os.path.join(HERE, "read_tools.json"), encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return None
    lists = ("read_tools", "claude_ai_tools")
    return found if isinstance(found, dict) and all(isinstance(found.get(key), list) for key in lists) else None


def on_claude_ai(tool):
    return tool.startswith(CLAUDE_AI)


def passing(tool, found):
    """The names that pass on this tool's server, and the one that is looked up in them."""
    if on_claude_ai(tool):
        return found["claude_ai_tools"], tool
    return found["read_tools"], tool.rsplit("__", 1)[-1]


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
    if found is None or not isinstance(hook_input.get("tool_input"), dict):
        return False
    names, name = passing(hook_input["tool_name"], found)
    return name in names


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
    reads, name = passing(tool, found)
    if name in reads and isinstance(hook_input.get("tool_input"), dict):
        return
    shown = name if name and isinstance(hook_input.get("tool_input"), dict) else "an unreadable call"
    which = ("On the claude.ai Slack connector only these tools pass" if on_claude_ai(tool)
             else "Only these read tools pass")
    answer.signal(f"{PART}.read_tools_only", "refused")
    answer.refuse(f"Slack is read-only here, so {shown} is refused. {which}: {', '.join(reads)}. "
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
        lines.append(("note", f"on the claude.ai Slack connector only these tools pass: "
                              f"{', '.join(found['claude_ai_tools'])}."))
    return lines
