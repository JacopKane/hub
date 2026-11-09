#!/usr/bin/env python3
"""A recording stand-in for launchctl, systemctl, osascript, caffeinate and tmux.

Copy it into a folder that comes first on PATH under each tool's name. It
picks its part by the name it runs as. Every call adds a line
{"tool", "args", "stdin"} to $HUB_FAKE_TOOLS/calls.jsonl, and the tools that
keep state (what launchd loaded, what systemd runs) keep it in
$HUB_FAKE_TOOLS/state.json, so a test reads back what the adapter asked for
and the adapter reads back what it loaded. It never reaches a real job runner,
terminal or app.

osascript answers from $HUB_FAKE_TOOLS/iterm.json, a list of [session id, tty]
for each iTerm session, when the script asks for ttys. A script for one
session answers OK only when that session exists, and a row may add a pane
of a private tmux server that stands in for the tab. tmux always says no
server runs, for tests that must not reach a real tmux.
"""
import json
import os
import plistlib
import sys

FOLDER = os.environ["HUB_FAKE_TOOLS"]
TOOL = os.path.basename(sys.argv[0])
ARGS = sys.argv[1:]


def load(name, missing):
    try:
        with open(os.path.join(FOLDER, name)) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return missing


def save_state(state):
    with open(os.path.join(FOLDER, "state.json"), "w") as handle:
        json.dump(state, handle)


stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with open(os.path.join(FOLDER, "calls.jsonl"), "a") as handle:
    handle.write(json.dumps({"tool": TOOL, "args": ARGS, "stdin": stdin}) + "\n")
state = load("state.json", {"launchd": [], "systemd": []})

if TOOL == "launchctl":
    verb = ARGS[0] if ARGS else ""
    if verb == "bootstrap":
        with open(ARGS[2], "rb") as handle:
            label = plistlib.load(handle)["Label"]
        if label in state["launchd"]:
            print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
            sys.exit(5)
        state["launchd"].append(label)
        save_state(state)
    elif verb == "bootout":
        label = ARGS[1].rsplit("/", 1)[-1]
        if label not in state["launchd"]:
            print("Boot-out failed: 3: No such process", file=sys.stderr)
            sys.exit(3)
        state["launchd"].remove(label)
        save_state(state)
    elif verb == "print":
        label = ARGS[1].rsplit("/", 1)[-1]
        if label not in state["launchd"]:
            print(f"Could not find service \"{label}\"", file=sys.stderr)
            sys.exit(113)
        print(f"{ARGS[1]} = {{\n\tstate = waiting\n\tlast exit code = 0\n}}")
    sys.exit(0)

if TOOL == "systemctl":
    words = [word for word in ARGS if not word.startswith("--")]
    verb, units = (words[0], words[1:]) if words else ("", [])
    if verb == "enable":
        state["systemd"] = sorted(set(state["systemd"]) | set(units))
    elif verb == "disable":
        state["systemd"] = sorted(set(state["systemd"]) - set(units))
    elif verb == "show":  # only -p Result --value, for the doctor
        print("success" if any(unit.endswith(".service") for unit in units) else "")
    elif verb == "is-active":
        active = all(unit in state["systemd"] for unit in units)
        print("active" if active else "inactive")
        sys.exit(0 if active else 3)
    save_state(state)
    sys.exit(0)

if TOOL == "osascript":
    import re
    import subprocess
    script = " ".join(ARGS) + stdin
    if "tty of" in script:
        for session, tty, *_ in load("iterm.json", []):
            print(f"{session}\t{tty}")
        sys.exit(0)
    # A script for one session: answer OK only when that session exists. A row
    # may name a pane of a private tmux server {"tmux": name, "pane": id} that
    # stands in for the tab, so its screen and keys are real.
    wanted = re.search(r'if id of each_session is "((?:[^"\\]|\\.)*)" then', script)
    rows = {row[0]: (row[2] if len(row) > 2 else None) for row in load("iterm.json", [])}
    if not wanted or wanted.group(1) not in rows:
        print("NONE")
        sys.exit(0)
    pane = rows[wanted.group(1)]

    def tmux(*words):
        own = os.path.dirname(os.path.abspath(sys.argv[0]))
        folders = [folder for folder in os.environ["PATH"].split(os.pathsep) if os.path.abspath(folder) != own]
        real = next((os.path.join(folder, "tmux") for folder in folders
                     if os.access(os.path.join(folder, "tmux"), os.X_OK)), None)
        if pane and real:
            return subprocess.run([real, "-L", pane["tmux"], *words], capture_output=True, text=True).stdout
        return ""

    text = re.search(r'write text "((?:[^"\\]|\\.)*)"( newline NO)?', script)
    key = re.search(r"write text \(character id (\d+)\) newline NO", script)
    if "contents of each_session" in script:
        print(tmux("capture-pane", "-p", "-t", pane["pane"]) if pane else "", end="")
    elif key:
        tmux("send-keys", "-t", pane and pane["pane"], {"27": "Escape", "127": "BSpace"}.get(key.group(1), "Enter"))
        print("OK")
    elif text:
        typed = re.sub(r"\\(.)", r"\1", text.group(1))
        if typed:
            tmux("send-keys", "-t", pane and pane["pane"], "-l", typed)
        if not text.group(2):
            tmux("send-keys", "-t", pane and pane["pane"], "Enter")
        print("OK")
    else:
        print("OK")
    sys.exit(0)

if TOOL == "tmux":
    print("no server running on /tmp/tmux-fake/default", file=sys.stderr)
    sys.exit(1)

sys.exit(0)  # caffeinate and anything else: recorded only
