"""Apply: everything that follows from the settings, written in one go.

    run(tree)        the caller holds the settings lock
    ensure_fresh()   takes the lock and runs apply only when it is stale

A run turns off, pinned, each part whose hub link the user deleted or whose
Claude Code key the user changed (see keys.pin_changed), resolves
every flag, makes and removes the links and built copies, sets outputStyle
while hub_style runs (see keys.py), writes the slot
files and cost.json, marks the parts of the hook groups that start Python
only when one may run (see pre_check.sh), installs the background jobs whose
flags run and removes the others (see adapters/jobs.py), adds a line to
applies.jsonl, and writes resolved.json
last. So a hook that finds resolved.json fresh also finds the files built
from it. It returns the lines hubctl apply prints. While cursor_link does
not run, apply writes no Cursor slots and removes the old ones, since Cursor
support is beta and off by default.

After hubctl uninstall, apply changes nothing until a new copy of hub is
installed (see uninstall.is_out), and ensure_fresh raises Uninstalled.

Apply is stale when resolved.json is missing, a settings file, the profile or
a manifest changed since, the plugin copy moved, a recorded link is gone, or
an apply stopped partway and left a step marked in the record.
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
CURSOR_PART = "cursor_link"


def run(tree=None):
    if uninstall.is_out():
        return [uninstall.OUT]
    uninstall.clear_old_mark()
    lines = []
    links.pin_deleted(lines)
    keys.pin_changed(lines)
    tree = tree or tree_module.load(files.PARTS_DIR)
    data = resolve.apply(tree, write=False)
    listing = links.reconcile(tree, data, lines)
    set_style(data, lines)
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
    from .adapters import jobs  # only an apply needs the job code
    lines += jobs.sync(wanted_jobs(tree, data))
    files.append_line(files.state_path(APPLIES), {"time": resolve.now(), "lines": len(lines)})
    files.write_json(files.state_path(resolve.RESOLVED), data)
    mark_group_parts(data)
    return lines


def set_style(data, lines):
    """outputStyle names hub's style while hub_style runs in Claude Code, else the user's earlier value."""
    if data["tools"]["claude_code"]["flags"].get(STYLE_PART, {}).get("runs"):
        keys.want(STYLE_KEY, STYLE_NAME, STYLE_PART, lines)
    else:
        keys.release(STYLE_KEY, lines)


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


def wanted_jobs(tree, data):
    """The jobs that the parts name in part.json, for each job whose flag runs in some tool."""
    def record(flag_id):
        for tool in tree_module.TOOLS:
            found = data["tools"][tool]["flags"].get(flag_id)
            if found and found["runs"]:
                return found
        return None

    from .adapters import jobs
    wanted = []
    for part in tree.parts:
        for spec in part.data.get("jobs", []):
            if record(spec["flag"]) is None:
                continue
            every = spec.get("every_seconds", 0)
            if isinstance(every, str):
                every = (record(every) or data["tools"]["claude_code"]["flags"].get(every, {})).get("value") or 0
            script = "/".join(["parts", part.id, spec["script"]])
            wanted.append(jobs.Job(
                name=spec["name"], part=part.id, command=jobs.shim_command(script, *spec.get("args", [])),
                every_seconds=max(0, int(every)), at_login=spec.get("at_login", False),
                keep_alive=spec.get("keep_alive", False),
                start=jobs.shim_command(script, *spec["start"]) if spec.get("start") else [],
                stop=jobs.shim_command(script, *spec["stop"]) if spec.get("stop") else []))
    return wanted


def is_fresh(data):
    if data is None or data.get("signature") != resolve.signature():
        return False
    entries = links.read_record()
    return all(os.path.lexists(entry["path"]) and "state" not in entry for entry in entries)


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
