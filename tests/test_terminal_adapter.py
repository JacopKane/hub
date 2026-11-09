"""Slice 10: the terminal adapter.

It tells which terminal a chat runs in, finds that chat's tmux pane or iTerm
tab from the tty in its session record, and types a command there, as a
restart does to resume a chat. tmux is real, on a private server that the
test starts and ends. iTerm is reached through osascript, which is the
recording fake in tests/fakes/os_tools.py, because driving the real app is
unsafe. A fake tmux that says no server runs stands in wherever a scenario
must not reach a tmux server.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import PLUGIN, REPO
from steps import and_, given, then, when

sys.path.insert(0, os.path.join(PLUGIN, "lib"))
from hub import sessions  # noqa: E402
from hub.adapters import terminal  # noqa: E402

FAKE = os.path.join(REPO, "tests", "fakes", "os_tools.py")
REAL_TMUX = shutil.which("tmux")


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Recording osascript first on PATH; real_tmux=False also puts the fake tmux there."""
    tools, folder = tmp_path / "fake-tools", tmp_path / "fake-bin"
    tools.mkdir()
    folder.mkdir()
    monkeypatch.setenv("HUB_FAKE_TOOLS", str(tools))
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")

    def add(*names):
        for name in names:
            shutil.copy(FAKE, folder / name)
            os.chmod(folder / name, 0o755)

    def calls(tool):
        path = tools / "calls.jsonl"
        if not path.exists():
            return []
        return [entry for entry in map(json.loads, path.read_text().splitlines()) if entry["tool"] == tool]

    def iterm(rows):
        (tools / "iterm.json").write_text(json.dumps(rows))

    add("osascript")
    return {"add": add, "calls": calls, "iterm": iterm}


@pytest.fixture
def tmux_server():
    if REAL_TMUX is None:
        pytest.fail("tmux is missing; the test image and macOS CI both install it")
    name = f"zztest{os.getpid()}"
    yield name
    subprocess.run([REAL_TMUX, "-L", name, "kill-server"], capture_output=True)


def tmux(server, *args):
    return subprocess.run([REAL_TMUX, "-L", server, *args], capture_output=True, text=True, check=True).stdout.strip()


def test_the_terminal_is_tmux_inside_any_app_then_iterm_then_plain():
    with then("TMUX set means tmux, even inside iTerm, because the pane is what a restart reopens"):
        assert terminal.detect({"TMUX": "/tmp/tmux-1/default,1,0", "TERM_PROGRAM": "iTerm.app"}) == "tmux"
    with and_("iTerm alone is iterm, and anything else is plain"):
        assert terminal.detect({"TERM_PROGRAM": "iTerm.app"}) == "iterm"
        assert terminal.detect({"TERM_PROGRAM": "Apple_Terminal"}) == "plain"
        assert terminal.detect({}) == "plain"


def test_a_chat_in_a_tmux_pane_is_found_by_its_tty_and_gets_the_resume_command(tmp_path, fakes, tmux_server):
    with given("a shell runs in a pane of a private tmux server"):
        tmux(tmux_server, "new-session", "-d", "-x", "100", "-y", "20", "sh")
        pane_pid = int(tmux(tmux_server, "display-message", "-p", "#{pane_pid}"))
    with and_("the chat's session record holds that shell's tty, as sessions.note writes it"):
        record = {"session": "WEB", "pid": pane_pid, "tty": sessions.process_info(pane_pid)["tty"]}
        assert record["tty"]
    with when("the adapter locates the chat"):
        location = terminal.locate(record, tmux_server=tmux_server)
    with then("it names the tmux pane on that server"):
        assert location == {"kind": "tmux", "server": tmux_server, "pane": tmux(tmux_server, "display-message", "-p",
                                                                                 "#{pane_id}")}
    with when("a command with quotes and spaces is typed into it"):
        out = tmp_path / "resumed.txt"
        assert terminal.type_into(location, f"echo 'resumed \"WEB\"' > {out}") is True
    with then("the shell in that pane ran it"):
        deadline = time.time() + 10
        while time.time() < deadline and not out.exists():
            time.sleep(0.05)
        assert out.read_text().strip() == 'resumed "WEB"'
    with and_("focus selects that pane"):
        assert terminal.focus(location) is True
    with and_("iTerm was never asked"):
        assert fakes["calls"]("osascript") == []
    with when("a record has a tty no pane holds"):
        lost = terminal.locate({"session": "DOCS", "tty": "ttys999"}, tmux_server=tmux_server, kinds=("tmux",))
    with then("there is no location"):
        assert lost is None


def test_a_chat_in_an_iterm_tab_is_found_and_typed_into_through_osascript_only(fakes):
    with given("no tmux server runs, and iTerm has two sessions"):
        fakes["add"]("tmux")
        fakes["iterm"]([["A1B2-WEB", "/dev/ttys004"], ["C3D4-DOCS", "/dev/ttys007"]])
    with when("the adapter locates the chat whose record holds ttys007"):
        location = terminal.locate({"session": "DOCS", "tty": "ttys007"})
    with then("it names DOCS's iTerm session"):
        assert location == {"kind": "iterm", "session": "C3D4-DOCS"}
    with when("the resume command is typed there, with a quote and a backslash in it"):
        assert terminal.type_into(location, 'claude --resume "DOCS" --name D\\x') is True
    with then("osascript got one script that writes the escaped text to that session only"):
        script = " ".join(fakes["calls"]("osascript")[-1]["args"])
        assert 'if id of each_session is "C3D4-DOCS" then' in script
        assert 'write text "claude --resume \\"DOCS\\" --name D\\\\x"' in script
    with when("focus is asked for"):
        assert terminal.focus(location) is True
    with then("the script selects that session"):
        assert "select" in " ".join(fakes["calls"]("osascript")[-1]["args"])


def test_a_plain_terminal_has_no_location_so_nothing_is_typed(fakes):
    with given("no tmux server runs and iTerm has no session with the chat's tty"):
        fakes["add"]("tmux")
        fakes["iterm"]([["A1B2-WEB", "/dev/ttys004"]])
    with when("the adapter locates a chat in a plain terminal"):
        location = terminal.locate({"session": "API", "tty": "ttys012"})
    with then("there is no location, and typing or focusing without one does nothing"):
        assert location is None
        assert terminal.type_into(None, "claude --resume API") is False
        assert terminal.focus(None) is False
    with and_("a record without a tty is never matched"):
        assert terminal.locate({"session": "API", "tty": ""}) is None
