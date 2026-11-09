"""Claude Code settings keys that hub writes, and the record to put them back.

    want(key, value, part, lines)   write one key by its dot path, such as env.HUB_NAME
    release(key, lines)             put back the user's earlier value
    pin_changed(lines)       at apply: a key the user changed since turns its part off
    restore_all(lines)       at uninstall: put back every earlier value

The key goes into the user's ~/.claude/settings.json. A write keeps the
file's key order and indent, and goes to a link's target, so the link stays.
Each key hub wrote is in ~/.local/state/hubctl/keys.json with the value hub
wrote and the value the file held before. A value hub did not write is the
user's own choice: apply turns the part that wanted the key off, pinned, with
one line, and leaves the user's value. hub puts an earlier value back only
where the file still holds hub's value. When the file does not parse, hub
leaves it and the key as they are, with one line, and the doctor names the
file until a later apply can read it (see unreadable).
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
    return f"hub could not read {path}, so it left {key} as it is. Fix the file, then run hubctl apply."


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
    """Turn off, pinned, each part whose key the user set to another value since hub wrote it."""
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
        return
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
                     f"Run hubctl on {part} to bring it back.")
    settings.write(path, data)
    write_record(kept)


def restore_all(lines):
    for entry in read_record():
        if not put_back(entry):
            lines.append(f"hub could not read {entry['file']}, so {entry['key']} stays as it is.")
