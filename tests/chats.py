"""Chats for the slice 10 scenarios: hook input for any tool, fake chat processes and a fake chat listing.

A chat in a test is the hook input that chat would send, fed to the real
dispatcher. A chat that must be alive, such as a coordinator whose process
can end, is a small process (tests/fakes/chat_process.py) that runs its
SessionStart hook as a child, the way Claude Code does, and then waits.

The listing is what `claude agents --json` prints. Slice 0 recorded it in a
fresh HOME, where it printed `[]`, so the rows here follow the documented
fields (docs/agent-view.md, "List sessions as JSON"): cwd, kind, startedAt,
pid, status, sessionId and name.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from conftest import CURSOR_EVENTS, FIXTURES, REPO, load_fixture

FAKES = os.path.join(REPO, "tests", "fakes")

SESSIONS = {"LOBBY": "00000000-0000-4000-8000-0000000000a1", "WEB": "00000000-0000-4000-8000-0000000000a2",
            "DOCS": "00000000-0000-4000-8000-0000000000a3", "API": "00000000-0000-4000-8000-0000000000a4",
            "HUB": "00000000-0000-4000-8000-0000000000a5"}


class FakeChat:
    def __init__(self, name, session, process, address, answer_path=None):
        self.name = name
        self.session = session
        self.process = process
        self.address = address
        self.answer_path = answer_path

    @property
    def start_context(self):
        """The context its SessionStart hook gave the chat."""
        with open(self.answer_path) as handle:
            text = handle.read().strip()
        return json.loads(text).get("hookSpecificOutput", {}).get("additionalContext", "") if text else ""

    @property
    def pid(self):
        return self.process.pid

    def run(self, args, env=None):
        """Run a command as the chat's own child. Returns (exit code, stdout, stderr)."""
        self.process.stdin.write(json.dumps(list(args)) + "\n")
        self.process.stdin.flush()
        reply = json.loads(self.process.stdout.readline())
        return reply["code"], reply["stdout"], reply["stderr"]

    def end(self):
        self.process.terminate()
        self.process.wait(timeout=10)


class Chats:
    def __init__(self, home):
        self.home = home
        self.calls = 0
        self.processes = []
        self.sockets = []
        self.folder = tempfile.mkdtemp(prefix="hubchats", dir="/tmp")  # short, so a socket path fits
        self.listing_file = os.path.join(home.root, "listing.json")
        replay_bin = os.path.join(home.root, "replay-bin")
        shutil.copy(os.path.join(FIXTURES, "claude_replay.py"), os.path.join(replay_bin, "claude-replay"))
        shutil.copy(os.path.join(FAKES, "claude_listing.py"), os.path.join(replay_bin, "claude"))
        os.chmod(os.path.join(replay_bin, "claude"), 0o755)
        home.env["HUB_FAKE_LISTING"] = self.listing_file
        self.set_listing([])

    # hook input

    def input(self, name, event, tool_name=None, tool_input=None, **extra):
        session = SESSIONS.get(name, name)
        if event == "SessionStart":
            data = load_fixture("claude_code", "SessionStart.json")
        else:
            data = load_fixture("claude_code", "PreToolUse.Bash.json")
            data.pop("tool_name", None)
            data.pop("tool_input", None)
            data.pop("tool_use_id", None)
            data["hook_event_name"] = event
        data.update(session_id=session, cwd=self.home.work,
                    transcript_path=os.path.join(self.home.root, ".claude", "projects", "demo", session + ".jsonl"))
        if tool_name is not None:
            self.calls += 1
            data.update(tool_name=tool_name, tool_input=tool_input or {}, tool_use_id=f"toolu_{self.calls:024d}")
        data.update(extra)
        return data

    def send(self, name, event, tool_name=None, tool_input=None, **extra):
        return self.home.send("claude_code", event, self.input(name, event, tool_name, tool_input, **extra))

    def tool(self, name, tool_name, tool_input, **extra):
        return self.send(name, "PreToolUse", tool_name, tool_input, **extra)

    def cursor(self, event, tool_name=None, tool_input=None, **extra):
        data = load_fixture("cursor", f"{event}.Shell.json" if event == "preToolUse" else f"{event}.json")
        data.update(cwd=self.home.work, workspace_roots=[self.home.work])
        if tool_name is not None:
            self.calls += 1
            data.update(tool_name=tool_name, tool_input=tool_input or {}, tool_use_id=f"call_{self.calls:024d}")
        data.update(extra)
        claude_event = next(claude for claude, cursor in CURSOR_EVENTS.items() if cursor == event)
        return self.home.send("cursor", claude_event, data)

    def start(self, name, title=None, live=False, inbox=False, socket_name=None, commands=False):
        """Start a chat. A live chat is a process that waits; an inbox is a real socket the test listens on.

        socket_name names the inbox, so a resumed chat can get a new address, as a real resume does.
        A chat with commands runs FakeChat.run commands as its own children.
        """
        data = self.input(name, "SessionStart", source="startup")
        if title is not None:
            data["session_title"] = title
        if not live:
            return self.home.send("claude_code", "SessionStart", data)
        address = None
        env = dict(self.home.env)
        if inbox:
            address = os.path.join(self.folder, (socket_name or name.lower()) + ".sock")
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(address)
            listener.listen(8)
            self.sockets.append(listener)
            env["CLAUDE_CODE_MESSAGING_SOCKET"] = address
        input_path = os.path.join(self.home.root, f"start-{name}.json")
        with open(input_path, "w") as handle:
            json.dump(data, handle)
        dispatch = os.path.join(self.home.plugin, "lib", "hub", "dispatch.py")
        process = subprocess.Popen([sys.executable, os.path.join(FAKES, "chat_process.py"), sys.executable,
                                    dispatch, input_path, *(["--commands"] if commands else [])],
                                   cwd=self.home.work, env=env, stdin=subprocess.PIPE if commands else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = process.stdout.readline()
        if line.strip() != "ready":
            raise AssertionError(f"the fake chat {name} did not start: {line}{process.stderr.read()}")
        self.processes.append(process)
        return FakeChat(name, data["session_id"], process, address, input_path + ".answer.json")

    # the listing

    def row(self, name, pid=None, status="idle", kind="interactive", session=None):
        row = {"cwd": self.home.work, "kind": kind, "startedAt": int(time.time() * 1000) - 60000,
               "sessionId": session or SESSIONS.get(name, name), "name": name}
        if pid is not None:
            row.update(pid=pid, status=status)
        return row

    def set_listing(self, rows, delay=0, exit_code=0, raw=None):
        """What the fake `claude agents --json` prints: the rows, or the raw text when it is given."""
        listing = {"rows": rows, "delay": delay, "exit": exit_code}
        if raw is not None:
            listing["raw"] = raw
        with open(self.listing_file, "w") as handle:
            json.dump(listing, handle)
        cache = os.path.join(self.home.state, "sessions", "listing.json")
        if os.path.exists(cache):
            os.unlink(cache)

    def set_version(self, version):
        """What `claude --version` prints, as a chat's SessionStart reads it."""
        with open(self.listing_file + ".version", "w") as handle:
            handle.write(f"{version} (Claude Code)\n")

    def listing_calls(self):
        path = self.listing_file + ".calls"
        if not os.path.exists(path):
            return 0
        with open(path) as handle:
            return len(handle.read().splitlines())

    def doctor_log(self):
        path = os.path.join(self.home.state, "doctor.log")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return handle.read().splitlines()

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for listener in self.sockets:
            listener.close()
        shutil.rmtree(self.folder, ignore_errors=True)
