"""The coordinator's four guards, in Claude Code.

- start_nudge: the chat with the coordinator's name is told to load the
  hub-coordinator skill, at session start and on each prompt, until a Skill
  call or a typed /hub-coordinator loads it. A compaction starts over.
- work_guard: in the coordinator, an edit outside its bookkeeping paths and
  a Bash command that runs tests or makes a git commit are refused, with a
  reason that sends the step to the chat that owns the work.
- delegate_nudge: in the coordinator, every few Read, Grep, Glob, Bash, Edit
  or Write calls on one prompt carry one line that asks whether to delegate.
- question_guard: while a live chat with the coordinator's name runs, a
  question dialog in any other chat is refused, and the reason gives the
  coordinator's address and asks for the whole question.

Calls from a subagent carry agent_id and always pass. Each guard decides
from cheap facts first and reads names last, so most calls cost one file
read. Any error lets the call run (the dispatcher logs it), so a broken
guard never stops ordinary work. A slow or failing chat listing lets the
dialog open and leaves one line in the doctor log.
"""
import fnmatch
import os
import re
import shlex
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files, sessions  # noqa: E402

PART = "coordinator"
SKILL = "hub-coordinator"
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
COUNTED = {"Bash", "Read", "Grep", "Glob", "Edit", "Write"}
URGENT = "[urgent]"
# A command segment that starts with one of these only reads or prints, so a test name inside it runs nothing.
READERS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "rg", "wc", "ls", "sed", "awk", "echo",
           "printf", "file", "stat", "find", "diff", "cmp", "jq", "realpath", "dirname", "basename"}
# Words that wrap another command, skipped to find the command a segment really runs.
WRAPPERS = re.compile(
    r"^(?:(?:sudo|env|time|nice|command|exec|do|then|else|nohup|xargs(?:\s+-\S+)*|timeout\s+\S+"
    r"|[A-Za-z_][A-Za-z0-9_]*=\S*)\s+)+")
SEGMENTS = re.compile(r"&&|\|\||[;|&\n()`{}]|\$\(")
TEST_RUN = re.compile(
    r"\.test\.sh\b|\bpytest\b|-m\s+(?:pytest|unittest)\b|\bbats\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b|\b(?:jest|vitest)\b|\bplaywright\s+test\b"
    r"|\b(?:go|cargo|deno)\s+test\b|\bmake\s+(?:-\S+\s+)*[\w:-]*test")
GIT_COMMIT = re.compile(r"\bgit(?:\s+(?:-[Cc]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--\S+))*\s+commit\b")


def coordinator_name(flags):
    return flags.value(f"{PART}.name") or "HUB"


def is_coordinator(hook_input, flags):
    return sessions.same_name(sessions.name_of(hook_input), coordinator_name(flags))


def text(flags, flag_id, **values):
    return flags.text(f"{PART}.{flag_id}").format(**values)


# bookkeeping paths

def patterns(flags):
    state = files.state_dir()
    found = []
    for pattern in flags.value(f"{PART}.work_guard.allowed") or []:
        pattern = pattern.replace("{state}", state)
        found.append(os.path.join(files.home(), pattern[2:]) if pattern.startswith("~/") else pattern)
    return found


def full_path(path, cwd):
    if path.startswith("~/"):
        path = os.path.join(files.home(), path[2:])
    return os.path.normpath(os.path.join(cwd or "/", path))


def bookkeeping(path, cwd, flags):
    full = full_path(path, cwd)
    return any(fnmatch.fnmatchcase(full, pattern) for pattern in patterns(flags))


def bash_bookkeeping(command, cwd, flags):
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if words and os.path.basename(words[0]) == "hubctl":
        return True
    if len(words) > 1 and re.fullmatch(r"python[\d.]*", os.path.basename(words[0])):
        script = next((word for word in words[1:] if not word.startswith("-")), "")
        if os.path.basename(script) == "hub_switch.py":
            return True
    return any(bookkeeping(word, cwd, flags) for word in words if os.sep in word)


def topic_bash(command):
    for segment in SEGMENTS.split(command):
        segment = WRAPPERS.sub("", segment.strip())
        first = os.path.basename(segment.split(" ", 1)[0].strip("\"'")) if segment else ""
        if first in READERS:
            continue
        if TEST_RUN.search(segment) or GIT_COMMIT.search(segment):
            return True
    return False


# the guards

def work_guard(hook_input, flags, answer):
    tool, tool_input = hook_input.get("tool_name"), hook_input.get("tool_input")
    if not flags.runs(f"{PART}.work_guard") or not isinstance(tool_input, dict):
        return False
    if tool in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(path, str) or not path or bookkeeping(path, hook_input.get("cwd"), flags):
            return False
    elif tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not topic_bash(command):
            return False
    else:
        return False
    if not is_coordinator(hook_input, flags):  # last, because it may read the listing
        return False
    answer.signal(f"{PART}.work_guard", "blocked")
    answer.refuse(text(flags, "work_guard", name=coordinator_name(flags)))
    return True


def delegate_nudge(hook_input, flags, answer):
    tool, tool_input = hook_input.get("tool_name"), hook_input.get("tool_input") or {}
    every = flags.value(f"{PART}.delegate_nudge.every")
    if not flags.runs(f"{PART}.delegate_nudge") or tool not in COUNTED or not isinstance(every, int) or every < 1:
        return
    session = hook_input.get("session_id")
    data = sessions.record(session)
    if not data or not data.get("coordinator"):
        return
    cwd = hook_input.get("cwd")
    if tool == "Bash":
        if bash_bookkeeping(str(tool_input.get("command") or ""), cwd, flags):
            return
    else:
        target = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(target, str) and target and bookkeeping(target, cwd, flags):
            return
    with files.locked(os.path.join(sessions.FOLDER, "counts.lock")):
        data = sessions.record(session) or {}
        count = int(data.get("prompt_calls") or 0) + 1
        sessions.update(session, prompt_calls=count)
    if count % every == 0:
        answer.add_context(text(flags, "delegate_nudge", name=coordinator_name(flags)))


def question_guard(hook_input, flags, answer):
    if not flags.runs(f"{PART}.question_guard"):
        return
    name = coordinator_name(flags)
    if is_coordinator(hook_input, flags):
        return
    live = sessions.find_live(name, sessions.listing(), exclude=hook_input.get("session_id"))
    if live is None:
        return
    urgent = flags.value("queue.urgent_prefix") or URGENT
    answer.signal(f"{PART}.question_guard", "blocked")
    answer.refuse(text(flags, "question_guard", name=name, address=live["address"], urgent=urgent))


def start_nudge(event, hook_input, flags, answer, data):
    if not data or not data.get("coordinator") or data.get("skill_loaded"):
        return
    if flags.runs(f"{PART}.start_nudge"):
        answer.add_context(text(flags, "start_nudge", name=coordinator_name(flags)))


def typed_skill(prompt):
    return isinstance(prompt, str) and prompt.strip().split(" ", 1)[0] in ("/" + SKILL, "/hub:" + PART)


def handle(event, hook_input, flags, answer):
    if hook_input.get("agent_id"):
        return
    session = hook_input.get("session_id")
    if event == "SessionEnd":
        sessions.note(event, hook_input)
        return
    if event in ("SessionStart", "UserPromptSubmit"):
        earlier = sessions.record(session) or {}
        data = sessions.note(event, hook_input)
        if data is None:
            return
        values = {"coordinator": is_coordinator(hook_input, flags), "prompt_calls": 0}
        if event == "SessionStart":
            values["skill_loaded"] = hook_input.get("source") == "resume" and bool(earlier.get("skill_loaded"))
        elif typed_skill(hook_input.get("prompt")):
            values["skill_loaded"] = True
        data = sessions.update(session, **values)
        start_nudge(event, hook_input, flags, answer, data)
        return
    tool = hook_input.get("tool_name")
    if event == "PostToolUse":
        skill = (hook_input.get("tool_input") or {}).get("skill")
        if tool == "Skill" and skill in (SKILL, "hub:" + PART):
            sessions.update(session, skill_loaded=True)
        return
    if event != "PreToolUse":
        return
    if tool == "AskUserQuestion":
        question_guard(hook_input, flags, answer)
        return
    if not work_guard(hook_input, flags, answer):
        delegate_nudge(hook_input, flags, answer)


def doctor(flags):
    """What hubctl doctor says: who the coordinator is, and whether it runs now."""
    name = coordinator_name(flags)
    rows, problem = sessions.run_listing()
    if problem:
        return [("warning", f"the chat listing (claude agents --json) failed, {problem}, so hub can not tell "
                            f"whether {name} runs, and question dialogs in other chats open as usual.")]
    running = "it runs now" if sessions.live_chats(name, rows) else f"no chat named {name} runs now"
    return [("note", f"the chat listing works. The coordinator is the chat named {name}, and {running}.")]
