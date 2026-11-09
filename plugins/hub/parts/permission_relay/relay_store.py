"""The permission relay's records, shared by its hook handler and its command.

A record is one file <state>/relay/waiting/<id>.json for one permission
prompt or auto mode block in one chat. It keeps a cleaned one-line preview
and a hash of the tool input, never the input itself. The relay folder and
its waiting folder are 0700 and every file in them is 0600.

Each record is signed with an HMAC key that only hub keeps, in
<state>/relay/key. A file that is not a regular file, whose name is not its
id, or whose signature does not match is removed and never shown, so a
record a chat's own command writes can not reach the coordinator. A record
whose chat ended, or whose chat is now another process, is removed too.

All changes happen under one lock in the relay folder. At most 20 records
are kept for one chat and 200 in all, and the oldest go first.
"""
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
import unicodedata

from hub import files, sessions

PART = "permission_relay"
FOLDER = "relay"
WAITING = os.path.join(FOLDER, "waiting")
LOCK = os.path.join(FOLDER, "lock")
KEY = os.path.join(FOLDER, "key")
OWNER = os.path.join(FOLDER, "owner.json")
MARKS = os.path.join(FOLDER, "marks.json")
PROMPT, BLOCK = "prompt", "block"
PER_CHAT, IN_ALL = 20, 200
PREVIEW_LIMIT = 2000
BLOCK_SECONDS = 3600
PROMPT_SECONDS = 12 * 3600
ID = re.compile(r"[0-9a-f]{16}\Z")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI, such as a colour or a cleared screen
                  r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC, such as a window title, to BEL or ST
                  r"|\x1b[ -/]*[0-~]?")  # any other escape
SPACES = re.compile(r"\s+")


def folder():
    return files.state_path(FOLDER)


def waiting_folder():
    return files.state_path(WAITING)


def private_folders():
    """Make the relay folder and its waiting folder 0700, replacing a link or a file in their place."""
    for path in (folder(), waiting_folder()):
        if os.path.islink(path) or (os.path.lexists(path) and not os.path.isdir(path)):
            os.unlink(path)
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)


@contextlib.contextmanager
def store():
    """Hold the relay's lock, with its folders made private first."""
    private_folders()
    with files.locked(LOCK):
        yield


def seconds(name, default):
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def block_seconds():
    return seconds("HUB_RELAY_BLOCK_SECONDS", BLOCK_SECONDS)


def prompt_seconds():
    return seconds("HUB_RELAY_PROMPT_SECONDS", PROMPT_SECONDS)


# text

def clean(text, limit=PREVIEW_LIMIT):
    """One line of printable text, at most limit characters, cut with three dots.

    Escape codes go, tabs, line breaks and every other space become one
    space, control and format characters (such as bidi marks and zero-width
    characters) are dropped, and a double quote becomes a single one, so the
    text can not end a quoted part of a dialog.
    """
    text = ANSI.sub("", str(text))
    kept = []
    for character in text:
        category = unicodedata.category(character)
        if character.isspace() or category in ("Zl", "Zp", "Zs"):
            kept.append(" ")
        elif category in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            kept.append("'" if character == '"' else character)
    text = SPACES.sub(" ", "".join(kept)).strip()
    if limit <= 0:
        return ""
    if len(text) > limit:
        text = text[:max(limit - 3, 0)].rstrip() + "..."
    return text[:limit]


def preview(tool_input):
    """The part of a tool's input a person needs to see: the command, the path, the address, or all of it."""
    if not isinstance(tool_input, dict):
        return clean(json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str))
    for name in ("command", "file_path", "notebook_path", "url"):
        if isinstance(tool_input.get(name), str) and tool_input[name].strip():
            return clean(tool_input[name])
    return clean(json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str))


def input_hash(tool_input):
    raw = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


# signing

def key():
    """The relay's own signing key, made once. A link or a short file in its place gets a new key."""
    path = files.state_path(KEY)
    try:
        info = os.lstat(path)
        if stat.S_ISREG(info.st_mode):
            with open(path, "rb") as handle:
                data = handle.read()
            if len(data) == 32:
                return data
        os.unlink(path)
    except FileNotFoundError:
        pass
    data = secrets.token_bytes(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return data


def signature(record, secret):
    body = {name: value for name, value in record.items() if name != "sig"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    return hmac.new(secret, raw, hashlib.sha256).hexdigest()


def record_path(record_id):
    return os.path.join(waiting_folder(), record_id + ".json")


def read_valid(name, secret):
    """The record in this file of the waiting folder, or None. Never follows a link."""
    path = os.path.join(waiting_folder(), name)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or not name.endswith(".json"):
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not ID.match(record["id"]) \
            or name != record["id"] + ".json" or not isinstance(record.get("sig"), str) \
            or not hmac.compare_digest(record["sig"], signature(record, secret)):
        return None
    return record


def remove(name):
    with contextlib.suppress(FileNotFoundError, IsADirectoryError, PermissionError):
        os.unlink(os.path.join(waiting_folder(), name))


def chat_is_back(record):
    """Whether the chat that opened the prompt still runs as the same process."""
    data = sessions.record(record.get("session"))
    return bool(data) and sessions.is_alive(data) and data.get("pid") == record.get("pid") \
        and data.get("start") == record.get("start")


def too_old(record, now):
    try:
        age = now - float(record.get("at"))
    except (TypeError, ValueError):
        return True
    limit = block_seconds() if record.get("kind") == BLOCK else prompt_seconds()
    return age > limit or age < -60


def all_records(secret, check_chats=False):
    """Every valid record, oldest first. Files that fail a check are removed. Call it under store()."""
    found = []
    now = time.time()
    for name in sorted(os.listdir(waiting_folder())):
        if name.startswith(".hub-"):
            continue  # a write that is under way
        record = read_valid(name, secret)
        if record is None or too_old(record, now) or (check_chats and not chat_is_back(record)):
            remove(name)
            continue
        found.append(record)
    found.sort(key=lambda record: (record.get("at", 0), record.get("seq", 0)))
    return found


# writing and clearing

def chat_record(hook_input):
    """hub's session record of the chat, made from this hook when the chat has none yet."""
    session = hook_input.get("session_id")
    data = sessions.record(session)
    if data is None:
        data = sessions.note("PermissionRequest", dict(hook_input, agent_id=None))
    return data or {}


def add(kind, hook_input, reason=""):
    """Keep one record for a prompt or a block. The same prompt twice is kept once."""
    session = str(hook_input.get("session_id") or "")
    if not session:
        return None
    tool_input = hook_input.get("tool_input")
    agent = str(hook_input.get("agent_id") or "")
    tool = clean(hook_input.get("tool_name") or "", 200)
    hashed = input_hash(tool_input)
    dedupe = hashlib.sha256("\0".join([session, agent, tool, hashed, kind]).encode()).hexdigest()[:16]
    chat = chat_record(hook_input)
    with store():
        secret = key()
        records = all_records(secret)
        if any(record.get("key") == dedupe for record in records):
            return None
        record = {"id": secrets.token_hex(8), "key": dedupe, "kind": kind, "session": session, "agent": agent,
                  "tool": tool, "input_hash": hashed, "preview": preview(tool_input), "pid": chat.get("pid"),
                  "start": chat.get("start"), "at": time.time(), "seq": time.monotonic_ns()}
        if kind == BLOCK:
            record["reason"] = clean(reason)
        record["sig"] = signature(record, secret)
        files.write_json(record_path(record["id"]), record)
        records.append(record)
        same_chat = [entry for entry in records if entry["session"] == session]
        for old in same_chat[:max(len(same_chat) - PER_CHAT, 0)]:
            remove(old["id"] + ".json")
            records.remove(old)
        for old in records[:max(len(records) - IN_ALL, 0)]:
            remove(old["id"] + ".json")
        return record


def clear(session, keep=lambda record: False):
    """Remove the session's records for which keep says no."""
    with store():
        for record in all_records(key()):
            if record["session"] == session and not keep(record):
                remove(record["id"] + ".json")


# the coordinator's lock and marks

def take_owner(session):
    """Whether this session is the coordinator that holds the relay, taking it when nobody live holds it.

    Call it under store(). The session must have a live hub record.
    """
    data = sessions.record(session)
    if not data or not sessions.is_alive(data):
        return False
    path = files.state_path(OWNER)
    owner = files.read_json(path) if os.path.isfile(path) and not os.path.islink(path) else None
    if isinstance(owner, dict) and owner.get("session") == session and owner.get("pid") == data.get("pid") \
            and owner.get("start") == data.get("start"):
        return True
    if isinstance(owner, dict) and sessions.alive(owner.get("pid"), owner.get("start")):
        return False
    if os.path.lexists(path) and not os.path.isfile(path) or os.path.islink(path):
        os.unlink(path)
    files.write_json(path, {"session": session, "pid": data.get("pid"), "start": data.get("start")})
    return True


def marks():
    path = files.state_path(MARKS)
    data = files.read_json(path) if os.path.isfile(path) and not os.path.islink(path) else None
    return {name: value for name, value in (data or {}).items() if ID.match(str(name))} \
        if isinstance(data, dict) else {}


def save_marks(data, records):
    ids = {record["id"] for record in records}
    path = files.state_path(MARKS)
    if os.path.islink(path):
        os.unlink(path)
    files.write_json(path, {name: value for name, value in data.items() if name in ids})
