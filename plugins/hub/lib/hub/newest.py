"""The newest hub copy builds the shared files, and an older one leaves them alone.

Each open chat runs the hub copy it started with, and Claude Code keeps one
folder per version. All copies build the same shared files, such as the
links, the built copies, the slots and resolved.json. Without a check the
copy that built last wins, and the files switch between versions while chats
on two versions stay open.

An apply records the copy that built the files in built_by.json, as
{"version", "root"}. A copy reads that record before it builds. When the
record names a newer version, the copy builds nothing (see apply.run and
apply.ensure_fresh). Versions compare as numbers, so 0.20.10 is newer than
0.20.9. A newer copy, or one of the same version, builds as before.

Every copy also builds as before when the record is missing or cannot be
read, or when either version is not plain numbers. The same goes for a record
from a folder outside Claude Code's plugin cache, such as a --plugin-dir
checkout or a Cursor copy, so a copy that is not installed never holds the
files for good. A record whose folder now holds another version does not
count either. Nor does a newer copy that is no longer installed, because its
folder is gone or Claude Code marked it with .orphaned_at after the user went
back to an older version.

An older copy that skips its build may still have changed what the build needs,
such as a held file or a setting. So it marks resolved.json stale, and the
newer copy rebuilds on its next run (see apply.run).

Copies from before this check have none, so they still build until their
chats restart.
"""
import os
import re

from . import files, links

STAMP = "built_by.json"
# Claude Code leaves this file in a cache folder of a version that is no longer installed.
ORPHANED = ".orphaned_at"
PLAIN_VERSION = re.compile(r"\d+(?:\.\d+)*")


def version_key(text):
    """The version as numbers, such as (0, 20, 10) for "0.20.10", or None when it is not plain numbers.

    Trailing zeros drop, so 0.21 and 0.21.0 are the same version.
    """
    if not isinstance(text, str) or not PLAIN_VERSION.fullmatch(text):
        return None
    numbers = [int(part) for part in text.split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def manifest_version(root):
    """The version in a copy's plugin.json, or None when it has none or the file can not be read."""
    try:
        manifest = files.read_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    except (OSError, ValueError):
        return None
    return manifest.get("version") if isinstance(manifest, dict) else None


def plugin_cache():
    """The folder where Claude Code keeps the installed copies of each plugin."""
    return os.path.join(links.claude_dir(), "plugins", "cache")


def is_inside(folder, root):
    folder, root = os.path.realpath(folder), os.path.realpath(root)
    return root != folder and os.path.commonpath([folder, root]) == folder


def still_installed(record):
    """Whether the recorded copy sits in the plugin cache, still holds the recorded version and is not taken out."""
    root = record.get("root")
    return (isinstance(root, str) and is_inside(plugin_cache(), root)
            and manifest_version(root) == record.get("version")
            and not os.path.lexists(os.path.join(root, ORPHANED)))


def newer_version(record, own, installed):
    """The version in record when it is newer than own and its copy is installed, else None.

    record is what built_by.json holds, own is this copy's version, and
    installed(record) tells whether the recorded copy is still installed.
    """
    if not isinstance(record, dict):
        return None
    theirs, mine = version_key(record.get("version")), version_key(own)
    if theirs is None or mine is None or theirs <= mine or not installed(record):
        return None
    return record["version"]


def read_record():
    try:
        return files.read_json(files.state_path(STAMP))
    except (OSError, ValueError):
        return None


def newer_build():
    """The version of the newer copy that built the shared files, or None when this copy may build."""
    return newer_version(read_record(), manifest_version(files.PLUGIN_ROOT), still_installed)


def record_build():
    files.write_json(files.state_path(STAMP), {"version": manifest_version(files.PLUGIN_ROOT),
                                               "root": files.PLUGIN_ROOT})


def left_alone_line(newer):
    own = manifest_version(files.PLUGIN_ROOT)
    return (f"hub {own} changed no built files, because the newer hub {newer} built them. "
            f"Your settings are saved, a chat on hub {newer} applies them on its next run, "
            f"and new chats start on hub {newer}.")
