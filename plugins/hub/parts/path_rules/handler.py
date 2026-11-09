"""path_rules: the rules for a kind of file arrive when a chat first touches one.

After a Read, Edit or Write, the file's path is matched against each
behavior's globs, taken from the repo's top folder (or the working folder
outside a repo). The text of each behavior that matches and runs is added to
the chat once per session. The sessions that got a text are kept in
~/.local/state/hubctl/path_rules/<session>.json.

A glob knows `**` (any folders), `*` and `?` (inside one name) and `{a,b}`
(either word). Cursor's file path key is a guess from its docs, so the
handler also takes `path` and `target_file`.
"""
import os
import re
import time

from hub import files, settings

PART = "path_rules"
BEHAVIORS = ("ui", "tests", "docs")
FOLDER = "path_rules"
PATH_KEYS = ("file_path", "path", "target_file")
RECORD_AGE = 7 * 24 * 3600


def glob_pattern(glob):
    """A compiled regex for one glob."""
    out, index = [], 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        else:
            character = glob[index]
            if character == "*":
                out.append("[^/]*")
            elif character == "?":
                out.append("[^/]")
            elif character == "{":
                end = glob.find("}", index)
                if end == -1:
                    out.append(re.escape(character))
                else:
                    words = glob[index + 1:end].split(",")
                    out.append("(?:" + "|".join(glob_pattern(word).pattern[:-2] for word in words) + ")")
                    index = end
            else:
                out.append(re.escape(character))
            index += 1
    return re.compile("".join(out) + r"\Z")


def matches(path, globs):
    return any(glob_pattern(glob).match(path) for glob in globs or [] if isinstance(glob, str))


def relative(path, cwd):
    """The path from the repo's top folder, else from cwd, with / between names."""
    cwd = cwd or os.getcwd()
    full = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    base = settings.find_repo(os.path.dirname(full)) or os.path.realpath(cwd)
    found = os.path.relpath(full, base)
    if found.startswith(".."):
        found = full.lstrip(os.sep)
    return found.replace(os.sep, "/")


def record_path(session):
    safe = "".join(character for character in str(session) if character.isalnum() or character in "-_") or "none"
    return files.state_path(os.path.join(FOLDER, safe + ".json"))


def prune(folder):
    cutoff = time.time() - RECORD_AGE
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            pass


def handle(event, hook_input, flags, answer):
    if event != "PostToolUse":
        return
    tool_input = hook_input.get("tool_input") or {}
    path = next((tool_input[key] for key in PATH_KEYS if isinstance(tool_input.get(key), str)), None)
    if not path:
        return
    found = relative(path, hook_input.get("cwd"))
    wanted = [name for name in BEHAVIORS if flags.runs(f"{PART}.{name}")
              and matches(found, flags.value(f"{PART}.{name}.paths"))]
    if not wanted:
        return
    record = record_path(hook_input.get("session_id"))
    with files.locked(os.path.join(FOLDER, os.path.basename(record) + ".lock")):
        sent = (files.read_json(record, {}) or {}).get("sent", [])
        new = [name for name in wanted if name not in sent]
        if not new:
            return
        files.write_json(record, {"sent": sent + new})
    for name in new:
        answer.add_context(flags.text(f"{PART}.{name}"))
        answer.signal(f"{PART}.{name}", "sent")
    if int.from_bytes(os.urandom(1), "big") < 8:  # about one run in 32
        prune(os.path.dirname(record))
