"""Where hub keeps its files, and how it writes them.

Every write is atomic, a temp file and then a rename. A write to a link goes
to the link's target, so the link stays. Settings writes take one lock file.
"""
import contextlib
import fcntl
import json
import os
import tempfile
import unicodedata

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PARTS_DIR = os.path.join(PLUGIN_ROOT, "parts")


def home():
    return os.path.expanduser("~")


def in_tool_call():
    """Whether this process is a command the model runs in a tool call.

    Claude Code starts a tool's command with AI_AGENT ending in "_agent" and a
    hook with AI_AGENT ending in "_harness". Cursor's marker is CURSOR_AGENT=1.
    The markers are a hint for hub's checks, not a security boundary.
    """
    return os.environ.get("AI_AGENT", "").endswith("_agent") or os.environ.get("CURSOR_AGENT") == "1"


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")
    return os.path.join(base, "hubctl")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(home(), ".local", "state")
    return os.path.join(base, "hubctl")


def state_path(name):
    return os.path.join(state_dir(), name)


def user_settings_path():
    return os.path.join(config_dir(), "hub.json")


def profile_path():
    return os.path.join(config_dir(), "profile.json")


def read_json(path, missing=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return missing


def one_line(text):
    """Text as one printable line, for a notice or a strip line a file may have forged.

    Line breaks, tabs and other spaces become one space each. Control and
    format characters, such as escape codes and direction marks, are dropped.
    """
    kept = (" " if char.isspace() else char for char in str(text)
            if char.isspace() or not unicodedata.category(char).startswith("C"))
    return " ".join("".join(kept).split())


def read_text(path):
    """A file's text, or None when it is missing."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def write_text(path, text):
    target = os.path.realpath(path) if os.path.islink(path) else path
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".hub-", dir=folder)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        if os.path.exists(target):
            os.chmod(temp, os.stat(target).st_mode & 0o777)
        os.replace(temp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def append_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(text)


def append_line(path, data):
    append_text(path, json.dumps(data, sort_keys=True, ensure_ascii=False) + "\n")


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        return []


@contextlib.contextmanager
def locked(name="settings.lock"):
    """Hold one lock file in the state folder for the length of the block."""
    path = state_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def stamp(path):
    """A file's change stamp, or None when it is missing."""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    return [info.st_mtime_ns, info.st_size]
