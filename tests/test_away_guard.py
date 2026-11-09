"""away_guard: while the user is away, only urgent questions reach them."""
import os

from dialogs import answered, ask, prompt, question, set_clock, state_lines
from steps import and_, given, then, when

URGENT_LOG = "away_urgent.jsonl"


def test_while_alex_is_away_only_urgent_questions_reach_him(home):
    with given("away_guard is on, and the fixed clock is 61 minutes past Alex's last typed prompt"):
        home.install()
        home.hubctl("on", "away_guard")
        set_clock(home, "2026-03-02T08:00:00Z")
        prompt(home, "Add the layout test, then wait for me.")
        set_clock(home, "2026-03-02T09:01:00Z")

    with when("a chat opens a dialog with no urgent mark"):
        held = ask(home, [question()])
    with then("it is held, and the reason says what counts as urgent"):
        assert held.refused
        assert "urgent: <why>" in held.reason and "metadata.source" in held.reason
        assert "08:00" in held.reason or "silent" in held.reason

    with when('a dialog carries "urgent: the release breaks at 09:00"'):
        urgent = ask(home, [question()], metadata={"source": "urgent: the release breaks at 09:00"})
    with then("it opens, and the urgent log has one line"):
        assert urgent.ran
        lines = state_lines(home, URGENT_LOG)
        assert len(lines) == 1 and lines[0]["reason"] == "the release breaks at 09:00"

    with when("a prompt arrives from another session or from the harness"):
        note = prompt(home, "<task-notification>The tests finished.</task-notification>")
        peer = prompt(home, "Another Claude session sent a message: the build is green.")
        reminder = prompt(home, "<system-reminder>Context is low.</system-reminder>")
    with then("Alex still counts as away, and no prompt asks the chat to judge"):
        assert not (note.context or peer.context or reminder.context)
        assert ask(home, [question()]).refused
        assert "away" in home.hubctl("away", "status").stdout

    with when("Alex types a prompt"):
        back = prompt(home, "I am at my desk again, show me the open questions.")
    with then("the chat is asked to judge from his words whether he is back"):
        assert f"python3 -I {os.path.join(home.plugin, 'lib', 'hub_switch.py')} away clear" in back.context and "1 urgent pass" in back.context
    with and_("a dialog is still held until a chat clears the away state"):
        assert ask(home, [question()]).refused

    with when("the chat runs hubctl away clear"):
        home.hubctl("away", "clear")
    with then("dialogs open again"):
        assert ask(home, [question()]).ran
        assert home.hubctl("away", "status").stdout.strip() == "not away"

    with when('Alex runs hubctl away set "train" --hours 2'):
        out = home.hubctl("away", "set", "train", "--hours", "2").stdout
    with then("he is away for the train, and dialogs are held"):
        assert "train" in out and "train" in home.hubctl("away", "status").stdout
        assert ask(home, [question()]).refused
    with when("the clock moves on 2 hours and 1 minute"):
        set_clock(home, "2026-03-02T11:02:00Z")
    with then("the away state has ended, and dialogs open"):
        assert home.hubctl("away", "status").stdout.strip() == "not away"
        assert ask(home, [question()]).ran

    with when("Alex answers a dialog, and 59 minutes pass"):
        answered(home)
        set_clock(home, "2026-03-02T12:01:00Z")
    with then("the answer counted as activity, so dialogs still open"):
        assert ask(home, [question()]).ran


def test_quiet_hours_count_as_away_in_the_time_zone_the_user_set(home):
    with given("away_guard is on with quiet hours 22:00-07:00 in Asia/Tokyo, nine hours ahead of UTC"):
        home.install()
        home.hubctl("on", "away_guard")
        home.hubctl_from_mod("set", "away_guard.quiet_hours", "22:00-07:00")
        home.hubctl_from_mod("set", "away_guard.time_zone", "Asia/Tokyo")
    with and_("Alex typed his last prompt at 21:50 Tokyo time"):
        set_clock(home, "2026-03-02T12:50:00Z")
        prompt(home, "Add the layout test.")

    with when("a chat opens a dialog at 21:59 Tokyo time"):
        set_clock(home, "2026-03-02T12:59:00Z")
        before = ask(home, [question()])
    with then("it opens, because quiet hours have not begun"):
        assert before.ran

    with when("a chat opens a dialog at 22:10 Tokyo time, only 20 minutes after his last prompt"):
        set_clock(home, "2026-03-02T13:10:00Z")
        held = ask(home, [question()])
    with then("it is held, and the reason names the quiet hours in his time zone"):
        assert held.refused and "22:00-07:00" in held.reason and "Asia/Tokyo" in held.reason

    with when("Alex types a prompt at 22:15 Tokyo time"):
        set_clock(home, "2026-03-02T13:15:00Z")
        prompt(home, "Still here, what is open?")
    with then("he typed inside the quiet hours, so dialogs open"):
        assert ask(home, [question()]).ran

    with when("the user switches the quiet hours off with the empty default in /hub, and the time zone too"):
        home.hubctl_from_mod("set", "away_guard.quiet_hours", "")
        home.hubctl_from_mod("set", "away_guard.time_zone", "")
        set_clock(home, "2026-03-02T13:40:00Z")
    with then("only the idle limit counts"):
        assert ask(home, [question()]).ran
