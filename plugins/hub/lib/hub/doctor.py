"""hubctl doctor: checks the setup and names what needs the user.

- A local settings file that git tracks. Such a file comes with every clone,
  so hub treats it like a project file, and it can not lower a privacy flag.
- Start text that needs more than eight slots. It names each instruction that
  did not fit, in Claude Code, and in Cursor when the Cursor link runs.
- A user's agent with the same name as one of hub's, in Claude Code or in
  Cursor. hub keeps the user's file and makes no link or copy of its own.
- A Claude Code settings file that did not parse at the last apply, so a
  key hub writes there, such as outputStyle, stays as it is.
- A section marker in a shipped file that names a value its option does not take.
- ~/.claude/agents made by hub. Claude Code watches that folder only when it
  was there at the start of a session, so hub's agents show from the next one.
- A hub link whose target is gone, a program hub needs that is missing, a
  Claude Code version that is not in tested-versions.json, and
  disableAllHooks in any Claude Code settings file.
- hub's terminal link in ~/.local/bin while that folder is not on PATH. It
  names the shell file to add it to.
- A /hub that Claude Code gave to something else, such as the user's own
  skill named hub. The mod then takes another name and tells hubctl with
  `hubctl mod-command --json`, which keeps the record in mod-command.json.

report() also names what runs, the start text it costs, and each beta part
(untested, off by default) with whether it is on, as "beta" in the JSON. When the Cursor
link runs, it names each flag that runs in Claude Code but not in Cursor, with
the reason from its manifest. The text puts those flags in a "Runs in Claude
Code only:" section after the warnings. hubctl doctor --json gives the report
as JSON.

part_lines adds what each running part says about itself. A handler that
has doctor(flags) returns [(level, text)], with level "note" or "warning",
and each line starts with the part's id. A part that is on but runs only in
Claude Code gets a note with its reason from cursor_gaps when Cursor is
linked, and the log the hooks keep for the doctor is named with its newest
line. report() holds these lines and job_lines as "part_lines", and the text
prints each as "level: text" after the warnings.

job_lines adds a line for each background job hub recorded, loaded or not,
with what the job runner says about its last run. A job that is not loaded or
lost a file is a warning that names hubctl apply. While a chat part runs, it
also names the terminal hub sees (tmux, iTerm or plain).
"""
import json
import os
import shutil
import subprocess
import time

from . import apply, board, build, files, keys, links, resolve, settings, slots, tree as tree_module
from .adapters import jobs, terminal

TESTED = os.path.join(files.PLUGIN_ROOT, "tested-versions.json")
COMMAND_RECORD = "mod-command.json"
WANTED_COMMAND = "hub"
QUIET = ("Claude Code runs no hooks, so hub's hooks, start text slots and band stay quiet, but links, the switch skill "
         "and the web page still work.")


def warnings(cwd=None):
    found = []
    path = settings.scope_path("local", cwd)
    if path is not None and settings.shared("local", path):
        found.append(f"git tracks {path}, so hub treats it like a project file and ignores any privacy "
                     f"flag it lowers. If the file is only yours, stop tracking it with "
                     f"git rm --cached {path}")
    resolved = files.read_json(files.state_path("resolved.json"), {}) or {}
    cursor_runs = resolved.get("tools", {}).get("cursor", {}).get("flags", {}).get("cursor_link", {}).get("runs")
    for tool, name in (("claude_code", "Claude Code"), ("cursor", "Cursor")):
        if tool == "cursor" and not cursor_runs:
            continue
        left_out = slots.left_out(tool)
        if left_out:
            room = (f"more than {slots.COUNT} slots" if slots.count(tool) > 1
                    else f"more than the {slots.LIMIT:,} characters of one string")
            found.append(f"the start text in {name} needs {room}, so these "
                         f"instructions are left out: {', '.join(left_out)}. Switch off some start text "
                         f"to make room.")
    for clash in links.clashes():
        if clash.get("kind") == "not_a_file":
            found.append(f"{clash['path']} is not a plain file, so hub did not write it and leaves it as it is.")
            continue
        name = "Claude Code" if clash["tool"] == "claude_code" else "Cursor"
        found.append(f"your agent {clash['path']} has the name {clash['part']}, so in {name} hub made no "
                     f"{clash['skipped']} and your agent stays as it is.")
    for key, unread in sorted(keys.unreadable().items()):
        found.append(keys.unreadable_line(key, unread))
    found.extend(build.marker_problems(files.PARTS_DIR))
    made = files.read_json(files.state_path(links.AGENTS_FOLDER_MADE))
    if made:
        found.append(f"hub made {made.get('made', os.path.join(links.claude_dir(), 'agents'))}, so hub's "
                     f"agents show from the next new session.")
    return found


def cursor_gaps(data=None):
    """{part, flag, tool, reason} for each flag that runs in Claude Code, not in Cursor, and says why.

    Empty unless the Cursor link runs. data is the resolved state. Without it, the last resolved.json is used.
    """
    if data is None:
        data = files.read_json(files.state_path("resolved.json"), {}) or {}
    claude = data.get("tools", {}).get("claude_code", {}).get("flags", {})
    cursor = data.get("tools", {}).get("cursor", {}).get("flags", {})
    if not cursor.get("cursor_link", {}).get("runs"):
        return []
    found = []
    for flag_id, node in tree_module.load(files.PARTS_DIR).nodes.items():
        reason = (node.data.get("reason_not_in") or {}).get("cursor")
        if reason and claude.get(flag_id, {}).get("runs") and not cursor.get(flag_id, {}).get("runs"):
            found.append({"part": flag_id.split(".")[0], "flag": flag_id, "tool": "cursor", "reason": reason})
    return found


def note_command(record, folder):
    """Keep what the mod's last session start found: the name it got, and each name refused and why.

    A record with nothing refused clears the clash.
    """
    if not isinstance(record, dict):
        record = {"refused": None}
    used, refused = record.get("used"), record.get("refused")
    if not (used is None or isinstance(used, str)) or not isinstance(refused, dict) or not all(
            isinstance(name, str) and isinstance(reason, str) for name, reason in refused.items()):
        raise ValueError('mod-command takes {"used": a name or null, "refused": {name: reason}}')
    path = files.state_path(COMMAND_RECORD)
    if not refused:
        if os.path.exists(path):
            os.remove(path)
        return None
    kept = {"used": used, "refused": refused, "folder": folder, "time": resolve.now()}
    files.write_json(path, kept)
    return kept


def command_clash():
    """The clash the mod last found, or None."""
    record = files.read_json(files.state_path(COMMAND_RECORD))
    return record if isinstance(record, dict) and record.get("refused") else None


def clash_text(record):
    refused = record["refused"]
    given = [f"/{name} ({reason})" for name, reason in refused.items()]
    said = f"In {record['folder']}, Claude Code gave {' and '.join(given)} to something else"
    if record["used"]:
        return f"{said}, so hub's command there is /{record['used']}. Rename the other /{WANTED_COMMAND} to get it back."
    return (f"{said}, so hub has no slash command there. Rename the other "
            f"/{WANTED_COMMAND}, then use /{WANTED_COMMAND} in Claude Code.")


def claude_code_version():
    """The version `claude --version` prints, or None when it can not be read."""
    if shutil.which("claude") is None:
        return None
    try:
        answer = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = answer.stdout.split()
    return words[0] if answer.returncode == 0 and words else None


def settings_files(cwd):
    paths = [os.path.join(links.claude_dir(), "settings.json"), os.path.join(links.claude_dir(), "settings.local.json")]
    repo = settings.find_repo(cwd)
    if repo:
        paths += [os.path.join(repo, ".claude", "settings.json"), os.path.join(repo, ".claude", "settings.local.json")]
    return paths


def problems(cwd=None):
    """Each problem as {kind, text}, one line that says what to do."""
    cwd = cwd or os.getcwd()
    found = [{"kind": "warning", "text": line} for line in warnings(cwd)]
    for entry in links.read_record():
        path = entry["path"]
        if os.path.islink(path) and not os.path.exists(path):
            found.append({"kind": "broken_link", "text": f"the hub link {path} points to {os.readlink(path)}, "
                          "which is gone. Say \"apply hub's settings\" to make it again."})
    for program in ("git", "python3", "claude"):
        if shutil.which(program) is None:
            found.append({"kind": "missing_program", "text": f"{program} is not on PATH, so the parts that need it "
                          f"do not run. Install {program}, then say \"apply hub's settings\"."})
    clash = command_clash()
    if clash:
        found.append({"kind": "command_clash", "text": clash_text(clash)})
    tested = files.read_json(TESTED, {}) or {}
    version = claude_code_version()
    if version and version not in tested.get("claude_code", []):
        listed = ", ".join(tested.get("claude_code", [])) or "none"
        found.append({"kind": "untested_version", "text": f"hub is tested with Claude Code {listed}, not with "
                      f"{version}. Most parts still work. Say \"update hub\" to get a newer hub."})
    for path in settings_files(cwd):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("disableAllHooks") is True:
            found.append({"kind": "hooks_disabled", "text": f"{path} sets disableAllHooks. {QUIET} "
                          "Remove the key to turn hooks back on."})
    return found


def report(cwd=None):
    cwd = cwd or os.getcwd()
    data = resolve.current(cwd)
    tree = tree_module.load(files.PARTS_DIR)
    flags = data["tools"]["claude_code"]["flags"]
    found = {"runs": [part.id for part in tree.parts if part.family != "core" and flags[part.id]["runs"]],
             "start_text": apply.cost("claude_code")["total"]["start_text"],
             "beta": [{"part": part.id, "runs": flags[part.id]["runs"]}
                      for part in tree.parts if part.stage == tree_module.BETA],
             "cursor_gaps": cursor_gaps(data), "problems": problems(cwd),
             "part_lines": [{"level": level, "text": text} for level, text in part_lines(cwd) + job_lines(cwd)]}
    # The band's failed checks counter reads this, so the band never runs the checks itself.
    failed = len(found["problems"]) + sum(1 for line in found["part_lines"] if line["level"] == "warning")
    files.write_json(files.state_path(board.DOCTOR_LAST), {"time": time.time(), "failed": failed})
    return found


def render(found):
    lines = [f"These parts run: {', '.join(found['runs'])}" if found["runs"] else "No part runs.",
             f"The start text costs {found['start_text']:,} characters at the start of each session."]
    if found["beta"]:
        states = ", ".join(f"{entry['part']} is {'on' if entry['runs'] else 'off'}" for entry in found["beta"])
        lines.append(f"{tree_module.BETA_LABEL}: {states}.")
    lines += [f"warning: {problem['text']}" for problem in found["problems"]]
    lines += [f"{line['level']}: {line['text']}" for line in found["part_lines"]]
    if not found["problems"] and all(line["level"] != "warning" for line in found["part_lines"]):
        lines.append("No problems found.")
    if found["cursor_gaps"]:
        lines.append("Runs in Claude Code only:")
        lines += [f"  {gap['flag']}: {gap['reason']}" for gap in found["cursor_gaps"]]
    return "\n".join(lines) + "\n"


def doctor_log_line():
    path = files.state_path("doctor.log")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    count = f"{len(lines)} line" + ("" if len(lines) == 1 else "s")
    return f"hub's hooks wrote {count} to {path}. The newest is \"{lines[-1]}\""


def part_lines(cwd=None):
    """[(level, text)] about the parts that run, in tree order, then the doctor log."""
    from . import dispatch  # the dispatcher's Flags and handler loader, only when the doctor runs

    data = resolve.current(cwd)
    section = data["tools"]["claude_code"]
    records = section["flags"]
    only_in_claude = {gap["flag"]: gap["reason"] for gap in cursor_gaps(data)}
    flags = dispatch.Flags(section, data.get("texts", {}), "claude_code", resolve.load_profile())
    found = []
    for part in tree_module.load(files.PARTS_DIR).parts:
        if not records.get(part.id, {}).get("runs"):
            continue
        if os.path.isfile(os.path.join(files.PARTS_DIR, part.id, "handler.py")):
            try:
                handler = dispatch.load_handler(part.id)
                own = handler.doctor(flags) if hasattr(handler, "doctor") else []
            except Exception as error:  # one broken part never hides the others
                own = [("warning", f"its own check failed ({type(error).__name__}).")]
            found.extend((level, f"{part.id}: {text}") for level, text in own)
        reason = only_in_claude.get(part.id)
        if reason:
            found.append(("note", f"{part.id}: it is on but runs only in Claude Code. {reason}"))
    log = doctor_log_line()
    if log:
        found.append(("note", log))
    return found


def job_lines(cwd=None):
    """[(level, text)] for the recorded jobs, then the terminal while a chat part runs."""
    found = []
    record = jobs.recorded()
    for name in sorted(record):
        state = jobs.status(name, record)
        where = f"job {name} ({record[name].get('part', '?')})"
        if not state["files_present"]:
            found.append(("warning", f"{where}: a file of this job is missing. Say \"apply hub's settings\" to write it again."))
        elif state["backend"] == "none":
            found.append(("note", f"{where} does not run: {state['detail']}."))
        elif not state["loaded"]:
            found.append(("warning", f"{where} is not loaded ({state['detail']}). Say \"apply hub's settings\" to load it."))
        else:
            last = jobs.last_run(name, record)
            found.append(("note", f"{where} is loaded ({state['detail']})" + (f", {last}." if last else ".")))
    data = resolve.current(cwd)
    flags = data["tools"]["claude_code"]["flags"]
    chats = [part for part in tree_module.load(files.PARTS_DIR).parts
             if part.family == "chats" and flags.get(part.id, {}).get("runs")]
    if chats:
        found.append(("note", f"terminal: {terminal.detect()}"))
    return found
