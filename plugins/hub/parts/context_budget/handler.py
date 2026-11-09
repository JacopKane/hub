"""context_budget: tells a chat or an agent when its context grows big.

On every tool call it reads what the caller's own transcript gained since
the last call. The state for each agent id, or for the main chat's session
id, sits in the state folder. The context size of one model reply is its
input tokens plus the cache reads and cache writes. A reply that the
transcript writes on several lines counts once, and stand-in replies with the
model <synthetic> do not count.

A reminder comes when the size or the step count crosses a point between two
calls. The points are the first one, then one after each gap. A line such as
"Hand-back point: 180k" in the agent's brief moves its first size point, up
to brief_point.max. Agents whose model starts with a name in skip_models get
no hand-back reminder. After a compaction an agent gets the path of its full
transcript. It only adds context and never stops a call. A damaged state
file starts over and an unreadable transcript is skipped, so this part never
makes the dispatcher fail. Claude Code only.
"""
import json
import os
import re
import time

from hub import files

PART = "context_budget"
STATE = "context_budget"
TAIL = 4 * 1024 * 1024  # a first read starts this far from the end of a long transcript
HEAD = 256 * 1024  # how much of an agent transcript to search for the brief
STATE_AGE = 7 * 24 * 3600
STATE_KEYS = {"offset": int, "steps": int, "size": int, "seen": list, "point": int}
BRIEF_POINT = re.compile(r"hand[- ]?back\s+point:?\s*(\d{2,3})(?:\s*k\b|,?000\b)", re.I)

AGENT_TEXT = ("This agent is at {size}k context after {steps} steps, and every step reads "
              "all of it again. Hand back at the next safe point, after the current step. Say what is done, "
              "what is left and the file paths, so a fresh agent can go on from a short brief. "
              "Never stop work half done. ({flags})")
MAIN_TEXT = ("The main chat is at {size}k context, and every step reads all of it again. "
             "Hand bigger reading to agents. ({flag})")
NOTE_TEXT = ("Your context was just compacted, so your earlier steps are now a summary. Before the next step, "
             "read your brief and the files it names again. For a detail the summary left out, search your "
             "full transcript at {path} with grep or a short script, and never read it whole. ({flag})")


def transcript_for(hook_input):
    """The caller's transcript path, or None."""
    main = str(hook_input.get("transcript_path") or "")
    agent = hook_input.get("agent_id")
    if not agent:
        return main if os.path.isfile(main) else None
    derived = main[:-len(".jsonl")] + f"/subagents/agent-{agent}.jsonl" if main.endswith(".jsonl") else None
    for path in (hook_input.get("agent_transcript_path"), derived):
        if path and os.path.isfile(path):
            return path
    return None


def replies(lines, seen):
    """(size, model) of each new model reply in the lines, and whether a compaction is among them."""
    found, compacted = [], False
    for line in lines:
        if b'"compact_boundary"' in line:
            record = loads(line)
            if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                compacted = True
            continue
        if b'"usage"' not in line:
            continue
        record = loads(line)
        message = record.get("message") if record.get("type") == "assistant" else None
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict) or message.get("model") == "<synthetic>":
            continue
        key = (str(record.get("requestId")), str(message.get("id")))
        if key in seen:
            continue
        seen.append(key)
        size = sum(int(usage.get(name) or 0) for name in
                   ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        found.append((size, message.get("model")))
    return found, compacted


def loads(line):
    try:
        record = json.loads(line)
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def brief_point(path):
    """The hand-back point the agent's brief sets, in tokens, or 0. Reads the first user record only."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(HEAD)
    except OSError:
        return 0
    for line in head.split(b"\n"):
        if b'"user"' not in line:
            continue
        record = loads(line)
        if record.get("type") != "user":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = " ".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
        match = BRIEF_POINT.search(str(content or ""))
        return int(match.group(1)) * 1000 if match else 0
    return 0


def state_file(key):
    safe = "".join(character if character.isalnum() or character in "-_" else "_" for character in str(key))
    return os.path.join(STATE, (safe or "main") + ".json")


def prune(folder):
    cutoff = time.time() - STATE_AGE
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return
    for entry in entries:
        try:
            if entry.name.endswith(".json") and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            pass


def start_over_if_needed(state, path):
    """Reading starts over when the state is damaged, or the transcript is shorter than what was read."""
    whole = all(isinstance(state.get(key), kind) for key, kind in STATE_KEYS.items())
    if not whole or os.path.getsize(path) < state["offset"]:
        state.clear()


def read_new(state, path, is_agent):
    """Read what the transcript gained since state["offset"]. Returns (new replies, compacted)."""
    if "offset" not in state:
        start = max(0, os.path.getsize(path) - TAIL)
        state.update(offset=start, partial=start > 0, steps=0, size=0, model=None, seen=[],
                     point=brief_point(path) if is_agent else 0)
    with open(path, "rb") as handle:
        handle.seek(state["offset"])
        raw = handle.read()
    end = raw.rfind(b"\n")
    if end < 0:
        return [], False
    lines = raw[:end].split(b"\n")
    if state.pop("partial", False):
        lines = lines[1:]  # a first read from the middle starts inside a line
    seen = [tuple(key) for key in state["seen"]]
    found, compacted = replies(lines, seen)
    state["seen"] = [list(key) for key in seen[-20:]]
    state["offset"] += end + 1
    return found, compacted


def crossed(before, after, first, every):
    """Whether a point (first, then first plus each gap) lies in (before, after]."""
    if every <= 0:
        return before < first <= after
    point = first if before < first else first + ((before - first) // every + 1) * every
    return before < point <= after


def number(flags, flag_id):
    try:
        return int(flags.value(flag_id) or 0)
    except (TypeError, ValueError):
        return 0


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse":
        return
    try:
        path = transcript_for(hook_input)
        if path is not None:
            check(path, hook_input, flags, answer)
    except (OSError, ValueError, TypeError):
        return


def check(path, hook_input, flags, answer):
    is_agent = bool(hook_input.get("agent_id"))
    key = hook_input.get("agent_id") or hook_input.get("session_id") or os.path.basename(path)
    name = state_file(key)
    with files.locked(name + ".lock"):
        stored = files.read_json(files.state_path(name))
        if stored is None:
            prune(files.state_path(STATE))
        state = stored if isinstance(stored, dict) else {}
        start_over_if_needed(state, path)
        before_size, before_steps = state.get("size", 0), state.get("steps", 0)
        found, compacted = read_new(state, path, is_agent)
        if found:
            state["size"] = found[-1][0]
            state["model"] = found[-1][1] or state.get("model")
            state["steps"] += len(found)
        files.write_json(files.state_path(name), state)
    if is_agent:
        agent_reminders(state, before_size, before_steps, bool(found), flags, answer)
        if compacted and flags.runs(f"{PART}.compaction_note"):
            answer.add_context(NOTE_TEXT.format(path=path, flag=f"{PART}.compaction_note"))
            answer.signal(f"{PART}.compaction_note", "noted")
    elif found and flags.runs(f"{PART}.main_tokens"):
        flag = f"{PART}.main_tokens"
        if crossed(before_size, state["size"], number(flags, f"{flag}.at"), number(flags, f"{flag}.every")):
            answer.add_context(MAIN_TEXT.format(size=state["size"] // 1000, flag=flag))
            answer.signal(flag, "reminded")


def agent_reminders(state, before_size, before_steps, grew, flags, answer):
    model = str(state.get("model") or "")
    skipped = flags.value(f"{PART}.skip_models") or []
    if not grew or any(start and model.startswith(str(start)) for start in skipped):
        return
    fired = []
    tokens = f"{PART}.agent_tokens"
    if flags.runs(tokens):
        first = number(flags, f"{tokens}.at")
        if state.get("point") and flags.runs(f"{PART}.brief_point"):
            first = min(state["point"], number(flags, f"{PART}.brief_point.max") or state["point"])
        if crossed(before_size, state["size"], first, number(flags, f"{tokens}.every")):
            fired.append(tokens)
    steps = f"{PART}.steps"
    if flags.runs(steps) and crossed(before_steps, state["steps"], number(flags, f"{steps}.at"),
                                     number(flags, f"{steps}.every")):
        fired.append(steps)
    if fired:
        answer.add_context(AGENT_TEXT.format(size=state["size"] // 1000, steps=state["steps"],
                                             flags=", ".join(fired)))
        for flag in fired:
            answer.signal(flag, "reminded")
