"""The task list shows what each chat started or waits on, with its state and cost so far.

It uses the standard library only, so a setup without hub can copy this one
file and call `tasks.py --store <folder> hook` from its own hooks.

Each task is one file, <store>/tasks/<id>.json, where the id is the agent id,
the shell's backgroundTaskId or the monitor's taskId. Hook events open and
close tasks:

- An Agent result opens a running agent (background) or a done one
  (foreground). A failed Agent call is a done agent with the outcome failed.
- A Bash result with backgroundTaskId and a Monitor result with taskId open a
  running job. TaskStop closes its task as stopped, since a stopped shell
  sends no notice.
- A task notice in the prompt closes its task. Completed means ok, failed
  means failed and killed means stopped. A later notice always sets the
  outcome.
- A running task that is not on the Stop list of the thread that opened it,
  and whose notice is not in the transcript, closes as gone. A Stop with no
  list closes nothing.
- A question dialog (PreToolUse AskUserQuestion) and a permission prompt
  (PermissionRequest) open a question that needs you. The dialog closes when
  its result comes; a prompt closes when its call runs or its result reaches
  the asking thread's transcript. What is still open when that thread stops,
  or when the user types a new prompt in the main thread, closes as gone.
- A SendMessage with notify_when_idle opens a wait on that chat. Its reply
  or its idle notice in a prompt closes it.
- Stop lists the chat's timers in session_crons. Each is a held timer. One
  that leaves the list closes: a one-shot timer fired, a recurring one was
  deleted.
- Stop also lists the background tasks still in flight, and SubagentStop does
  the same for the tasks that subagent started.
- SessionEnd closes everything still open in that chat as gone.

Each read also reads the chat transcripts from where the last read stopped,
so a notice or a reply that no prompt hook saw still closes its task. Done tasks go
after seven days. One lock file covers every change.
"""
import argparse
import calendar
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

TEXTS = {
    "empty": "No tasks yet. Agents, background jobs, questions, waits and timers show up here when they start.",
    "empty_brief": "No tasks yet.",
    "estimate": "Costs are API-equivalent estimates, not a bill.",
    "none_done": "Nothing finished today.",
    "missing": "No task is called \"{name}\". /hub tasks lists the names.",
    "brief_needs_you": "Needs you {count}: {rows}",
    "brief_running": "Running {count}: {rows}",
    "brief_counts": "Running {running} · done today {done}",
    "brief_cost": "agents ~${usd} so far today",
    "brief_open": "needs you {needs_you} · waits on a chat {waits} · held {held}",
    "show_started": "{name}, started {time}",
    "show_cost": "~${usd} so far, API-equivalent estimate",
    "show_now": "its last tool call, {age} ago",
    "permission": "Allow {tool}: {what}",
    "show_waits_on": "Waits on",
    "group_needs_you": "NEEDS YOU",
    "group_running": "RUNNING",
    "group_waits": "WAITS ON A CHAT",
    "group_held": "HELD",
    "group_done": "DONE TODAY",
}
LABELS = ("Owner", "Call", "Model", "Now", "Cost", "Source")
OUTCOMES = ("ok", "failed", "stopped", "gone")
KINDS = ("agent", "job", "question", "wait", "timer")
OPEN_STATES = ("running", "waiting_on_you", "waiting_on_chat", "held")
SOURCES = {"agent": "agent", "shell": "background shell", "monitor": "monitor", "question": "question",
           "permission": "permission prompt", "message": "message", "timer": "timer"}

KEEP_SECONDS = 7 * 86400
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")
NOTICE = re.compile(r"<task-notification>(?:\s|\\n)*<task-id>([\w-]+)</task-id>(.*?)</task-notification>", re.S)
STATUS = re.compile(r"<status>(\w+)</status>")
MESSAGE = re.compile(r'<cross-session-message from="([^"]*)"(?: from-name="([^"]*)")?')
IDLE = re.compile(r'\[Cross-session idle notice\] "([^"]*)"')
NOT_TYPED = ("<task-notification>", "<cross-session-message", "[Cross-session idle notice]")
LOOK_BACK = 256 * 1024  # how far before a prompt opened its own tool call may sit in the transcript
NOTICE_OUTCOMES = {"completed": "ok", "failed": "failed", "killed": "stopped"}
GLYPHS = {
    "waiting_on_you": "?", "running": "▶", "waiting_on_chat": "…", "held": "‖",
    "ok": "✓", "failed": "✗", "stopped": "■", "gone": "?",
}
SEEN_KEPT = 64  # one reply can fill several lines in a row, so the last few reply keys are enough

# $ per million tokens: input, output, cache read. A cache write costs input x1.25 for five minutes and x2
# for an hour, and a write with no split counts as an hour. The order matters: a longer name goes first.
PRICES = (
    ("opus-5-5", (4, 20, 0.20)),
    ("sonnet-5-5", (2, 10, 0.20)),
    ("haiku-5-5", (0.10, 0.50, 0.01)),
    ("fable", (10, 50, 0.25)),
    ("opus-5", (5, 25, 0.50)),
    ("sonnet-5", (2, 10, 0.20)),
    ("opus-4", (5, 25, 0.50)),
    ("sonnet-4", (3, 15, 0.30)),
    ("haiku-4", (1, 5, 0.10)),
)


def now():
    """Seconds since 1970. HUB_NOW fixes it, so tests pass a fixed time."""
    try:
        return float(os.environ["HUB_NOW"])
    except (KeyError, ValueError):
        return time.time()


def price_of(model):
    name = str(model or "").replace("claude-", "")
    return next((price for family, price in PRICES if name.startswith(family)), None)


def reply_cost(model, usage):
    """The API-equivalent dollars of one reply, or 0 for a model the table does not know."""
    price = price_of(model)
    if price is None or not isinstance(usage, dict):
        return 0.0
    paid_in, paid_out, paid_read = price
    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    hour = number(split.get("ephemeral_1h_input_tokens"))
    five = number(split.get("ephemeral_5m_input_tokens"))
    unsplit = max(number(usage.get("cache_creation_input_tokens")) - hour - five, 0)
    return (number(usage.get("cache_read_input_tokens")) * paid_read
            + five * paid_in * 1.25 + (hour + unsplit) * paid_in * 2
            + number(usage.get("input_tokens")) * paid_in + number(usage.get("output_tokens")) * paid_out) / 1e6


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def text(value, limit=200):
    """One plain line: no control characters, at most limit characters."""
    if not isinstance(value, str):
        return ""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())[:limit]


# The store


class Store:
    def __init__(self, folder):
        self.folder = folder
        self.tasks_dir = os.path.join(folder, "tasks")
        self.sessions_dir = os.path.join(folder, "sessions")

    @contextlib.contextmanager
    def locked(self):
        os.makedirs(self.tasks_dir, mode=0o700, exist_ok=True)
        os.makedirs(self.sessions_dir, mode=0o700, exist_ok=True)
        with open(os.path.join(self.folder, "lock"), "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def task_path(self, task_id):
        return os.path.join(self.tasks_dir, task_id + ".json") if SAFE_ID.fullmatch(task_id or "") else None

    def session_path(self, session):
        return os.path.join(self.sessions_dir, session + ".json") if SAFE_ID.fullmatch(session or "") else None

    def get(self, task_id):
        path = self.task_path(task_id)
        return read_json(path) if path else None

    def put(self, task):
        path = self.task_path(task.get("id"))
        if path:
            write_json(path, task)

    def all(self):
        found = []
        for name in sorted(listdir(self.tasks_dir)):
            task = read_json(os.path.join(self.tasks_dir, name)) if name.endswith(".json") else None
            if isinstance(task, dict) and SAFE_ID.fullmatch(str(task.get("id", ""))):
                found.append(task)
        return found

    def sessions(self):
        found = {}
        for name in listdir(self.sessions_dir):
            data = read_json(os.path.join(self.sessions_dir, name)) if name.endswith(".json") else None
            if isinstance(data, dict):
                found[name[:-5]] = data
        return found


def listdir(folder):
    try:
        return os.listdir(folder)
    except OSError:
        return []


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    """Write the whole file or nothing, so a reader never sees half a task."""
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".write-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def remove(path):
    with contextlib.suppress(OSError):
        os.unlink(path)


# Hook events


def on_event(folder, event, hook_input, owner_name=""):
    """Change the store for one hook event. Events that open or close no task touch no file."""
    if not isinstance(hook_input, dict):
        return
    store = Store(folder)
    session = hook_input.get("session_id") if isinstance(hook_input.get("session_id"), str) else ""
    thread = hook_input.get("agent_id") or None
    tool = hook_input.get("tool_name")
    response = hook_input.get("tool_response") if isinstance(hook_input.get("tool_response"), dict) else {}
    tool_input = hook_input.get("tool_input") if isinstance(hook_input.get("tool_input"), dict) else {}
    owner = {"session": session, "name": text(owner_name, 60)}
    if event in ("PostToolUse", "PostToolUseFailure") and isinstance(tool, str) and store_exists(store):
        with store.locked():  # the call ran or failed, so its prompt or dialog was answered
            close(store, prompt_key(session, thread, tool, tool_input), "ok", only_open=True)
            if tool == "AskUserQuestion" and isinstance(hook_input.get("tool_use_id"), str):
                close(store, hook_input["tool_use_id"], "ok", only_open=True)
    if event == "PreToolUse" and tool == "AskUserQuestion":
        call = hook_input.get("tool_use_id")
        if isinstance(call, str) and SAFE_ID.fullmatch(call):
            asked = new_task("question", call, "question", call, {}, hook_input)
            questions = tool_input.get("questions")
            first = questions[0] if isinstance(questions, list) and questions else {}
            asked["title"] = text(first.get("question") if isinstance(first, dict) else "", 200) or call
            open_task(store, asked, owner, hook_input)
    elif event == "PermissionRequest" and isinstance(tool, str) and tool != "AskUserQuestion":
        key = prompt_key(session, thread, tool, tool_input)
        asked = new_task("question", key, "permission", key, {}, hook_input)
        what = next((text(tool_input.get(field), 120) for field in ("description", "command", "file_path", "url",
                                                                   "pattern") if text(tool_input.get(field))), "")
        asked["title"] = TEXTS["permission"].format(tool=text(tool, 60), what=what) if what else text(tool, 60)
        asked["asked"]["tool"], asked["asked"]["input"] = tool, digest(tool_input)
        open_task(store, asked, owner, hook_input)
    elif event == "PostToolUse" and tool == "SendMessage":
        call, target = hook_input.get("tool_use_id"), tool_input.get("to")
        if (tool_input.get("notify_when_idle") is True and response.get("success") is True and not thread
                and isinstance(call, str) and SAFE_ID.fullmatch(call) and text(target)):
            wait = new_task("wait", call, "message", call, {}, hook_input)
            wait.update(title=text(tool_input.get("summary"), 200) or text(tool_input.get("message"), 200) or call,
                        waits_on=text(target, 200))
            open_task(store, wait, owner, hook_input)
    elif event == "PostToolUse" and tool in ("Agent", "Bash", "Monitor"):
        task = opened(tool, tool_input, response, hook_input)
        if task is None:
            return
        task["owner"] = owner
        with store.locked():
            follow(store, session, hook_input.get("transcript_path"))
            old = store.get(task["id"])
            if old and old.get("state") == "done" and task["state"] == "running":
                return  # a notice or a stop already closed it
            if old:
                for field in ("started", "cost", "now", "now_at"):
                    task[field] = old.get(field, task[field])
            store.put(task)
    elif event == "PostToolUseFailure" and tool == "Agent":
        call = hook_input.get("tool_use_id")
        if not isinstance(call, str) or not SAFE_ID.fullmatch(call):
            return
        task = new_task("agent", call, "agent", call, tool_input, hook_input)
        stopped = hook_input.get("is_interrupt") is True
        task.update(state="done", outcome="stopped" if stopped else "failed", ended=now(),
                    detail=text(hook_input.get("error"), 400), owner=owner)
        with store.locked():
            store.put(task)
    elif event == "PostToolUse" and tool == "TaskStop":
        key = response.get("task_id") or tool_input.get("task_id")
        if store_exists(store) and isinstance(key, str):
            with store.locked():
                close(store, key, "stopped", only_open=True)
    elif event == "UserPromptSubmit":
        prompt = hook_input.get("prompt") if isinstance(hook_input.get("prompt"), str) else ""
        if store_exists(store):
            with store.locked():
                for key, outcome in notices(prompt):
                    close(store, key, outcome)
                close_waits(store, session, prompt)
                if not any(mark in prompt for mark in NOT_TYPED):
                    close_questions(store, session, None)  # a typed prompt means no dialog is open
    elif event in ("Stop", "SubagentStop"):
        crons = hook_input.get("session_crons") if event == "Stop" else None
        if not store_exists(store) and not (isinstance(crons, list) and crons):
            return
        with store.locked():
            read_transcripts(store)
            close_questions(store, session, thread)
            listed = hook_input.get("background_tasks")
            if isinstance(listed, list):
                ids = {item.get("id") for item in listed if isinstance(item, dict)}
                for task in store.all():
                    if (task.get("state") == "running" and task["owner"].get("session") == session
                            and task.get("opened_by") == thread and task["source"]["key"] not in ids):
                        close(store, task["id"], "gone", only_open=True)
            if isinstance(crons, list):
                follow_timers(store, session, crons, owner, hook_input)
    elif event == "SessionEnd" and store_exists(store):
        with store.locked():
            read_transcripts(store)
            for task in store.all():
                if is_open(task) and task["owner"].get("session") == session:
                    if task.get("kind") == "question" and settle(task):
                        store.put(task)
                        continue
                    close(store, task["id"], "gone", only_open=True)


def is_open(task):
    return task.get("state") in OPEN_STATES


def digest(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prompt_key(session, thread, tool, tool_input):
    """The id of a permission prompt: one tool call with this input, in one thread of one chat."""
    return "p" + digest([session, thread or "", tool, tool_input])[:20]


def thread_transcript(hook_input):
    """The transcript of the thread that asks: the chat's own, or the subagent's."""
    transcript, session, agent = (hook_input.get("transcript_path"), hook_input.get("session_id"),
                                  hook_input.get("agent_id"))
    if not isinstance(transcript, str):
        return ""
    if not agent:
        return transcript
    if not (isinstance(session, str) and SAFE_ID.fullmatch(session) and isinstance(agent, str)
            and SAFE_ID.fullmatch(agent)):
        return ""
    return os.path.join(os.path.dirname(transcript), session, "subagents", f"agent-{agent}.jsonl")


def open_task(store, task, owner, hook_input):
    """Write a question or a wait. One that is still open keeps its start; a done one opens again."""
    task["owner"] = owner
    task["state"] = "waiting_on_chat" if task["kind"] == "wait" else "waiting_on_you"
    with store.locked():
        follow(store, owner["session"], hook_input.get("transcript_path"))
        old = store.get(task["id"])
        if old and is_open(old):
            return
        store.put(task)


def close_waits(store, session, prompt):
    """Close the waits of this chat on a chat whose message or idle notice is in the prompt."""
    names = {match.group(1).lower() for match in IDLE.finditer(prompt)}
    for match in MESSAGE.finditer(prompt):
        names |= {name.lower() for name in match.groups() if name}
    if not names:
        return
    for task in store.all():
        if (task.get("kind") == "wait" and is_open(task) and task["owner"].get("session") == session
                and str(task.get("waits_on", "")).lower() in names):
            close(store, task["id"], "ok", only_open=True)


def close_questions(store, session, thread):
    """Close one thread's open questions: ok when the transcript holds the answer, else gone."""
    for task in store.all():
        if (task.get("kind") == "question" and is_open(task) and task["owner"].get("session") == session
                and task.get("opened_by") == thread):
            if settle(task):
                store.put(task)
            else:
                close(store, task["id"], "gone", only_open=True)


def settle(task):
    """Mark a question done ok when its call's result reached the asking thread's transcript after it opened."""
    asked = task.get("asked") or {}
    path, offset = asked.get("transcript"), asked.get("offset")
    if not isinstance(path, str) or not path or not isinstance(offset, int):
        return False
    start = max(0, offset - LOOK_BACK) if asked.get("tool") else offset
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read()
    except OSError:
        return False
    calls, results, position = {task["source"].get("call")} - {"", None}, set(), start
    for raw in data.split(b"\n"):
        line_start, position = position, position + len(raw) + 1
        if b"tool_" not in raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if (part.get("type") == "tool_use" and asked.get("tool") == part.get("name")
                    and digest(part.get("input")) == asked.get("input")):
                calls.add(part.get("id"))
            elif part.get("type") == "tool_result" and line_start >= offset:
                results.add(part.get("tool_use_id"))
    if not calls & results:
        return False
    task.update(state="done", outcome="ok", ended=task.get("ended") or now())
    return True


def follow_timers(store, session, crons, owner, hook_input):
    """Hold one timer per listed cron, and close this chat's timers that left the list."""
    listed = {}
    for cron in crons:
        if isinstance(cron, dict) and isinstance(cron.get("id"), str) and SAFE_ID.fullmatch("cron-" + cron["id"]):
            listed["cron-" + cron["id"]] = cron
    for key, cron in listed.items():
        if store.get(key):
            continue
        timer = new_task("timer", key, "timer", key, {}, hook_input)
        timer.update(state="held", owner=owner, title=text(cron.get("prompt"), 200) or key,
                     detail=text(cron.get("schedule"), 100), recurring=cron.get("recurring") is True)
        store.put(timer)
    for task in store.all():
        if (task.get("kind") == "timer" and is_open(task) and task["owner"].get("session") == session
                and task["id"] not in listed):
            close(store, task["id"], "stopped" if task.get("recurring") else "ok", only_open=True)


def store_exists(store):
    return os.path.isdir(store.tasks_dir)


def new_task(kind, task_id, source, key, tool_input, hook_input):
    return {
        "id": task_id, "kind": kind, "state": "running", "outcome": None,
        "title": text(tool_input.get("description"), 200) or text(tool_input.get("command"), 200) or key,
        "detail": "", "model": None,
        "source": {"type": source, "key": key, "call": text(hook_input.get("tool_use_id"), 100)},
        "owner": {}, "opened_by": hook_input.get("agent_id") or None,
        "transcript": hook_input.get("transcript_path") if isinstance(hook_input.get("transcript_path"), str) else "",
        "started": now(), "ended": None, "cost": None, "now": None, "now_at": None,
    } | (asked_at(hook_input) if kind == "question" else {})


def asked_at(hook_input):
    """Where the asking thread's transcript ended when a question opened."""
    path = thread_transcript(hook_input)
    try:
        offset = os.path.getsize(path) if path else 0
    except OSError:
        offset = 0
    return {"asked": {"transcript": path, "offset": offset}}


def opened(tool, tool_input, response, hook_input):
    """The task an Agent, Bash or Monitor result opens, or None."""
    if tool == "Agent":
        key = response.get("agentId")
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key):
            return None
        task = new_task("agent", key, "agent", key, tool_input, hook_input)
        task["model"] = text(response.get("resolvedModel"), 60) or None
        task["agent_type"] = text(response.get("agentType") or tool_input.get("subagent_type"), 60) or None
        if response.get("status") == "async_launched" or response.get("isAsync") is True:
            return task
        if response.get("status") == "completed":
            task.update(state="done", outcome="ok", ended=now())
            return task
        return None
    key = response.get("backgroundTaskId") if tool == "Bash" else response.get("taskId")
    if not isinstance(key, str) or not SAFE_ID.fullmatch(key):
        return None
    return new_task("job", key, "shell" if tool == "Bash" else "monitor", key, tool_input, hook_input)


def notices(prompt):
    """(task id, outcome) for each task notice with a status in the text. A monitor event has no status."""
    found = []
    for match in NOTICE.finditer(prompt if isinstance(prompt, str) else ""):
        status = STATUS.search(match.group(2))
        if status and status.group(1) in NOTICE_OUTCOMES:
            found.append((match.group(1), NOTICE_OUTCOMES[status.group(1)]))
    return found


def close(store, key, outcome, only_open=False):
    task = store.get(key)
    if not task or (only_open and not is_open(task)):
        return
    task.update(state="done", outcome=outcome)
    task["ended"] = task.get("ended") or now()
    store.put(task)


def follow(store, session, transcript):
    """Start reading a chat's transcript at its present end, the first time the chat opens a task."""
    path = store.session_path(session)
    if not path or os.path.exists(path) or not isinstance(transcript, str):
        return
    try:
        size = os.path.getsize(transcript)
    except OSError:
        size = 0
    write_json(path, {"transcript": transcript, "offset": size})


def new_lines(path, offset):
    """The whole lines after offset, and the offset after the last of them."""
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n") + 1
    return data[:end].decode("utf-8", "replace").splitlines(), offset + end


def read_transcripts(store):
    """Close the tasks whose notice, reply or idle notice reached a transcript after the last read."""
    for session, data in store.sessions().items():
        transcript, offset = data.get("transcript"), data.get("offset")
        if not isinstance(transcript, str) or not isinstance(offset, int):
            continue
        try:
            if os.path.getsize(transcript) < offset:
                offset = 0  # the file was written anew
        except OSError:
            continue
        lines, end = new_lines(transcript, offset)
        for line in lines:
            if "task-notification" not in line and "ross-session" not in line:
                continue
            prompt = " ".join(prompt_texts(line))
            for key, outcome in notices(prompt):
                close(store, key, outcome)
            close_waits(store, session, prompt)
        if end != data.get("offset"):
            write_json(store.session_path(session), dict(data, offset=end))


def prompt_texts(line):
    """The prompt texts in one transcript line: a queued prompt or a user message. Model text is left out."""
    try:
        entry = json.loads(line)
    except ValueError:
        return []
    if not isinstance(entry, dict):
        return []
    found = []
    attachment = entry.get("attachment")
    if entry.get("type") == "attachment" and isinstance(attachment, dict) and isinstance(attachment.get("prompt"), str):
        found.append(attachment["prompt"])
    message = entry.get("message")
    if entry.get("type") == "user" and isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            found.append(content)
        elif isinstance(content, list):
            found += [part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
    return found


def agent_transcript(task):
    transcript, session = task.get("transcript"), task.get("owner", {}).get("session")
    if not transcript or not session or not SAFE_ID.fullmatch(session):
        return None
    return os.path.join(os.path.dirname(transcript), session, "subagents", f"agent-{task['source']['key']}.jsonl")


def read_agent(task):
    """Add the replies written to an agent's transcript since the last read: their cost and its last tool call."""
    path = agent_transcript(task) if task.get("kind") == "agent" else None
    cost = task.get("cost") or {"usd": 0.0, "read_to": 0, "seen": []}
    if not path:
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= cost["read_to"]:
        return False
    lines, end = new_lines(path, cost["read_to"])
    seen = list(cost.get("seen") or [])
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or not isinstance(entry.get("message"), dict):
            continue
        message = entry["message"]
        for part in message.get("content") if isinstance(message.get("content"), list) else []:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                given = part.get("input") if isinstance(part.get("input"), dict) else {}
                task["now"] = text(given.get("description"), 120) or text(part.get("name"), 60) or None
                task["now_at"] = seconds_of(entry.get("timestamp")) or now()
        reply = [str(entry.get("requestId")), str(message.get("id"))]
        if reply in seen:
            continue
        seen = (seen + [reply])[-SEEN_KEPT:]
        cost["usd"] = cost["usd"] + reply_cost(message.get("model"), message.get("usage"))
        if not task.get("model") and isinstance(message.get("model"), str):
            task["model"] = text(message["model"], 60)
    task["cost"] = dict(cost, read_to=end, seen=seen)
    return True


def seconds_of(stamp):
    """An ISO time such as 2026-11-10T11:00:00.000Z as seconds since 1970, or None."""
    if not isinstance(stamp, str):
        return None
    try:
        return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def refresh(store):
    """Read what changed since the last read, drop old done tasks, and return the tasks left."""
    if not store_exists(store):
        return []
    with store.locked():
        read_transcripts(store)
        kept, cutoff = [], now() - KEEP_SECONDS
        for task in store.all():
            if task.get("state") == "done" and number(task.get("ended")) < cutoff:
                remove(store.task_path(task["id"]))
                continue
            if read_agent(task) or (task.get("kind") == "question" and is_open(task) and settle(task)):
                store.put(task)
            kept.append(task)
        sessions = {task.get("owner", {}).get("session") for task in kept}
        for session in store.sessions():
            if session not in sessions:
                remove(store.session_path(session))
    return kept


# The view


def today(task):
    ended = task.get("ended")
    return number(ended) > 0 and time.localtime(ended)[:3] == time.localtime(now())[:3]


def model_name(model):
    name = str(model or "").replace("claude-", "")
    return next((family.split("-")[0] for family, _ in PRICES if name.startswith(family)), name[:8])


def money(usd):
    return f"{usd:.2f}"


def usd_of(task):
    return (task.get("cost") or {}).get("usd") or 0.0


def age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def owner_of(task):
    owner = task.get("owner") or {}
    return owner.get("name") or str(owner.get("session") or "")[:8]


def cut(value, width, plain):
    if len(value) <= width:
        return value
    end = "..." if plain else "…"
    return value[:max(0, width - len(end))] + end if width > len(end) else value[:width]


def groups(tasks):
    """The open tasks by state in OPEN_STATES order, oldest first, then today's done tasks, newest first."""
    found = {state: sorted((task for task in tasks if task.get("state") == state), key=lambda task: task["started"])
             for state in OPEN_STATES}
    found["done"] = sorted((task for task in tasks if task.get("state") == "done" and today(task)),
                           key=lambda task: -number(task.get("ended")))
    return found


HEADINGS = (("waiting_on_you", "group_needs_you"), ("running", "group_running"), ("waiting_on_chat", "group_waits"),
            ("held", "group_held"), ("done", "group_done"))
ALWAYS_SHOWN = ("running", "done")


def columns(width, any_cost):
    """The optional columns that fit: the cost goes first, then the model."""
    shown = ["model", "cost"] if any_cost else ["model"]
    while shown and width - fixed_width(shown) < 24:
        shown.pop()
    return shown


OWNER, KIND, STATE, MODEL, COST = 12, 8, 7, 6, 7


def fixed_width(shown):
    return 2 + (OWNER + 2) + (KIND + 2) + (STATE + 2) + sum((MODEL if name == "model" else COST) + 2 for name in shown)


def row(task, width, shown, plain):
    state = task.get("outcome") or task.get("state")
    mark = "" if plain else GLYPHS.get(state, "?") + " "
    status = age(now() - number(task.get("started"))) if is_open(task) else state
    rest = [cut(owner_of(task), OWNER, plain).ljust(OWNER), task.get("kind", "").ljust(KIND), status.ljust(STATE)]
    if "model" in shown:
        rest.append(model_name(task.get("model")).ljust(MODEL))
    if "cost" in shown:
        usd = usd_of(task)
        rest.append(("~$" + money(usd) if usd else "").ljust(COST))
    title_width = max(8, width - fixed_width(shown))
    line = mark + cut(task.get("title") or task["id"], title_width, plain).ljust(title_width) + "  " + "  ".join(rest)
    return line.rstrip()


def heading(label, count, width, plain):
    line = f"{label}  {count}"
    return line if plain else (line + " " + "─" * max(0, width - len(line) - 1))


def render(tasks, width, plain, only_done=False):
    found = groups(tasks)
    if only_done and not found["done"]:
        return TEXTS["none_done"] + "\n"
    if not any(found.values()):
        return TEXTS["empty"] + "\n"
    shown = columns(width, any(usd_of(task) for items in found.values() for task in items))
    lines = []
    for state, label in HEADINGS:
        items = found[state]
        if (only_done and state != "done") or (not items and state not in ALWAYS_SHOWN):
            continue
        lines.append(heading(TEXTS[label], len(items), width, plain))
        lines += [row(task, width, shown, plain) for task in items]
        lines.append("")
    if "cost" in shown:
        lines.append(TEXTS["estimate"])
    return "\n".join(lines).rstrip("\n") + "\n"


def brief_rows(items):
    rows = ", ".join(f"{cut(task.get('title') or task['id'], 40, True)} ({owner_of(task)})" for task in items[:3])
    return rows + (f", +{len(items) - 3}" if len(items) > 3 else "")


def brief(tasks):
    found = groups(tasks)
    if not any(found.values()):
        return TEXTS["empty_brief"] + "\n"
    lines = []
    needs_you, running, done = found["waiting_on_you"], found["running"], found["done"]
    if needs_you:
        lines.append(TEXTS["brief_needs_you"].format(count=len(needs_you), rows=brief_rows(needs_you)))
    if running:
        lines.append(TEXTS["brief_running"].format(count=len(running), rows=brief_rows(running)))
    counts = TEXTS["brief_counts"].format(running=len(running), done=len(done))
    if needs_you or found["waiting_on_chat"] or found["held"]:
        counts += " · " + TEXTS["brief_open"].format(needs_you=len(needs_you), waits=len(found["waiting_on_chat"]),
                                                     held=len(found["held"]))
    usd = sum(usd_of(task) for task in running + done if task.get("kind") == "agent")
    if usd:
        counts += " · " + TEXTS["brief_cost"].format(usd=money(usd))
    lines.append(counts)
    return "\n".join(lines) + "\n"


def find(tasks, name):
    wanted = name.strip().lower()
    for test in (lambda task: (task.get("title") or "").lower() == wanted,
                 lambda task: task["source"]["key"].lower() == wanted,
                 lambda task: (task.get("title") or "").lower().startswith(wanted)):
        found = [task for task in tasks if test(task)]
        if found:
            return max(found, key=lambda task: number(task.get("started")))
    return None


def show(task):
    started = time.strftime("%H:%M", time.localtime(number(task.get("started"))))
    lines = [TEXTS["show_started"].format(name=task.get("title") or task["id"], time=started)]
    state = task.get("state", "")
    if task.get("outcome"):
        state += ", " + task["outcome"]
    if task.get("detail"):
        state += ". " + task["detail"]
    source = task["source"]
    values = [("State", state), ("Owner", owner_of(task)), ("Call", source.get("call")),
              ("Model", task.get("model")), ("Now", None), ("Cost", None),
              (TEXTS["show_waits_on"], task.get("waits_on")),
              ("Source", f"{SOURCES.get(source.get('type'), source.get('type'))} {source.get('key')}")]
    if task.get("now"):
        values[4] = ("Now", f"{task['now']} ({TEXTS['show_now'].format(age=age(now() - number(task.get('now_at'))))})")
    if usd_of(task):
        values[5] = ("Cost", TEXTS["show_cost"].format(usd=money(usd_of(task))))
    lines += [f"{label.ljust(10)}{value}" for label, value in values if value]
    return "\n".join(lines) + "\n"


def plain_wanted(flag):
    if flag or os.environ.get("TERM") == "dumb":
        return True
    locale = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or ""
    return bool(locale) and "utf-8" not in locale.lower() and "utf8" not in locale.lower()


def main(argv, folder=None, hook_verb=True):
    """The task list command: the list, done, show <name>, or hook, which reads one hook input from stdin."""
    parser = argparse.ArgumentParser(prog="/hub tasks")
    parser.add_argument("view", nargs="?", default="list", choices=["list", "done", "show", "hook"])
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--store", default=folder)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plain", action="store_true")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--width", type=int)
    args = parser.parse_args(argv)
    if args.view == "hook" and not hook_verb:
        parser.error("hook runs only from tasks.py itself")
    if not hook_verb:
        args.store = folder  # /hub tasks reads only hub's own task list
    if not args.store:
        parser.error("--store names the task list's folder")
    if args.view == "hook":
        try:
            hook_input = json.load(sys.stdin)
        except ValueError:
            return 0
        if isinstance(hook_input, dict):
            on_event(args.store, hook_input.get("hook_event_name"), hook_input, hook_input.get("session_title") or "")
        return 0
    tasks = refresh(Store(args.store))
    plain = plain_wanted(args.plain)
    width = min(max(args.width or shutil.get_terminal_size((80, 24)).columns, 40), 300)
    if args.view == "show":
        task = find(tasks, " ".join(args.rest))
        if task is None:
            print(TEXTS["missing"].format(name=" ".join(args.rest)), file=sys.stderr)
            return 1
        sys.stdout.write(json.dumps(task, ensure_ascii=False) + "\n" if args.json else show(task))
        return 0
    if args.json:
        print(json.dumps({"tasks": tasks}, ensure_ascii=False))
    elif args.brief:
        sys.stdout.write(brief(tasks))
    else:
        sys.stdout.write(render(tasks, width, plain, only_done=args.view == "done"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
