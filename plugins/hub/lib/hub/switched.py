"""The commands a part runs when it is switched on or off.

A part names them in part.json under when_switched: a script in its folder,
the arguments for "on" and the arguments for "off". Apply runs "off" for a
part that ran at the last apply and no longer runs, and "on" for a part that
runs now and did not before. Uninstall runs "off" for every part that runs.
The queue uses this to send what it holds before it stops.

Each command runs as its own process with python3 -I, because apply also
runs inside hooks, where this process's output is the hook's answer. What it
prints goes to the lines apply and uninstall print.
"""
import os
import subprocess
import sys

from . import files, tree as tree_module

TIMEOUT_SECONDS = 120  # the queue's off step posts what waits, so it gets the time a post may take


def running_parts(data):
    """The ids of the parts that run in some tool in a resolved.json, or an empty set for none."""
    if not isinstance(data, dict):
        return set()
    return {flag_id for tool in tree_module.TOOLS
            for flag_id, record in ((data.get("tools") or {}).get(tool) or {}).get("flags", {}).items()
            if "." not in flag_id and isinstance(record, dict) and record.get("runs")}


def changes(tree, before, after):
    """[(part, "on" or "off")] from the parts that ran before and the parts that run after."""
    named = [part for part in tree.parts if part.data.get("when_switched")]
    offs = [(part, "off") for part in named if part.id in before and part.id not in after]
    ons = [(part, "on") for part in named if part.id in after and part.id not in before]
    return offs + ons


def restart(tree, ids):
    """Run "on" for the parts in ids, such as those an older hub's job just stopped. Returns the lines printed."""
    return [line for part in tree.parts if part.id in ids and part.data.get("when_switched")
            for line in run(part, "on")]


def run(part, which):
    """Run the part's on or off command. Returns the lines it printed."""
    spec = part.data["when_switched"]
    script = os.path.join(files.PARTS_DIR, part.id, spec["script"])
    try:
        done = subprocess.run([sys.executable, "-I", script, *spec.get(which, [])], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return [f"{part.id}: its {which} step did not finish ({type(error).__name__})"]
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]
