"""Claude Code settings keys that hub writes, and the record to put them back.

    want(key, value, part, lines)   write one key by its dot path, such as env.HUB_NAME
    release(key, lines)             put back the user's earlier value
    pin_changed(lines)       at apply: a key the user changed since turns its part off
    stand_down(key, value, part, lines, env_names)   at apply: a default-on part whose key the user
                             already set to another value, or whose opt-out variable is set, goes off
    restore_all(lines)       at uninstall: put back every earlier value

The key goes into the user's ~/.claude/settings.json. A write keeps the
file's key order and indent, and goes to a link's target, so the link stays.
Each key hub wrote is in ~/.local/state/claude-hub/keys.json with the value hub
wrote and the value the file held before. A value hub did not write is the
user's own choice: apply turns the part that wanted the key off, pinned, with
one line, and leaves the user's value. hub puts an earlier value back only
where the file still holds hub's value. When the file does not parse, hub
leaves it and the key as they are, with one line, and the doctor names the
file until a later apply can read it (see unreadable).

Before setup writes hub's own settings file, pin_changed and stand_down
write nothing. They return the keys apply must leave alone. hub.json is how
the tuner knows setup ran, so creating it early would skip setup. Setup's
own apply records the pin later.
"""
import contextlib
import json
import os
import re

from . import files, history, settings

RECORD = "keys.json"
UNREADABLE = "keys_unreadable.json"  # {key: file} hub could not read at the last apply
MISSING = object()
INDENT = re.compile(r"^\{\s*\n([ \t]+)\S")


def settings_file():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(files.home(), ".claude")
    return os.path.join(base, "settings.json")


def global_config_file():
    """~/.claude.json, or .claude.json in CLAUDE_CONFIG_DIR. Older Claude Code versions kept some keys there."""
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or files.home(), ".claude.json")


def setup_done():
    return os.path.exists(files.user_settings_path())


def read_record():
    return files.read_json(files.state_path(RECORD), {"keys": []})["keys"]


def write_record(entries):
    files.write_json(files.state_path(RECORD), {"keys": entries})


def load(path):
    """(data, indent) of a settings file. A missing file is empty, with two spaces."""
    text = files.read_text(path)
    if text is None or not text.strip():
        return {}, "  "
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    match = INDENT.match(text)
    return data, match.group(1) if match else "  "


def save(path, data, indent):
    files.write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


def get(data, key):
    node = data
    for name in key.split("."):
        if not isinstance(node, dict) or name not in node:
            return MISSING
        node = node[name]
    return node


def put(data, key, value):
    *parents, last = key.split(".")
    node = data
    for name in parents:
        if not isinstance(node.get(name), dict):
            node[name] = {}
        node = node[name]
    node[last] = value


def delete(data, key):
    *parents, last = key.split(".")
    node = data
    for name in parents:
        node = node.get(name)
        if not isinstance(node, dict):
            return
    node.pop(last, None)


def find(entries, key):
    return next((entry for entry in entries if entry["key"] == key), None)


def unreadable():
    return files.read_json(files.state_path(UNREADABLE), {}) or {}


def note_unreadable(key, path):
    """Remember that the file of key did not parse, or with path None, that it did."""
    found = unreadable()
    if path is None and key not in found:
        return
    if path is None:
        del found[key]
    else:
        found[key] = path
    if found:
        files.write_json(files.state_path(UNREADABLE), found)
    else:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(files.state_path(UNREADABLE))


def unreadable_line(key, path):
    return f"hub could not read {path}, so it left {key} as it is. Fix the file, then say \"apply hub's settings\"."


def want(key, value, part, lines):
    """Make the file hold value at key, and record what it held before."""
    entries = read_record()
    entry = find(entries, key)
    path = entry["file"] if entry else settings_file()
    try:
        data, indent = load(path)
    except ValueError:
        note_unreadable(key, path)
        lines.append(unreadable_line(key, path))
        return
    note_unreadable(key, None)
    current = get(data, key)
    if entry is None:
        entry = {"file": path, "key": key, "part": part, "hub_value": value,
                 "had_earlier": current is not MISSING, "earlier": None if current is MISSING else current}
        entries.append(entry)
    entry["hub_value"] = value
    write_record(entries)
    if current != value:
        put(data, key, value)
        save(path, data, indent)


def put_back(entry):
    """Put the earlier value back where the file still holds hub's value. Returns False when unreadable."""
    try:
        data, indent = load(entry["file"])
    except ValueError:
        return False
    if get(data, entry["key"]) != entry["hub_value"]:
        return True
    if entry.get("had_earlier"):
        put(data, entry["key"], entry["earlier"])
    else:
        delete(data, entry["key"])
    save(entry["file"], data, indent)
    return True


def release(key, lines):
    """Put back the earlier value of key. An unreadable file keeps the record, to try again next apply."""
    entries = read_record()
    entry = find(entries, key)
    if entry is None:
        note_unreadable(key, None)
        return
    if not put_back(entry):
        note_unreadable(key, entry["file"])
        lines.append(unreadable_line(key, entry["file"]))
        return
    note_unreadable(key, None)
    write_record([item for item in entries if item is not entry])


def pin_changed(lines):
    """Turn off, pinned, each part whose key the user set to another value since hub wrote it.

    Returns the keys apply must leave alone this run. Before setup, a changed
    key keeps its record, and its part waits for setup's own apply.
    """
    entries, kept, changed = read_record(), [], []
    for entry in entries:
        try:
            data, _indent = load(entry["file"])
        except ValueError:
            kept.append(entry)
            continue
        current = get(data, entry["key"])
        if current == entry["hub_value"] or not entry.get("part"):
            kept.append(entry)
        else:
            changed.append((entry, current))
    if not changed:
        return set()
    if not setup_done():
        return {entry["key"] for entry, _current in changed}
    path = files.user_settings_path()
    data = settings.read(path) or settings.empty()
    for entry, current in changed:
        part = entry["part"]
        stored = settings.entry(data, part)
        settings.put(data, part, "off", "user")
        shown = "nothing" if current is MISSING else json.dumps(current, ensure_ascii=False)
        history.add(part, "user", stored["value"] if stored else "on", "off", "user", "apply",
                    f"{entry['key']} was set to {shown} by hand")
        lines.append(f"You set {entry['key']} to {shown}, so {part} is now off and your value stays. "
                     f"Say \"turn on {part}\" to bring it back.")
    settings.write(path, data)
    write_record(kept)
    return set()


def is_set(value):
    """Read an opt-out variable as a standard boolean. Unset, empty, 0, false, no and off all count as not set."""
    return value is not None and str(value).strip().lower() not in ("", "0", "false", "no", "off")


def users_value(key):
    """The user's own value at key. The settings file comes first, then ~/.claude.json, which Claude Code still reads.

    Raises ValueError when the settings file does not parse.
    """
    current = get(load(settings_file())[0], key)
    if current is not MISSING:
        return current
    try:
        data = files.read_json(global_config_file(), {})
    except (ValueError, OSError):
        return MISSING
    return get(data, key) if isinstance(data, dict) else MISSING


def opt_out_variable(env_names):
    """The first of env_names set in the environment or in the settings file's env block, else None."""
    try:
        block = get(load(settings_file())[0], "env")
    except ValueError:
        block = MISSING
    for name in env_names:
        if is_set(os.environ.get(name)) or (isinstance(block, dict) and is_set(block.get(name))):
            return name
    return None


def stand_down(key, value, part, lines, env_names=()):
    """Turn a part off, pinned, at apply when the user's own choice comes first. Runs before resolving.

    This is for a part that is on by default and that no hub setting names.
    The user's choice is one of env_names being set, such as DO_NOT_TRACK. Or,
    while hub has not written key yet, it is a value other than the part's in
    the settings file or in ~/.claude.json. The part then goes off with one
    line, and the user's value stays. If the user later turns the part on by
    hand, hub writes its value and keeps theirs to put back.

    Returns True when apply must leave key alone this run. That is when the
    choice is there but setup has not run yet, so hub writes nothing until
    setup's own apply.
    """
    path = files.user_settings_path()
    data = settings.read(path) or settings.empty()
    if settings.entry(data, part) is not None:
        return False
    variable = opt_out_variable(env_names)
    if variable:
        reason = f"{variable} is set"
        line = f"{variable} is set, so {part} is now off."
    else:
        if find(read_record(), key) is not None:
            return False
        try:
            current = users_value(key)
        except ValueError:
            return False  # want names the file that does not parse
        if current is MISSING or current == value:
            return False
        shown = json.dumps(current, ensure_ascii=False)
        reason = f"{key} was already {shown} in your settings"
        line = f"Your settings already set {key} to {shown}, so {part} is now off and your value stays."
    if not setup_done():
        return True
    settings.put(data, part, "off", "user")
    history.add(part, "user", "on", "off", "user", "apply", reason)
    lines.append(f"{line} Say \"turn on {part}\" to bring it back.")
    settings.write(path, data)
    return False


def restore_all(lines):
    for entry in read_record():
        if not put_back(entry):
            lines.append(f"hub could not read {entry['file']}, so {entry['key']} stays as it is.")
