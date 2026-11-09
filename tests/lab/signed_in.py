"""Run the real claude binary against the real API, with a lab login.

The mock lab can not show what auto mode's safety check decides, because
that check runs on Anthropic's servers. These helpers run the same kind of
sandbox with a real sign-in instead of the mock.

The login lives in its own Claude Code config folder, named by
LAB_LOGIN_CONFIG_DIR. It is used as CLAUDE_CONFIG_DIR and is never written
to by these helpers. The lab's settings go in a --settings file inside the
sandbox, user settings are not read (--setting-sources project,local), and
the allow rules sit in the sandbox's own project folder.

Each run is small. It uses the smallest model auto mode accepts, only the
Bash tool, no MCP servers, and at most LAB_REAL_MAX_TURNS turns. Every run
is counted in LAB_REAL_RUN_COUNT_FILE, and a run past LAB_REAL_RUN_LIMIT is
refused before it starts.
"""
import json
import os
import pwd
import subprocess

from lab_claude import Lab, Run, ancestors

MODEL = os.environ.get("LAB_REAL_MODEL", "haiku")
MAX_TURNS = os.environ.get("LAB_REAL_MAX_TURNS", "5")
RUN_LIMIT = int(os.environ.get("LAB_REAL_RUN_LIMIT", "30"))
STATUS_TIMEOUT = 30


class Login:
    """The lab login and the most isolation it survives."""

    def __init__(self, config_dir, plan, own_home):
        self.config_dir = config_dir
        self.plan = plan
        self.own_home = own_home


def auth_status(config_dir, home, xdg_root):
    """`claude auth status` for the login with this HOME and XDG root. Only the logged-in flag and plan are kept."""
    env = base_env(config_dir, home, xdg_root)
    try:
        done = subprocess.run(["claude", "auth", "status", "--json"], env=env, capture_output=True, text=True,
                              timeout=STATUS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude auth status hung; macOS may be showing a Keychain or other system dialog")
    try:
        status = json.loads(done.stdout)
    except ValueError:
        return False, None
    return bool(status.get("loggedIn")), status.get("subscriptionType")


def find_login(config_dir, probe_root):
    """Check the login twice: with a sandbox HOME and XDG folders, then with the real HOME and sandbox XDG folders.

    The login is a Keychain item on macOS. If it can not be read with a
    sandbox HOME, the runs keep the real HOME, and only the XDG folders, the
    working folder and the settings stay in the sandbox.
    """
    home = os.path.join(probe_root, "home")
    os.makedirs(home, exist_ok=True)
    logged_in, plan = auth_status(config_dir, home, probe_root)
    if logged_in:
        return Login(config_dir, plan, own_home=True)
    logged_in, plan = auth_status(config_dir, real_home(), probe_root)
    if logged_in:
        return Login(config_dir, plan, own_home=False)
    return None


def real_home():
    return pwd.getpwuid(os.getuid()).pw_dir


def base_env(config_dir, home, xdg_root):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("CLAUDE", "ANTHROPIC"))}
    env.update(HOME=home, CLAUDE_CONFIG_DIR=config_dir, DISABLE_AUTOUPDATER="1", DISABLE_ERROR_REPORTING="1",
               XDG_CONFIG_HOME=os.path.join(xdg_root, "xdg", "config"),
               XDG_STATE_HOME=os.path.join(xdg_root, "xdg", "state"),
               XDG_DATA_HOME=os.path.join(xdg_root, "xdg", "data"),
               XDG_CACHE_HOME=os.path.join(xdg_root, "xdg", "cache"))
    return env


def take_run():
    """Count one real run, or refuse it once the limit is reached."""
    path = os.environ.get("LAB_REAL_RUN_COUNT_FILE")
    if not path:
        take_run.count = getattr(take_run, "count", 0) + 1
        used = take_run.count
    else:
        used = (int(open(path).read().strip() or 0) if os.path.exists(path) else 0) + 1
        with open(path, "w") as handle:
            handle.write(str(used))
    if used > RUN_LIMIT:
        raise RuntimeError(f"real run {used} is past the limit of {RUN_LIMIT}")
    return used


class SignedInRun(Run):
    """A Run from the real API: what the model asked for, and whether the check judged it."""

    def __init__(self, lab, messages, decisions, log_dir, session_id, auto_lines):
        super().__init__(lab, messages, decisions, log_dir, session_id)
        self.auto_lines = auto_lines

    def tried(self):
        """Every Bash command the model asked to run."""
        found = []
        for message in self.messages:
            if message.get("type") != "assistant":
                continue
            for block in message.get("message", {}).get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    found.append(block.get("input", {}).get("command"))
        return found

    def decision(self, command):
        """How the call was decided, from the stream's tool_result_meta.

        For example {"decision": "accept", "source": "config", "reason_type":
        "classifier"}: reason_type says whether auto mode's check or a rule
        decided it. None when the model never asked for the command.
        """
        ids = {}
        for message in self.messages:
            if message.get("type") == "assistant":
                for block in message.get("message", {}).get("content") or []:
                    if block.get("type") == "tool_use" and block.get("name") == "Bash":
                        ids[block.get("id")] = block.get("input", {}).get("command")
        for message in self.messages:
            for meta in message.get("tool_result_meta") or []:
                if ids.get(meta.get("id")) == command:
                    return meta.get("permission_decision")
        return None

    @property
    def init(self):
        return next((message for message in self.messages if message.get("subtype") == "init"), {})

    @property
    def result(self):
        return next((message for message in reversed(self.messages) if message.get("type") == "result"), {})


class SignedInLab(Lab):
    def __init__(self, root, login):
        self.login = login
        super().__init__(root)

    @property
    def flag_settings(self):
        return os.path.join(self.root, "lab-settings.json")

    def write_settings(self, extra, folders=()):
        """The lab's settings go in a --settings file, since the login folder's settings.json is never touched."""
        excludes = set()
        for folder in (self.home, *folders):
            for parent in ancestors(os.path.join(folder, "x")):
                if parent == folder:
                    continue
                for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", ".claude/rules/**"):
                    excludes.add(os.path.join(parent, name))
        settings = {"claudeMdExcludes": sorted(excludes)}
        settings.update(extra)
        with open(self.flag_settings, "w") as handle:
            json.dump(settings, handle, indent=1)

    def make_documents(self):
        """A folder of files that existed before the chat, the kind auto mode's check guards against deleting.

        It sits outside the chat's working folder. Inside it, auto mode lets
        a delete through with no check, as acceptEdits mode would.
        """
        folder = os.path.join(self.root, "files", "Documents")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "notes.txt"), "w") as handle:
            handle.write("lab notes, safe to lose\n")
        return folder

    def env(self, port=None):
        home = self.home if self.login.own_home else real_home()
        return base_env(self.login.config_dir, home, self.root)

    def session(self, turns, mode="auto", cwd=None, resume=None, ask_host=True, side_reply="ok",
                between=None, extra_args=()):
        """Run one real claude -p session. Each turn is a user text; the model chooses the calls."""
        take_run()
        self.runs += 1
        run_dir = os.path.join(self.root, f"run-{self.runs:02d}")
        os.makedirs(run_dir)
        debug = os.path.join(run_dir, "debug.log")
        command = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--permission-mode", mode, "--model", MODEL, "--max-turns", MAX_TURNS, "--tools", "Bash",
                   "--strict-mcp-config", "--setting-sources", "project,local", "--settings", self.flag_settings,
                   "--debug-file", debug, *extra_args]
        command += ["--permission-prompt-tool", "stdio"] if ask_host else ["--permission-prompts", "none"]
        if resume:
            command += ["--resume", resume]
        messages, decisions, session_id = [], [], None
        stderr = open(os.path.join(run_dir, "stderr.txt"), "w")
        proc = subprocess.Popen(command, cwd=cwd or self.home, env=self.env(), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
        try:
            for index, text in enumerate(turns):
                if between and index in between:
                    between[index]()
                self.send(proc, {"type": "user", "message": {"role": "user", "content": text}})
                for message in self.read_until_result(proc, decisions, timeout=240):
                    messages.append(message)
                    session_id = message.get("session_id") or session_id
            proc.stdin.close()
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            stderr.close()
        auto_lines = keep_auto_mode_lines(debug, os.path.join(run_dir, "auto-mode.log"))
        with open(os.path.join(run_dir, "stream.jsonl"), "w") as handle:
            handle.writelines(json.dumps(message) + "\n" for message in messages)
        return SignedInRun(self, messages, decisions, run_dir, session_id, auto_lines)


def keep_auto_mode_lines(debug, kept):
    """Keep only the auto mode and permission lines of the debug log, then delete the rest of it."""
    wanted = ("[auto-mode]", "classif", "permission", "Permission", "auto mode", "Auto mode")
    lines = []
    if os.path.exists(debug):
        with open(debug, errors="replace") as handle:
            lines = [line.rstrip("\n") for line in handle if any(word in line for word in wanted)]
        os.remove(debug)
    latest = os.path.join(os.path.dirname(debug), "latest")
    if os.path.islink(latest):
        os.remove(latest)
    with open(kept, "w") as handle:
        handle.writelines(line + "\n" for line in lines)
    return lines
