"""Which terminal a chat runs in, where its tab or pane is, and typing into it.

    detect(env)                 "tmux", "iterm" or "plain"
    locate(record)              the chat's tmux pane or iTerm session, or None
    type_into(location, text)   types text and Enter there (enter=False: no Enter)
    press(location, key)        presses enter, esc or backspace there
    screen(location)            the text on that pane or tab now, or None
    focus(location)             brings that pane or tab to the front
    processes_on(tty)           [(pid, words)] of every process in that terminal now

tmux inside iTerm counts as tmux, because the pane is what a restart reopens.
locate() matches the tty in a chat's session record (sessions.note writes
it) against `tmux list-panes -a` and then the iTerm sessions, read through
osascript. A plain terminal has no location, so a restart holds those chats
and names them. A part never calls tmux or osascript itself.
"""
import os
import shutil
import subprocess

TIMEOUT = 5
ITERM_TTYS = ('tell application "iTerm2" to repeat with each_window in windows\n'
              ' repeat with each_tab in tabs of each_window\n'
              '  repeat with each_session in sessions of each_tab\n'
              '   log (id of each_session) & tab & (tty of each_session)\n'
              '  end repeat\n end repeat\nend repeat')


def detect(env=None):
    env = os.environ if env is None else env
    if env.get("TMUX"):
        return "tmux"
    if env.get("TERM_PROGRAM") == "iTerm.app":
        return "iterm"
    return "plain"


def _run(args, stdin=None):
    program = shutil.which(args[0])
    if program is None:
        return None
    try:
        return subprocess.run([program, *args[1:]], capture_output=True, text=True, timeout=TIMEOUT, input=stdin,
                              stdin=None if stdin is not None else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _tmux(server, *args):
    return _run(["tmux", *(["-L", server] if server else []), *args])


def _device(tty):
    """(name, major:minor) of a tty as a device path, a ps name such as ttys003, or a /proc major:minor."""
    tty = (tty or "").strip()
    if not tty:
        return None, None
    if ":" in tty and tty.replace(":", "").isdigit():
        return None, tty
    path = tty if tty.startswith("/dev/") else "/dev/" + tty
    try:
        device = os.stat(path).st_rdev
        number = f"{os.major(device)}:{os.minor(device)}"
    except OSError:
        number = None
    return os.path.basename(path), number


def same_tty(first, second):
    first_name, first_number = _device(first)
    second_name, second_number = _device(second)
    if first_name and first_name == second_name:
        return True
    return bool(first_number) and first_number == second_number


def _linux_tty(tty_nr):
    """The major:minor of the tty_nr field in /proc/<pid>/stat, or None for no terminal."""
    if not tty_nr:
        return None
    return f"{(tty_nr >> 8) & 0xfff}:{(tty_nr & 0xff) | ((tty_nr >> 12) & 0xfff00)}"


def _processes_from_proc(tty):
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8", errors="replace") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
            with open(f"/proc/{name}/cmdline", "rb") as handle:
                words = [word.decode("utf-8", "replace") for word in handle.read().split(b"\0") if word]
        except (OSError, IndexError):
            continue
        number = _linux_tty(int(fields[4])) if len(fields) > 4 and fields[4].lstrip("-").isdigit() else None
        if number and words and same_tty(number, tty):
            found.append((int(name), words))
    return found


def processes_on(tty):
    """[(pid, words)] of every process whose terminal is tty, read from /proc or else from ps.

    words is the command line. From ps it is split on spaces, so a path with
    a space in it comes back in pieces. An unreadable listing gives [].
    """
    name, _number = _device(tty)
    if not name and not _number:
        return []
    if os.path.isdir("/proc/self"):
        return _processes_from_proc(tty)
    if not name:
        return []
    result = _run(["ps", "-ww", "-t", name, "-o", "pid=,command="])
    if result is None or result.returncode != 0:
        return []
    found = []
    for line in result.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and command.split():
            found.append((int(pid), command.split()))
    return found


def _locate_tmux(tty, server):
    result = _tmux(server, "list-panes", "-a", "-F", "#{pane_id}\t#{pane_tty}")
    if result is None or result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        pane, _, pane_tty = line.partition("\t")
        if pane and same_tty(tty, pane_tty):
            return {"kind": "tmux", "server": server, "pane": pane}
    return None


def _locate_iterm(tty):
    result = _run(["osascript", "-e", ITERM_TTYS])
    if result is None or result.returncode != 0:
        return None
    for line in (result.stdout + result.stderr).splitlines():  # `log` writes to stderr
        session, _, session_tty = line.partition("\t")
        if session and same_tty(tty, session_tty):
            return {"kind": "iterm", "session": session.strip()}
    return None


def locate(record, tmux_server=None, kinds=("tmux", "iterm")):
    """The location of the chat whose session record this is, or None."""
    tty = (record or {}).get("tty")
    if not tty:
        return None
    for kind in kinds:
        if kind == "tmux":
            found = _locate_tmux(tty, tmux_server)
        elif kind == "iterm":
            found = _locate_iterm(tty)
        else:
            found = None
        if found:
            return found
    return None


def _applescript_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _iterm_script(session, action):
    """A script that runs action on the one session with that id. It prints OK, or NONE when no session has it."""
    return (f'tell application "iTerm2" to repeat with each_window in windows\n'
            f' repeat with each_tab in tabs of each_window\n'
            f'  repeat with each_session in sessions of each_tab\n'
            f'   if id of each_session is {_applescript_string(session)} then\n'
            f'    {action}\n'
            f'   end if\n'
            f'  end repeat\n end repeat\nend repeat\nreturn "NONE"')


def _iterm(session, action):
    result = _run(["osascript", "-e", _iterm_script(session, action + '\n    return "OK"')])
    return bool(result) and result.returncode == 0 and result.stdout.strip() == "OK"


KEYS = {"enter": ("Enter", 13), "esc": ("Escape", 27), "backspace": ("BSpace", 127)}


def type_into(location, text, enter=True):
    """Type text, then Enter unless enter is False, at the location. Returns whether it was typed.

    tmux and iTerm both aim at the pane or session id, never the tty, so a
    tty that a new tab took over gets nothing, and a closed one returns False.
    """
    if not location:
        return False
    if location["kind"] == "tmux":
        server, pane = location.get("server"), location["pane"]
        typed = _tmux(server, "send-keys", "-t", pane, "-l", text) if text else _tmux(server, "has-session",
                                                                                         "-t", pane)
        if typed is None or typed.returncode != 0:
            return False
        if not enter:
            return True
        entered = _tmux(server, "send-keys", "-t", pane, "Enter")
        return bool(entered) and entered.returncode == 0
    if location["kind"] == "iterm":
        ending = "" if enter else " newline NO"
        return _iterm(location["session"], f"tell each_session to write text {_applescript_string(text)}{ending}")
    return False


def press(location, key):
    """Press enter, esc or backspace at the location. Returns whether it was sent."""
    if not location or key not in KEYS:
        return False
    tmux_key, code = KEYS[key]
    if location["kind"] == "tmux":
        sent = _tmux(location.get("server"), "send-keys", "-t", location["pane"], tmux_key)
        return bool(sent) and sent.returncode == 0
    if location["kind"] == "iterm":
        return _iterm(location["session"], f"tell each_session to write text (character id {code}) newline NO")
    return False


def screen(location):
    """The text the pane or tab shows now, or None when it is gone."""
    if not location:
        return None
    if location["kind"] == "tmux":
        result = _tmux(location.get("server"), "capture-pane", "-p", "-t", location["pane"])
        return result.stdout if result is not None and result.returncode == 0 else None
    if location["kind"] == "iterm":
        result = _run(["osascript", "-e", _iterm_script(location["session"], "return contents of each_session")])
        if result is None or result.returncode != 0 or result.stdout.strip() == "NONE":
            return None
        return result.stdout
    return None


def focus(location):
    """Bring the location to the front. Written for the board's go button; no v0.1 part calls it."""
    if not location:
        return False
    if location["kind"] == "tmux":
        server, pane = location.get("server"), location["pane"]
        window = _tmux(server, "select-window", "-t", pane)
        chosen = _tmux(server, "select-pane", "-t", pane)
        return bool(window) and bool(chosen) and window.returncode == 0 and chosen.returncode == 0
    if location["kind"] == "iterm":
        return _iterm(location["session"], "select each_session")
    return False
