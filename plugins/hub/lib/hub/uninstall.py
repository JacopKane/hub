"""The step's uninstall: take hub out and keep what belongs to the user.

It settles the steps an apply left half done (see links.repair), then
removes every link and copy in the record that is still as hub left it,
takes away the background jobs an older hub ran (see old_jobs.py), runs the
off command of each part that runs (see switched.py), and removes hub's own
state in ~/.local/state/claude-hub/ (resolved.json, the slots, the built copies,
the records, offers, signals and history). It keeps the user's settings in
~/.config/claude-hub/, the files hub held in ~/.local/state/claude-hub/held/,
each folder of the state that a part names under keeps_on_uninstall, and the
state folder of an earlier hub that was still there beside the new one. It
removes the link the state folder move left under the old name.

It puts back each Claude Code value hub wrote, from keys.json, but only where
the file still holds hub's value, so a value the user changed since stays.
Then it removes hub's plugins with `claude plugin uninstall`, or prints that
command when the claude program is missing or fails. The last lines name each
kept folder with the command that deletes it, then the plugin removal.

Before it removes anything it writes a mark, uninstalled.json, that names
this copy of hub. While the mark names the copy in place, every hook does
nothing and apply changes nothing, so a session start or a tool call can not
bring hub back. A new install is a new copy, and its first apply removes the
mark.
"""
import contextlib
import os
import shlex
import shutil
import subprocess

from . import files, resolve

# Every hook asks is_out, so this module loads on every tool call. It loads the
# cleanup of old jobs and the part switch code only in run(). Keys, links and
# the part tree still load through apply.

HELD = "held"
MARK = "uninstalled.json"
MARKETPLACE = "hub"
OUT = ("hub is uninstalled, so it changes nothing. To bring it back, install the plugin again: "
       "claude plugin install hub@hub")


class Uninstalled(ValueError):
    pass


def copy_stamp():
    """What tells this copy of hub from a new install: its folder, and its manifest's inode and change time.

    A fresh copy gets a new inode and change time even at the same path.
    """
    path = os.path.join(files.PLUGIN_ROOT, ".claude-plugin", "plugin.json")
    try:
        info = os.stat(path)
    except FileNotFoundError:
        info = os.stat(files.PLUGIN_ROOT)
    return [files.PLUGIN_ROOT, info.st_ino, info.st_ctime_ns]


def is_out():
    """Whether hub was uninstalled and this is still the same copy."""
    try:
        mark = files.read_json(files.state_path(MARK))
    except ValueError:
        return True  # a broken mark still means out
    return bool(mark) and mark.get("copy") == copy_stamp()


def clear_old_mark():
    """Remove a mark that names an earlier copy, because this copy is a new install."""
    if os.path.exists(files.state_path(MARK)) and not is_out():
        os.unlink(files.state_path(MARK))


def plugin_ids():
    """hub's plugins, as name@marketplace."""
    manifest = files.read_json(os.path.join(files.PLUGIN_ROOT, ".claude-plugin", "plugin.json"), {}) or {}
    return [f"{manifest.get('name', 'hub')}@{MARKETPLACE}"]


def remove_plugins(lines):
    program = shutil.which("claude")
    for plugin in plugin_ids():
        command = ["claude", "plugin", "uninstall", plugin]
        if program:
            try:
                done = subprocess.run([program, *command[1:]], capture_output=True, text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                done = None
            if done is not None and done.returncode == 0:
                lines.append(f"hub removed its plugin {plugin} from Claude Code.")
                continue
        lines.append(f"To remove the plugin, run: {shlex.join(command)}")


def restore_keys(lines):
    from . import keys
    keys.restore_all(lines)


def kept_names():
    from . import tree as tree_module
    names = {HELD, MARK}
    try:
        tree = tree_module.load(files.PARTS_DIR)
    except tree_module.TreeError:
        return names
    for part in tree.parts:
        names.update(part.data.get("keeps_on_uninstall", []))
    return names


def run():
    """Returns the lines to print."""
    from . import links, old_jobs, switched
    from . import tree as tree_module
    lines = []
    with files.locked():
        files.write_json(files.state_path(MARK), {"copy": copy_stamp(), "time": resolve.now()})
        for entry in links.repair():
            if links.is_own(entry):
                links.remove(entry, lines)
            elif os.path.lexists(entry["path"]):
                lines.append(f"hub left {entry['path']} as it is, because it changed since hub made it.")
        restore_keys(lines)
        old_lines, finished = old_jobs.remove()
        lines += old_lines
        # A part whose old stop timed out or failed still gets its own "off", so what it holds is sent.
        running = switched.running_parts(files.read_json(files.state_path(resolve.RESOLVED))) - finished
        try:
            parts = tree_module.load(files.PARTS_DIR).parts
        except tree_module.TreeError:
            parts = []
        for part in parts:
            if part.id in running and part.data.get("when_switched"):
                lines += switched.run(part, "off")
        keep = kept_names()
        state = files.state_dir()
        for name in sorted(os.listdir(state)):
            if name in keep:
                continue
            path = os.path.join(state, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
    lines.append("hub is out of Claude Code and Cursor. It kept your files:")
    for name in sorted(keep - {HELD, MARK}):
        path = os.path.join(state, name)
        if os.path.exists(path):
            lines.append(f"  to delete {name}: rm -rf {shlex.quote(path)}")
    lines.append(f"  to delete your settings: rm -rf {shlex.quote(files.config_dir())}")
    held = os.path.join(state, HELD)
    if os.path.exists(held):
        lines.append(f"  to delete the files hub held for you: rm -rf {shlex.quote(held)}")
    old_state = files.old_state_dir()
    if files.is_old_state_link(old_state, state):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(old_state)  # the link the move left for chats that ran an earlier hub
    elif os.path.isdir(old_state) and not os.path.islink(old_state):
        old_held = os.path.join(old_state, HELD)
        if os.path.exists(old_held):
            lines.append(f"  to delete the files an earlier hub held for you: rm -rf {shlex.quote(old_held)}")
        lines.append(f"  to delete the state folder of an earlier hub: rm -rf {shlex.quote(old_state)}")
    remove_plugins(lines)
    return lines
