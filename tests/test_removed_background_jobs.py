"""An install from before hub dropped its background jobs updates and uninstalls cleanly.

Older versions ran the queue's flush as a launchd or systemd job, through a
shim in hub's state folder, and kept a record of each job. Apply and
uninstall now take all of that away. launchctl and systemctl are the
recording fakes, so the scenarios read back what hub asked them to do.
"""
import fcntl
import json
import os
import plistlib
import threading

import pytest

from chats import Chats
from steps import and_, given, then, when
from test_queue import Queue

JOB = "queue-flush"
LABEL = f"hubctl.{JOB}"
OLD_FLAGS = ["queue.flush_job", "queue.flush_job.every_seconds"]


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


@pytest.fixture
def queue(home, chats):
    return Queue(home, chats)


def plist_file(home):
    return os.path.join(home.root, "Library", "LaunchAgents", f"{LABEL}.plist")


def unit_files(home):
    folder = os.path.join(home.root, ".config", "systemd", "user")
    return [os.path.join(folder, f"hubctl-{JOB}.{suffix}") for suffix in ("service", "timer")]


def shim(home):
    return os.path.join(home.state, "bin", "hubctl")


def record(home):
    return os.path.join(home.state, "jobs.json")


def log_file(home):
    return os.path.join(home.state, "logs", f"job-{JOB}.log")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)


def old_install(home, backend="launchd", job_file_left=True, state_left=True):
    """What an older hub left on disk: the shim, the job record, the job's files, its log, and the loaded job.
    With state_left false, only the job's own files and the loaded job are left, as when the state folder is gone."""
    if state_left:
        write(shim(home), "#!/bin/sh\nexec python3 -I \"$root/lib/hub_switch.py\" \"$@\"\n")
        os.chmod(shim(home), 0o755)
        write(log_file(home), "")
    command = [shim(home), "--script", "parts/queue/queue.py"]
    if backend == "launchd":
        paths, loaded = [plist_file(home)], {"launchd": [LABEL], "systemd": []}
        if job_file_left:
            os.makedirs(os.path.dirname(plist_file(home)), exist_ok=True)
            with open(plist_file(home), "wb") as handle:
                plistlib.dump({"Label": LABEL, "ProgramArguments": [*command, "flush"], "StartInterval": 30}, handle)
        else:
            loaded = {"launchd": [], "systemd": []}
    else:
        paths, loaded = unit_files(home), {"launchd": [], "systemd": [f"hubctl-{JOB}.timer"]}
        for path in paths:
            write(path, "[Unit]\n")
    home.write_json(os.path.join(home.env["HUB_FAKE_TOOLS"], "state.json"), loaded)
    if not state_left:
        return
    home.write_json(record(home), {JOB: {
        "part": "queue", "command": [*command, "flush"], "every_seconds": 30, "at_login": False,
        "keep_alive": False, "start": [*command, "on"], "stop": [*command, "stop"], "backend": backend,
        "files": paths, "written": "2026-11-01T10:00:00Z", "load_exit": 0}})
    data = home.settings()
    data["flags"]["queue.flush_job"] = {"by": "user", "value": "on"}
    data["flags"]["queue.flush_job.every_seconds"] = {"by": "user", "value": 30}
    home.write_json(home.user_file, data)


def calls(home, tool):
    path = os.path.join(home.env["HUB_FAKE_TOOLS"], "calls.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [line["args"] for line in map(json.loads, handle) if line["tool"] == tool]


def loaded(home):
    with open(os.path.join(home.env["HUB_FAKE_TOOLS"], "state.json")) as handle:
        return json.load(handle)


def nothing_left(home, *paths, bin_folder_gone=True):
    folders = [os.path.dirname(shim(home))] if bin_folder_gone else []
    left = [path for path in (shim(home), record(home), log_file(home), *folders, *paths) if os.path.lexists(path)]
    assert left == [], left


def queue_is_off(home):
    return os.path.exists(os.path.join(home.state, "queue", "off"))


def test_an_update_takes_away_the_old_flush_job_and_its_shim_and_the_queue_sends_from_its_hooks(home, queue):
    with given("an older install with the queue on, its flush job loaded in launchd, the shim and the job record"):
        queue.on()
        old_install(home)
    with when("the updated hub applies its settings"):
        applied = home.step("apply")
    with then("the job is booted out of launchd, and its plist, the shim, the record and the log are gone"):
        assert ["bootout", f"gui/{os.getuid()}/{LABEL}"] in calls(home, "launchctl")
        assert loaded(home)["launchd"] == []
        nothing_left(home, plist_file(home))
        assert "Traceback" not in applied.stdout + applied.stderr
    with and_("the job's old stop delivered nothing it should not, and the queue is still on"):
        assert not os.path.exists(os.path.join(home.state, "queue", "off"))
    with when("LOBBY sends WEB a message"):
        queue.start("LOBBY", "WEB")
        sent = queue.send("LOBBY", queue.address("WEB"), "The build is green.")
    with then("it is held, with no flush job running"):
        assert sent.refused and [item["body"] for item in queue.held("WEB")] == ["The build is green."]
    with when("the quiet time passes and LOBBY's next prompt runs a hook"):
        queue.wait(3)
        queue.chats.send("LOBBY", "UserPromptSubmit", prompt="next task")
    with then("that hook sends the message on to WEB"):
        posts = queue.posts("WEB", wait=10)
        assert len(posts) == 1 and "The build is green." in posts[0][0]["message"]["content"]
    with and_("show and the doctor name no flush job flag, and hub logged no error"):
        assert not set(OLD_FLAGS) & set(home.show("--all")["flags"])
        doctor = home.step("doctor")
        assert "flush_job" not in doctor.stdout + doctor.stderr and "job " not in doctor.stdout
        assert home.errors() == ""
    with when("the user then changes another queue setting"):
        home.step("set", "queue.quiet_seconds", "5")
    with then("the change goes through, and the settings file no longer holds the old flags"):
        assert home.show()["flags"]["queue.quiet_seconds"]["value"] == 5
        assert not set(OLD_FLAGS) & set(home.settings()["flags"])


def test_the_cleanup_with_no_record_a_plist_already_gone_or_systemd_instead_of_launchd(home):
    with given("hub is installed with the queue on, and only the old shim is left, with no job record"):
        home.install()
        home.step("on", "queue")
        write(shim(home), "#!/bin/sh\n")
    with when("apply runs"):
        home.step("apply")
    with then("the shim is gone, and launchctl and systemctl were never called"):
        nothing_left(home)
        assert calls(home, "launchctl") == [] and calls(home, "systemctl") == []
    with given("a launchd record whose plist is already gone and whose job is not loaded"):
        old_install(home, job_file_left=False)
    with when("apply runs"):
        applied = home.step("apply")
    with then("the bootout is asked for anyway, its failure is ignored, and nothing is left"):
        assert ["bootout", f"gui/{os.getuid()}/{LABEL}"] in calls(home, "launchctl")
        nothing_left(home, plist_file(home))
        assert home.errors() == "" and "Traceback" not in applied.stderr
    with given("a systemd record with its service and timer units and a running timer"):
        old_install(home, backend="systemd")
    with when("apply runs"):
        home.step("apply")
    with then("the timer is disabled and stopped, the units are gone, and systemd reloads its units"):
        made = calls(home, "systemctl")
        assert ["--user", "disable", "--now", f"hubctl-{JOB}.timer"] in made
        assert ["--user", "daemon-reload"] in made
        assert loaded(home)["systemd"] == []
        nothing_left(home, *unit_files(home))
    with and_("the queue is still on"):
        assert not os.path.exists(os.path.join(home.state, "queue", "off"))


def test_uninstall_takes_away_an_old_flush_job_too(home):
    with given("an older install with the queue on and its flush job loaded in launchd"):
        home.install()
        home.step("on", "queue")
        old_install(home)
    with when("the user uninstalls hub before any update applied"):
        home.step("uninstall")
    with then("the job is booted out, and its plist, the shim and the record are gone"):
        assert ["bootout", f"gui/{os.getuid()}/{LABEL}"] in calls(home, "launchctl")
        assert loaded(home)["launchd"] == []
        nothing_left(home, plist_file(home))


def test_the_old_flush_job_is_found_by_its_own_name_when_the_state_folder_holds_nothing_of_it(home):
    with given("hub is installed with the queue on, and the state folder has no shim, record or log of the old job"):
        home.install()
        home.step("on", "queue")
    with and_("the old flush job is still loaded in launchd from its plist, as after the state folder moved"):
        old_install(home, state_left=False)
    with when("apply runs"):
        applied = home.step("apply")
    with then("the job is booted out and its plist is gone"):
        assert ["bootout", f"gui/{os.getuid()}/{LABEL}"] in calls(home, "launchctl")
        assert loaded(home)["launchd"] == []
        nothing_left(home, plist_file(home))
        assert home.errors() == "" and "Traceback" not in applied.stderr
    with given("the old flush job's systemd units are left the same way, and its timer runs"):
        old_install(home, backend="systemd", state_left=False)
    with when("apply runs"):
        home.step("apply")
    with then("the timer is disabled and stopped, and the units are gone"):
        assert ["--user", "disable", "--now", f"hubctl-{JOB}.timer"] in calls(home, "systemctl")
        assert loaded(home)["systemd"] == []
        nothing_left(home, *unit_files(home))
    with and_("the queue is still on"):
        assert not queue_is_off(home)


def test_the_bin_folder_goes_with_the_shim_unless_something_else_is_in_it(home):
    with given("an older install whose bin folder also holds a file the user put there"):
        home.install()
        home.step("on", "queue")
        old_install(home)
        write(os.path.join(os.path.dirname(shim(home)), "notes.txt"), "mine\n")
    with when("apply runs"):
        home.step("apply")
    with then("the shim is gone, and the bin folder stays with the user's file"):
        nothing_left(home, plist_file(home), bin_folder_gone=False)
        assert os.listdir(os.path.dirname(shim(home))) == ["notes.txt"]


def test_an_apply_that_fails_right_after_the_old_stop_still_leaves_the_queue_on(home, queue):
    with given("an older install with the queue on and its flush job loaded in launchd"):
        queue.on()
        old_install(home)
    with and_("the apply log can not be written, so the apply fails after the old job's stop"):
        log = os.path.join(home.state, "applies.jsonl")
        os.unlink(log)
        os.makedirs(log)
    with when("the updated hub applies its settings"):
        home.step("apply", check=False)
    with and_("the log can be written again, and apply runs once more"):
        os.rmdir(log)
        home.step("apply")
    with then("the old job is gone and the queue is on"):
        nothing_left(home, plist_file(home))
        assert not queue_is_off(home)
    with when("LOBBY sends WEB a message"):
        queue.start("LOBBY", "WEB")
        sent = queue.send("LOBBY", queue.address("WEB"), "Still holding?")
    with then("it is held"):
        assert sent.refused and [item["body"] for item in queue.held("WEB")] == ["Still holding?"]


def test_an_old_stop_that_times_out_at_uninstall_is_followed_by_the_new_stop(home, queue):
    with given("the queue is on, and LOBBY's message to WEB is held"):
        queue.on()
        queue.start("LOBBY", "WEB")
        assert queue.send("LOBBY", queue.address("WEB"), "Do not drop me.").refused
    with and_("an older install's flush job is still recorded, and its stop gets 3 seconds"):
        old_install(home)
        source = os.path.join(home.plugin, "lib", "hub", "old_jobs.py")
        with open(source) as handle:
            code = handle.read()
        assert "STOP_TIMEOUT = 120" in code
        with open(source, "w") as handle:
            handle.write(code.replace("STOP_TIMEOUT = 120", "STOP_TIMEOUT = 3"))
    with and_("another flush holds the queue lock for 6 seconds, so the old stop runs out of time"):
        lock_path = os.path.join(home.state, "queue", "lock")
        lock = open(lock_path, "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        release = threading.Timer(6, lock.close)
        release.start()
    with when("the user uninstalls hub"):
        try:
            home.step("uninstall")
        finally:
            release.cancel()
            lock.close()
    with then("the new stop ran after the old one was cut off, so WEB got the message once and nothing is held"):
        posts = queue.posts("WEB", wait=2)
        assert len(posts) == 1 and "Do not drop me." in posts[0][0]["message"]["content"]
        assert queue.held("WEB") == []
