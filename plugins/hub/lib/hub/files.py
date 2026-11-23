"""Where hub keeps its files, and how it writes them.

Every write is atomic, a temp file and then a rename. A write to a link goes
to the link's target, so the link stays. Settings writes take one lock file.
"""
import contextlib
import fcntl
import json
import os
import pwd
import tempfile
import time
import unicodedata

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PARTS_DIR = os.path.join(PLUGIN_ROOT, "parts")
# hub's internal step, which the switch skill runs. It sits outside bin/, so no PATH holds it.
STEP = os.path.join(PLUGIN_ROOT, "lib", "hub_switch.py")
# The settings folder's name, under ~/.config. It sits in its own file, which pre_check.sh reads too.
CONFIG_NAME_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_name")
with open(CONFIG_NAME_FILE, encoding="utf-8") as _name_file:
    CONFIG_NAME = _name_file.read().strip()
# The folder name the settings had before they moved to config_dir(), and the flags renamed with the move.
OLD_CONFIG_NAME = "hubctl"
RENAMED_FLAGS = {"self_protection.hubctl": "self_protection.lowering"}
# Flags that went with the terminal link, which put hubctl on the PATH.
DROPPED_FLAGS = ("core.terminal_link",)


def home():
    return os.path.expanduser("~")


def in_tool_call():
    """Whether this process is a command the model runs in a tool call.

    Claude Code starts a tool's command with AI_AGENT ending in "_agent" and a
    hook with AI_AGENT ending in "_harness". Cursor's marker is CURSOR_AGENT=1.
    The markers are a hint for hub's checks, not a security boundary.
    """
    return os.environ.get("AI_AGENT", "").endswith("_agent") or os.environ.get("CURSOR_AGENT") == "1"


def config_base():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")


def config_dir():
    folder = os.path.join(config_base(), CONFIG_NAME)
    move_old_config(old_config_dir(), folder)
    return folder


def old_config_dir():
    """The settings folder's old name. self_protection guards it too, since hub moves what it finds there."""
    return os.path.join(config_base(), OLD_CONFIG_NAME)


def move_old_config(old, new):
    """Move the settings folder from its old name on first use, with every choice in it.

    A renamed flag keeps its value under the new name. When both folders
    exist, the new one wins and the old one stays as it is.
    """
    if os.path.lexists(new) or not os.path.isdir(old) or os.path.islink(old):
        return
    try:
        os.rename(old, new)
    except OSError:
        return
    path = os.path.join(new, "hub.json")
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return  # hub reports a file it can not read when it loads the settings
    flags = data.get("flags") if isinstance(data, dict) else None
    if not isinstance(flags, dict) or not any(name in flags for name in (*RENAMED_FLAGS, *DROPPED_FLAGS)):
        return
    for name in DROPPED_FLAGS:
        flags.pop(name, None)
    for old_name, new_name in RENAMED_FLAGS.items():
        if old_name in flags:
            flags.setdefault(new_name, flags.pop(old_name))
    write_json(path, data)


def step_command():
    """The shell words that run the internal step, for a text the model reads."""
    import shlex
    return f"python3 -I {shlex.quote(STEP)}"


# A text the model reads writes {{step}} where it runs the step, since its path differs on each machine.
STEP_PLACEHOLDER = "{{step}}"


def fill_step(text):
    return text.replace(STEP_PLACEHOLDER, step_command())


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


LOCK_TRY_GAP = 0.05  # seconds between tries while a lock with a wait is held


def take_lock(handle, wait):
    """Take the lock, waiting for it when wait is None, else for at most wait seconds, then raise TimeoutError."""
    if wait is None:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{handle.name} stayed locked for {wait} s") from None
            time.sleep(LOCK_TRY_GAP)


@contextlib.contextmanager
def locked(name="settings.lock", wait=None):
    """Hold one lock file in the state folder for the length of the block.

    With wait, a lock another process holds longer than wait seconds raises TimeoutError.
    """
    path = state_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        take_lock(handle, wait)
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


# Temp folders


def account_home():
    """The account's home from the password database, which no variable of the process changes."""
    return pwd.getpwuid(os.getuid()).pw_dir


def temp_roots():
    """The temp folders of this process, as real paths: /tmp and the one Python picks, such as $TMPDIR."""
    return sorted({os.path.realpath("/tmp"), os.path.realpath(tempfile.gettempdir())})


def kept_folders():
    """The folders a temp folder must stay away from, as real paths: both homes, and hub's settings, state and code
    folders and the Claude Code and Cursor folders, from this process's variables and from the account's home."""
    homes = [home(), account_home()]
    from_variables = [os.path.join(config_base(), CONFIG_NAME), old_config_dir(), state_dir(), PLUGIN_ROOT,
                      os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home(), ".claude")]
    from_homes = [os.path.join(folder, *path) for folder in homes
                  for path in ((".config", CONFIG_NAME), (".config", OLD_CONFIG_NAME),
                               (".local", "state", "hubctl"), (".claude",), (".cursor",))]
    return sorted({os.path.realpath(folder) for folder in homes + from_variables + from_homes})


def folded(path):
    """A path compared the way a case-insensitive volume compares it."""
    return unicodedata.normalize("NFC", path).casefold().rstrip(os.sep) or os.sep


def is_inside(path, folder):
    """Whether path sits below folder, not at it. Both are folded real paths."""
    return path.startswith(folder.rstrip(os.sep) + os.sep)


def is_temp_folder(path, roots, kept):
    """Whether a real path sits below a temp root and is not equal to, below or above any kept folder."""
    path = folded(path)
    kept = [folded(folder) for folder in kept]
    below_a_root = any(is_inside(path, folded(root)) for root in roots)
    near_a_kept_folder = any(path == folder or is_inside(path, folder) or is_inside(folder, path) for folder in kept)
    return below_a_root and not near_a_kept_folder


def ancestors(path):
    """The path and every folder above it."""
    parent = os.path.dirname(path)
    return [path] if parent == path else [path, *ancestors(parent)]


def same_folder(one, other):
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def in_temp_folder(path):
    """Whether a folder is a temp folder, read from the real filesystem: is_temp_folder on its real path, and no
    folder at or above it is the same folder as a kept one under another spelling."""
    real = os.path.realpath(path)
    kept = kept_folders()
    return is_temp_folder(real, temp_roots(), kept) and not any(
        same_folder(above, folder) for above in ancestors(real) for folder in kept if os.path.exists(folder))
