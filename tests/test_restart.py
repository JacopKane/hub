"""Slice 10: the restart part.

A restart asks each chat first, stops a chat only when it is idle with
nothing to lose, and brings it back in its own terminal under its own name
and flags. Chats that are not safe are held, untouched, and one report names
each with its reason. The calling chat restarts last.

The chats are tests/fakes/claude_restart.py, a stand-in for `claude` that
draws an input box in a real terminal and exits on /exit. tmux is real, on
private servers the test starts and ends. iTerm is the recording osascript
in tests/fakes/os_tools.py, whose sessions are panes of a second private
tmux server, because driving the real app is unsafe. No real chat, terminal
or tmux session is touched.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from chats import SESSIONS
from conftest import FIXTURES, REPO, load_fixture
from steps import and_, given, then, when

FAKES = os.path.join(REPO, "tests", "fakes")
REAL_TMUX = shutil.which("tmux")
TIMINGS = {"HUB_RESTART_POLL": "0.2", "HUB_RESTART_EXIT_WAIT": "8", "HUB_RESTART_RESUME_WAIT": "15",
           "HUB_RESTART_STILL": "1", "HUB_RESTART_CALLER_WAIT": "8", "HUB_RESTART_RENAME_WAIT": "3"}


def wait_for(check, what, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"waited {seconds} s for {what}")


class World:
    """hub with restart on, chats in private tmux servers, and the fakes on PATH."""

    def __init__(self, home, tmp_path):
        if REAL_TMUX is None:
            pytest.fail("tmux is missing; the test image and macOS CI both install it")
        self.home = home
        home.install()
        home.hubctl("on", "restart")
        self.chats = str(tmp_path / "chats")
        self.tools = str(tmp_path / "fake-tools")
        tool_bin = str(tmp_path / "fake-bin")
        for folder in (self.chats, os.path.join(self.chats, "status"), self.tools, tool_bin):
            os.makedirs(folder)
        for name in ("osascript", "caffeinate"):
            shutil.copy(os.path.join(FAKES, "os_tools.py"), os.path.join(tool_bin, name))
            os.chmod(os.path.join(tool_bin, name), 0o755)
        replay_bin = os.path.join(home.root, "replay-bin")
        shutil.copy(os.path.join(FIXTURES, "claude_replay.py"), os.path.join(replay_bin, "claude-replay"))
        shutil.copy(os.path.join(FAKES, "claude_restart.py"), os.path.join(replay_bin, "claude"))
        os.chmod(os.path.join(replay_bin, "claude"), 0o755)
        with open(os.path.join(self.chats, "session_start.json"), "w") as handle:
            json.dump(load_fixture("claude_code", "SessionStart.json"), handle)
        self.servers = {kind: f"zzt{os.getpid()}{kind}" for kind in ("r", "i", "p")}
        home.env.update(TIMINGS, HUB_FAKE_CHATS=self.chats, HUB_FAKE_TOOLS=self.tools,
                        HUB_FAKE_DISPATCH=os.path.join(home.plugin, "lib", "hub", "dispatch.py"),
                        HUB_TMUX_SERVER=self.servers["r"],
                        PATH=tool_bin + os.pathsep + home.env["PATH"])
        self.panes = {}
        self.iterm = []

    # terminals

    def tmux(self, server, *words):
        return subprocess.run([REAL_TMUX, "-L", self.servers[server], "-f", os.devnull, *words], env=self.home.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def open_pane(self, name, where):
        """A shell in a new pane: "tmux" on the server restart looks at, "iterm" for an iTerm tab, "plain"."""
        server = {"tmux": "r", "iterm": "i", "plain": "p"}[where]
        try:
            self.tmux(server, "has-session")
            self.tmux(server, "new-window", "-t", "main", "sh")
        except subprocess.CalledProcessError:
            self.tmux(server, "new-session", "-d", "-s", "main", "-x", "120", "-y", "30", "sh")
        pane, tty = self.tmux(server, "display-message", "-p", "-t", "main", "#{pane_id} #{pane_tty}").split()
        self.panes[name] = (server, pane, tty)
        if where == "iterm":
            self.iterm.append([f"ITERM-{name}", tty, {"tmux": self.servers["i"], "pane": pane}])
            with open(os.path.join(self.tools, "iterm.json"), "w") as handle:
                json.dump(self.iterm, handle)
        return server, pane

    def screen(self, name):
        server, pane, _tty = self.panes[name]
        return self.tmux(server, "capture-pane", "-p", "-t", pane)

    def type(self, name, text):
        server, pane, _tty = self.panes[name]
        self.tmux(server, "send-keys", "-t", pane, "-l", text)
        self.tmux(server, "send-keys", "-t", pane, "Enter")

    # chats

    def status(self, name, value, suffix=""):
        with open(os.path.join(self.chats, "status", name + suffix), "w") as handle:
            handle.write(value)

    def start(self, name, where="tmux", flags=("--model", "opus", "--effort", "high"), cwd=None):
        session = SESSIONS[name]
        if where == "background":
            self.home.run(["claude", "--bg", "--session-id", session, "-n", name], cwd=cwd)
        else:
            self.open_pane(name, where)
            words = ["claude", "--session-id", session, "-n", name, *flags]
            self.type(name, f"cd {self.home.work} && " + " ".join(words))
        wait_for(lambda: self.row(name) and (where == "background" or self.record(name)), f"{name} to run")
        return session

    def row(self, name):
        path = os.path.join(self.chats, "rows", SESSIONS[name] + ".json")
        try:
            with open(path) as handle:
                row = json.load(handle)
        except (OSError, ValueError):
            return None
        try:
            os.kill(row["pid"], 0)
        except OSError:
            return None
        return row

    def record(self, name):
        path = os.path.join(self.home.state, "sessions", SESSIONS[name] + ".json")
        if not os.path.exists(path):
            return None
        with open(path) as handle:
            data = json.load(handle)
        return data if self.row(name) and data.get("pid") == self.row(name)["pid"] else None

    def events(self, since=0):
        path = os.path.join(self.chats, "events.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [line for line in map(json.loads, handle) if line["time"] >= since]

    def osascript_calls(self):
        path = os.path.join(self.tools, "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [" ".join(line["args"]) for line in map(json.loads, handle) if line["tool"] == "osascript"]

    # the restart

    def restart(self, *words, check=True):
        script = os.path.join(self.home.plugin, "parts", "restart", "restart.py")
        return self.home.run([sys.executable, "-I", script, *words], check=check)

    def answer(self, name, *words):
        return self.restart(*words, "--session", SESSIONS[name])

    def run_to_end(self, *words):
        started = time.time()
        printed = self.restart("run", *words).stdout
        report = printed.split("Report: ", 1)[1].split()[0]
        result = os.path.join(os.path.dirname(report), "result.json")
        wait_for(lambda: os.path.exists(result), "the run to end", seconds=120)
        # result.json comes last, so a restart started as soon as it appears is not refused as a second run
        assert not os.path.exists(os.path.join(self.home.state, "restart", "lock"))
        with open(report) as handle:
            text = handle.read()
        with open(result) as handle:
            return started, text, json.load(handle)["results"]

    def close(self):
        for server in self.servers.values():
            subprocess.run([REAL_TMUX, "-L", server, "kill-server"], capture_output=True)
        for name in os.listdir(os.path.join(self.chats, "rows")) if os.path.isdir(
                os.path.join(self.chats, "rows")) else []:
            try:
                with open(os.path.join(self.chats, "rows", name)) as handle:
                    os.kill(json.load(handle)["pid"], 15)
            except (OSError, ValueError, KeyError):
                pass


@pytest.fixture
def world(home, tmp_path):
    made = World(home, tmp_path)
    yield made
    made.close()


def steps_of(events, names):
    return [(line["event"], line.get("name")) for line in events
            if line["event"] in ("stop", "upgrade", "resume") and line.get("name") in names | {None}]


def test_restart_asks_first_and_resumes_each_chat_in_its_own_terminal(world):
    with given("WEB and HUB run in tmux panes, DOCS in an iTerm tab, and API has an open question"):
        for name, where in (("WEB", "tmux"), ("DOCS", "iterm"), ("API", "tmux"), ("HUB", "tmux")):
            world.start(name, where)
        world.status("API", "waiting")
        before = {name: world.record(name) for name in ("WEB", "DOCS", "API", "HUB")}
    with when("HUB plans the restart"):
        plan = json.loads(world.restart("plan", "--json", "--caller", SESSIONS["HUB"]).stdout)
    with then("every chat is listed in its own terminal with its flags, and HUB comes last"):
        assert [row["name"] for row in plan] == ["API", "DOCS", "WEB", "HUB"]
        assert {row["name"]: row["terminal"] for row in plan} == {"API": "tmux", "DOCS": "iterm", "WEB": "tmux",
                                                                   "HUB": "tmux"}
        assert plan[-1]["caller"] is True
        assert plan[2]["flags"] == ["--model", "opus", "--effort", "high"]
        assert all(row["action"] == "restart" for row in plan)
    with and_("planning typed nothing into any chat"):
        assert not [line for line in world.events() if line["event"] in ("stop", "sent")]
    with when("WEB, DOCS and API each answer ready, as the skill asks them to"):
        for name in ("WEB", "DOCS", "API"):
            world.answer(name, "ready")
    with and_("the restart runs with --upgrade"):
        started, report, results = world.run_to_end("--upgrade", "--caller", SESSIONS["HUB"], "--wait", "0.1")
    with then("the upgrade runs once, between the last stop and the first resume, and HUB restarts last"):
        assert steps_of(world.events(started), {"WEB", "DOCS", "API", "HUB"}) == [
            ("stop", "DOCS"), ("stop", "WEB"), ("upgrade", None), ("resume", "DOCS"), ("resume", "WEB"),
            ("stop", "HUB"), ("resume", "HUB")]
    with and_("WEB and DOCS come back under their names and with their flags"):
        for name in ("WEB", "DOCS"):
            resumed = [line for line in world.events(started) if line["event"] == "resume" and line["name"] == name]
            assert resumed[0]["args"][:8] == ["--resume", SESSIONS[name], "-n", name, "--model", "opus",
                                              "--effort", "high"]
            assert "saved" in resumed[0]["prompt"] and name + "-screen.txt" in resumed[0]["prompt"]
            assert results[name] == "back"
    with and_("each came back in the same pane or tab it ran in"):
        for name in ("WEB", "DOCS", "HUB"):
            now = wait_for(lambda name=name: world.record(name), f"{name}'s new record")
            assert now["pid"] != before[name]["pid"]
            assert now["tty"] == before[name]["tty"]
        assert any('if id of each_session is "ITERM-DOCS"' in call and "claude --resume" in call
                   for call in world.osascript_calls())
    with and_("API is held untouched, and the report names it with the reason"):
        assert results["API"].startswith("HELD") and "a question dialog is open" in results["API"]
        assert world.record("API")["pid"] == before["API"]["pid"]
        assert "/exit" not in world.screen("API")
        assert "- API (interactive): HELD, not restarted: a question dialog is open" in report
    with and_("the report gives the version change, and the answers are cleared for the next run"):
        assert "2.1.319 -> 2.1.320" in report
        assert "the restart run is done" in [line for line in world.events(started)
                                             if line["event"] == "resume" and line["name"] == "HUB"][0]["prompt"]
        assert os.listdir(os.path.join(world.home.state, "restart", "answers")) == []


def test_a_chat_keeps_its_flags_and_gets_its_name_back_when_the_resume_drops_it(world):
    with given("WEB runs with --autocompact and --brief, and DOCS and API come back from a resume without a name"):
        world.start("WEB", flags=("--model", "opus", "--autocompact", "80", "--brief"))
        for name in ("DOCS", "API"):
            world.start(name)
            world.status(name, "", suffix=".drops-name")
        world.status(SESSIONS["API"][-8:], "waiting")
        for name in ("WEB", "DOCS", "API"):
            world.answer(name, "ready")
    with when("the restart runs"):
        started, report, results = world.run_to_end("--wait", "0.1")
    with then("WEB comes back with both flags"):
        resumed = [line for line in world.events(started) if line["event"] == "resume" and line["name"] == "WEB"]
        assert resumed[0]["args"][:9] == ["--resume", SESSIONS["WEB"], "-n", "WEB", "--model", "opus",
                                          "--autocompact", "80", "--brief"]
        assert results["WEB"] == "back"
    with and_("DOCS, back at an idle prompt, is renamed to its name"):
        assert results["DOCS"] == "back, renamed"
        assert ("rename", "DOCS") in [(line["event"], line["name"]) for line in world.events(started)]
        assert world.row("DOCS")["name"] == "DOCS"
    with and_("API, back with a dialog open, gets nothing typed, and the report says how to rename it"):
        assert results["API"].startswith("back, but not renamed") and "/rename API" in results["API"]
        assert not [line for line in world.events(started) if line["event"] in ("rename", "sent")
                    and line["name"] in ("API", SESSIONS["API"][-8:])]
        assert "- API (interactive): back, but not renamed" in report


def test_a_chat_is_marked_as_restarting_from_before_its_exit_until_it_is_back(world):
    with given("WEB runs in a tmux pane and answered ready"):
        world.start("WEB")
        world.answer("WEB", "ready")
        mark = os.path.join(world.home.state, "sessions", "restarting", SESSIONS["WEB"] + ".json")
    with when("the restart runs, and the queue's view of WEB is watched the whole time"):
        started = time.time()
        printed = world.restart("run", "--wait", "0.1").stdout
        result = os.path.join(os.path.dirname(printed.split("Report: ", 1)[1].split()[0]), "result.json")
        first_seen = None
        while not os.path.exists(result) and time.time() < started + 120:
            if first_seen is None and os.path.exists(mark):
                first_seen = time.time()
            time.sleep(0.02)
        with open(result) as handle:
            results = json.load(handle)["results"]
    with then("WEB was marked before /exit went in, so the queue held its posts through the stop"):
        [stopped] = [line for line in world.events(started) if line["event"] == "stop" and line["name"] == "WEB"]
        assert first_seen is not None and first_seen < stopped["time"]
    with and_("WEB is back, and the mark is gone, so what waited for it goes now"):
        assert results["WEB"] == "back"
        assert not os.path.exists(mark)


def test_a_chat_with_anything_to_lose_is_held_untouched(world):
    with given("WEB has text typed in its box, DOCS gave no answer, and API runs in a plain terminal"):
        world.status("WEB", "half a thought", ".typed")
        for name, where in (("WEB", "tmux"), ("DOCS", "tmux"), ("API", "plain")):
            world.start(name, where)
        pids = {name: world.record(name)["pid"] for name in ("WEB", "DOCS", "API")}
        world.answer("WEB", "ready")
        world.answer("API", "ready")
    with when("the plan is read"):
        plan = {row["name"]: row for row in json.loads(world.restart("plan", "--json").stdout)}
    with then("it already says API's terminal can not be typed into, and DOCS did not answer"):
        assert plan["API"]["terminal"] == "plain"
        assert "it did not answer ready" in plan["DOCS"]["why"] and plan["DOCS"]["action"] == "leave"
    with when("the restart runs"):
        started, report, results = world.run_to_end("--wait", "0.1")
    with then("no chat stopped, and each is named with its reason"):
        assert steps_of(world.events(started), {"WEB", "DOCS", "API"}) == []
        assert all(world.record(name)["pid"] == pid for name, pid in pids.items())
        assert "text is typed in its input box" in results["WEB"] and "taken back" in results["WEB"]
        assert "not an iTerm tab or tmux pane" in results["API"]
        assert "it did not answer ready" in report
    with and_("WEB's typed text is still in its box, without /exit after it"):
        box = [line for line in world.screen("WEB").splitlines() if line.startswith("❯")]
        assert box == ["❯ half a thought"]
    with when("asking first is switched off and only DOCS restarts"):
        world.home.hubctl("off", "restart.ask_first")
        started, report, results = world.run_to_end("--only", "DOCS", "--wait", "0.1")
    with then("DOCS, idle with nothing to lose, restarts without an answer"):
        assert steps_of(world.events(started), {"WEB", "DOCS", "API"}) == [("stop", "DOCS"), ("resume", "DOCS")]
        assert results["DOCS"] == "back"
        assert "- WEB (interactive): not selected" in report
    with when("the upgrade behavior is off and a run asks for --upgrade"):
        world.home.hubctl("off", "restart.upgrade")
        refused = world.restart("run", "--upgrade", check=False)
    with then("nothing runs, and the answer says how to allow it"):
        assert refused.returncode == 1 and "turn on restart.upgrade" in refused.stderr
        assert not [line for line in world.events() if line["event"] == "upgrade"]


def test_a_failed_upgrade_still_resumes_every_stopped_chat_and_keeps_the_caller_up(world):
    with given("API runs in the background, WEB and HUB in tmux, and DOCS says its tests must finish"):
        world.start("API", "background")
        for name in ("WEB", "DOCS", "HUB"):
            world.start(name, "tmux")
        for name in ("API", "WEB"):
            world.answer(name, "ready")
        world.answer("DOCS", "hold", "the tests still run")
        hub_pid = world.record("HUB")["pid"]
    with and_("the Claude Code upgrade will fail"):
        open(os.path.join(world.chats, "update-fails"), "w").close()
    with when("HUB runs the restart with --upgrade"):
        started, report, results = world.run_to_end("--upgrade", "--caller", SESSIONS["HUB"], "--wait", "0.1")
    with then("API and WEB still come back, each in its own mode"):
        assert steps_of(world.events(started), {"API", "WEB", "DOCS", "HUB"}) == [
            ("stop", "API"), ("stop", "WEB"), ("upgrade", None), ("resume", "API"), ("resume", "WEB")]
        background = [line for line in world.events(started) if line["event"] == "resume" and line["name"] == "API"]
        assert background[0]["args"][:2] == ["--resume", SESSIONS["API"]]
        assert results["API"] == "back" and results["WEB"] == "back"
    with and_("DOCS is left alone with its own reason"):
        assert "- DOCS (interactive): it answered hold: the tests still run" in report
    with and_("HUB stays up to deal with the failure, and the report says the upgrade failed"):
        assert results["HUB"].startswith("HELD") and "upgrade" in results["HUB"]
        assert world.record("HUB")["pid"] == hub_pid
        assert "Claude Code upgrade FAILED" in report


def test_a_background_chat_is_stopped_only_when_its_work_is_done_or_it_let_the_work_stop(world):
    with given("API runs in the background and is still working, and the listing gives LOBBY no status"):
        world.status("API", "working", ".state")
        world.status("LOBBY", "missing")
        for name in ("API", "LOBBY"):
            world.start(name, "background")
            world.answer(name, "ready")
        pids = {name: world.row(name)["pid"] for name in ("API", "LOBBY")}
    with when("the restart runs"):
        started, report, results = world.run_to_end("--wait", "0.1")
    with then("neither chat is stopped, and each is named with its reason"):
        assert steps_of(world.events(started), {"API", "LOBBY"}) == []
        assert all(world.row(name)["pid"] == pid for name, pid in pids.items())
        assert results["API"].startswith("HELD") and "still working" in results["API"]
        assert results["LOBBY"].startswith("HELD") and "no status" in results["LOBBY"]
        assert "- LOBBY (background): HELD" in report
    with when("API says its work may stop, LOBBY's row shows it idle with its work done, and the restart runs"):
        world.answer("API", "ready", "--jobs-may-stop")
        world.answer("LOBBY", "ready")
        world.status("LOBBY", "idle")
        wait_for(lambda: world.row("LOBBY").get("status") == "idle", "LOBBY's row to show a status")
        started, report, results = world.run_to_end("--wait", "0.1")
    with then("both are stopped and come back in the background"):
        assert steps_of(world.events(started), {"API", "LOBBY"}) == [
            ("stop", "API"), ("resume", "API"), ("stop", "LOBBY"), ("resume", "LOBBY")]
        assert results["API"] == "back" and results["LOBBY"] == "back"


def age_answer(world, name, seconds):
    path = os.path.join(world.home.state, "restart", "answers", SESSIONS[name] + ".json")
    with open(path) as handle:
        data = json.load(handle)
    with open(path, "w") as handle:
        json.dump(dict(data, time=data["time"] - seconds), handle)


def test_an_answer_from_an_earlier_round_counts_for_nothing(world):
    with given("WEB and DOCS run in tmux, and WEB answered ready with --jobs-may-stop 20 minutes ago"):
        for name in ("WEB", "DOCS"):
            world.start(name, "tmux")
        pids = {name: world.record(name)["pid"] for name in ("WEB", "DOCS")}
        world.answer("WEB", "ready", "--jobs-may-stop")
        age_answer(world, "WEB", 20 * 60)
    with when("the plan is read"):
        plan = {row["name"]: row for row in json.loads(world.restart("plan", "--json").stdout)}
    with then("the old answer does not count, so WEB is still to be asked and may not stop its work"):
        assert plan["WEB"]["why"] == "it is asked first" and plan["WEB"]["jobs_may_stop"] is False
    with when("the restart runs"):
        started, report, results = world.run_to_end("--wait", "0.1")
    with then("WEB is left running, because it did not answer ready in this round"):
        assert steps_of(world.events(started), {"WEB", "DOCS"}) == []
        assert results["WEB"] == "it did not answer ready" and world.record("WEB")["pid"] == pids["WEB"]
    with given("WEB answers ready now, and a restart of DOCS alone is cut off during its upgrade"):
        world.answer("WEB", "ready")
        hangs = os.path.join(world.chats, "update-hangs")
        open(hangs, "w").close()
        started = time.time()
        world.restart("run", "--upgrade", "--only", "DOCS", "--wait", "0.1")
        wait_for(lambda: [line for line in world.events(started) if line["event"] == "upgrade"], "the upgrade")
        run_pid = int(open(os.path.join(world.home.state, "restart", "lock")).read())
        os.kill(run_pid, 9)
        wait_for(lambda: has_ended(run_pid), "the run to be gone")
        os.unlink(hangs)
    with when("the next restart runs"):
        started, report, results = world.run_to_end("--wait", "0.1")
    with then("WEB's answer went with the run that was cut off, so WEB is left running"):
        assert steps_of(world.events(started), {"WEB", "DOCS"}) == []
        assert results["WEB"] == "it did not answer ready" and world.record("WEB")["pid"] == pids["WEB"]
    with when("WEB answers ready in this round, and the restart runs"):
        world.answer("WEB", "ready")
        started, report, results = world.run_to_end("--only", "WEB", "--wait", "0.1")
    with then("WEB restarts"):
        assert steps_of(world.events(started), {"WEB"}) == [("stop", "WEB"), ("resume", "WEB")]
        assert results["WEB"] == "back"
    with and_("the skill's ask starts with the queue's urgent mark, so a queue on the asking side sends it at once"):
        with open(os.path.join(REPO, "plugins", "hub", "parts", "queue", "part.json")) as handle:
            urgent = next(child["default"] for child in json.load(handle)["children"]
                          if child["id"] == "urgent_prefix")
        with open(os.path.join(REPO, "plugins", "hub", "parts", "restart", "skill", "SKILL.md")) as handle:
            step = next(line for line in handle if line.startswith("2. "))
        assert step.split('"', 2)[1].startswith(urgent + " May I restart you?")


def processes_named(path):
    """Pids of the processes whose program is path, from /proc on Linux (the test image has no ps), else ps."""
    if os.path.isdir("/proc/self"):
        found = []
        for name in filter(str.isdigit, os.listdir("/proc")):
            try:
                with open(f"/proc/{name}/cmdline", "rb") as handle:
                    if handle.read().split(b"\0")[:1] == [path.encode()]:
                        found.append(int(name))
            except OSError:
                continue
        return found
    listed = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True).stdout
    return [int(line.split()[0]) for line in listed.splitlines() if line.split()[1:2] == [path]]


def test_a_resume_that_fails_never_leaves_a_later_chat_down(world, tmp_path):
    with given("API runs in the background in its own folder, DOCS and WEB run in tmux, and all answered ready"):
        folder = str(tmp_path / "api-folder")
        os.makedirs(folder)
        world.start("API", "background", cwd=folder)
        for name in ("DOCS", "WEB"):
            world.start(name, "tmux")
        for name in ("API", "DOCS", "WEB"):
            world.answer(name, "ready")
    with and_("the Claude Code upgrade will wait until the test lets it go"):
        hangs = os.path.join(world.chats, "update-hangs")
        open(hangs, "w").close()
    with when("the restart runs with --upgrade and stops both chats"):
        started = time.time()
        printed = world.restart("run", "--upgrade", "--wait", "0.1").stdout
        report_path = printed.split("Report: ", 1)[1].split()[0]
        wait_for(lambda: [line for line in world.events(started) if line["event"] == "upgrade"], "the upgrade")
    with and_("API's folder turns into a file before the resume, so its resume fails"):
        shutil.rmtree(folder)
        open(folder, "w").close()
    with and_("a Claude program that hub has no record of starts in DOCS's tab"):
        other = str(tmp_path / "other-bin" / "claude")
        os.makedirs(os.path.dirname(other))
        os.symlink(shutil.which("sleep"), other)
        wait_for(lambda: not world.row("DOCS"), "DOCS to exit")
        world.type("DOCS", f"{other} 600")
        wait_for(lambda: processes_named(other), "the other Claude program to run")
        os.unlink(hangs)
        result = os.path.join(os.path.dirname(report_path), "result.json")
        wait_for(lambda: os.path.exists(result), "the run to end", seconds=120)
        with open(result) as handle:
            results = json.load(handle)["results"]
        with open(report_path) as handle:
            report = handle.read()
    with then("nothing is typed into DOCS's tab, and DOCS is named NOT BACK with the reason"):
        assert results["DOCS"].startswith("NOT BACK") and "another chat runs in its tab" in results["DOCS"]
        assert "claude --resume" not in world.screen("DOCS")
        assert ("resume", "DOCS") not in steps_of(world.events(started), {"DOCS"})
    with and_("WEB, after API and DOCS in the order, still comes back"):
        assert results["WEB"] == "back"
        assert ("resume", "WEB") in steps_of(world.events(started), {"WEB"})
    with and_("API is named NOT BACK with the command that resumes it, in the results and in the report"):
        assert results["API"].startswith("NOT BACK") and "--resume " + SESSIONS["API"] in results["API"]
        assert "- API (background): NOT BACK" in report
        assert "not back yet" in report and "- API: cd " in report


def has_ended(pid):
    """Whether pid has exited. In docker pytest is PID 1 and never reaps, so a zombie counts as ended."""
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    if os.path.exists(f"/proc/{pid}/stat"):
        with open(f"/proc/{pid}/stat") as handle:
            stat = handle.read()
        return stat[stat.rindex(")") + 2:].split()[0] == "Z"
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                          text=True).stdout.strip().startswith("Z")


def restart_lines(world):
    lines = world.home.hubctl("doctor").stdout.splitlines()
    return [line for line in lines if line.split(": ", 2)[1:2] == ["restart"]]


def test_the_doctor_says_what_a_restart_would_do_and_names_the_chats_a_cut_off_run_left_down(world):
    with given("WEB runs in a tmux pane and API in a plain terminal"):
        world.start("WEB", "tmux")
        world.start("API", "plain")
    with when("the user runs hubctl doctor"):
        [line] = restart_lines(world)
    with then("one note says the listing works, WEB could come back in tmux, and API would be left with its reason"):
        assert line.startswith("note: restart: the chat listing works.")
        assert "WEB (tmux). Each one is asked first." in line
        assert "It leaves API, because its terminal is not an iTerm tab or tmux pane" in line
    with given("WEB answered ready, and the Claude Code upgrade will hang"):
        world.answer("WEB", "ready")
        hangs = os.path.join(world.chats, "update-hangs")
        open(hangs, "w").close()
    with when("the restart runs with --upgrade and stops WEB"):
        printed = world.restart("run", "--upgrade", "--wait", "0.1").stdout
        report = printed.split("Report: ", 1)[1].split()[0]
        wait_for(lambda: [line for line in world.events() if line["event"] == "upgrade"], "the upgrade to start")
        lock = os.path.join(world.home.state, "restart", "lock")
        run_pid = int(open(lock).read())
    with then("the doctor says a restart runs now, and where its report goes"):
        [line] = [line for line in restart_lines(world) if "runs now" in line]
        assert line.startswith("note: ") and f"pid {run_pid}" in line and report in line
    with when("the run is cut off before it brings WEB back"):
        os.kill(run_pid, 9)
        wait_for(lambda: not world.row("WEB") and has_ended(run_pid), "the run and WEB to be gone")
        os.unlink(hangs)
    with then("the doctor warns that WEB is down, and gives the file with its resume command"):
        [line] = [line for line in restart_lines(world) if "did not bring" in line]
        stopped = os.path.join(os.path.dirname(report), "stopped.json")
        assert line.startswith("warning: ") and "WEB" in line and stopped in line
        with open(stopped) as handle:
            command = json.load(handle)["WEB"]
        assert "--resume " + SESSIONS["WEB"] in command
    with when("the user resumes WEB with that command in a new pane"):
        world.open_pane("WEB", "tmux")
        world.type("WEB", command)
        wait_for(lambda: world.record("WEB"), "WEB to run again")
    with then("the doctor no longer warns, and says the run was cut off with every chat back"):
        lines = restart_lines(world)
        assert not [line for line in lines if line.startswith("warning: ")]
        [line] = [line for line in lines if "cut off" in line]
        assert line.startswith("note: ") and "restart.log" in line
    with when("the next restart runs to its end"):
        world.answer("WEB", "ready")
        _started, _report, results = world.run_to_end("--wait", "0.1")
        assert results["WEB"] == "back"
    with then("the doctor names the newest report"):
        lines = restart_lines(world)
        assert not [line for line in lines if line.startswith("warning: ") or "cut off" in line]
        assert [line for line in lines if "the last restart ended" in line and "report.md" in line]
