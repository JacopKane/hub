"""Fixtures for the behavior scenarios.

`home` is a fresh temporary HOME with a copy of the plugin, its own config,
state and git config, and a `claude` replay first on PATH. launchctl,
systemctl, caffeinate and osascript are the recording fakes in
tests/fakes/os_tools.py, with their calls in $HUB_FAKE_TOOLS, so no test loads
a real job, keeps the machine awake or drives a terminal. XDG_RUNTIME_DIR is
set, so on Linux the job adapter picks systemd and never starts a real loop.
Every program runs as a real process. `tool` runs a scenario once with Claude Code's hook input
and once with Cursor's, through the adapter.

pytest's own process also gets a temporary HOME and XDG folders, for code a
test imports. At the end of the run, hub's real settings and state folders
must still be absent if they were absent at the start, and the real Claude
Code and Cursor folders must hold no hub- entry they did not hold at the
start, or the run fails.
"""
import hashlib
import itertools
import json
import os
import pty
import pwd
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO, "tests", "fixtures")
PLUGIN = os.path.join(REPO, "plugins", "hub")
DASH = chr(0x2014)  # the em dash, never typed as itself in this repo

CURSOR_EVENTS = {"PreToolUse": "preToolUse", "PostToolUse": "postToolUse", "SessionStart": "sessionStart",
                 "UserPromptSubmit": "beforeSubmitPrompt", "Stop": "stop"}
FINDING = re.compile(r"\(public_text_gate\.([a-z_]+)\)")
TOOL_CALL_MARKERS = ("AI_AGENT", "CURSOR_AGENT")
XDG_FOLDERS = {"XDG_CONFIG_HOME": ".config", "XDG_STATE_HOME": os.path.join(".local", "state"),
               "XDG_DATA_HOME": os.path.join(".local", "share"), "XDG_CACHE_HOME": ".cache"}


def commit(message):
    return "git commit -m " + shlex.quote(message)


def real_home():
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def watched_folders():
    """hub's real settings and state folders, from the real home and the XDG values pytest started with."""
    folders = {os.path.join(real_home(), ".config", "hubctl"), os.path.join(real_home(), ".local", "state", "hubctl")}
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        if os.environ.get(name):
            folders.add(os.path.join(os.environ[name], "hubctl"))
    return sorted(folders)


TOOL_FOLDERS = [os.path.join(".claude", "skills"), os.path.join(".claude", "agents"),
                os.path.join(".cursor", "agents"), os.path.join(".cursor", "plugins", "local")]


def hub_entries(home):
    """The hub- entries in the tool folders of a home, and Cursor's plugins/local/hub folder."""
    found = set()
    for folder in TOOL_FOLDERS:
        path = os.path.join(home, folder)
        if os.path.isdir(path):
            found.update(os.path.join(path, name) for name in os.listdir(path)
                         if name.startswith("hub-") or (name == "hub" and folder.endswith("local")))
    return found


def new_hub_entries(before, after):
    return sorted(after - before)


def pytest_configure(config):
    config.addinivalue_line("markers", "lab: runs the real claude binary against a local mock API (tests/lab)")


def pytest_ignore_collect(collection_path, config):
    """tests/lab runs the real claude binary, so only `-m lab` collects it."""
    lab = os.path.join(REPO, "tests", "lab")
    inside = str(collection_path) == lab or str(collection_path).startswith(lab + os.sep)
    if inside and "lab" not in (config.getoption("markexpr", default="") or ""):
        return True
    return None


def pytest_sessionstart(session):
    session.config.hub_absent = [folder for folder in watched_folders() if not os.path.exists(folder)]
    session.config.hub_entries = hub_entries(real_home())


def pytest_sessionfinish(session, exitstatus):
    leaked = [folder for folder in getattr(session.config, "hub_absent", []) if os.path.exists(folder)]
    leaked += new_hub_entries(getattr(session.config, "hub_entries", set()), hub_entries(real_home()))
    if leaked:
        print("\nA test wrote into a real hub folder: " + ", ".join(leaked), file=sys.stderr)
        session.exitstatus = 1


class HookResult:
    def __init__(self, tool, process):
        self.tool = tool
        self.process = process
        self.output = json.loads(process.stdout) if process.stdout.strip() else {}
        if tool == "claude_code":
            specific = self.output.get("hookSpecificOutput", {})
            self.refused = specific.get("permissionDecision") == "deny"
            self.reason = specific.get("permissionDecisionReason", "")
            self.context = specific.get("additionalContext", "")
        else:
            self.refused = self.output.get("permission") == "deny"
            self.reason = self.output.get("agent_message", "") if self.refused else ""
            self.context = "" if self.refused else self.output.get("agent_message", "")
        self.findings = FINDING.findall(self.reason)

    @property
    def ran(self):
        return not self.refused


class Home:
    _ids = itertools.count(1)

    def __init__(self, root):
        root = os.path.realpath(str(root))
        if root == real_home() or root.startswith(real_home() + os.sep + ".") or root == os.path.realpath(
            os.environ.get("HOME", "")
        ):
            raise RuntimeError("refusing to run in the real HOME")
        self.root = root
        self.plugin = os.path.join(root, "plugin-copy", "hub")
        self.config = os.path.join(root, ".config", "hubctl")
        self.state = os.path.join(root, ".local", "state", "hubctl")
        self.work = os.path.join(root, "work", "demo")
        replay = os.path.join(root, "replay-bin")
        os.makedirs(replay)
        shutil.copy(os.path.join(FIXTURES, "claude_replay.py"), os.path.join(replay, "claude"))
        os.chmod(os.path.join(replay, "claude"), 0o755)
        # No test may reach a real job runner, terminal or keep-awake: these are the recording fakes.
        for name in ("launchctl", "systemctl", "caffeinate", "osascript"):
            shutil.copy(os.path.join(REPO, "tests", "fakes", "os_tools.py"), os.path.join(replay, name))
            os.chmod(os.path.join(replay, name), 0o755)
        os.makedirs(os.path.join(root, "fake-tools"))
        os.makedirs(os.path.join(root, "run"), mode=0o700)
        with open(os.path.join(root, ".gitconfig"), "w") as handle:
            handle.write("[user]\n\tname = Sam\n\temail = sam@example.com\n[init]\n\tdefaultBranch = main\n")
        self.env = {
            "HOME": root,
            "PATH": replay + os.pathsep + os.environ.get("PATH", ""),
            "GIT_CONFIG_GLOBAL": os.path.join(root, ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LANG": "C.UTF-8",
            "HUB_REPLAY_DIR": os.path.join(FIXTURES, "claude_replay"),
            "HUB_FAKE_TOOLS": os.path.join(root, "fake-tools"),
            "XDG_RUNTIME_DIR": os.path.join(root, "run"),
            **{name: os.path.join(root, folder) for name, folder in XDG_FOLDERS.items()},
        }
        os.makedirs(self.work)
        self.git("init", "-q", cwd=self.work)
        self.git("commit", "-q", "--allow-empty", "-m", "start", cwd=self.work)

    # programs

    def run(self, args, cwd=None, stdin=None, check=True):
        process = subprocess.run(
            args, cwd=cwd or self.work, env=self.env, input=stdin, capture_output=True, text=True, timeout=60
        )
        if check and process.returncode != 0:
            raise AssertionError(f"{args} exited {process.returncode}\n{process.stdout}\n{process.stderr}")
        return process

    def git(self, *args, cwd=None):
        return self.run(["git", *args], cwd=cwd)

    def record_claude(self):
        """Put a `claude` first on PATH that records each call. Returns the calls file."""
        shutil.copy(os.path.join(REPO, "tests", "fakes", "claude_recorder.py"),
                    os.path.join(self.root, "replay-bin", "claude"))
        return os.path.join(self.root, "claude-calls.jsonl")

    def claude_calls(self):
        path = os.path.join(self.root, "claude-calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def install(self, source=PLUGIN):
        shutil.copytree(source, self.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        self.hubctl("apply")

    def hubctl(self, *args, cwd=None, check=True):
        return self.run([sys.executable, os.path.join(self.plugin, "bin", "hubctl"), *args], cwd=cwd, check=check)

    def set_by(self, by, flag_id, value):
        """Write a value the way setup or the tuner does, through changes.set_flag with that setter."""
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from hub import changes; "
                "changes.set_flag(sys.argv[2], json.loads(sys.argv[3]), by=sys.argv[4], door=sys.argv[4])")
        return self.run([sys.executable, "-I", "-c", code, os.path.join(self.plugin, "lib"), flag_id,
                         json.dumps(value), by])

    def hubctl_as_user(self, *args, typed=None, cwd=None, check=True):
        """hubctl as the user runs it in their own terminal, which can raise a protected flag but not lower it.

        No tool-call mark and no mod mark, and stdin is a terminal (a pty) with one
        line typed into it: the word after the verb, unless `typed` says what to type.
        hubctl never reads a terminal, so the typed line shows that typing opens no door.
        """
        env = {name: value for name, value in self.env.items() if name not in (*TOOL_CALL_MARKERS, "HUB_DOOR")}
        controller, terminal = pty.openpty()
        try:
            process = subprocess.Popen(
                [sys.executable, os.path.join(self.plugin, "bin", "hubctl"), *args], cwd=cwd or self.work,
                env=env, stdin=terminal, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            os.close(terminal)
            terminal = None
            answer = typed if typed is not None else (args[1] if len(args) > 1 else "")
            os.write(controller, (answer + "\n").encode())
            stdout, stderr = process.communicate(timeout=60)
        finally:
            if terminal is not None:
                os.close(terminal)
            os.close(controller)
        done = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        if check and done.returncode != 0:
            raise AssertionError(f"{args} exited {done.returncode}\n{stdout}\n{stderr}")
        return done

    def mod_ticket(self, age_seconds=0):
        """A door ticket as the mod makes one: random bytes, and a file named by their sha256 in the doors folder.

        age_seconds makes the file that much older, as a ticket the mod made a while ago.
        """
        ticket = secrets.token_hex(32)
        folder = os.path.join(self.state, "doors")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, hashlib.sha256(ticket.encode()).hexdigest())
        with open(path, "w") as handle:
            handle.write("{}")
        if age_seconds:
            then = time.time() - age_seconds
            os.utime(path, (then, then))
        return ticket

    def hubctl_from_mod(self, *args, ticket=None, cwd=None, check=True):
        """hubctl as the mod runs it for a change the user typed or clicked: no tool-call marker, no
        terminal, and a fresh door ticket on stdin unless `ticket` gives the line to send."""
        env = {name: value for name, value in self.env.items() if name not in (*TOOL_CALL_MARKERS, "HUB_DOOR")}
        line = self.mod_ticket() if ticket is None else ticket
        process = subprocess.run([sys.executable, "-I", os.path.join(self.plugin, "bin", "hubctl"), *args],
                                 cwd=cwd or self.work, env=env, input=line + "\n", capture_output=True, text=True,
                                 timeout=60)
        if check and process.returncode != 0:
            raise AssertionError(f"{args} exited {process.returncode}\n{process.stdout}\n{process.stderr}")
        return process

    def hubctl_from_mod_started(self, *args):
        """hubctl started as hubctl_from_mod starts it, with its fresh ticket already sent, and not waited
        for, so several can run at once."""
        env = {name: value for name, value in self.env.items() if name not in (*TOOL_CALL_MARKERS, "HUB_DOOR")}
        process = subprocess.Popen([sys.executable, "-I", os.path.join(self.plugin, "bin", "hubctl"), *args],
                                   cwd=self.work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        process.stdin.write(self.mod_ticket() + "\n")
        process.stdin.close()
        return process

    def hubctl_process(self, *args):
        return subprocess.Popen(
            [sys.executable, os.path.join(self.plugin, "bin", "hubctl"), *args],
            cwd=self.work, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def hook_input(self, tool, event, command, cwd=None, session=None, tool_use_id=None):
        tool_use_id = tool_use_id or f"call_{next(self._ids):06d}"
        cwd = cwd or self.work
        if tool == "claude_code":
            data = load_fixture("claude_code", f"{event}.Bash.json")
            data.update(cwd=cwd, tool_use_id=tool_use_id)
            data["tool_input"] = {"command": command, "description": "Run it"}
            if session:
                data["session_id"] = session
        else:
            data = load_fixture("cursor", f"{CURSOR_EVENTS[event]}.Shell.json")
            data.update(cwd=cwd, tool_use_id=tool_use_id, workspace_roots=[cwd])
            data["tool_input"] = {"command": command, "working_directory": cwd}
            if session:
                data["conversation_id"] = session
        return data

    def hook(self, tool, event, command, errors_expected=False, **options):
        data = self.hook_input(tool, event, command, **options)
        return self.send(tool, event, data, errors_expected=errors_expected)

    def send(self, tool, event, data, errors_expected=False):
        if tool == "claude_code":
            script = os.path.join(self.plugin, "lib", "hub", "dispatch.py")
            args = [sys.executable, "-I", script, event]
        else:
            script = os.path.join(self.plugin, "lib", "hub", "adapters", "cursor_hooks.py")
            args = [sys.executable, "-I", script, CURSOR_EVENTS[event]]
        process = self.run(args, cwd=data.get("cwd"), stdin=json.dumps(data))
        errors = os.path.join(self.state, "errors.log")
        if os.path.exists(errors) and not errors_expected:
            raise AssertionError("the dispatcher logged an error:\n" + open(errors).read())
        return HookResult(tool, process)

    # files

    def errors(self):
        path = os.path.join(self.state, "errors.log")
        if not os.path.exists(path):
            return ""
        with open(path) as handle:
            return handle.read()

    @property
    def user_file(self):
        return os.path.join(self.config, "hub.json")

    def settings(self):
        with open(self.user_file) as handle:
            return json.load(handle)

    def state_file(self, name):
        with open(os.path.join(self.state, name)) as handle:
            return json.load(handle)

    def signals(self):
        path = os.path.join(self.state, "signals.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def history(self):
        path = os.path.join(self.state, "history.jsonl")
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def show(self, *args):
        return json.loads(self.hubctl("show", "--json", *args).stdout)

    def write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump(data, handle, indent=2)

    def switch_on(self, *flag_ids):
        """Turn flags on in the user's settings file, as the user does by hand. Runs no apply.

        Before install it gives a user who already turned these parts on, such as builder and
        tier_rule, which a new user has off.
        """
        data = self.settings() if os.path.exists(self.user_file) else {"version": 1, "flags": {}}
        for flag_id in flag_ids:
            data.setdefault("flags", {})[flag_id] = {"by": "user", "value": "on"}
        self.write_json(self.user_file, data)

    def edit_manifest(self, part, change):
        path = os.path.join(self.plugin, "parts", part, "part.json")
        with open(path) as handle:
            data = json.load(handle)
        change(data)
        self.write_json(path, data)


def load_fixture(*parts):
    with open(os.path.join(FIXTURES, "hook_input", *parts)) as handle:
        data = json.load(handle)
    return {key: value for key, value in data.items() if not key.startswith("_")}


@pytest.fixture(scope="session", autouse=True)
def temp_home_for_pytest(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("pytest-home"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", root)
        for name, folder in XDG_FOLDERS.items():
            patch.setenv(name, os.path.join(root, folder))
        for name in TOOL_CALL_MARKERS:  # a run started from a chat's tool call must not look like one
            patch.delenv(name, raising=False)
        yield root


@pytest.fixture
def home(tmp_path):
    return Home(tmp_path / "home")


@pytest.fixture(params=["claude_code", "cursor"])
def tool(request):
    return request.param
