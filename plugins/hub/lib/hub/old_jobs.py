"""Takes away the background jobs that older versions of hub ran.

hub used to run the queue's flush as a job: a launchd agent on macOS, a
systemd user timer on Linux, or a plain loop process. Each job called a shim
in hub's state folder, and a record in the state folder listed the jobs. hub
no longer runs anything outside Claude Code's own hooks, so apply and
uninstall call remove() to take all of that away. remove() costs one
lexists call per old file when nothing is left.

For each recorded job it unloads the job, deletes its files, and runs the
job's recorded stop command through this plugin copy, since the shim is
going. The queue's flush job is also found by its own plist or systemd
units when no record names it, so it goes even when the state folder does
not hold its record. A caller may pass restart, which gets the ids of the
parts whose stop started, so it can start them again. restart runs before
the record goes, so a crash in between leaves the record for the next run.
Then remove() deletes the shim, an empty bin folder, the record, the job
logs and the job lock. The names below are the ones older versions wrote.
"""
import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys

from . import files, sessions

RECORD = "jobs.json"
BIN = "bin"
SHIM = os.path.join(BIN, "hubctl")
FLUSH_JOB = "queue-flush"  # the one job older versions ran
LOCK = "jobs.lock"
LOGS = "logs"
LOG_NAME = re.compile(r"^job-[a-z][a-z0-9-]*\.log$")
JOB_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
LAUNCHD_LABEL = "hubctl.{name}"
LAUNCH_AGENTS = os.path.join("Library", "LaunchAgents")
SYSTEMD_UNIT = "hubctl-{name}.{suffix}"
# Files the queue's flush job wrote for itself. The held messages next to them stay.
QUEUE_JOB_FILES = (os.path.join("queue", "heartbeat"), os.path.join("queue", "config.json"))
TOOL_TIMEOUT = 10
STOP_TIMEOUT = 120  # the queue's stop posts what waits, so it gets more time than a tool call


def left_behind():
    """The old files that still exist in the state folder."""
    names = (RECORD, SHIM, LOCK, LOGS, *QUEUE_JOB_FILES)
    return [files.state_path(name) for name in names if os.path.lexists(files.state_path(name))]


def remove(restart=None):
    """Returns (lines to print, the ids of the parts whose old stop command finished).
    restart, when given, gets the ids of the parts whose stop started and returns the lines it printed."""
    if not left_behind() and not found_jobs([]):
        return [], set()
    recorded = recorded_jobs()
    entries = recorded + found_jobs(recorded)
    for name, entry in entries:
        unload(name, entry)
    stops = [(entry.get("part"), run_stop(entry.get("stop"))) for _name, entry in entries]
    started = {part for part, result in stops if result is not None}
    finished = {part for part, result in stops if result is not None and result[0]}
    restarted = restart(started) if restart and started else []
    for path in left_behind():
        delete(path)
    with contextlib.suppress(OSError):
        os.rmdir(files.state_path(BIN))  # only when empty, so a user's own file there stays
    lines = [f"hub removed the background job {name} that an older version ran" for name, _entry in entries]
    printed = [line for _part, result in stops if result for line in result[1]]
    return lines + printed + restarted, finished


def found_jobs(recorded):
    """[(name, entry)] for the queue's flush job when no record names it but its plist or units are left.
    Found this way it has no stop command, since only the record held one."""
    if any(name == FLUSH_JOB for name, _entry in recorded):
        return []
    if os.path.lexists(launchd_plist(FLUSH_JOB)):
        return [(FLUSH_JOB, {"backend": "launchd"})]
    service, timer = systemd_units(FLUSH_JOB)
    if os.path.lexists(service) or os.path.lexists(timer):
        return [(FLUSH_JOB, {"backend": "systemd", "every_seconds": os.path.lexists(timer)})]
    return []


def recorded_jobs():
    """[(name, entry)] for each well-formed entry of the old record."""
    try:
        data = files.read_json(files.state_path(RECORD), {})
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return sorted((name, entry) for name, entry in data.items()
                  if isinstance(name, str) and JOB_NAME.match(name) and isinstance(entry, dict))


def launchd_plist(name):
    return os.path.join(files.home(), LAUNCH_AGENTS, LAUNCHD_LABEL.format(name=name) + ".plist")


def systemd_units(name):
    folder = os.path.join(files.config_base(), "systemd", "user")
    return [os.path.join(folder, SYSTEMD_UNIT.format(name=name, suffix=suffix)) for suffix in ("service", "timer")]


def unload(name, entry):
    backend = entry.get("backend")
    if backend == "launchd":
        # Asked even when the plist is gone, since launchd may still hold the job. A job it does not hold fails.
        run_tool(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL.format(name=name)}"])
        delete(launchd_plist(name))
    elif backend == "systemd":
        suffix = "timer" if entry.get("every_seconds") else "service"
        run_tool(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT.format(name=name, suffix=suffix)])
        for path in systemd_units(name):
            delete(path)
        run_tool(["systemctl", "--user", "daemon-reload"])
    elif backend == "loop":
        stop_loop(entry.get("pid"), entry.get("start"))


def stop_loop(pid, start):
    if isinstance(pid, int) and sessions.alive(pid, start):
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGTERM)


def run_stop(command):
    """Run a recorded stop command [shim, "--script", script, *args] with this plugin copy's script.
    Returns (True when it exited with 0, the lines it printed), (False, []) when it ran out of time,
    or None when there is no such script or it could not start."""
    if not isinstance(command, list) or len(command) < 3 or command[1] != "--script" \
            or not all(isinstance(word, str) for word in command):
        return None
    script = os.path.normpath(os.path.join(files.PLUGIN_ROOT, command[2]))
    if not script.startswith(os.path.join(files.PLUGIN_ROOT, "parts") + os.sep) or not os.path.isfile(script):
        return None
    try:
        done = subprocess.run([sys.executable, "-I", script, *command[3:]], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=STOP_TIMEOUT)
    except OSError:
        return None
    except subprocess.TimeoutExpired:
        return False, []
    return done.returncode == 0, [line.strip() for line in done.stdout.splitlines() if line.strip()]


def run_tool(args):
    program = shutil.which(args[0])
    if program is None:
        return
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run([program, *args[1:]], stdin=subprocess.DEVNULL, capture_output=True, timeout=TOOL_TIMEOUT)


def delete(path):
    """Delete an old file, or the logs folder when it holds only job logs."""
    if os.path.isdir(path) and not os.path.islink(path):
        for name in os.listdir(path):
            if LOG_NAME.match(name):
                delete(os.path.join(path, name))
        with contextlib.suppress(OSError):
            os.rmdir(path)
        return
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
