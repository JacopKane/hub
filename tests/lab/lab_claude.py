"""Run the real claude binary in a throwaway sandbox against mock_api.py.

The mock plays the model. Each turn it asks for one Bash command, and the
real permission system in claude decides whether it runs. Nothing signs in
and no real model is called. The mock logs every request, so a test can see
whether claude made a side call, such as a summary or a title, before it ran
or refused a command.

A Lab is one sandbox. HOME, CLAUDE_CONFIG_DIR and the XDG folders all point
inside it. Its settings.json hides every CLAUDE.md in the folders above, so
nothing from the real setup leaks in.
"""
import glob
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MOCK_KEY = "sk-ant-lab-mock-0000"
FAKE_SCRIPT = """#!/bin/zsh
# A fake of the real job script: it only notes that it ran, and with what.
print -r -- "{name} $*" >> {log}
"""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ancestors(path):
    found = []
    path = os.path.dirname(path)
    while True:
        found.append(path)
        parent = os.path.dirname(path)
        if parent == path:
            return found
        path = parent


class Run:
    """What one claude session did: its stream, its decisions and the mock's log."""

    def __init__(self, lab, messages, decisions, log_dir, session_id):
        self.lab = lab
        self.messages = messages
        self.decisions = decisions
        self.session_id = session_id
        self.requests = []
        for path in sorted(glob.glob(os.path.join(log_dir, "req-*.json"))):
            with open(path) as handle:
                self.requests.append(json.load(handle))

    @property
    def denials(self):
        found = []
        for message in self.messages:
            if message.get("type") == "result":
                found += [denial.get("tool_input", {}).get("command") for denial in message.get("permission_denials", [])]
        return found

    @property
    def side_calls(self):
        """Messages API calls outside the main loop, such as a summary or a title."""
        return [request for request in self.requests
                if request["path"].endswith("/v1/messages") and not request["main_loop"]]

    def asked(self):
        """Commands claude sent to the host for a decision (a permission prompt)."""
        return [decision["input"].get("command") for decision in self.decisions]

    def tool_results(self):
        found = []
        for message in self.messages:
            if message.get("type") != "user":
                continue
            content = message.get("message", {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(part.get("text", "") for part in text)
                    found.append({"error": bool(block.get("is_error")), "text": str(text)})
        return found


class Lab:
    def __init__(self, root):
        self.root = os.path.realpath(str(root))
        self.home = os.path.join(self.root, "home")
        self.config = os.path.join(self.home, ".claude")
        self.jobs = os.path.join(self.root, "jobs")
        self.out = os.path.join(self.root, "out")
        for folder in (self.config, self.jobs, self.out, os.path.join(self.home, ".config"),
                       os.path.join(self.home, ".local", "state")):
            os.makedirs(folder, exist_ok=True)
        self.ran_log = os.path.join(self.out, "ran.log")
        self.trash = self.write_script("run_trash.sh")
        self.batches = self.write_script("run_batches.sh")
        self.runs = 0
        self.write_settings({})

    def write_script(self, name, folder=None, body=None):
        path = os.path.join(folder or self.jobs, name)
        with open(path, "w") as handle:
            handle.write(body or FAKE_SCRIPT.format(name=name, log=self.ran_log))
        os.chmod(path, 0o755)
        return path

    def ran(self):
        if not os.path.exists(self.ran_log):
            return []
        with open(self.ran_log) as handle:
            return [line.rstrip("\n") for line in handle]

    def write_settings(self, extra, folders=()):
        """The sandbox's user settings.json, hiding every CLAUDE.md above the folders claude runs in."""
        excludes = set()
        for folder in (self.home, *folders):
            for parent in ancestors(os.path.join(folder, "x")):
                if parent == folder:
                    continue
                for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", ".claude/rules/**"):
                    excludes.add(os.path.join(parent, name))
        settings = {"claudeMdExcludes": sorted(excludes)}
        settings.update(extra)
        with open(os.path.join(self.config, "settings.json"), "w") as handle:
            json.dump(settings, handle, indent=1)

    def set_local_rules(self, allow, folder=None):
        """Allow rules in <folder>/.claude/settings.local.json; the default folder is HOME, like ~/.claude."""
        path = os.path.join(folder or self.home, ".claude", "settings.local.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"permissions": {"allow": list(allow)}}, handle, indent=1)
        return path

    def env(self, port):
        env = {key: value for key, value in os.environ.items() if not key.startswith(("CLAUDE", "ANTHROPIC"))}
        env.update(HOME=self.home, CLAUDE_CONFIG_DIR=self.config,
                   XDG_CONFIG_HOME=os.path.join(self.home, ".config"),
                   XDG_STATE_HOME=os.path.join(self.home, ".local", "state"),
                   ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}", ANTHROPIC_API_KEY=MOCK_KEY,
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_AUTOUPDATER="1",
                   DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1")
        return env

    def session(self, turns, mode="default", cwd=None, resume=None, ask_host=True, side_reply="ok",
                between=None, extra_args=()):
        """Run one claude -p session. Each turn is (user text, Bash command or None).

        A text that starts with "/" is sent as it is, as a slash command.

        ask_host: claude sends permission prompts to this host (stdio), which
        records each one and answers deny, so a prompt is visible and never
        runs anything. between: {turn index: function} run before that turn.
        """
        self.runs += 1
        run_dir = os.path.join(self.root, f"run-{self.runs:02d}")
        log_dir = os.path.join(run_dir, "log")
        os.makedirs(log_dir)
        rules = [{"match": f"[turn {index}]", "reply": {"tool": "Bash", "input": {"command": command, "description": "lab"}}}
                 for index, (_, command) in enumerate(turns) if command]
        script = os.path.join(run_dir, "script.json")
        with open(script, "w") as handle:
            json.dump({"rules": rules, "side_reply": side_reply}, handle)
        port = free_port()
        mock = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mock_api.py"), log_dir, script, str(port)])
        command = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                   "--permission-mode", mode, *extra_args]
        if ask_host:
            command += ["--permission-prompt-tool", "stdio"]
        else:
            command += ["--permission-prompts", "none"]
        if resume:
            command += ["--resume", resume]
        messages, decisions, session_id = [], [], None
        proc = None
        stderr = open(os.path.join(run_dir, "stderr.txt"), "w")
        try:
            self.wait_port(port)
            proc = subprocess.Popen(command, cwd=cwd or self.home, env=self.env(port), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=stderr,
                                    text=True, bufsize=1)
            for index, (text, _) in enumerate(turns):
                if between and index in between:
                    between[index]()
                content = text if text.startswith("/") else f"[turn {index}] {text}"
                self.send(proc, {"type": "user", "message": {"role": "user", "content": content}})
                for message in self.read_until_result(proc, decisions):
                    messages.append(message)
                    session_id = message.get("session_id") or session_id
            proc.stdin.close()
            proc.wait(timeout=30)
        finally:
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()
            mock.terminate()
            mock.wait()
            stderr.close()
        with open(os.path.join(run_dir, "stream.jsonl"), "w") as handle:
            handle.writelines(json.dumps(message) + "\n" for message in messages)
        return Run(self, messages, decisions, log_dir, session_id)

    @staticmethod
    def wait_port(port, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("the mock API did not start")

    @staticmethod
    def send(proc, message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def read_until_result(self, proc, decisions, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    raise RuntimeError("claude exited before the turn ended")
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("type") == "control_request":
                request = message.get("request", {})
                if request.get("subtype") == "can_use_tool":
                    decisions.append(request)
                    answer = {"behavior": "deny", "message": "the lab host refuses every prompt"}
                else:
                    answer = {}
                self.send(proc, {"type": "control_response", "response": {
                    "subtype": "success", "request_id": message.get("request_id"), "response": answer}})
                continue
            yield message
            if message.get("type") == "result":
                return
        raise RuntimeError("the turn did not end in time")
