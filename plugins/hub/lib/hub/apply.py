"""Apply: everything that follows from the settings, written in one go.

    run(tree)        the caller holds the settings lock
    ensure_fresh()   takes the lock and runs apply only when it is stale

A run turns off, pinned, each part whose hub link the user deleted or whose
Claude Code key the user changed (see keys.pin_changed), and
turns off, pinned, a default-on part whose key the user set to another value
before hub wrote it or whose opt-out variable is set (see keys.stand_down).
Before setup, those steps leave the link or key alone and pin nothing (see
keys.py). Setup's own apply turns remote_control on over a false the user
set earlier, and keeps that false to put back. Only an opt-out variable
still turns it off there. It resolves
every flag, makes and removes the links and built copies, sets outputStyle
while hub_style runs, and while remote_control runs sets remoteControlAtStartup
to true and attribution.sessionUrl to false, so commits and pull requests
carry no claude.ai session link (see keys.py). It writes the slot
files and cost.json, marks the parts of the hook groups that start Python
only when one may run (see pre_check.sh), takes away the background jobs an
older hub ran (see old_jobs.py), adds a line to applies.jsonl, and writes
resolved.json last. So a hook that finds resolved.json fresh also finds the
files built from it. Then it runs the on and off commands of the parts that
were switched (see switched.py). It returns the lines the step's apply prints.
While cursor_link does not run, apply writes no Cursor slots and removes the
old ones, since Cursor support is beta and off by default.

After the step's uninstall, apply changes nothing until a new copy of hub is
installed (see uninstall.is_out), and ensure_fresh raises Uninstalled.

Apply is stale when resolved.json is missing, a settings file, the profile or
a manifest changed since, the plugin copy moved, a recorded link is gone or
points into a folder that is neither the state folder nor the plugin copy,
or an apply stopped partway and left a step marked in the record.
At session start the dispatcher and the eight slot hooks start at once. The
first to take the lock applies, and the others then find it fresh.
"""
import contextlib
import os
import re
import shutil

from . import files, keys, links, resolve, slots, tree as tree_module, uninstall

APPLIES = "applies.jsonl"
COST = "cost.json"
# The marker pre_check.sh reads for each event, by name.
MARKERS = {"PreToolUse": "every_tool_parts", "PostToolUseFailure": "post_tool_use_failure_parts",
           "UserPromptSubmit": "user_prompt_parts", "Stop": "stop_parts",
           "SubagentStop": "subagent_stop_parts"}
# The PostToolUse group for every tool no part names starts Python only for the parts that handle every tool.
EVERY_TOOL_MARKERS = {"PostToolUse": "post_tool_use_every_parts"}
# The folders of PreToolUse and PostToolUse markers, one file per tool a running part names, and the mark
# written after every marker. pre_check.sh trusts no marker while the mark is missing or older than
# resolved.json. Older versions kept one PostToolUse marker for every named tool, which apply removes.
TOOL_PARTS, POST_TOOL_PARTS, MARKERS_DONE = "tool_parts", "post_tool_parts", "markers_done"
OLD_MARKERS = ("post_tool_use_parts",)
# The names pre_check.sh reads from hook input. Any other name starts the dispatcher there.
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]+")
STYLE_PART, STYLE_KEY, STYLE_NAME = "hub_style", "outputStyle", "hub-style"  # the name in style.md
REMOTE_PART, REMOTE_KEY = "remote_control", "remoteControlAtStartup"
SESSION_LINK_KEY = "attribution.sessionUrl"  # false keeps the claude.ai link out of commits and pull requests
REMOTE_OPT_OUT = ("DO_NOT_TRACK",)  # a user who opts out of tracking gets no Remote Control from hub
CURSOR_PART = "cursor_link"
in_setup = False  # True while setup applies the user's replies (see setup_run)


@contextlib.contextmanager
def setup_run():
    """Mark the applies inside it as setup's own, which turn remote_control on over an earlier false."""
    global in_setup
    in_setup = True
    try:
        yield
    finally:
        in_setup = False


def run(tree=None):
    if uninstall.is_out():
        return [uninstall.OUT]
    uninstall.clear_old_mark()
    from . import old_jobs, switched  # only an apply needs them, not every hook that loads this module
    lines = []
    ran_before = switched.running_parts(files.read_json(files.state_path(resolve.RESOLVED)))
    held_links = links.pin_deleted(lines)
    held = keys.pin_changed(lines, retake=(REMOTE_KEY,) if in_setup else ())
    if keys.stand_down(REMOTE_KEY, True, REMOTE_PART, lines, REMOTE_OPT_OUT, users_value_counts=not in_setup):
        held.add(REMOTE_KEY)
    tree = tree or tree_module.load(files.PARTS_DIR)
    data = resolve.apply(tree, write=False)
    listing = links.reconcile(tree, data, lines, held_links)
    set_style(data, lines, held)
    set_remote_control(data, lines, held)
    cost = {}
    for tool in tree_module.TOOLS:
        flags = data["tools"][tool]["flags"]
        if tool == "cursor" and not flags[CURSOR_PART]["runs"]:
            slots.clear(tool)  # Cursor is beta, so while it is off hub writes nothing for it
            cost[tool] = {"parts": {}, "total": {"start_text": 0}}
            continue
        texts, placed, _left_out = slots.write(tool, tree, flags, first=first_units(tool, tree, flags))
        parts = {}
        for _node_id, part_id, text in placed:
            parts.setdefault(part_id, {"listing": 0, "start_text": 0})["start_text"] += len(text)
        for part_id, size in listing.items():
            if data["tools"][tool]["flags"][part_id]["runs"]:
                parts.setdefault(part_id, {"listing": 0, "start_text": 0})["listing"] = size
        cost[tool] = {"parts": parts, "total": {"start_text": len(slots.start_text(texts))}}
    files.write_json(files.state_path(COST), cost)
    remove(files.state_path(MARKERS_DONE))
    running = switched.running_parts(data)
    # A part that an old job's stop just stopped starts again before the old record goes. A part that did
    # not run before gets its "on" below anyway, and one that no longer runs gets its "off".
    old_lines, _finished = old_jobs.remove(restart=lambda parts: switched.restart(tree, parts & ran_before & running))
    lines += old_lines
    files.append_line(files.state_path(APPLIES), {"time": resolve.now(), "lines": len(lines)})
    files.write_json(files.state_path(resolve.RESOLVED), data)
    mark_group_parts(data)
    for part, which in switched.changes(tree, ran_before, running):
        lines += switched.run(part, which)
    return lines


def set_style(data, lines, held=()):
    """outputStyle names hub's style while hub_style runs in Claude Code, else the user's earlier value."""
    if STYLE_KEY in held:
        return
    if data["tools"]["claude_code"]["flags"].get(STYLE_PART, {}).get("runs"):
        keys.want(STYLE_KEY, STYLE_NAME, STYLE_PART, lines)
    else:
        keys.release(STYLE_KEY, lines)


def set_remote_control(data, lines, held=()):
    """While remote_control runs in Claude Code, remoteControlAtStartup is true and attribution.sessionUrl is
    false. Otherwise both hold the user's earlier values again. The session link key has no part of its own,
    so a value the user sets there stays and turns nothing off."""
    if REMOTE_KEY in held:
        return
    if data["tools"]["claude_code"]["flags"].get(REMOTE_PART, {}).get("runs"):
        keys.want(REMOTE_KEY, True, REMOTE_PART, lines)
        keys.want(SESSION_LINK_KEY, False, None, lines)
    else:
        keys.release(REMOTE_KEY, lines)
        keys.release(SESSION_LINK_KEY, lines)


def first_units(tool, tree, flags):
    """Units that go before the tree's start text: the style as rule text, for Cursor."""
    if tool != "cursor" or not flags.get("cursor_link.style_as_rule", {}).get("runs"):
        return []
    text = links.load_cursor_handler().style_text(tree, flags)
    return [("cursor_link.style_as_rule", STYLE_PART, text)] if text else []


def mark_group_parts(data):
    """Write each marker with the Claude Code parts its group may start, or remove it when there are none.

    For PreToolUse, and for the PostToolUse group for every other tool, only
    the parts that handle every tool count here, since the tools a part names
    get their own markers, one file per tool. The done mark goes first and comes back
    last, after resolved.json, so pre_check.sh never trusts markers that an
    apply left half written.
    """
    done = files.state_path(MARKERS_DONE)
    remove(done)
    handlers = data["tools"]["claude_code"]["handlers"]
    wanted = [(event, name, event == "PreToolUse") for event, name in MARKERS.items()]
    wanted += [(event, name, True) for event, name in EVERY_TOOL_MARKERS.items()]
    for event, name, every_tool_only in wanted:
        path = files.state_path(name)
        parts = [entry["part"] for entry in handlers.get(event, [])
                 if not every_tool_only or not entry["tools"]]
        if parts:
            files.write_text(path, "".join(part + "\n" for part in parts))
        else:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
    mark_tool_parts(handlers.get("PreToolUse", []), TOOL_PARTS)
    mark_tool_parts(handlers.get("PostToolUse", []), POST_TOOL_PARTS)
    for name in OLD_MARKERS:
        remove(files.state_path(name))
    files.write_text(done, "")


def mark_tool_parts(entries, name):
    """Write a marker in the folder name for each tool that a running part names, and remove the others."""
    folder = files.state_path(name)
    named = {}
    for entry in entries:
        for tool in entry["tools"]:
            if TOOL_NAME.fullmatch(tool):
                named.setdefault(tool, []).append(entry["part"])
    os.makedirs(folder, mode=0o700, exist_ok=True)
    for name in os.listdir(folder):
        if name not in named:
            remove(os.path.join(folder, name))
    for tool, parts in sorted(named.items()):
        files.write_text(os.path.join(folder, tool), "".join(part + "\n" for part in parts))


def remove(path):
    """Delete a file or a folder that is in the way. A missing one is fine; any other error stops apply."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def is_fresh(data):
    if data is None or data.get("signature") != resolve.signature():
        return False
    entries, set_up = links.read_record(), keys.setup_done()
    roots = (files.state_dir(), files.PLUGIN_ROOT)
    # Before setup, a link the user deleted waits for setup's own apply (see links.pin_deleted).
    # A link whose root is another folder points into an earlier state folder, or a plugin copy that moved.
    return all((os.path.lexists(entry["path"]) or not set_up) and "state" not in entry
               and (entry["type"] != "link" or entry.get("root") in roots) for entry in entries)


def ensure_fresh(session=None):
    """resolved.json, after applying first when it is stale.

    When this run made ~/.claude/agents, the doctor's note about it is marked
    with the session, so that session's own start does not clear it.
    """
    if uninstall.is_out():
        raise uninstall.Uninstalled(uninstall.OUT)
    path = files.state_path(resolve.RESOLVED)
    data = files.read_json(path)
    if is_fresh(data):
        return data
    with files.locked():
        data = files.read_json(path)
        if not is_fresh(data):
            made_before = files.read_json(files.state_path(links.AGENTS_FOLDER_MADE))
            run()
            data = files.read_json(path)
            note = files.read_json(files.state_path(links.AGENTS_FOLDER_MADE))
            if session and note and not made_before:
                files.write_json(files.state_path(links.AGENTS_FOLDER_MADE), dict(note, session=session))
    return data


def cost(tool):
    return files.read_json(files.state_path(COST), {}).get(tool, {"parts": {}, "total": {"start_text": 0}})
