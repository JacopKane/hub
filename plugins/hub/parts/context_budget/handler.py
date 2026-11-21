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
transcript.

Once a day per project, the main chat is told when the ledger file or the
memory folder beside its transcript outgrows the store_size limits: the
ledger's size and its lines whose first date is old, the size of MEMORY.md,
the count and size of the other notes, and notes that have not changed for a
long time. The day is kept for the project, so other chats in it are not told
that day. It reads these files and never changes them.

It only adds context and never stops a call. A damaged state
file starts over and an unreadable transcript is skipped, so this part never
makes the dispatcher fail. Claude Code only.
"""
import datetime
import json
import os
import re
import time

from hub import clock, files
from hub.clock import local as zoned_time

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
STORE = f"{PART}.store_size"
DAY = 24 * 3600
LEDGER_HEAD = 40  # a ledger line's date counts when it is this near the start of the line
MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
MONTH_SPELLINGS = {"mär": "mar", "mrz": "mar", "mai": "may", "okt": "oct", "dez": "dec"}  # German short months
LINE_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b"
                       r"|\b(\d{1,2})\.? (Jan|Feb|Mar|Mär|Mrz|Apr|May|Mai|Jun|Jul|Aug|Sep|Oct|Okt|Nov|Dec|Dez)[a-z.]*")
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
        return
    flag = f"{PART}.main_tokens"
    if found and flags.runs(flag) and crossed(before_size, state["size"], number(flags, f"{flag}.at"),
                                              number(flags, f"{flag}.every")):
        answer.add_context(MAIN_TEXT.format(size=state["size"] // 1000, flag=flag))
        answer.signal(flag, "reminded")
    if flags.runs(STORE):
        store_reminder(os.path.dirname(path), flags, answer)


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


# the ledger and memory size reminder

def store_reminder(project, flags, answer):
    """Tell the main chat, once a day per project, what in the ledger and memory is over its limits."""
    now = clock.epoch()
    day = zoned_time(now).strftime("%Y-%m-%d")
    name = state_file("store-" + os.path.basename(project))
    with files.locked(name + ".lock"):
        stored = files.read_json(files.state_path(name))
        if isinstance(stored, dict) and stored.get("day") == day:
            return
        files.write_json(files.state_path(name), {"day": day})
    ledger = os.path.basename(str(flags.value(f"{STORE}.ledger") or "ledger.md"))
    issues = store_issues(store_facts(project, ledger), ledger, store_limits(flags), now)
    text = flags.text(STORE)
    if issues and text:
        answer.add_context(text.replace("{issues}", " ".join(issues)) + f" ({STORE})")
        answer.signal(STORE, "reminded")


def store_limits(flags):
    names = ("ledger_bytes", "ledger_days", "index_bytes", "notes", "notes_bytes", "note_days")
    return {limit: number(flags, f"{STORE}.{limit}") for limit in names}


def store_facts(project, ledger):
    """What the limits are checked against. A file or folder that can not be read is None."""
    return {"ledger": read_ledger(os.path.join(project, ledger)),
            "index": size_of(os.path.join(project, "memory", "MEMORY.md")),
            "notes": read_notes(os.path.join(project, "memory"))}


def read_ledger(path):
    """(size in bytes, the start of each line), or None."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    lines = raw.decode("utf-8", "replace").splitlines()
    return len(raw), [line[:LEDGER_HEAD] for line in lines]


def size_of(path):
    try:
        return os.path.getsize(path) if os.path.isfile(path) else None
    except OSError:
        return None


def read_notes(folder):
    """(size, time of last change) of each note but MEMORY.md, or None."""
    try:
        entries = [entry for entry in os.scandir(folder)
                   if entry.name.endswith(".md") and entry.name != "MEMORY.md" and entry.is_file()]
        return [(entry.stat().st_size, entry.stat().st_mtime) for entry in entries]
    except OSError:
        return None


def store_issues(facts, ledger, limits, now):
    """One plain sentence for each limit the facts pass. A limit of 0 is not checked."""
    return [*ledger_issues(facts["ledger"], ledger, limits, now),
            *index_issues(facts["index"], limits),
            *notes_issues(facts["notes"], limits, now)]


def ledger_issues(found, ledger, limits, now):
    if found is None:
        return []
    size, heads = found
    bytes_limit, days = limits["ledger_bytes"], limits["ledger_days"]
    too_big = [f"{ledger} is {size // 1000}k bytes, over the {bytes_limit // 1000}k limit."] \
        if bytes_limit and size > bytes_limit else []
    dates = [line_date(head, now) for head in heads] if days else []
    old = sum(1 for date in dates if date is not None and date < now - days * DAY)
    too_old = [f"{ledger} has {counted(old, 'entry', 'entries')} dated more than {days} days ago."] if old else []
    return too_big + too_old


def index_issues(size, limits):
    # The index_bytes default, 20000, is about 80% of the 25KB of MEMORY.md that Claude Code loads at
    # session start (https://code.claude.com/docs/en/memory#how-it-works, changelog 2.1.83), so the
    # reminder comes before Claude Code cuts the index off. Its 200-line limit is not checked.
    limit = limits["index_bytes"]
    return [f"MEMORY.md is {size // 1000}k bytes, over the {limit // 1000}k limit."] \
        if size is not None and limit and size > limit else []


def notes_issues(notes, limits, now):
    if notes is None:
        return []
    count, total = len(notes), sum(size for size, _ in notes)
    count_limit, bytes_limit, days = limits["notes"], limits["notes_bytes"], limits["note_days"]
    over = (count_limit and count > count_limit) or (bytes_limit and total > bytes_limit)
    named = [f"{count_limit} notes"] * bool(count_limit) + [f"{bytes_limit // 1000}k bytes"] * bool(bytes_limit)
    too_many = [f"The memory folder has {counted(count, 'note', 'notes')} and {total // 1000}k bytes, "
                f"over the limit of {' and '.join(named)}."] if over else []
    stale = sum(1 for _, changed in notes if changed < now - days * DAY) if days else 0
    too_stale = [f"{counted(stale, 'memory note has', 'memory notes have')} not changed in more than "
                 f"{days} days."] if stale else []
    return too_many + too_stale


def counted(count, one, many):
    return f"{count} {one if count == 1 else many}"


def line_date(text, now):
    """The first date in a ledger line as seconds since 1970, or None.

    A day and month with no year, such as "3 Okt", is in the year of now, or
    in the year before when that would put it more than a day after now.
    """
    match = LINE_DATE.search(text)
    if not match:
        return None
    try:
        if match.group(1):
            return day_start(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        spelled = match.group(5)[:3].lower()
        month, day_of_month = MONTHS[MONTH_SPELLINGS.get(spelled, spelled)], int(match.group(4))
        year = zoned_time(now).year
        this_year = day_start(year, month, day_of_month)
        return this_year if this_year <= now + DAY else day_start(year - 1, month, day_of_month)
    except (ValueError, KeyError, OverflowError):
        return None


def day_start(year, month, day_of_month):
    return datetime.datetime(year, month, day_of_month, tzinfo=datetime.timezone.utc).timestamp()
