"""Slice 10: the job adapter.

A part asks for a named job (run this every few seconds, at login, or keep it
running) and the adapter turns it into what the machine has: a launchd agent
on macOS, a systemd user service and timer on Linux, a plain loop process
where neither runs, or nothing at all. launchctl and systemctl are the
recording fakes in tests/fakes/os_tools.py, because loading a real job on the
machine that runs the tests is unsafe. The loop is a real process.
"""
import json
import os
import plistlib
import shutil
import sys
import time

import pytest

from conftest import PLUGIN, REPO
from steps import and_, given, then, when

sys.path.insert(0, os.path.join(PLUGIN, "lib"))
from hub.adapters import jobs  # noqa: E402

FAKE = os.path.join(REPO, "tests", "fakes", "os_tools.py")


class Machine:
    """A temp HOME with recording launchctl, systemctl and osascript first on PATH."""

    def __init__(self, root, patch):
        self.root = root
        self.tools = os.path.join(root, "fake-tools")
        self.bin = os.path.join(root, "fake-bin")
        os.makedirs(self.tools)
        os.makedirs(self.bin)
        for name in ("launchctl", "systemctl", "osascript", "tmux"):
            shutil.copy(FAKE, os.path.join(self.bin, name))
            os.chmod(os.path.join(self.bin, name), 0o755)
        patch.setenv("HOME", root)
        patch.setenv("XDG_CONFIG_HOME", os.path.join(root, ".config"))
        patch.setenv("XDG_STATE_HOME", os.path.join(root, ".local", "state"))
        patch.setenv("PATH", self.bin + os.pathsep + os.environ["PATH"])
        patch.setenv("HUB_FAKE_TOOLS", self.tools)

    def calls(self, tool):
        path = os.path.join(self.tools, "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [entry["args"] for entry in map(json.loads, handle) if entry["tool"] == tool]

    def path(self, *parts):
        return os.path.join(self.root, *parts)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    machine = Machine(str(tmp_path), monkeypatch)
    yield machine
    jobs.remove_all()  # stops any loop process a failed scenario left


def flush_job(every=30, **options):
    return jobs.Job(name="queue-flush", part="queue", command=jobs.shim_command("parts/queue/queue.py", "flush"),
                    every_seconds=every, **options)


def test_a_job_on_macos_is_a_launchd_agent_that_loads_reports_and_goes_away(machine):
    with given("no job is recorded"):
        assert jobs.recorded() == {}
    with when("the queue asks for its flush job every 30 seconds, also at login, through launchd"):
        jobs.install(flush_job(at_login=True), backend="launchd")
    with then("a plist in ~/Library/LaunchAgents runs hub's shim with the part's script"):
        plist_path = machine.path("Library", "LaunchAgents", "hubctl.queue-flush.plist")
        with open(plist_path, "rb") as handle:
            plist = plistlib.load(handle)
        shim = os.path.join(machine.root, ".local", "state", "hubctl", "bin", "hubctl")
        assert plist["Label"] == "hubctl.queue-flush"
        assert plist["ProgramArguments"] == [shim, "--script", "parts/queue/queue.py", "flush"]
        assert plist["StartInterval"] == 30 and plist["RunAtLoad"] is True and "KeepAlive" not in plist
    with and_("the shim runs the current plugin folder's script"):
        with open(shim) as handle:
            assert os.path.realpath(PLUGIN) in handle.read()
        assert os.access(shim, os.X_OK)
    with and_("launchctl was asked to load it in the user's own domain"):
        domain = f"gui/{os.getuid()}"
        assert ["bootstrap", domain, plist_path] in machine.calls("launchctl")
    with and_("the record lists the job with its part, backend and files, and status says it is loaded"):
        entry = jobs.recorded()["queue-flush"]
        assert entry["part"] == "queue" and entry["backend"] == "launchd" and entry["files"] == [plist_path]
        assert jobs.status("queue-flush")["loaded"] is True
    with when("the same job is asked for again"):
        before = len(machine.calls("launchctl"))
        jobs.install(flush_job(at_login=True), backend="launchd")
    with then("nothing is written or loaded again"):
        assert [call for call in machine.calls("launchctl")[before:] if call[0] != "print"] == []
    with when("the job changes to every 10 seconds"):
        jobs.install(flush_job(every=10, at_login=True), backend="launchd")
    with then("the old agent is unloaded before the new one loads"):
        verbs = [call[0] for call in machine.calls("launchctl")[before:] if call[0] != "print"]
        assert verbs == ["bootout", "bootstrap"]
        with open(plist_path, "rb") as handle:
            assert plistlib.load(handle)["StartInterval"] == 10
    with when("the job is removed"):
        assert jobs.remove("queue-flush") is True
    with then("launchctl unloads it, the plist is gone and the record no longer lists it"):
        assert ["bootout", f"{domain}/hubctl.queue-flush"] in machine.calls("launchctl")
        assert not os.path.exists(plist_path)
        assert jobs.recorded() == {}
        assert jobs.status("queue-flush") == {"name": "queue-flush", "recorded": False, "loaded": False,
                                              "files_present": False, "backend": None, "detail": "not recorded"}


def test_on_linux_a_job_is_a_systemd_user_timer_or_a_service_that_stays_up(machine):
    with when("the flush job is installed through systemd"):
        jobs.install(flush_job(), backend="systemd")
    with then("a service and a timer are written in ~/.config/systemd/user, and the timer is enabled now"):
        unit_dir = machine.path(".config", "systemd", "user")
        with open(os.path.join(unit_dir, "hubctl-queue-flush.service")) as handle:
            service = handle.read()
        with open(os.path.join(unit_dir, "hubctl-queue-flush.timer")) as handle:
            timer = handle.read()
        shim = os.path.join(machine.root, ".local", "state", "hubctl", "bin", "hubctl")
        assert f"ExecStart={shim} --script parts/queue/queue.py flush" in service
        assert "OnUnitActiveSec=30s" in timer and "OnBootSec=30s" in timer and "WantedBy=timers.target" in timer
        calls = machine.calls("systemctl")
        assert ["--user", "daemon-reload"] in calls
        assert ["--user", "enable", "--now", "hubctl-queue-flush.timer"] in calls
        assert jobs.status("queue-flush")["loaded"] is True
    with when("a job that must stay up starts at login with no interval"):
        jobs.install(jobs.Job(name="mail-watch", part="mailbox", command=jobs.shim_command("parts/x.py", "watch"),
                              at_login=True, keep_alive=True), backend="systemd")
    with then("it is a service with Restart=always, wanted at login, and has no timer"):
        with open(os.path.join(unit_dir, "hubctl-mail-watch.service")) as handle:
            service = handle.read()
        assert "Restart=always" in service and "WantedBy=default.target" in service
        assert not os.path.exists(os.path.join(unit_dir, "hubctl-mail-watch.timer"))
        assert ["--user", "enable", "--now", "hubctl-mail-watch.service"] in machine.calls("systemctl")
    with when("both jobs are removed"):
        jobs.remove("queue-flush")
        jobs.remove("mail-watch")
    with then("each unit is disabled, its files are gone, and systemd reloads"):
        calls = machine.calls("systemctl")
        assert ["--user", "disable", "--now", "hubctl-queue-flush.timer"] in calls
        assert ["--user", "disable", "--now", "hubctl-mail-watch.service"] in calls
        assert calls[-1] == ["--user", "daemon-reload"]
        assert os.listdir(unit_dir) == [] and jobs.recorded() == {}


def test_a_plain_loop_runs_the_job_where_no_job_runner_is_there_and_stops_with_it(machine):
    with given("a job whose command adds a line to a file"):
        out = machine.path("ticks.txt")
        job = jobs.Job(name="tick", part="demo", command=[sys.executable, "-c",
                       f"open({out!r}, 'a').write('tick\\n')"], every_seconds=1)
    with when("it is installed as a plain loop"):
        jobs.install(job, backend="loop")
    with then("a real background process runs the command every second"):
        deadline = time.time() + 10
        while time.time() < deadline and (not os.path.exists(out) or open(out).read().count("tick") < 2):
            time.sleep(0.1)
        assert open(out).read().count("tick") >= 2
    with and_("status says it is loaded, and the doctor learns that a loop does not come back after a restart"):
        status = jobs.status("tick")
        assert status["loaded"] is True and status["backend"] == "loop"
        assert "not start again after the computer restarts" in status["detail"]
        assert machine.calls("launchctl") == [] and machine.calls("systemctl") == []
    with when("the job is removed"):
        pid = jobs.recorded()["tick"]["pid"]
        jobs.remove("tick")
    with then("the loop process has ended and no more lines come"):
        deadline = time.time() + 5
        while time.time() < deadline and jobs.process_runs(pid):
            time.sleep(0.05)
        assert not jobs.process_runs(pid)
        count = open(out).read().count("tick")
        time.sleep(1.5)
        assert open(out).read().count("tick") == count


def test_with_no_job_runner_a_job_is_only_recorded_and_the_reason_is_named(machine):
    with when("the flush job is installed with the none backend"):
        jobs.install(flush_job(), backend="none")
    with then("no file is written, no tool is called, and status says it does not run and why"):
        assert not os.path.exists(machine.path("Library")) and not os.path.exists(machine.path(".config", "systemd"))
        assert machine.calls("launchctl") == [] and machine.calls("systemctl") == []
        status = jobs.status("queue-flush")
        assert status["recorded"] is True and status["loaded"] is False
        assert "no job runner" in status["detail"]


def test_the_backend_follows_the_machine_and_sync_keeps_only_the_wanted_jobs(machine):
    with then("macOS with launchctl takes launchd, Linux with a user systemd takes systemd, else a plain loop"):
        found = {"launchctl": "/bin/launchctl", "systemctl": "/usr/bin/systemctl"}
        assert jobs.backend_for("Darwin", found.get, {}) == "launchd"
        assert jobs.backend_for("Linux", found.get, {"XDG_RUNTIME_DIR": "/run/user/1000"}) == "systemd"
        assert jobs.backend_for("Linux", found.get, {}) == "loop"  # systemctl without a user session
        assert jobs.backend_for("Linux", {}.get, {"XDG_RUNTIME_DIR": "/run/user/1000"}) == "loop"
        assert jobs.backend_for("Darwin", found.get, {}, preferred="none") == "none"
    with given("two jobs are recorded, and one of them belongs to a part whose folder is gone"):
        jobs.install(flush_job(), backend="launchd")
        jobs.install(jobs.Job(name="old", part="gone_part", command=["true"], every_seconds=60), backend="launchd")
    with when("sync runs with only the flush job wanted"):
        lines = jobs.sync([flush_job()], backend="launchd")
    with then("the left-over job is unloaded and removed, and the wanted one stays as it was"):
        assert set(jobs.recorded()) == {"queue-flush"}
        assert not os.path.exists(machine.path("Library", "LaunchAgents", "hubctl.old.plist"))
        assert any("old" in line for line in lines)
    with when("every job is removed, as at uninstall"):
        jobs.remove_all()
    with then("the record is empty and no plist is left"):
        assert jobs.recorded() == {}
        assert os.listdir(machine.path("Library", "LaunchAgents")) == []


def stop_that_fails(counter, times):
    """A stop command that exits 1 the first `times` runs, then prints what it kept and exits 0."""
    script = ("import os, sys; path = %r; runs = len(open(path).read()) if os.path.exists(path) else 0; "
              "open(path, 'a').write('x'); ok = runs >= %d; print('2 queued messages kept' if ok else 'busy'); "
              "sys.exit(0 if ok else 1)" % (counter, times))
    return [sys.executable, "-c", script]


def test_a_stop_command_that_fails_runs_again_and_what_it_prints_reaches_the_user(machine):
    with given("a job whose stop command fails once, then prints a line"):
        counter = machine.path("stops.txt")
        jobs.install(jobs.Job(name="flaky", part="demo", command=["true"], every_seconds=60,
                              stop=stop_that_fails(counter, 1)), backend="launchd")
    with when("sync removes it"):
        lines = jobs.sync([], backend="launchd")
    with then("the stop ran twice, and its line reaches the user next to the removal"):
        assert open(counter).read() == "xx"
        assert "job flaky removed" in lines and "2 queued messages kept" in lines
    with given("a job whose stop command always fails"):
        counter = machine.path("never.txt")
        jobs.install(jobs.Job(name="broken", part="demo", command=["true"], every_seconds=60,
                              stop=stop_that_fails(counter, 99)), backend="launchd")
    with when("every job is removed, as at uninstall"):
        lines = jobs.remove_all()
    with then("the stop was tried three times, and one line names the failure and the job's log"):
        assert open(counter).read() == "xxx"
        failed = [line for line in lines if "broken" in line and "stop" in line]
        assert len(failed) == 1 and jobs.log_path("broken") in failed[0]
