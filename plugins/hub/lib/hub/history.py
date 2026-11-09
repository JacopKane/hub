"""The change log every door writes and undo reads.

Each line of ~/.local/state/hubctl/history.jsonl holds an id, the UTC time,
the flag, the scope, the repo for the project and local scopes, the old and
the new value, who set it, the door and a reason. The reason stays empty
until the tuner fills it in. A change that is not a flag value, such as
taking an offer to switch, has an action instead of a flag. Undo writes to the recorded repo, wherever it
is run from.
"""
import os

from . import files

NAME = "history.jsonl"


def path():
    return files.state_path(NAME)


def add(flag, scope, old, new, by, door, reason="", undoes=None, repo=None):
    line = {"id": os.urandom(6).hex(), "time": files_time(), "flag": flag, "scope": scope,
            "old": old, "new": new, "by": by, "door": door, "reason": reason}
    if repo:
        line["repo"] = repo
    if undoes:
        line["undoes"] = undoes
    files.append_line(path(), line)
    return line


def add_action(action, door, undoes=None, **fields):
    """A line for a change that is not a flag value, such as taking an offer."""
    line = {"id": os.urandom(6).hex(), "time": files_time(), "action": action, "door": door, **fields}
    if undoes:
        line["undoes"] = undoes
    files.append_line(path(), line)
    return line


def files_time():
    from .resolve import now
    return now()


def lines():
    return files.read_lines(path())


def last_undoable():
    """The newest change that is not an undo and has not been undone."""
    every = lines()
    undone = {line.get("undoes") for line in every}
    for line in reversed(every):
        if "undoes" not in line and line["id"] not in undone:
            return line
    return None


def undoable(line_id):
    """The change with this id, when it can still be undone. Raises ValueError with the reason."""
    from .changes import ChangeError
    every = lines()
    found = [line for line in every if line["id"] == line_id]
    if not found:
        raise ChangeError(f"no change has the id {line_id!r}")
    if "undoes" in found[0]:
        raise ChangeError(f"{line_id} is an undo itself, so it can not be undone")
    if any(line.get("undoes") == line_id for line in every):
        raise ChangeError(f"{line_id} is undone already")
    return found[0]
