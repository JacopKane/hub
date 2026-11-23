"""away_guard: while the user is away, every question still reaches them, with at most a one-line note."""
import os

from dialogs import answered, ask, prompt, question, set_clock, state_lines
from steps import and_, given, then, when

URGENT_LOG = "away_urgent.jsonl"


def reaches_with_note(result):
    """The dialog opens, and the chat gets one line that the user may answer late."""
    return (result.ran and "may answer late" in result.context and "\n" not in result.context
            and "urgent" not in result.context.lower() and "hold" not in result.context.lower())


def test_as_a_hub_user_away_when_claude_asks_me_a_question_it_reaches_me_with_at_most_a_note(home):
    with given("away_guard is on, and the fixed clock is 61 minutes past Alex's last typed prompt"):
        home.install()
        home.hubctl("on", "away_guard")
        set_clock(home, "2026-03-02T08:00:00Z")
        prompt(home, "Add the layout test, then wait for me.")
        set_clock(home, "2026-03-02T09:01:00Z")

    with when("a chat opens a dialog with no urgent mark"):
        asked = ask(home, [question()])
    with then("the question reaches Alex, and the chat gets one note that he has been silent and may answer late"):
        assert not asked.refused and asked.reason == ""
        assert reaches_with_note(asked), asked.context
        assert "silent since" in asked.context

    with when('a dialog carries the old mark "urgent: the release breaks at 09:00"'):
        marked = ask(home, [question()], metadata={"source": "urgent: the release breaks at 09:00"})
    with then("it reaches him the same way, and no urgent log is written"):
        assert reaches_with_note(marked), marked.context
        assert state_lines(home, URGENT_LOG) == []

    with when("a prompt arrives from another session or from the harness"):
        note = prompt(home, "<task-notification>The tests finished.</task-notification>")
        peer = prompt(home, "Another Claude session sent a message: the build is green.")
        reminder = prompt(home, "<system-reminder>Context is low.</system-reminder>")
    with then("Alex still counts as away, no prompt asks the chat to judge, and questions still reach him"):
        assert not (note.context or peer.context or reminder.context)
        assert reaches_with_note(ask(home, [question()]))
        assert "away" in home.hubctl("away", "status").stdout

    with when("Alex types a prompt"):
        back = prompt(home, "I am at my desk again, show me the open questions.")
    with then("the chat is asked to judge from his words whether he is back, and told questions are never held"):
        assert f"python3 -I {os.path.join(home.plugin, 'lib', 'hub_switch.py')} away clear" in back.context
        assert "Questions to the user are never held." in back.context
        assert "urgent" not in back.context and "held questions" not in back.context
    with and_("a dialog still reaches him with the note until a chat clears the away state"):
        assert reaches_with_note(ask(home, [question()]))

    with when("the chat runs hubctl away clear"):
        home.hubctl("away", "clear")
    with then("dialogs open with no note"):
        cleared = ask(home, [question()])
        assert cleared.ran and cleared.context == ""
        assert home.hubctl("away", "status").stdout.strip() == "not away"

    with when('Alex runs hubctl away set "train" --hours 2'):
        out = home.hubctl("away", "set", "train", "--hours", "2").stdout
    with then("he is away for the train, and a dialog reaches him with a note that names the train"):
        assert "train" in out and "train" in home.hubctl("away", "status").stdout
        on_train = ask(home, [question()])
        assert reaches_with_note(on_train) and "train" in on_train.context
    with when("the clock moves on 2 hours and 1 minute"):
        set_clock(home, "2026-03-02T11:02:00Z")
    with then("the away state has ended, and dialogs open with no note"):
        assert home.hubctl("away", "status").stdout.strip() == "not away"
        assert ask(home, [question()]).context == ""

    with when("Alex answers a dialog, and 59 minutes pass"):
        answered(home)
        set_clock(home, "2026-03-02T12:01:00Z")
    with then("the answer counted as activity, so dialogs open with no note"):
        after = ask(home, [question()])
        assert after.ran and after.context == ""


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
    with then("it opens with no note, because quiet hours have not begun"):
        assert before.ran and before.context == ""

    with when("a chat opens a dialog at 22:10 Tokyo time, only 20 minutes after his last prompt"):
        set_clock(home, "2026-03-02T13:10:00Z")
        quiet = ask(home, [question()])
    with then("it still reaches him, and the note names the quiet hours in his time zone"):
        assert reaches_with_note(quiet), quiet.context
        assert "22:00-07:00" in quiet.context and "Asia/Tokyo" in quiet.context

    with when("Alex types a prompt at 22:15 Tokyo time"):
        set_clock(home, "2026-03-02T13:15:00Z")
        prompt(home, "Still here, what is open?")
    with then("he typed inside the quiet hours, so dialogs open with no note"):
        assert ask(home, [question()]).context == ""

    with when("the user switches the quiet hours off with the empty default in /hub, and the time zone too"):
        home.hubctl_from_mod("set", "away_guard.quiet_hours", "")
        home.hubctl_from_mod("set", "away_guard.time_zone", "")
        set_clock(home, "2026-03-02T13:40:00Z")
    with then("only the idle limit counts"):
        assert ask(home, [question()]).context == ""
