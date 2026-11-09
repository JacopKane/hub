"""Which chats run, what each is named, and whether a chat is still alive.

It uses only documented signals. The session id and `session_title` come
from hook input (SessionStart and UserPromptSubmit carry the title once one
is set). A chat's inbox address is CLAUDE_CODE_MESSAGING_SOCKET, which Claude
Code exports to its hooks. The list of chats is `claude agents --json`,
which only reads and takes about 0.1 s, cached here for a few seconds.

Parts that read chats call note() for SessionStart, UserPromptSubmit and
SessionEnd. At SessionStart it writes one record per chat in
<state>/sessions/<session id>.json, with the pid of the chat's own process
(the hook's parent, past any shell), that process's start time, its tty and
its arguments. SessionEnd removes it. alive(pid, start) is the one liveness
test, so a reused pid never counts as alive.

A listing that is slow or fails returns None and writes one line to the
doctor log, and callers then go on without it.
"""
import contextlib
import fcntl
import json
import os
import re
import shutil
import struct
import subprocess
import time
import unicodedata

from . import files

FOLDER = "sessions"
LISTING = os.path.join(FOLDER, "listing.json")
LISTING_SECONDS = 3  # how long one listing answers every hook
LISTING_TIMEOUT = 1.5
DOCTOR_LOG = "doctor.log"
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}
SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


# processes

def _ps(field, pid):
    try:
        process = subprocess.run(["ps", "-o", field + "=", "-p", str(pid)], capture_output=True, text=True,
                                 env=dict(os.environ, LC_ALL="C", TZ="UTC"), timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return " ".join(process.stdout.split())


def _darwin_start(pid):
    """The start time of pid to the microsecond on macOS, from sysctl, or "".

    `ps -o lstart` counts whole seconds, so a pid reused in the same second
    would pass for the old process. kinfo_proc begins with the start timeval.
    """
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
        buffer = ctypes.create_string_buffer(1024)
        size = ctypes.c_size_t(len(buffer))
        if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0 or size.value < 16:
            return ""
        seconds, microseconds = struct.unpack_from("qi", buffer.raw, 0)
    except (OSError, AttributeError, ValueError, struct.error):
        return ""
    return f"us:{seconds}.{microseconds:06d}" if seconds > 0 else ""


def process_info(pid):
    """{ppid, start, tty, name, args} of a running process, or None."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    proc = f"/proc/{pid}"
    if os.path.isdir("/proc/self"):
        try:
            with open(proc + "/stat") as handle:
                stat = handle.read()
            with open(proc + "/cmdline", "rb") as handle:
                args = [part.decode(errors="replace") for part in handle.read().split(b"\0") if part]
        except OSError:
            return None
        name = stat[stat.index("(") + 1:stat.rindex(")")]
        fields = stat[stat.rindex(")") + 2:].split()
        tty = int(fields[4])
        return {"ppid": int(fields[1]), "start": "ticks:" + fields[19], "name": name,
                "tty": f"{tty >> 8 & 0xfff}:{(tty & 0xff) | (tty >> 12 & 0xfff00)}" if tty else "",
                "args": args}
    start = (_darwin_start(pid) if os.uname().sysname == "Darwin" else "") or _ps("lstart", pid)
    if not start:
        return None
    ppid = _ps("ppid", pid)
    tty = _ps("tty", pid)
    return {"ppid": int(ppid) if ppid.isdigit() else 0, "start": start, "name": _ps("comm", pid),
            "tty": "" if tty in ("", "??", "?") else tty, "args": _ps("args", pid).split(" ")}


def alive(pid, start):
    """Whether pid runs and started at start, so a reused pid is not alive."""
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    info = process_info(pid)
    return bool(info) and info["start"] == start


def chat_process():
    """(pid, info) of the chat's own process: the hook's parent, past any shell."""
    pid = os.getppid()
    for _ in range(4):
        info = process_info(pid)
        if info is None:
            return None, None
        name = os.path.basename(info["name"]).lstrip("-")
        if name in SHELLS and info["ppid"] > 1:
            pid = info["ppid"]
            continue
        return pid, info
    return None, None


# records

def record_path(session):
    session = SAFE_ID.sub("", str(session or ""))
    return files.state_path(os.path.join(FOLDER, session + ".json")) if session else None


def record(session):
    path = record_path(session)
    return files.read_json(path) if path else None


def records():
    folder = files.state_path(FOLDER)
    found = []
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return found
    for name in names:
        if name.endswith(".json") and name != os.path.basename(LISTING):
            data = files.read_json(os.path.join(folder, name))
            if isinstance(data, dict) and data.get("session"):
                found.append(data)
    return found


def note(event, hook_input):
    """Keep this chat's record. Returns the record, or None inside a subagent or without a session."""
    session = hook_input.get("session_id")
    path = record_path(session)
    if path is None or hook_input.get("agent_id"):
        return None
    if event == "SessionEnd":
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return None
    data = files.read_json(path)
    if event == "SessionStart" or data is None:
        pid, info = chat_process()
        data = dict(data or {}, session=session, pid=pid, start=(info or {}).get("start"),
                    tty=(info or {}).get("tty", ""), args=(info or {}).get("args", []),
                    started=(data or {}).get("started") or time.time())
        address = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
        data["address"] = address or data.get("address")
    title = hook_input.get("session_title")
    if isinstance(title, str) and title.strip():
        data["title"] = title.strip()
    data["cwd"] = hook_input.get("cwd") or data.get("cwd")
    files.write_json(path, data)
    return data


def update(session, **values):
    """Set values in a chat's own record, when it has one."""
    path = record_path(session)
    data = files.read_json(path) if path else None
    if data is None:
        return None
    data.update(values)
    files.write_json(path, data)
    return data


def is_alive(data):
    return bool(data) and alive(data.get("pid"), data.get("start"))


# A restart marks a chat from just before its stop until it is back. A post that lands during /exit is lost, so the
# queue posts nothing to a marked chat. posting_to() holds a shared lock around each post. mark_restarting() takes
# the same lock alone once, so when it returns, no post is under way.

RESTARTING = os.path.join(FOLDER, "restarting")
RESTARTING_SECONDS = 30 * 60  # a mark this old belongs to a run that went wrong, so it no longer holds posts
BARRIER_SECONDS = 30


def restarting_path(session, suffix=".json"):
    session = SAFE_ID.sub("", str(session or ""))
    return files.state_path(os.path.join(RESTARTING, session + suffix)) if session else None


def mark_restarting(session, owner_pid):
    """Mark the chat as restarting, owned by owner_pid, then wait until no post to it is under way."""
    path = restarting_path(session)
    if path is None:
        return False
    info = process_info(owner_pid) or {}
    files.write_json(path, {"owner": owner_pid, "owner_start": info.get("start"), "time": time.time()})
    with open(restarting_path(session, ".lock"), "a") as handle:
        deadline = time.time() + BARRIER_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    return False
                time.sleep(0.05)
    return True


def clear_restarting(session):
    path = restarting_path(session)
    try:
        os.unlink(path)
    except (OSError, TypeError):
        pass


def restarting(session):
    """Whether a live restart run marked this chat less than RESTARTING_SECONDS ago."""
    path = restarting_path(session)
    data = files.read_json(path) if path else None
    if not isinstance(data, dict):
        return False
    try:
        age = time.time() - float(data.get("time"))
    except (TypeError, ValueError):
        return False
    return 0 <= age <= RESTARTING_SECONDS and alive(data.get("owner"), data.get("owner_start"))


@contextlib.contextmanager
def posting_to(session):
    """Yields whether a post to the chat may go now. The shared lock keeps a restart's mark from landing mid-post."""
    lock = restarting_path(session, ".lock")
    if lock is None:
        yield True
        return
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    with open(lock, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        yield not restarting(session)


# the listing

def doctor_line(text):
    files.append_text(files.state_path(DOCTOR_LOG), f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {text}\n")


def run_listing():
    """Run `claude agents --json` once, uncached. Returns (rows, None), or (None, why it failed)."""
    program = shutil.which("claude")
    if program is None:
        return None, "claude is not on PATH"
    try:
        process = subprocess.run([program, "agents", "--json"], capture_output=True, text=True,
                                 timeout=LISTING_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, f"it took longer than {LISTING_TIMEOUT} s"
    except OSError as error:
        return None, f"it could not run ({type(error).__name__})"
    if process.returncode != 0:
        return None, f"it exited with {process.returncode}"
    try:
        rows = json.loads(process.stdout)
    except ValueError as error:
        return None, f"it could not run ({type(error).__name__})"
    if not isinstance(rows, list):
        return None, "it printed no list"
    return rows, None


def listing(max_age=LISTING_SECONDS):
    """The rows of `claude agents --json`, or None when the call failed.

    A cached listing answers while it is younger than max_age seconds, so
    max_age=0 always asks again, as a restart does before each stop.
    """
    path = files.state_path(LISTING)
    cached = files.read_json(path)
    if isinstance(cached, dict) and time.time() - cached.get("time", 0) < max_age:
        return cached.get("rows")
    rows, problem = run_listing()
    if problem:
        doctor_line(f"sessions: the chat listing (claude agents --json) failed, {problem}, so hub went on "
                    f"without it.")
    files.write_json(path, {"time": time.time(), "rows": rows})
    return rows


def row_for(rows, session):
    return next((row for row in rows or [] if isinstance(row, dict) and row.get("sessionId") == session), None)


def name_of(hook_input):
    """This chat's name: its title from hook input or its record, else its row in the listing."""
    title = hook_input.get("session_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    session = hook_input.get("session_id")
    data = record(session)
    if data and data.get("title"):
        return data["title"]
    row = row_for(listing(), session)
    return row.get("name") if row else None


def normalized(name):
    """Claude Code's own name match: NFKC, no control or format characters, trimmed, lower case, spaces as hyphens."""
    name = unicodedata.normalize("NFKC", name)
    name = "".join(character for character in name
                   if character.isspace() or unicodedata.category(character) not in ("Cc", "Cf", "Cs"))
    return re.sub(r"\s+", "-", name.strip().lower())


def same_name(first, second):
    return isinstance(first, str) and isinstance(second, str) and bool(first.strip()) and \
        normalized(first) == normalized(second)


def pid_runs(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    return True


def live_chats(name, rows, exclude=None):
    """Every live interactive chat named name, as {session, pid, name, address}.

    A row counts while it has a pid. When hub holds a record of the chat, its
    pid must be alive with the recorded start time; without one, the row's
    pid must still run. The address is "uds:<socket>" when the record names a
    socket that exists, else None.
    """
    found = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("kind", "interactive") != "interactive":
            continue
        session = row.get("sessionId")
        if session == exclude or not same_name(row.get("name"), name) or not row.get("pid"):
            continue
        data = record(session)
        if (data is not None and not is_alive(data)) or (data is None and not pid_runs(row.get("pid"))):
            continue
        address = data.get("address") if data else None
        found.append({"session": session, "pid": row.get("pid"), "name": row.get("name"),
                      "address": "uds:" + address if isinstance(address, str) and os.path.exists(address) else None})
    return found


def find_live(name, rows, exclude=None):
    """The first live chat named name, with its address, or None.

    The address is the recorded inbox when its socket exists, else the name
    itself, which reaches one chat of that name.
    """
    for chat in live_chats(name, rows, exclude):
        return dict(chat, address=chat["address"] or chat["name"])
    return None


def chat_at(address):
    """The title of the chat whose record holds this inbox address, or None."""
    for data in records():
        if data.get("address") and "uds:" + data["address"] == address:
            return data.get("title")
    return None
