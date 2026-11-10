"""hubctl uninstall: take hub out and keep what belongs to the user.

It settles the steps an apply left half done (see links.repair), then
removes every link and copy in the record that is still as hub left it,
every background job in the jobs record (each one's stop command runs, even
when its part is gone), and hub's own state in ~/.local/state/hubctl/ (resolved.json, the slots, the
built copies, the records, offers, signals and history). It keeps the user's
settings in ~/.config/hubctl/, the files hub held in ~/.local/state/hubctl/held/,
and each folder of the state that a part names under keeps_on_uninstall.

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
import os
import shlex
import shutil
import subprocess

from . import files, resolve

# Every hook asks is_out, so this module loads on every tool call. It skips only
# the job code, plistlib and the XML parser. Keys, links and the part tree still
# load through apply.

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
    from . import links
    from .adapters import jobs
    lines = []
    with files.locked():
        files.write_json(files.state_path(MARK), {"copy": copy_stamp(), "time": resolve.now()})
        for entry in links.repair():
            if links.is_own(entry):
                links.remove(entry, lines)
            elif os.path.lexists(entry["path"]):
                lines.append(f"hub left {entry['path']} as it is, because it changed since hub made it.")
        restore_keys(lines)
        lines += jobs.remove_all()
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
    remove_plugins(lines)
    return lines
