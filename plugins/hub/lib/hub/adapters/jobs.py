"""Named background jobs, on whatever job runner the machine has.

    install(Job(...))   write and load the job, or nothing when it is already so
    remove(name)        unload it and delete its files
    status(name)        whether it is recorded, its files exist and it is loaded
    last_run(name)      what the job runner says about the last run, or None
    sync(wanted)        install the wanted jobs and remove every other recorded one
    remove_all()        remove every recorded job, as uninstalling hub does

A job runs a command every_seconds, at_login, or keeps it running
(keep_alive). Its start command runs once, right after its first install.
Its stop command runs right after the job is removed, and up to three times
if it fails. The user sees what the stop prints. Both commands are kept in the
record, so uninstall still stops a job whose part is gone. The backends:

- launchd on macOS: ~/Library/LaunchAgents/hubctl.<name>.plist, loaded with
  `launchctl bootstrap gui/<uid>` and removed with `launchctl bootout`.
- systemd on Linux with a user session: hubctl-<name>.service, plus a .timer
  for every_seconds, in ~/.config/systemd/user, with `systemctl --user`.
- loop where neither runs: a detached process that runs the command, then
  sleeps. It ends with the computer, so it is not started again at login.
- none: the job is only recorded, and status says why it does not run.

A part's command should start with shim_command(), which runs hub's shim in
the state folder. Apply points the shim at the current plugin folder, so a
plugin update never leaves a job on an old path and never rewrites a job file.
The record is <state>/jobs.json. A part never calls launchctl or systemctl
itself.
"""
import dataclasses
import os
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

LIB = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files  # noqa: E402

RECORD = "jobs.json"
SHIM = os.path.join("bin", "hubctl")
BACKENDS = ("launchd", "systemd", "loop", "none")
TOOL_TIMEOUT = 10
HOOK_TIMEOUT = 120  # a stop command may post what waits, so it gets more time than a tool call
STOP_TRIES = 3
LOOP_NOTE = "a plain loop, which does not start again after the computer restarts"
NONE_NOTE = "no job runner was found or chosen, so this job does not run"


@dataclasses.dataclass
class Job:
    name: str
    part: str
    command: list
    every_seconds: int = 0
    at_login: bool = False
    keep_alive: bool = False
    start: list = dataclasses.field(default_factory=list)
    stop: list = dataclasses.field(default_factory=list)

    def spec(self):
        return {"part": self.part, "command": list(self.command), "every_seconds": int(self.every_seconds or 0),
                "at_login": bool(self.at_login), "keep_alive": bool(self.keep_alive), "start": list(self.start),
                "stop": list(self.stop)}


# where things are

def backend_for(sysname=None, which=shutil.which, env=None, preferred=None):
    """The backend for this machine, or the preferred one when it is a known name."""
    if preferred in BACKENDS:
        return preferred
    sysname = sysname or os.uname().sysname
    env = os.environ if env is None else env
    if sysname == "Darwin" and which("launchctl"):
        return "launchd"
    if sysname == "Linux" and which("systemctl") and env.get("XDG_RUNTIME_DIR"):
        return "systemd"
    return "loop"


def shim_path():
    return files.state_path(SHIM)


def shim_command(script, *args):
    """The command that runs a script of the current plugin copy, given relative to the plugin folder."""
    return [shim_path(), "--script", script, *args]


def write_shim(plugin_root=None):
    """Point the shim at plugin_root (the running copy by default). Returns True when it changed."""
    root = os.path.realpath(plugin_root or files.PLUGIN_ROOT)
    text = ("#!/bin/sh\n"
            "# Written by hub. Jobs run this, so a plugin update only moves the folder below.\n"
            f"root={shlex.quote(root)}\n"
            'if [ "$1" = "--script" ]; then script=$2; shift 2; exec python3 -I "$root/$script" "$@"; fi\n'
            'exec python3 -I "$root/lib/hub_switch.py" "$@"\n')
    path = shim_path()
    if files.read_text(path) == text:
        return False
    files.write_text(path, text)
    os.chmod(path, 0o755)
    return True


def label(name):
    return f"hubctl.{name}"


def plist_path(name):
    return os.path.join(files.home(), "Library", "LaunchAgents", label(name) + ".plist")


def unit_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(files.home(), ".config")
    return os.path.join(base, "systemd", "user")


def unit(name, suffix):
    return f"hubctl-{name}.{suffix}"


def log_path(name):
    return files.state_path(os.path.join("logs", f"job-{name}.log"))


def domain():
    return f"gui/{os.getuid()}"


def run_tool(args):
    """Run launchctl or systemctl from PATH. Returns the exit code, or None when the tool is missing."""
    program = shutil.which(args[0])
    if program is None:
        return None
    try:
        return subprocess.run([program, *args[1:]], capture_output=True, text=True, timeout=TOOL_TIMEOUT,
                              stdin=subprocess.DEVNULL).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


def tool_output(args):
    """The output of launchctl or systemctl, or None when it is missing or fails."""
    program = shutil.which(args[0])
    if program is None:
        return None
    try:
        done = subprocess.run([program, *args[1:]], capture_output=True, text=True, timeout=TOOL_TIMEOUT,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def run_hook(name, command):
    """Run a job's start or stop command once, with its output added to the job's log.
    Returns (exit code or None, what it printed)."""
    if not command:
        return None, ""
    os.makedirs(os.path.dirname(log_path(name)), exist_ok=True)
    try:
        process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=HOOK_TIMEOUT)
        code, output = process.returncode, process.stdout + process.stderr
    except subprocess.TimeoutExpired as error:
        code, output = None, "\n".join(part.decode(errors="replace") if isinstance(part, bytes) else part
                                       for part in (error.stdout, error.stderr) if part)
        output += f"\nstopped after {HOOK_TIMEOUT} s\n"
    except OSError as error:
        code, output = None, f"{error}\n"
    try:
        with open(log_path(name), "a") as log:
            log.write(output)
    except OSError:
        pass
    return code, output


# the record

def recorded():
    data = files.read_json(files.state_path(RECORD), {})
    return data if isinstance(data, dict) else {}


def _save(record):
    files.write_json(files.state_path(RECORD), record)


# the backends

def _launchd_text(name, spec):
    plist = {"Label": label(name), "ProgramArguments": spec["command"],
             "StandardErrorPath": log_path(name), "StandardOutPath": log_path(name)}
    if spec["every_seconds"]:
        plist["StartInterval"] = spec["every_seconds"]
    if spec["at_login"]:
        plist["RunAtLoad"] = True
    if spec["keep_alive"]:
        plist["KeepAlive"] = True
    return plistlib.dumps(plist).decode()


def _systemd_texts(name, spec):
    command = " ".join(shlex.quote(part) for part in spec["command"])
    service = ["[Unit]", f"Description=hub job {name} ({spec['part']})", "", "[Service]",
               "Type=simple" if spec["keep_alive"] else "Type=oneshot", f"ExecStart={command}"]
    if spec["keep_alive"]:
        service.append("Restart=always")
    if spec["at_login"] and not spec["every_seconds"]:
        service += ["", "[Install]", "WantedBy=default.target"]
    texts = {unit(name, "service"): "\n".join(service) + "\n"}
    if spec["every_seconds"]:
        seconds = spec["every_seconds"]
        texts[unit(name, "timer")] = "\n".join(
            ["[Unit]", f"Description=hub job {name} every {seconds} s", "", "[Timer]", f"OnBootSec={seconds}s",
             f"OnUnitActiveSec={seconds}s", f"Unit={unit(name, 'service')}", "", "[Install]",
             "WantedBy=timers.target"]) + "\n"
    return texts


def _systemd_main_unit(name, spec):
    return unit(name, "timer") if spec["every_seconds"] else unit(name, "service")


def _loop_script(spec):
    command = " ".join(shlex.quote(part) for part in spec["command"])
    pause = max(1, spec["every_seconds"] or 1)
    if spec["every_seconds"] or spec["keep_alive"]:
        return f"while :; do {command}; sleep {pause}; done"
    return command


def _start_loop(name, spec):
    os.makedirs(os.path.dirname(log_path(name)), exist_ok=True)
    with open(log_path(name), "a") as log:
        process = subprocess.Popen(["/bin/sh", "-c", _loop_script(spec)], stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=log, start_new_session=True)
    from hub import sessions
    info = sessions.process_info(process.pid) or {}
    return process.pid, info.get("start")


def process_runs(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    try:  # a loop this process started and that ended is a zombie until it is reaped
        done, _status = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def _loop_alive(entry):
    from hub import sessions
    return bool(entry.get("pid")) and process_runs(entry["pid"]) and sessions.alive(entry["pid"], entry.get("start"))


def _stop_loop(entry):
    if not _loop_alive(entry):
        return
    try:
        os.killpg(entry["pid"], signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + 5
    while time.time() < deadline and process_runs(entry["pid"]):
        time.sleep(0.05)


def _load(name, spec, backend):
    """Write the job's files and load it. Returns the record entry."""
    entry = dict(spec, backend=backend, files=[], written=time.time())
    if backend == "launchd":
        path = plist_path(name)
        files.write_text(path, _launchd_text(name, spec))
        if _launchd_loaded(name):  # bootstrap refuses a label that is still loaded
            run_tool(["launchctl", "bootout", f"{domain()}/{label(name)}"])
        entry["files"] = [path]
        entry["load_exit"] = run_tool(["launchctl", "bootstrap", domain(), path])
    elif backend == "systemd":
        for file_name, text in _systemd_texts(name, spec).items():
            files.write_text(os.path.join(unit_dir(), file_name), text)
            entry["files"].append(os.path.join(unit_dir(), file_name))
        run_tool(["systemctl", "--user", "daemon-reload"])
        entry["load_exit"] = run_tool(["systemctl", "--user", "enable", "--now", _systemd_main_unit(name, spec)])
    elif backend == "loop":
        entry["pid"], entry["start"] = _start_loop(name, spec)
    return entry


def _unload(name, entry):
    backend = entry.get("backend")
    if backend == "launchd":
        if _launchd_loaded(name):
            run_tool(["launchctl", "bootout", f"{domain()}/{label(name)}"])
    elif backend == "systemd":
        run_tool(["systemctl", "--user", "disable", "--now", _systemd_main_unit(name, entry)])
    elif backend == "loop":
        _stop_loop(entry)
    for path in entry.get("files", []):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    if backend == "systemd":
        run_tool(["systemctl", "--user", "daemon-reload"])


def _launchd_loaded(name):
    return run_tool(["launchctl", "print", f"{domain()}/{label(name)}"]) == 0


# the interface

def install(job, backend=None):
    """Make the job run as asked. Returns its record entry."""
    backend = backend_for(preferred=backend)
    if job.command and job.command[0] == shim_path():
        write_shim()
    spec = job.spec()
    with files.locked("jobs.lock"):
        record = recorded()
        old = record.get(job.name)
        same = old and old.get("backend") == backend and {key: old.get(key) for key in spec} == spec
        if same:
            now = status(job.name, record)
            if now["loaded"] == (backend != "none") and now["files_present"]:
                return old
        if old:
            _unload(job.name, old)
        record[job.name] = _load(job.name, spec, backend)
        _save(record)
        if not old:
            run_hook(job.name, spec["start"])
        return record[job.name]


def remove(name, lines=None):
    """Unload a recorded job and delete its files, then run its stop command, up to STOP_TRIES times until it
    exits 0. What the last try printed, and a line when every try failed, go to lines. Returns whether it was
    recorded."""
    with files.locked("jobs.lock"):
        record = recorded()
        entry = record.pop(name, None)
        if entry is None:
            return False
        _unload(name, entry)
        _save(record)
    if not entry.get("stop"):
        return True
    for _try in range(STOP_TRIES):
        code, output = run_hook(name, entry["stop"])
        if code == 0:
            break
    if lines is not None:
        lines += [line.strip() for line in output.splitlines() if line.strip()]
        if code != 0:
            lines.append(f"The stop command of job {name} failed {STOP_TRIES} times. Its output is in {log_path(name)}.")
    return True


def status(name, record=None):
    entry = (record if record is not None else recorded()).get(name)
    if entry is None:
        return {"name": name, "recorded": False, "loaded": False, "files_present": False, "backend": None,
                "detail": "not recorded"}
    backend = entry.get("backend")
    present = all(os.path.exists(path) for path in entry.get("files", []))
    if backend == "launchd":
        loaded, detail = _launchd_loaded(name), f"launchd agent {label(name)}"
    elif backend == "systemd":
        main = _systemd_main_unit(name, entry)
        loaded, detail = run_tool(["systemctl", "--user", "is-active", "--quiet", main]) == 0, f"systemd unit {main}"
    elif backend == "loop":
        loaded, detail = _loop_alive(entry), LOOP_NOTE
    else:
        loaded, detail = False, NONE_NOTE
    if not present:
        detail += "; a file of this job is missing, and saying \"apply hub's settings\" writes it again"
    return {"name": name, "recorded": True, "loaded": loaded, "files_present": present, "backend": backend,
            "detail": detail}


def last_run(name, record=None):
    """"last exit code N" from launchd or "last result R" from systemd, or None when the runner does not say."""
    entry = (record if record is not None else recorded()).get(name) or {}
    if entry.get("backend") == "launchd":
        found = re.search(r"last exit code = (.+)", tool_output(["launchctl", "print", f"{domain()}/{label(name)}"]) or "")
        return f"last exit code {found.group(1).strip()}" if found else None
    if entry.get("backend") == "systemd":
        result = (tool_output(["systemctl", "--user", "show", "-p", "Result", "--value", unit(name, "service")])
                  or "").strip()
        return f"last result {result}" if result else None
    return None


def sync(wanted, backend=None):
    """Install every wanted job and remove every other recorded one. Returns one line per change."""
    lines = []
    names = {job.name for job in wanted}
    for name in sorted(set(recorded()) - names):
        lines.append(f"job {name} removed")
        remove(name, lines)
    for job in wanted:
        before = recorded().get(job.name)
        after = install(job, backend)
        if after is not before and after != before:
            lines.append(f"job {job.name} installed ({after['backend']})")
    return lines


def remove_all():
    lines = []
    for name in sorted(recorded()):
        lines.append(f"job {name} removed")
        remove(name, lines)
    return lines
