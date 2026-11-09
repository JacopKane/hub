"""Which flags only the user may lower, and the one door the user comes through.

A flag is protected when it is a privacy flag, the mode of a gates part, a
guard node, or any node under a guard node (adding to a guard's allowed list
loosens the guard). A value the user pinned is protected too, and set_flag
keeps that rule.

Only the mod lowers a protected flag, for a change the user typed or clicked
in /hub. It proves the change with a ticket: random bytes it sends as the
first line of hubctl's stdin, and a file in the state folder's doors folder
named by their sha256. hubctl takes the ticket once, by removing that file,
and only while the file is under a minute old. Writing such a file needs a
write into hub's state folder, which self-protection refuses like a write to
hub.json, and the folder holds only hashes, so reading it gives nothing. No
environment variable, argument or terminal claims a door. hubctl in a
terminal can raise a protected flag, but not lower it. Setup, the tuner and a
command the model runs can not lower one either.

The one other door is the user's own words. When the newest message the user
typed asks for a change (see said.py), self-protection lets the step's tool
call run and leaves a words ticket for that exact change in that chat: a
file in the doors folder named by the sha256 of the flag, the value, the
scope and the chat's id. Only hub's switch step takes it, run as its own
script in the same chat, once, by removing it, while it is a few seconds old.
Uninstalling hub from a tool call takes the same kind of ticket.
"""
import contextlib
import hashlib
import json
import os
import re
import select
import stat
import sys
import time

from . import files, resolve

MOD_DOOR = "mod"
WORDS_DOOR = "words"
GUARD_NAME = "guard"
GUARD_SUFFIX = "_guard"
DOORS = "doors"
# How long a ticket file the mod wrote stays good, and how long hubctl waits for the ticket line.
TICKET_SECONDS = 60
# How long a words ticket stays good. The step runs right after the hook that left it.
WORDS_TICKET_SECONDS = 10
# The chat a tool call's command runs in, as Claude Code gives it to the command.
CHAT_VARIABLE = "CLAUDE_CODE_SESSION_ID"
TICKET_WAIT_SECONDS = 2
TICKET = re.compile(r"[0-9a-f]{64}")

ASK_THE_USER = ("{flag} is protected, and this change weakens it, so it runs only when the user asks for it. "
                "Ask the user to say the change in their own words.")
NO_DOOR = ("{flag} is protected, so only the user can weaken it, by asking for the change in their own words. If "
           "a model runs this, ask the user.")
STOPS_ASK_THE_USER = ("This change to {flag} stops {stopped}, and only the user can weaken {stopped}. It runs only "
                      "when the user asks for it. Ask the user to say the change to {flag} in their own words.")
STOPS_NO_DOOR = ("This change to {flag} stops {stopped}, and only the user can weaken {stopped}, by asking for the "
                 "change in their own words. If a model runs this, ask the user.")


def under_guard(node):
    """Whether the node or one of its ancestors is a guard."""
    while node is not None:
        if node.name == GUARD_NAME or node.name.endswith(GUARD_SUFFIX):
            return True
        node = node.parent
    return False


def is_protected(node):
    return (node.privacy != "none" or (node.parent is None and node.family == resolve.GATES_FAMILY)
            or under_guard(node))


def lowers(node, new, old):
    """Whether new is weaker than old for a protected flag.

    A guarded list is weaker when it loses an item. Any other list, such as a
    guard's allowed list, is weaker with any change, and so is a text, a
    number or a choice. A switch or a mode compares by strictness, and a value
    that does not compare counts as weaker.
    """
    if new == old:
        return False
    if node.privacy == "loosens":
        return resolve.lowers(node, new, old)
    if node.type == "list":
        if node.privacy == "guard":
            kept = new if isinstance(new, list) else []
            return any(item not in kept for item in (old if isinstance(old, list) else []))
        return True
    if node.type in ("switch", "mode"):
        new_strength, old_strength = resolve.strength(node, new), resolve.strength(node, old)
        return new_strength is None or old_strength is None or new_strength < old_strength
    return True


def doors_folder():
    """The doors folder, made with the rights of hub's state folder when it is missing."""
    folder = files.state_path(DOORS)
    if not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(folder, stat.S_IMODE(os.stat(files.state_dir()).st_mode))
    return folder


def ticket_line():
    """The first line of stdin, read without blocking for more than a moment. None from a terminal."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        descriptor = sys.stdin.fileno()
    except (OSError, ValueError):
        return None
    read = b""
    deadline = time.monotonic() + TICKET_WAIT_SECONDS
    while b"\n" not in read and len(read) <= 200:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([descriptor], [], [], left)[0]:
            break
        chunk = os.read(descriptor, 200)
        if not chunk:
            break
        read += chunk
    return read.split(b"\n", 1)[0].decode("ascii", "replace").strip()


def clear_old_tickets(folder, now):
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if now - os.lstat(path).st_mtime > TICKET_SECONDS:
                os.unlink(path)
        except OSError:
            pass


def door():
    """The user door this process came through, or None. Only the mod's ticket opens it.

    It reads stdin, so call it only for a change that needs the door, and only once.
    Never inside a tool call, and never from a terminal.
    """
    if files.in_tool_call():
        return None
    line = ticket_line()
    folder = doors_folder()
    now = time.time()
    taken = None
    if line and TICKET.fullmatch(line):
        path = os.path.join(folder, hashlib.sha256(line.encode()).hexdigest())
        try:
            info = os.lstat(path)
            os.unlink(path)
        except OSError:
            info = None
        # Removing the file is what makes a ticket single use. A link or a folder is no ticket.
        if info is not None and stat.S_ISREG(info.st_mode) and -5 <= now - info.st_mtime <= TICKET_SECONDS:
            taken = MOD_DOOR
    clear_old_tickets(folder, now)
    return taken


def words_ticket_path(flag_id, value, scope, chat):
    change = json.dumps([flag_id, value, scope, chat or ""], sort_keys=True, ensure_ascii=False)
    return os.path.join(doors_folder(), "words-" + hashlib.sha256(change.encode()).hexdigest())


def leave_words_ticket(flag_id, value, scope, chat):
    """Self-protection's note that the user's newest typed message in this chat asks for this change."""
    files.write_text(words_ticket_path(flag_id, value, scope, chat), "")


def runs_as_the_step():
    """Whether this process is hub's switch step run as its own script, not hub's code loaded by other code."""
    return bool(sys.argv) and os.path.realpath(sys.argv[0]) == os.path.realpath(files.STEP)


# No flag has this id, so the uninstall ticket never matches a flag's change.
UNINSTALL_TICKET = (":uninstall", "hub", "user")


def leave_uninstall_ticket(chat):
    """Self-protection's note that the user's newest typed message in this chat asks to uninstall hub."""
    leave_words_ticket(*UNINSTALL_TICKET, chat)


def take_uninstall_ticket():
    """True once when the user asked in words, in this chat and a moment ago, to uninstall hub."""
    return take_words_ticket(*UNINSTALL_TICKET)


def take_words_ticket(flag_id, value, scope):
    """True once for a change the user asked for in words, in this chat and a moment ago.

    Only the switch step takes it. Removing the file makes it single use.
    """
    if not runs_as_the_step():
        return False
    path = words_ticket_path(flag_id, value, scope, os.environ.get(CHAT_VARIABLE, ""))
    try:
        info = os.lstat(path)
        os.unlink(path)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and -2 <= time.time() - info.st_mtime <= WORDS_TICKET_SECONDS
