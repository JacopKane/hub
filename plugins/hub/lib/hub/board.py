"""One board model for every surface: the band, /hub board, and later the pane and the web page.

The chat_board part writes one state file per chat, in
<state>/board/chats/<session id>.json, only when the chat's state changes.
load() reads those files, hub's session records, the chat listing, the task
ledgers and the doctor's last result, and returns the cards, the task
columns, the counters and the Board strip. It never runs a model and runs
`claude agents --json` only when no listing was kept at all.

A chat is offline when its recorded process ended, or a new process took its
pid. A chat hub holds no record of follows the newest listing row. An
offline card leaves the board a day after it went offline, or sooner once
its session ended and hub holds neither its record nor a listing row for it.
The newest listing is the newer of the copy of the chat's own ListAgents
result and the sessions cache, so a listing the chat already has costs
nothing.

The clock is HUB_NOW when it is set, so tests pass a fixed time.
"""
import json
import os
import socket
import time
import unicodedata

from . import files, sessions

FOLDER = "board"
CHATS = os.path.join(FOLDER, "chats")
TASKS = os.path.join(FOLDER, "tasks")
LISTING = os.path.join(FOLDER, "listing.json")
STRIP = os.path.join(FOLDER, "strip.jsonl")
DOCTOR_LAST = "doctor-last.json"
ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons.json")

STATES = ["needs_you", "busy", "idle", "offline"]  # the card order, which is the counter order
TASK_STATES = ["needs_you", "working", "queued", "done"]  # the task columns
ICON_OF = {"needs_you": "needs_you", "busy": "busy", "idle": "waiting", "offline": "offline", "working": "busy",
           "queued": "waiting", "done": "done", "failed": "failed", "unread": "unread", "time": "time"}
WORDS = {"needs_you": "needs you", "busy": "busy", "idle": "idle", "offline": "offline",
         "working": "working", "queued": "queued", "done": "done"}
LISTING_STATES = {"busy": "busy", "running": "busy", "working": "busy", "idle": "idle", "waiting": "needs_you",
                  "needs_input": "needs_you", "blocked": "needs_you"}
DONE_HOURS = 24
LEDGER_DAYS = 7
OFFLINE_HOURS = 24  # hours an offline card stays on the board
STRIP_HOURS = 24
STRIP_LINES = 5
REMOTE_TASKS = "tasks show only for chats on this machine"
NOT_CURSOR = "Cursor chats are not visible to hub, so this board shows Claude Code chats only."
# /hub prints at most 15 body lines (surfaces.md, the /hub zones). A card takes 3.
BODY_LINES = 15
CARD_LINES = 3
EMPTY = "No chats yet. A Claude Code chat shows here after its first prompt."
NAME_CELLS = 40  # a chat or machine name is cut to this many terminal cells
TEXT_CHARS = 120  # a question, a task or a strip reason is cut to this many characters
HIDDEN = ("Cc", "Cf", "Cs")  # control, format and surrogate characters, such as escapes and direction overrides


def now():
    value = os.environ.get("HUB_NOW")
    try:
        return float(value) if value else time.time()
    except ValueError:
        return time.time()


def machine():
    """This machine's name, cut like every name read back from a card, so the two still match when it is long."""
    return clean(socket.gethostname().split(".")[0], cell_limit=NAME_CELLS) or "this machine"


# text from chats, hook input and the listing is data: one plain line, cut when it is too long

def cells(text):
    """How many terminal cells text takes: wide characters take two, combining marks none."""
    return sum(0 if unicodedata.combining(character) else
               2 if unicodedata.east_asian_width(character) in ("W", "F") else 1 for character in text)


def cut_cells(text, limit):
    if cells(text) <= limit:
        return text
    kept, used = [], 0
    for character in text:
        width = cells(character)
        if used + width > limit - 1:
            break
        kept.append(character)
        used += width
    return "".join(kept) + "…"


def clean(value, chars=TEXT_CHARS, cell_limit=None):
    """Make one line of plain text. Hidden characters go, each run of spaces or line breaks becomes one space, and
    long text is cut."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(character for character in text
                   if character.isspace() or unicodedata.category(character) not in HIDDEN)
    text = " ".join(text.split())
    if chars is not None and len(text) > chars:
        text = text[:chars - 1] + "…"
    return cut_cells(text, cell_limit) if cell_limit else text


def pad(text, width):
    return text + " " * max(0, width - cells(text))


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_pid(value):
    """Whether value is a real process id, a whole number above 0. A pid of 0, -1 or true would reach every process."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def read(path):
    """A state file's JSON, or None when it is missing, gone, a folder, half written or not JSON."""
    try:
        return files.read_json(path)
    except (OSError, ValueError):
        return None


def write(path, data):
    """Write a board file. A link in its place is removed first, so hub never writes through it."""
    if os.path.islink(path):
        os.unlink(path)
    files.write_json(path, data)


def record_of(session):
    try:
        data = sessions.record(session)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def icons():
    with open(ICONS, encoding="utf-8") as handle:
        return {icon["id"]: icon for icon in json.load(handle)["icons"]}


def safe(session):
    return sessions.SAFE_ID.sub("", str(session or ""))


def card_path(session):
    """The card file, or None when the session id has no letter or digit left."""
    return files.state_path(os.path.join(CHATS, safe(session) + ".json")) if safe(session) else None


def ledger_path(session):
    return files.state_path(os.path.join(TASKS, safe(session) + ".json")) if safe(session) else None


def read_folder(folder):
    path = files.state_path(folder)
    try:
        names = sorted(os.listdir(path))
    except FileNotFoundError:
        return []
    found = []
    for name in names:
        if name.endswith(".json"):
            data = read(os.path.join(path, name))  # a file can go between the list and the read
            if isinstance(data, dict):
                found.append((name[:-5], data))
    return found


# times

def since(seconds):
    """A time since: 12m, 3h, 4d, and after a week a date such as 6 Nov."""
    if seconds is None:
        return ""
    seconds = max(0, seconds)
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d"
    moment = time.localtime(now() - seconds)
    return f"{moment.tm_mday} {time.strftime('%b', moment)}"


# the strip

def add_strip(mark, text, why="", chat=None, at=None):
    files.append_text(files.state_path(STRIP), json.dumps(
        {"time": now() if at is None else at, "mark": mark, "text": text, "why": why, "chat": chat}) + "\n")


def kept_strip(at):
    """The strip lines of the last 24 hours. A broken line is skipped, and the others are kept."""
    try:
        with open(files.state_path(STRIP), encoding="utf-8", errors="replace") as handle:
            raw = handle.read().splitlines()
    except OSError:
        return []
    lines = []
    for text in raw:
        try:
            line = json.loads(text)
        except ValueError:
            continue
        if isinstance(line, dict) and is_number(line.get("time")) and at - line["time"] < STRIP_HOURS * 3600:
            lines.append({"time": line["time"], "mark": clean(line.get("mark"), 1) or "~",
                          "text": clean(line.get("text")), "why": clean(line.get("why")),
                          "chat": clean(line.get("chat"), cell_limit=NAME_CELLS) or None})
    return lines


# the listing

def listing_rows():
    """The newest listing rows, or [] when none was kept and the chat listing failed."""
    copies = [read(files.state_path(LISTING)), read(files.state_path(sessions.LISTING))]
    copies = [copy for copy in copies if isinstance(copy, dict) and isinstance(copy.get("rows"), list)
              and is_number(copy.get("time"))]
    if copies:
        newest = max(copies, key=lambda copy: copy["time"])
        return [row for row in newest["rows"] if isinstance(row, dict)], newest["time"]
    try:
        rows = sessions.listing()
    except (OSError, ValueError):  # a broken listing cache
        rows = None
    return [row for row in rows or [] if isinstance(row, dict)], time.time()


def keep_listing(rows):
    write(files.state_path(LISTING), {"time": time.time(), "rows": rows})


# tasks

def ledger(session):
    """The chat's tasks. A task with no text is dropped, and each text is one plain line."""
    path = ledger_path(session)
    data = read(path) if path else None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    found = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, dict) or clean(task.get("text")) == "":
            continue
        found.append({"text": clean(task["text"]),
                      "state": task.get("state") if task.get("state") in ("queued", "working", "done") else "queued",
                      "time": task["time"] if is_number(task.get("time")) else now(),
                      "from": clean(task.get("from"), 20) or "hub"})
    return found


def write_ledger(session, tasks):
    write(ledger_path(session), {"session": safe(session), "time": now(), "tasks": tasks})


def ordered(tasks):
    """Done first, then the working task, then the queue: the chat's line from past to next."""
    order = {"done": 0, "working": 1, "queued": 2}
    return sorted(tasks, key=lambda task: order.get(task.get("state"), 3))


def prune_ledgers(cards, at):
    """Remove the ledger of a chat that left the board more than a week ago."""
    for session, data in read_folder(TASKS):
        if session not in cards and is_number(data.get("time")) and at - data["time"] > LEDGER_DAYS * 86400:
            try:
                os.unlink(ledger_path(session))
            except FileNotFoundError:
                pass


# the model

def remove_card(session):
    path = card_path(session) if isinstance(session, str) else None
    try:
        if path:
            os.unlink(path)
    except FileNotFoundError:
        pass


def card_for_row(row, here):
    session = clean(row.get("sessionId"), 80)
    where = clean(row.get("machine"), cell_limit=NAME_CELLS) or here
    state = LISTING_STATES.get(clean(row.get("status"), 20).lower(), "idle")
    if not is_pid(row.get("pid")) or (where == here and not sessions.pid_runs(row["pid"])):
        state = "offline"
    return {"session": session, "name": clean(row.get("name"), cell_limit=NAME_CELLS) or session or "chat",
            "machine": where, "state": state, "since": None, "question": None, "from": "listing"}


def card_from_file(session, card, here):
    """A card from its state file, with every field checked, since the file may be broken or old."""
    if isinstance(card.get("session"), str) and safe(card["session"]):
        session = card["session"]
    question = clean(card.get("question")) if isinstance(card.get("question"), str) else ""
    return {"session": session, "name": clean(card.get("name"), cell_limit=NAME_CELLS) or session,
            "machine": clean(card.get("machine"), cell_limit=NAME_CELLS) or here,
            "state": card.get("state") if card.get("state") in STATES else "idle",
            "since": card["since"] if is_number(card.get("since")) else None,
            "question": question or None, "cwd": card.get("cwd") if isinstance(card.get("cwd"), str) else None}


def load(at=None, notice=True):
    """The board as data. With notice, a chat that just went offline gets its strip line once."""
    at = now() if at is None else at
    here = machine()
    rows, listing_time = listing_rows()
    by_session = {clean(row.get("sessionId"), 80): row for row in rows
                  if row.get("kind", "interactive") == "interactive" and clean(row.get("sessionId"), 80)}
    cards = {}
    for file_session, card in read_folder(CHATS):
        card = card_from_file(file_session, card, here)
        session = card["session"]
        record = record_of(session)
        row = by_session.get(session)
        if record is not None:
            alive = is_pid(record.get("pid")) and sessions.is_alive(record)
        elif row is not None:
            alive = card_for_row(row, here)["state"] != "offline"
        else:
            alive = True  # hub can not tell, so the card keeps its own state
        if card.get("state") == "offline" and (
                (record is None and row is None) or
                (card.get("since") is not None and at - card["since"] >= OFFLINE_HOURS * 3600)):
            remove_card(file_session)
            continue
        if not alive and card.get("state") != "offline":
            card = dict(card, state="offline", since=at, question=None)
            if card_path(session):
                write(card_path(session), card)
            if notice:
                add_strip("◌", f"{card.get('name')} went offline", chat=card.get("name"), at=at)
        cards[session] = dict(card, **{"from": "hook"})
    for session, row in by_session.items():
        if session and session not in cards:
            cards[session] = card_for_row(row, here)
    prune_ledgers(cards, at)
    for session, card in cards.items():
        tasks = [] if card["machine"] != here else ordered(ledger(session))
        if card["state"] == "needs_you":
            tasks = [dict(task, state="needs_you") if task.get("state") == "working" else task for task in tasks]
        card["tasks"] = tasks
        card["age"] = None if card.get("since") is None else at - card["since"]
    ordered_cards = sorted(cards.values(), key=lambda card: (
        STATES.index(card["state"]), card["age"] if card["age"] is not None else float("inf"), card["name"]))
    counters = {state: sum(1 for card in ordered_cards if card["state"] == state) for state in STATES}
    counters["failed"] = failed_checks()
    strip = [{"mark": "!", "text": f"{card['name']} asks", "why": now_text(card, words=False), "time": card["age"],
              "buttons": ["open"], "chat": card["name"]} for card in ordered_cards if card["state"] == "needs_you"]
    strip += [dict(line, time=at - line.get("time", at), buttons=[]) for line in reversed(kept_strip(at))]
    columns = []
    for state in TASK_STATES:
        tasks = [dict(task, chat=card["name"]) for card in ordered_cards for task in card["tasks"]
                 if task.get("state") == state and not (state == "done" and at - task.get("time", at) >
                                                         DONE_HOURS * 3600)]
        columns.append({"state": state, "tasks": [{"text": task["text"], "chat": task["chat"],
                                                   "from": task.get("from", "hub")} for task in tasks]})
    for card in ordered_cards:
        card["shown_tasks"] = [task for task in card["tasks"]
                               if not (task.get("state") == "done" and at - task.get("time", at) > DONE_HOURS * 3600)]
    return {"time": at, "machine": here, "cards": ordered_cards, "counters": counters, "strip": strip[:STRIP_LINES],
            "columns": columns, "listing_age": max(0, time.time() - listing_time)}


def failed_checks():
    """The failed checks of the doctor's last run, or 0 when it never ran or its file is broken."""
    last = read(files.state_path(DOCTOR_LAST))
    failed = last.get("failed") if isinstance(last, dict) else 0
    return failed if isinstance(failed, int) and not isinstance(failed, bool) and failed > 0 else 0


def own_session():
    """The session of the chat this process runs in, from its parent processes, or None.

    The mod and a chat's commands run as children of the chat's process, which
    hub's session record names.
    """
    records = [record for _, record in read_folder(sessions.FOLDER)
               if isinstance(record.get("session"), str) and is_pid(record.get("pid"))]
    pid = os.getppid()
    for _ in range(32):
        for record in records:
            if record["pid"] == pid and sessions.alive(pid, record.get("start")):
                return record["session"]
        info = sessions.process_info(pid)
        if not info or info["ppid"] <= 1:
            return None
        pid = info["ppid"]
    return None


# the band

def count_text(count):
    return str(count) if count < 100 else "99+"


def band_zone(data, viewer=None):
    """The band's chats zone, for the chat `viewer`, which never sees itself.

    The mod shows "full" when it fits in its share of the line, else "folded",
    where a chat that needs the user keeps its name and the rest fold to
    counters. The question is the first other chat that needs the user.
    Returns None when no other chat is on the board.
    """
    others = [card for card in data["cards"] if card["session"] != viewer]
    if not others:
        return None
    table = icons()
    entries = []
    for card in others:
        entry = f"{glyph(card['state'], table=table)} {card['name']}"
        tasks = card.get("shown_tasks", [])
        if card["state"] == "busy" and tasks:
            entry += f" {sum(1 for task in tasks if task.get('state') == 'done')}/{len(tasks)}"
        entries.append(entry)
    folded = [f"{glyph('needs_you', table=table)} {card['name']}" for card in others if card["state"] == "needs_you"]
    for state in ("busy", "idle", "offline"):
        count = sum(1 for card in others if card["state"] == state)
        if count:
            folded.append(f"{glyph(state, table=table)}{count_text(count)}")
    full, folded = "  ".join(entries), " ".join(folded)
    asking = next((card for card in others if card["state"] == "needs_you"), None)
    question = None if asking is None else {
        "chat": asking["name"], "session": asking["session"],
        "line": f"{glyph('needs_you', table=table)} {asking['name']} asks  {now_text(asking, words=False)}"}
    return {"full": full, "full_cells": cells(full), "folded": folded, "folded_cells": cells(folded),
            "question": question}


# words and glyphs

def glyph(key, plain=False, table=None):
    icon = (table or icons())[ICON_OF.get(key, key)]
    return icon["plain"] if plain else icon["glyph"]


def counter_line(counters, plain=False):
    table = icons()
    keys = ["needs_you", "failed", "busy", "idle", "offline"]
    shown = [(key, counters.get(key, 0)) for key in keys if counters.get(key, 0)]
    if plain:
        return ", ".join(f"{WORDS.get(key, glyph(key, True, table))} {count}" for key, count in shown)
    return " ".join(f"{glyph(key, table=table)}{count_text(count)}" for key, count in shown)


def now_text(card, words=True):
    """The now row: the waiting question, else the working task, else the last one."""
    tasks = card.get("tasks", [])
    if card["state"] == "needs_you":
        question = card.get("question") or "for your yes"
        question = question[:1].lower() + question[1:]
        return ("asks " if words else "") + question
    if card["state"] == "busy":
        working = next((task["text"] for task in tasks if task.get("state") == "working"), None)
        return "now " + (working or "working")
    if card["state"] == "idle":
        last = next((task["text"] for task in reversed(tasks) if task.get("state") in ("done", "working")), None)
        return "last " + last if last else "idle"
    return "offline"


def task_row(card, plain=False):
    if card["machine"] != machine():
        return REMOTE_TASKS
    tasks = card.get("shown_tasks", card.get("tasks", []))
    if not tasks:
        return "no tasks yet"
    table = icons()
    return "  ".join(f"{glyph(task.get('state'), plain, table)} {task['text']}" for task in tasks)


def render(data, plain=False, show_cursor_line=True, every=False):
    """/hub board as text: header, strip, title line, cards and next line.

    The cards fill at most BODY_LINES lines. One more line then counts the rest
    and names the command that shows them. every=True shows every card.
    """
    table = icons()
    dot = " - " if plain else " · "
    clock = "for " if plain else table["time"]["glyph"] + " "
    lines = [dot.join(part for part in ("hub", data["machine"], counter_line(data["counters"], plain)) if part)]
    for index, line in enumerate(data["strip"]):
        mark = {"!": "needs_you", "◌": "offline"}.get(line["mark"], line["mark"])
        mark = WORDS.get(mark, mark) if plain else line["mark"]
        time_text = f"  {clock}{since(line['time'])}" if line.get("time") is not None else ""
        lines.append(f"{'CHANGES  ' if index == 0 else '         '}{mark} {line['text']}  {line['why']}{time_text}"
                     .rstrip())
    cards = data["cards"]
    counts = data["counters"]
    summary = ", ".join(f"{counts[state]} {WORDS[state]}" for state in ("needs_you", "busy") if counts[state])
    lines.append(f"chats{dot}{len(cards)} chats" + (f", {summary}" if summary else ""))
    if not cards:
        lines.append(EMPTY)
    shown = len(cards) if every else BODY_LINES // CARD_LINES
    lefts = []
    for card in cards[:shown]:
        name = card["name"] + (f"{dot}{card['machine']}" if card["machine"] != data["machine"] else "")
        lefts.append(f"{glyph(card['state'], plain, table)} {name}")
    width = max([cells(left) for left in lefts] + [0]) + 2
    for card, left in zip(cards, lefts):
        title = f"{pad(left, width)}{WORDS[card['state']]:<11}"
        if card.get("age") is not None:
            title += f"{clock}{since(card['age'])}"
        lines += [title.rstrip(), "  " + now_text(card), "  " + task_row(card, plain)]
    if len(cards) > shown:
        lines.append(f"{len(cards) - shown} more chats. /hub board --all shows every chat.")
    if show_cursor_line:
        lines.append(NOT_CURSOR)
    lines.append(f"next: /hub board tasks{dot}/hub board show <chat>")
    return "\n".join(lines) + "\n"


def render_tasks(data, plain=False):
    table = icons()
    lines = []
    for column in data["columns"]:
        lines.append(f"{glyph(column['state'], plain, table)} {WORDS[column['state']]} {len(column['tasks'])}")
        lines += [f"  {task['text']:<20}{task['chat']}" for task in column["tasks"]]
    return "\n".join(lines) + "\n"


def render_show(data, name, plain=False):
    card = next((card for card in data["cards"] if sessions.same_name(card["name"], name)
                 or card["session"] == name), None)
    if card is None:
        return None
    table = icons()
    lines = [f"{glyph(card['state'], plain, table)} {card['name']}  {WORDS[card['state']]}", "  " + now_text(card)]
    lines += [f"  {glyph(task.get('state'), plain, table)} {task['text']}" for task in card["tasks"]]
    return "\n".join(lines) + "\n"


def plain_wanted(argv_plain=False):
    if argv_plain or os.environ.get("TERM") == "dumb":
        return True
    locale = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or ""
    return bool(locale) and "utf-8" not in locale.lower() and "utf8" not in locale.lower()
