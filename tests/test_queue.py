"""Slice 10: the message queue, Claude Code only.

A message from one main chat to another idle one waits a short time, so two
quick messages wake the receiver once. An urgent message, a message from an
agent and every send the queue can not vouch for take the native path. Each
chat is the hook input it would send, fed to the real dispatcher, with a fake
`claude agents --json`, fake chat processes and real inbox sockets. The flush
is the real queue.py command. caffeinate is the recording fake, because a
real one would keep the test machine awake.
"""
import fcntl
import json
import os
import plistlib
import shlex
import shutil
import sys
import threading
import time

import pytest

from chats import SESSIONS, Chats
from steps import and_, given, then, when

@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


class Queue:
    def __init__(self, home, chats):
        self.home, self.chats = home, chats
        self.script = os.path.join(home.plugin, "parts", "queue", "queue.py")
        self.live = {}
        self.now = int(time.time())

    def on(self, quiet=2, limit=60, every=1, chat_names=False):
        self.set_clock()
        self.home.install()
        self.home.hubctl("on", "queue")
        if chat_names:
            self.home.hubctl("on", "chat_names")
        self.home.hubctl("set", "queue.quiet_seconds", str(quiet))
        self.home.hubctl("set", "queue.hold_seconds", str(limit))
        self.home.hubctl("set", "queue.flush_job.every_seconds", str(every))

    def start(self, *names, version="2.1.319"):
        self.chats.set_version(version)
        for name in names:
            chat = self.chats.start(name, title=name, live=True, inbox=True)
            chat.listener = self.chats.sockets[-1]
            self.live[name] = chat
            transcript = self.chats.input(name, "Stop")["transcript_path"]
            os.makedirs(os.path.dirname(transcript), exist_ok=True)
            with open(transcript, "w") as handle:
                handle.write(json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}) + "\n")
        self.listing()

    def set_clock(self):
        """The queue's times come from the fixed test clock, so a busy machine never ages a heartbeat or a hold."""
        self.home.env["HUB_TEST_NOW"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now))

    def wait(self, seconds):
        """That much queue time passes, and nothing else happens."""
        self.now += seconds
        self.set_clock()

    def resume(self, name):
        """The chat ends and comes back in the same session on a new address, as a resume or an upgrade does."""
        self.live[name].end()
        chat = self.chats.start(name, title=name, live=True, inbox=True, socket_name=name.lower() + "-resumed")
        chat.listener = self.chats.sockets[-1]
        self.live[name] = chat
        self.listing()

    def listing(self, **status):
        self.chats.set_listing([self.chats.row(name, pid=chat.pid, status=status.get(name, "idle"))
                                for name, chat in self.live.items()])

    def send(self, sender, to, message, **extra):
        self.home.env["CLAUDE_CODE_MESSAGING_SOCKET"] = self.live[sender].address
        try:
            return self.chats.tool(sender, "SendMessage", {"to": to, "message": message}, **extra)
        finally:
            del self.home.env["CLAUDE_CODE_MESSAGING_SOCKET"]

    def address(self, name):
        return "uds:" + self.live[name].address

    def flush(self, *args):
        return self.home.run([sys.executable, "-I", self.script, "flush", *args])

    def command(self, *args):
        return self.home.run([sys.executable, "-I", self.script, *args])

    def held(self, name):
        path = os.path.join(self.home.state, "queue", "held", SESSIONS[name] + ".jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def events(self, kind):
        path = os.path.join(self.home.state, "queue", "events.jsonl")
        with open(path) as handle:
            return [event for event in map(json.loads, handle) if event["event"] == kind]

    def posts(self, name, wait=0.3):
        """Each connection that reached the chat's inbox within wait seconds, as its list of lines."""
        listener, found = self.live[name].listener, []
        deadline = time.time() + wait
        while True:
            listener.settimeout(max(0.01, deadline - time.time()))
            try:
                connection, _ = listener.accept()
            except (TimeoutError, OSError):
                return found
            connection.settimeout(5)
            data = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                data += chunk
            connection.close()
            found.append([json.loads(line) for line in data.decode().splitlines()])
            wait = 0  # take what already came, then stop


@pytest.fixture
def queue(home, chats):
    return Queue(home, chats)


def recorded(home, tool, wait=10):
    """The arguments of each call to the recording fake. caffeinate starts detached, so wait for its first line."""
    path = os.path.join(home.env["HUB_FAKE_TOOLS"], "calls.jsonl")
    deadline = time.time() + wait
    while True:
        calls = []
        if os.path.exists(path):
            with open(path) as handle:
                calls = [json.loads(line) for line in handle if line.endswith("\n")]
        found = [call["args"] for call in calls if call["tool"] == tool]
        if found or time.time() > deadline:
            return found
        time.sleep(0.05)


def queued_text(home):
    with open(os.path.join(home.plugin, "parts", "queue", "text", "queued.md")) as handle:
        return handle.read().strip().split("{")[0]


def test_two_quick_messages_to_an_idle_chat_wake_it_once_and_an_urgent_one_passes(home, queue):
    with given("the queue is on, its flush has run once, and LOBBY and WEB are idle live chats"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
    with when("LOBBY sends WEB two messages within the quiet time"):
        first = queue.send("LOBBY", queue.address("WEB"), "The build is green.")
        second = queue.send("LOBBY", queue.address("WEB"), "Docs are merged too.")
    with then("both are held, and LOBBY is told they are queued and how to send now"):
        assert first.refused and second.refused
        assert queued_text(home) in first.reason and "WEB" in first.reason and "[urgent]" in first.reason
        assert [item["body"] for item in queue.held("WEB")] == ["The build is green.", "Docs are merged too."]
    with and_("each hold keeps the machine awake past the limit"):
        awake = recorded(home, "caffeinate")
        assert awake and awake[0][:2] == ["-i", "-t"] and int(awake[0][2]) > 60
    with when("a flush runs before the quiet time has passed"):
        queue.flush()
    with then("nothing reaches WEB yet"):
        assert queue.posts("WEB") == []
    with when("the quiet time passes and the flush runs"):
        queue.wait(2)
        queue.flush()
    with then("one connection brings WEB one line with both messages, oldest first, from LOBBY"):
        posts = queue.posts("WEB")
        assert len(posts) == 1 and len(posts[0]) == 1
        line = posts[0][0]
        content = line["message"]["content"]
        assert line["from"] == queue.address("LOBBY") and line["type"] == "user"
        assert '<cross-session-message from="%s"' % queue.address("LOBBY") in content
        assert 'from-name="LOBBY"' in content
        assert "2 queued messages from this chat, oldest first." in content
        assert content.index("The build is green.") < content.index("Docs are merged too.")
        assert queue.held("WEB") == []
        assert len(queue.events("delivered")) == 2 and queue.events("posted")[0]["reason"] == "quiet"
    with when("LOBBY holds one more, then sends a message that starts with [urgent]"):
        assert queue.send("LOBBY", queue.address("WEB"), "One more thing.").refused
        urgent = queue.send("LOBBY", queue.address("WEB"), "[urgent] Stop the deploy.")
    with then("the urgent one goes natively, and what waited went first, so the order holds"):
        assert not urgent.refused
        posts = queue.posts("WEB")
        assert len(posts) == 1 and "One more thing." in posts[0][0]["message"]["content"]
        assert queue.held("WEB") == []
    with when("a message comes from one of LOBBY's agents"):
        from_agent = queue.send("LOBBY", queue.address("WEB"), "Agent result.", agent_id="agent-1")
    with then("it goes at once"):
        assert not from_agent.refused and queue.held("WEB") == []
    with when("LOBBY holds a message, and WEB wakes for another reason before the flush"):
        assert queue.send("LOBBY", queue.address("WEB"), "Read this when you wake.").refused
        queue.chats.send("WEB", "UserPromptSubmit", prompt="next task")
    with then("what waited for WEB goes with that wake"):
        posts = queue.posts("WEB", wait=10)
        assert len(posts) == 1 and "Read this when you wake." in posts[0][0]["message"]["content"]
    with when("WEB turns busy, and the flush job runs again"):
        queue.listing(WEB="busy")
        queue.flush()
        busy = queue.send("LOBBY", queue.address("WEB"), "Busy now?")
    with then("a send to it is not held"):
        assert not busy.refused
        reasons = {event["reason"] for event in queue.events("passed")}
        assert {"urgent", "from-agent", "busy"} <= reasons


def test_the_queue_holds_a_message_with_the_address_chat_names_resolved(queue):
    with given("chat_names and the queue are on, and DOCS is idle"):
        queue.on(chat_names=True)
        queue.start("WEB", "DOCS")
        queue.flush()
    with when("WEB sends a message to \"DOCS\" by name"):
        sent = queue.send("WEB", "DOCS", "Please review the README.")
    with then("the held message carries the live DOCS chat's own address and session"):
        assert sent.refused
        [item] = queue.held("DOCS")
        assert item["receiver_socket"] == queue.live["DOCS"].address
        assert item["receiver_session"] == SESSIONS["DOCS"] and item["sender_name"] == "WEB"


def test_off_first_then_flush_delivers_what_waits_exactly_once_and_holds_nothing_new(queue):
    with given("two messages wait for WEB"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
        queue.send("LOBBY", queue.address("WEB"), "First.")
        queue.send("LOBBY", queue.address("WEB"), "Second.")
        assert len(queue.held("WEB")) == 2
    with when("the queue is turned off first"):
        queue.command("off")
    with then("nothing is sent yet, so the off step alone loses and sends nothing"):
        assert queue.posts("WEB") == [] and len(queue.held("WEB")) == 2
    with when("the forced flush runs"):
        queue.flush("--force")
    with then("both messages reach WEB in one post"):
        posts = queue.posts("WEB")
        assert len(posts) == 1 and "First." in posts[0][0]["message"]["content"]
        assert "Second." in posts[0][0]["message"]["content"]
    with when("the forced flush runs again, and LOBBY sends one more message"):
        queue.flush("--force")
        later = queue.send("LOBBY", queue.address("WEB"), "Third.")
    with then("nothing reaches WEB twice, and the new message is not held"):
        assert queue.posts("WEB") == []
        assert not later.refused and queue.held("WEB") == []
        assert queue.events("passed")[-1]["reason"] == "off"
    with when("the queue is turned on again"):
        queue.command("on")
        queue.flush()
    with then("messages are held again"):
        assert queue.send("LOBBY", queue.address("WEB"), "Fourth.").refused


def test_a_send_that_races_the_stop_is_never_left_held_with_no_flush_to_come(queue):
    with given("the queue is on, and LOBBY and WEB are idle live chats"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
    with and_("the stop's last flush holds the queue lock"):
        lock_path = os.path.join(queue.home.state, "queue", "lock")
        lock = open(lock_path, "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        os.utime(lock_path, (0, 0))
    with when("LOBBY sends WEB a message, which checks the off switch and then waits for the lock"):
        sent = {}
        sender = threading.Thread(target=lambda: sent.update(result=queue.send("LOBBY", queue.address("WEB"),
                                                                             "Late.")))
        sender.start()
        deadline = time.time() + 10
        while os.path.getmtime(lock_path) == 0 and time.time() < deadline:
            time.sleep(0.02)
        assert os.path.getmtime(lock_path) != 0, "the send never reached the lock"
    with and_("the stop turns the queue off and its flush lets go of the lock"):
        open(os.path.join(queue.home.state, "queue", "off"), "a").close()
        lock.close()
        sender.join(30)
    with then("the message is not held, since no flush would ever deliver it, and it goes natively"):
        assert queue.held("WEB") == []
        assert not sent["result"].refused
        assert queue.events("passed")[-1]["reason"] == "off"


def test_a_held_message_is_posted_from_the_address_its_sender_has_now(queue):
    with given("LOBBY's message to WEB is held"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
        before = queue.address("LOBBY")
        assert queue.send("LOBBY", queue.address("WEB"), "Reply to me.").refused
    with when("LOBBY is resumed on a new address while the message waits, and the forced flush runs"):
        queue.resume("LOBBY")
        assert queue.address("LOBBY") != before
        queue.flush("--force")
    with then("WEB gets the message from LOBBY's new address, so a reply reaches LOBBY"):
        posts = queue.posts("WEB")
        assert len(posts) == 1 and "Reply to me." in posts[0][0]["message"]["content"]
        assert posts[0][0]["from"] == queue.address("LOBBY")
        assert f'from="{queue.address("LOBBY")}"' in posts[0][0]["message"]["content"]
        assert before not in posts[0][0]["message"]["content"]


def test_the_tested_versions_guard_is_an_option_and_a_changed_version_hands_the_message_back(home, queue):
    with given("the queue is on, and every chat runs a Claude Code version the queue has not tested"):
        queue.on(limit=7)
        queue.start("LOBBY", "WEB", version="9.0.0")
        queue.flush()
    with when("LOBBY sends WEB a message"):
        untested = queue.send("LOBBY", queue.address("WEB"), "Hello.")
    with then("it goes natively, and the reason is counted"):
        assert not untested.refused and queue.events("passed")[-1]["reason"] == "untested-version"
    with when("the user adds that version to the tested list"):
        home.hubctl_from_mod("set", "queue.version_guard.versions", "2.1.319", "9.0.0")
    with then("the message is held"):
        assert queue.send("LOBBY", queue.address("WEB"), "Held now.").refused
    with when("WEB restarts on another version while the message waits, and the flush runs"):
        path = os.path.join(home.state, "sessions", SESSIONS["WEB"] + ".json")
        with open(path) as handle:
            record = json.load(handle)
        with open(path, "w") as handle:
            json.dump(dict(record, version="9.1.0"), handle)
        queue.flush()
    with then("the message is posted once, saved, and LOBBY is told to send it again"):
        assert len(queue.posts("WEB")) == 1
        notices = queue.posts("LOBBY")
        assert len(notices) == 1 and "may not have arrived" in notices[0][0]["message"]["content"]
        assert "Held now." in notices[0][0]["message"]["content"]
        with open(os.path.join(home.state, "queue", "undelivered.jsonl")) as handle:
            assert json.loads(handle.readline())["body"] == "Held now."
    with when("the user switches the guard off in /hub"):
        home.hubctl_from_mod("off", "queue.version_guard")
    with then("a chat on any version gets held messages"):
        assert queue.send("LOBBY", queue.address("WEB"), "Any version.").refused
    with when("WEB ends while the message waits past the limit"):
        queue.live["WEB"].end()
        queue.wait(1)
        queue.flush()
    with then("LOBBY is told it could not be delivered, with its text"):
        notices = queue.posts("LOBBY")
        assert len(notices) == 1 and "could not be delivered" in notices[0][0]["message"]["content"]
        assert "Any version." in notices[0][0]["message"]["content"] and queue.held("WEB") == []


def test_with_no_flush_running_nothing_new_is_held(queue):
    with given("the queue is on, but its flush has never run"):
        queue.on()
        queue.start("LOBBY", "WEB")
    with when("LOBBY sends WEB a message"):
        sent = queue.send("LOBBY", queue.address("WEB"), "Hello.")
    with then("it goes natively, because nothing would send it on"):
        assert not sent.refused and queue.events("passed")[-1]["reason"] == "no-heartbeat"


def undelivered(home):
    path = os.path.join(home.state, "queue", "undelivered.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle]


def mark_restarting(home, session, on=True):
    """What a restart run does around a chat's stop, through the same hub call, owned by this test's process."""
    call = "mark_restarting(%r, %d)" % (session, os.getpid()) if on else "clear_restarting(%r)" % session
    home.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); from hub import sessions; "
              "sessions.%s" % (os.path.join(home.plugin, "lib"), call)])


def test_a_give_up_notice_for_a_sender_that_is_down_waits_for_its_next_session_start(home, queue):
    with given("LOBBY's message waits for WEB"):
        queue.on(limit=7)
        queue.start("LOBBY", "WEB")
        queue.flush()
        assert queue.send("LOBBY", queue.address("WEB"), "Are you there?").refused
        old_lobby = queue.live["LOBBY"]
    with and_("LOBBY is down in the middle of a restart, and WEB is gone for good"):
        old_lobby.end()
        queue.live["WEB"].end()
        del queue.live["WEB"]
        queue.listing()
    with when("the message waits past the limit and the flush runs"):
        queue.wait(1)
        queue.flush()
    with then("the message is saved as undelivered, and no notice goes to LOBBY's old address"):
        assert [item["body"] for item in undelivered(home)] == ["Are you there?"]
        assert queue.held("WEB") == []
        queue.live["LOBBY"] = old_lobby
        assert queue.posts("LOBBY") == []
    with when("LOBBY comes back in the same session"):
        queue.resume("LOBBY")
    with then("its session start tells it the message to WEB was not delivered, with the text"):
        context = queue.live["LOBBY"].start_context
        assert "could not be delivered" in context and "WEB" in context and "Are you there?" in context
    with and_("its next turn does not tell it again"):
        assert "Are you there?" not in queue.chats.send("LOBBY", "UserPromptSubmit", prompt="next").context


def test_a_message_for_a_chat_that_is_restarting_waits_until_it_is_back_and_arrives_in_order(home, queue):
    with given("two messages from LOBBY wait for WEB"):
        queue.on(limit=7)
        queue.start("LOBBY", "WEB")
        queue.flush()
        assert queue.send("LOBBY", queue.address("WEB"), "First.").refused
        assert queue.send("LOBBY", queue.address("WEB"), "Second.").refused
    with and_("a restart marks WEB as restarting, as it does before it types /exit"):
        mark_restarting(home, SESSIONS["WEB"])
    with when("a forced flush runs while WEB is still at its prompt"):
        queue.flush("--force")
    with then("nothing reaches WEB, so no post can land during its /exit"):
        assert queue.posts("WEB") == [] and len(queue.held("WEB")) == 2
    with when("WEB exits, the messages wait past the limit, and the flush runs"):
        queue.live["WEB"].end()
        queue.wait(1)
        queue.flush()
    with then("they are still held, not given up"):
        assert len(queue.held("WEB")) == 2 and undelivered(home) == []
        assert queue.posts("LOBBY") == []
    with when("WEB comes back in the same session, the restart clears its mark, and the flush runs"):
        queue.resume("WEB")
        mark_restarting(home, SESSIONS["WEB"], on=False)
        queue.flush()
    with then("WEB gets both messages in one post, oldest first"):
        posts = queue.posts("WEB")
        assert len(posts) == 1
        content = posts[0][0]["message"]["content"]
        assert content.index("First.") < content.index("Second.") and queue.held("WEB") == []


# The flush job. It is the queue's only job, installed through the job adapter
# by hubctl apply while queue.flush_job runs. launchctl and systemctl are the
# recording fakes, so the job is read back from its file and the fake's state.

JOB = "queue-flush"


def runner():
    return "launchd" if os.uname().sysname == "Darwin" else "systemd"


def job_file(home):
    if runner() == "launchd":
        return os.path.join(home.root, "Library", "LaunchAgents", f"hubctl.{JOB}.plist")
    return os.path.join(home.root, ".config", "systemd", "user", f"hubctl-{JOB}.service")


def job_bytes(home):
    with open(job_file(home), "rb") as handle:
        return handle.read()


def job_command(home):
    if runner() == "launchd":
        return plistlib.loads(job_bytes(home))["ProgramArguments"]
    line = [line for line in job_bytes(home).decode().splitlines() if line.startswith("ExecStart=")][0]
    return shlex.split(line.split("=", 1)[1])


def job_interval(home):
    if runner() == "launchd":
        return plistlib.loads(job_bytes(home))["StartInterval"]
    timer = job_file(home).replace(".service", ".timer")
    with open(timer) as handle:
        line = [line for line in handle.read().splitlines() if line.startswith("OnUnitActiveSec=")][0]
    return int(line.split("=", 1)[1].rstrip("s"))


def job_loaded(home):
    path = os.path.join(home.env["HUB_FAKE_TOOLS"], "state.json")
    if not os.path.exists(path):
        return False
    with open(path) as handle:
        state = json.load(handle)
    if runner() == "launchd":
        return f"hubctl.{JOB}" in state["launchd"]
    return f"hubctl-{JOB}.timer" in state["systemd"]


def test_the_flush_job_follows_its_flag(home):
    with given("hub is installed and the queue is off"):
        home.install()
    with then("no flush job exists"):
        assert not os.path.exists(job_file(home)) and not job_loaded(home)
    with when("the user turns the queue on"):
        home.hubctl("on", "queue")
    with then("the flush job is loaded on the machine's job runner, every 30 seconds, through hub's shim"):
        assert job_loaded(home) and job_interval(home) == 30
        command = job_command(home)
        assert command[0] == os.path.join(home.state, "bin", "hubctl")
        assert command[1:] == ["--script", "parts/queue/queue.py", "flush"]
    with when("the user sets the job to every 10 seconds"):
        home.hubctl("set", "queue.flush_job.every_seconds", "10")
    with then("the job is written again with the new time"):
        assert job_loaded(home) and job_interval(home) == 10
    with when("the user switches only the flush job off"):
        home.hubctl("off", "queue.flush_job")
    with then("the job is unloaded and its files are gone"):
        assert not job_loaded(home) and not os.path.exists(job_file(home))


def test_a_moved_plugin_keeps_the_job_file_and_the_job_runs_the_new_copy(home):
    with given("the queue is on and its flush job is loaded"):
        home.install()
        home.hubctl("on", "queue")
        before = job_bytes(home)
    with when("the plugin copy moves to another folder and apply runs from there"):
        moved = os.path.join(home.root, "plugin-moved", "hub")
        os.makedirs(os.path.dirname(moved))
        shutil.move(home.plugin, moved)
        home.plugin = moved
        home.hubctl("apply")
    with then("the job file is the same, byte for byte"):
        assert job_bytes(home) == before and job_loaded(home)
    with and_("the job's own command runs the moved copy's flush, which writes the heartbeat"):
        with open(os.path.join(home.state, "bin", "hubctl")) as handle:
            assert os.path.realpath(moved) in handle.read()
        home.run(job_command(home))
        assert os.path.exists(os.path.join(home.state, "queue", "heartbeat"))


def test_turning_the_queue_off_delivers_what_waits_and_on_holds_again(home, queue):
    with given("the queue is on, and two messages wait for WEB"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
        queue.send("LOBBY", queue.address("WEB"), "First.")
        queue.send("LOBBY", queue.address("WEB"), "Second.")
        assert len(queue.held("WEB")) == 2 and job_loaded(home)
    with when("the user turns the queue off in /hub"):
        home.hubctl_from_mod("off", "queue")
    with then("the job is gone, and both messages reach WEB in one post before hubctl returns"):
        assert not job_loaded(home) and not os.path.exists(job_file(home))
        posts = queue.posts("WEB")
        assert len(posts) == 1 and "First." in posts[0][0]["message"]["content"]
        assert "Second." in posts[0][0]["message"]["content"] and queue.held("WEB") == []
    with and_("a new message goes natively"):
        assert not queue.send("LOBBY", queue.address("WEB"), "Third.").refused
    with when("the user turns the queue on again, and the job's first flush runs"):
        home.hubctl("on", "queue")
        queue.flush()
    with then("the job is back, and messages are held again"):
        assert job_loaded(home)
        assert queue.send("LOBBY", queue.address("WEB"), "Fourth.").refused


def test_turning_the_queue_off_waits_for_a_running_flush_and_still_delivers_everything(home, queue):
    with given("the queue is on, and two messages wait for WEB"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
        queue.send("LOBBY", queue.address("WEB"), "First.")
        queue.send("LOBBY", queue.address("WEB"), "Second.")
    with and_("a flush of the job holds the queue lock for four seconds"):
        lock = open(os.path.join(home.state, "queue", "lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        releaser = threading.Timer(4, lock.close)
        releaser.start()
    with when("the user turns the queue off in /hub"):
        home.hubctl_from_mod("off", "queue")
        releaser.join()
    with then("the job is gone, and both messages reach WEB in one post"):
        assert not job_loaded(home)
        posts = queue.posts("WEB")
        assert len(posts) == 1 and "First." in posts[0][0]["message"]["content"]
        assert "Second." in posts[0][0]["message"]["content"]
        assert queue.held("WEB") == [] and undelivered(home) == []


def test_messages_the_queue_can_not_deliver_as_it_goes_are_kept_and_each_sender_is_told_at_its_next_turn(home,
                                                                                                         queue):
    with given("the queue is on, and two messages from LOBBY wait for WEB"):
        queue.on()
        queue.start("LOBBY", "WEB")
        queue.flush()
        queue.send("LOBBY", queue.address("WEB"), "First.")
        queue.send("LOBBY", queue.address("WEB"), "Second.")
    with and_("WEB's inbox no longer takes connections"):
        queue.live["WEB"].listener.close()
    with when("the user turns the queue off in /hub"):
        printed = home.hubctl_from_mod("off", "queue").stdout
    with then("nothing is left held, and both messages are kept in the undelivered file"):
        assert queue.held("WEB") == []
        assert [item["body"] for item in undelivered(home)] == ["First.", "Second."]
    with and_("hubctl names the count and the file in one line"):
        saved = os.path.join(home.state, "queue", "undelivered.jsonl")
        lines = [line for line in printed.splitlines() if saved in line]
        assert len(lines) == 1 and "2 queued messages" in lines[0]
    with and_("LOBBY gets no post now, so the notice can not be lost in a post no one confirms"):
        assert queue.posts("LOBBY") == []
    with when("LOBBY's next turn starts"):
        context = queue.chats.send("LOBBY", "UserPromptSubmit", prompt="next task").context
    with then("LOBBY is told that each message to WEB was not delivered, with its text"):
        assert context.count("could not be delivered") == 2 and "WEB" in context
        assert "First." in context and "Second." in context
    with and_("the turn after that is not told again"):
        assert "First." not in queue.chats.send("LOBBY", "UserPromptSubmit", prompt="again").context


def test_uninstall_removes_every_job_even_when_a_part_folder_is_gone(home):
    with given("the queue is on with its flush job, and then the queue's part folder is deleted"):
        home.install()
        home.hubctl("on", "queue")
        assert job_loaded(home)
        shutil.rmtree(os.path.join(home.plugin, "parts", "queue"))
    with when("the user runs hubctl uninstall"):
        home.hubctl("uninstall")
    with then("the job is unloaded, its files are gone, and hub keeps no record of it"):
        assert not job_loaded(home) and not os.path.exists(job_file(home))
        assert not os.path.exists(os.path.join(home.state, "jobs.json"))


def doctor_lines(home):
    return home.hubctl("doctor").stdout.splitlines()


def test_the_doctor_names_the_flush_job_its_last_run_and_the_terminal(home):
    with given("the queue is on and its flush job is loaded"):
        home.install()
        home.hubctl("on", "queue")
    with when("the user runs hubctl doctor in a plain terminal"):
        lines = doctor_lines(home)
    with then("it names the job as loaded with its last exit code, and the terminal"):
        [job] = [line for line in lines if f"job {JOB}" in line]
        assert job.startswith("note: ") and "(queue) is loaded" in job
        assert ("last exit code 0" if runner() == "launchd" else "last result success") in job
        assert "note: terminal: plain" in lines
    with when("the same doctor runs inside iTerm"):
        home.env["TERM_PROGRAM"] = "iTerm.app"
        lines = doctor_lines(home)
        del home.env["TERM_PROGRAM"]
    with then("it names iTerm"):
        assert "note: terminal: iterm" in lines
    with when("the job's file is deleted"):
        os.unlink(job_file(home))
        lines = doctor_lines(home)
    with then("the doctor warns and names the fix"):
        [job] = [line for line in lines if f"job {JOB}" in line]
        assert job.startswith("warning: ") and "file of this job is missing" in job and "apply hub's settings" in job
    with when("the user runs hubctl apply"):
        home.hubctl("apply")
    with then("the job file is back and the warning is gone"):
        assert os.path.exists(job_file(home)) and job_loaded(home)
        assert not [line for line in doctor_lines(home) if line.startswith("warning: ") and JOB in line]
    with when("the job runner no longer has the job loaded"):
        path = os.path.join(home.env["HUB_FAKE_TOOLS"], "state.json")
        with open(path) as handle:
            state = json.load(handle)
        state = {kind: [name for name in names if JOB not in name] for kind, names in state.items()}
        with open(path, "w") as handle:
            json.dump(state, handle)
    with then("the doctor warns that it is not loaded and names the fix"):
        [job] = [line for line in doctor_lines(home) if f"job {JOB}" in line]
        assert job.startswith("warning: ") and "not loaded" in job and "apply hub's settings" in job


def test_the_doctor_says_nothing_about_jobs_or_the_terminal_while_no_chat_part_runs(home):
    with given("hub is installed with the queue off"):
        home.install()
    with then("the doctor names no job and no terminal"):
        lines = doctor_lines(home)
        assert not [line for line in lines if "job " in line or "terminal:" in line]
