"""The settings files and their scopes.

    local     <repo>/.hub/hub.local.json   this repo only, never committed
    project   <repo>/.hub/hub.json         committed, the same for everyone in the repo
    user      ~/.config/hubctl/hub.json    the settings file
    shipped   part.json of each part       the defaults

The highest scope that sets a value wins. A file keeps only values that
differ from the default, plus pins (values the user set by hand). It is
written with sorted keys, one flag per line and a blank line between flags,
so edits to different flags on two machines merge without a conflict.

A project file comes with every clone, so anyone who can push to the repo
writes it. A local file that git tracks comes with the clone too, so it is
shared like a project file and gets the same limits.
"""
import json
import os
import subprocess

from . import files

SCOPES = ["user", "project", "local"]  # lowest to highest
SCHEMA_URL = "https://raw.githubusercontent.com/JacopKane/hub/main/schema/hub.schema.json"
VERSION = 1
MIGRATIONS = []  # numbered steps that move an older file forward, in order
TOP_KEYS = {"$schema", "version", "flags", "private_words", "private_words_files", "options"}
ENTRY_KEYS = {"value", "by"}
SETTERS = {"model", "setup", "tuner", "user"}
# Flags whose values live in their own top-level keys, so a person can read and edit them.
TOP_LEVEL_FLAGS = {"core.private_words": "private_words", "core.private_words_files": "private_words_files"}


class SettingsError(ValueError):
    pass


def find_repo(start):
    """The top folder of the git repo that holds a working folder, or None.

    The project and local files live in <repo>/.hub/, so a folder outside a
    git repo has neither. `.git` may be a folder or, in a worktree, a file.
    """
    folder = os.path.realpath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def tracked(path):
    """Whether git tracks a file. When git can not answer, the file counts as tracked."""
    folder = os.path.dirname(path)
    try:
        listed = subprocess.run(["git", "ls-files", "--", os.path.basename(path)], cwd=folder,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return True
    return listed.returncode != 0 or bool(listed.stdout.strip())


def shared(scope, path):
    """Whether a scope's file comes with a clone, so it may not lower a privacy flag."""
    if scope == "project":
        return True
    return scope == "local" and path is not None and os.path.exists(path) and tracked(path)


def scope_path(scope, cwd=None, repo=None):
    if scope == "user":
        return files.user_settings_path()
    repo = repo or find_repo(cwd)
    if repo is None:
        return None
    return os.path.join(repo, ".hub", "hub.local.json" if scope == "local" else "hub.json")


def empty():
    return {"$schema": SCHEMA_URL, "version": VERSION, "flags": {}}


def check(data, path):
    if not isinstance(data, dict):
        raise SettingsError(f"{path}: the settings must be an object")
    for key in data:
        if key not in TOP_KEYS:
            raise SettingsError(f"{path}: unknown key {key!r}")
    for flag, entry in data.get("flags", {}).items():
        if not isinstance(entry, dict) or set(entry) - ENTRY_KEYS or "value" not in entry:
            raise SettingsError(f"{path}: {flag}: an entry holds value and by")
        if entry.get("by", "user") not in SETTERS:
            raise SettingsError(f"{path}: {flag}: by must be one of {sorted(SETTERS)}")
    for key in ("private_words", "private_words_files"):
        if key in data and not (isinstance(data[key], list) and all(isinstance(word, str) for word in data[key])):
            raise SettingsError(f"{path}: {key} must be a list of texts")
    return migrate(data)


def migrate(data):
    version = data.get("version", VERSION)
    for number, step in MIGRATIONS:
        if version < number:
            data = step(data)
            version = number
    data["version"] = VERSION
    return data


def read(path):
    if path is None:
        return None
    try:
        data = files.read_json(path)
    except ValueError as error:  # such as merge conflict markers
        raise SettingsError(f"{path}: not valid JSON ({error})") from None
    except OSError as error:
        raise SettingsError(f"{path}: can not be read ({error.strerror})") from None
    if data is None:
        return None
    return check(data, path)


def layers(cwd=None):
    """[(scope, path, data)] from lowest to highest, for the files that exist."""
    found = []
    for scope in SCOPES:
        path = scope_path(scope, cwd)
        data = read(path)
        if data is not None:
            found.append((scope, path, data))
    return found


def entry(data, flag_id):
    """The stored entry for a flag in one file, as {"value", "by"}, or None."""
    if data is None:
        return None
    if flag_id in TOP_LEVEL_FLAGS:
        key = TOP_LEVEL_FLAGS[flag_id]
        return {"value": data[key], "by": "user"} if key in data else None
    return data.get("flags", {}).get(flag_id)


def put(data, flag_id, value, by):
    if flag_id in TOP_LEVEL_FLAGS:
        data[TOP_LEVEL_FLAGS[flag_id]] = value
    else:
        data.setdefault("flags", {})[flag_id] = {"by": by, "value": value}


def drop(data, flag_id):
    if flag_id in TOP_LEVEL_FLAGS:
        data.pop(TOP_LEVEL_FLAGS[flag_id], None)
    else:
        data.get("flags", {}).pop(flag_id, None)


def dumps(data):
    """Sorted keys, one flag per line, and a blank line between flags."""
    def one(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    lines = ["{"]
    keys = sorted(set(data) | {"$schema", "version", "flags"})
    for index, key in enumerate(keys):
        comma = "," if index < len(keys) - 1 else ""
        value = data.get(key, empty().get(key))
        if key == "flags":
            flags = value or {}
            if not flags:
                lines.append(f'  "flags": {{}}{comma}')
                continue
            lines.append('  "flags": {')
            names = sorted(flags)
            for spot, name in enumerate(names):
                lines.append("")
                lines.append(f"    {one(name)}: {one(flags[name])}" + ("," if spot < len(names) - 1 else ""))
            lines.append("")
            lines.append(f"  }}{comma}")
        else:
            lines.append(f"  {one(key)}: {one(value)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write(path, data):
    files.write_text(path, dumps(data))
    folder = os.path.dirname(path)
    if os.path.basename(folder) == ".hub":
        ignore = os.path.join(folder, ".gitignore")
        if not os.path.exists(ignore):
            files.write_text(ignore, "hub.local.json\n")
