"""The review loop: the stop after failed reviews, the verdict check and the list check.

Every scenario runs the real dispatcher and hub's step as processes in a
temporary HOME. Only the chat transcripts, the acceptance lists and the hook
input are made up. The loop runs in the chat WEB, another chat is DOCS, and
the coordinator chat is HUB.
"""
import itertools
import json
import os

import pytest

from chats import SESSIONS, Chats
from steps import and_, given, then, when

LOOP, OTHER, COORDINATOR = "WEB", "DOCS", "HUB"
ITEMS = ("A1", "A2", "A3")
AGENT_IDS = itertools.count(1)
SECONDS = itertools.count()
NO_GO = ("I read the export code.\nVerdict: NO-GO\nA1: PASS\n"
         "A2: FAIL /srv/app/export.py:88 start_export. A second click starts a second job.\nA3: PASS")
GO = "I read the export code.\nVerdict: GO\nA1: PASS\nA2: PASS\nA3: PASS"


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


# the made-up files

def acceptance(home, area, items=ITEMS, file_name=None, threat=True, check_for=None):
    """An acceptance list in the work folder. area None leaves out the area line; check_for drops one Check."""
    lines = [f"# Acceptance: {area or 'something'}"]
    if area is not None:
        lines.append(f"area: {area}")
    if threat:
        lines += ["", "## Threat model", "In scope:", "- A user who clicks twice by accident.", "Out of scope:",
                  "- Someone who sets out to misuse it."]
    lines += ["", "## Items"]
    for item in items:
        check = "" if item == check_for else f" Check: test row {item.lower()}_holds"
        lines.append(f"- {item}. The export does what {item} says.{check}")
    path = os.path.join(home.work, "acceptance", (file_name or area or "no-area") + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def brief(path, items=ITEMS, shape=True):
    lines = ["Review the export button.", f"Acceptance: {path}"]
    if shape:
        lines += ["End your report with these lines:", "Verdict: GO or NO-GO",
                  *[f"{item}: PASS or FAIL" for item in items]]
    return "\n".join(lines)


def transcript(home, chat):
    return os.path.join(home.root, ".claude", "projects", "demo", SESSIONS[chat] + ".jsonl")


def add_line(home, chat, record):
    """One transcript line, stamped one second after the line before it in any chat, as Claude Code stamps them."""
    second = next(SECONDS)
    stamped = dict(record, timestamp=f"2026-11-21T{10 + second // 3600:02d}:{second // 60 % 60:02d}:{second % 60:02d}.000Z")
    path = transcript(home, chat)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(stamped) + "\n")


def user_types(home, chat, text):
    add_line(home, chat, {"type": "user", "message": {"role": "user", "content": text}})


def another_sender_writes(home, chat, text):
    """A task notification, another chat's message and an agent's report, each saying text."""
    add_line(home, chat, {"type": "user", "message": {"role": "user",
                                                       "content": f"<task-notification>{text}</task-notification>"}})
    add_line(home, chat, {"type": "user", "message": {
        "role": "user", "content": f"<cross-session-message from=\"{OTHER}\">{text}</cross-session-message>"}})
    add_line(home, chat, {"type": "user", "toolUseResult": {"content": text}, "message": {
        "role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": text}]}})


def agent_brief(home, chat, agent_id, prompt):
    """Record 0 of the agent's own transcript, which is its brief word for word."""
    path = os.path.join(home.root, ".claude", "projects", "demo", SESSIONS[chat], "subagents",
                        f"agent-{agent_id}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")


# the calls

def start(chats, chat, agent, prompt):
    return chats.tool(chat, "Agent", {"description": "Run it", "prompt": prompt, "subagent_type": agent})


def hand_back(chats, chat, agent_id, report, agent_type="critic"):
    return chats.tool(chat, "SubagentHandback", {"message": report}, agent_id=agent_id, agent_type=agent_type)


def agent_stops(chats, chat, agent_id, last, agent_type="critic", kept_going_before=False):
    """The agent ends. kept_going_before says a stop hook already kept it going once, as Claude Code marks it."""
    return chats.send(chat, "SubagentStop", agent_id=agent_id, agent_type=agent_type,
                      last_assistant_message=last, stop_hook_active=kept_going_before)


def review(home, chats, chat, prompt, report, agent_type="critic"):
    """A reviewer start that runs, then its hand-back. Returns the agent id and the hand-back's result."""
    started = start(chats, chat, agent_type, prompt)
    assert started.ran, started.reason
    agent_id = f"a{next(AGENT_IDS):016d}"
    agent_brief(home, chat, agent_id, prompt)
    return agent_id, hand_back(chats, chat, agent_id, report, agent_type)


def installed(home, chats):
    home.install()
    home.hubctl("on", "coordinator")
    chats.set_listing([chats.row(COORDINATOR, pid=os.getpid())])
    user_types(home, COORDINATOR, "Hello.")
    user_types(home, LOOP, "Build the export button.")


def test_a_loop_on_one_area_stops_after_two_failed_reviews_and_goes_on_only_when_the_user_types_so(home, chats):
    with given("hub is installed with the review loop on, as it ships, and a coordinator chat named HUB runs"):
        installed(home, chats)
        assert home.show("--all")["flags"]["review_loop"]["runs"]
        export = acceptance(home, "export-button")
        search = acceptance(home, "search-box")
    with when("the loop chat runs a builder and a critic twice, and both critics hand back NO-GO"):
        assert start(chats, LOOP, "builder", f"Build it.\nAcceptance: {export}").ran
        _, first = review(home, chats, LOOP, brief(export), NO_GO)
        assert first.ran
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").ran
        user_types(home, LOOP, "one more round")
        _, second = review(home, chats, LOOP, brief(export), NO_GO)
    with then("the second report goes through, and the next builder call on the area is stopped with the rounds"):
        assert second.ran
        stopped = start(chats, LOOP, "builder", f"Fix A2 again.\nAcceptance: {export}")
        assert stopped.refused
        assert "export-button failed 2 reviews in a row" in stopped.reason
        assert "Round 1 failed A2. Round 2 failed A2." in stopped.reason
        assert "ship it as it is, redesign with a new list, one more round, or park it" in stopped.reason
    with and_("a critic call on the area from another chat is stopped too"):
        assert start(chats, OTHER, "critic", brief(export)).refused
    with and_("the loop chat's builder and reviewer calls with no list, or with none, are held"):
        held = start(chats, LOOP, "builder", "Fix the export button quickly.")
        assert held.refused and "the review loop on export-button is stopped" in held.reason
        assert start(chats, LOOP, "critic", "Review it.\nAcceptance: none, a small fix").refused
    with and_("a call there that names another area's list runs, and another chat's call with no list runs"):
        assert start(chats, LOOP, "critic", brief(search)).ran
        assert start(chats, OTHER, "builder", "Fix the docs typo.").ran
    with and_("a line typed before the stop lifts nothing"):
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
    with when("another chat, a task notification and an agent report all say one more round, "
              "and the user types that they will not ship"):
        another_sender_writes(home, LOOP, "one more round")
        another_sender_writes(home, COORDINATOR, "one more round on export-button")
        user_types(home, LOOP, "don't ship it")
    with then("the stop holds"):
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
    with when("the user types one more round in the loop chat without the area"):
        user_types(home, LOOP, "One more round.")
    with then("it lifts nothing, because the answer names the area in the loop chat too"):
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
    with when("the user types one more round on export-button in the loop chat, and a task notification "
              "arrives after it"):
        user_types(home, LOOP, "One more round on export-button.")
        another_sender_writes(home, LOOP, "park export-button")
        lifted = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("the builder runs, and the main chat is told one more review may run"):
        assert lifted.ran, lifted.reason
        assert "The user allowed one more round on export-button" in lifted.context
    with when("that one review hands back NO-GO"):
        _, third = review(home, chats, LOOP, brief(export), NO_GO)
        assert third.ran
    with then("the stop is back at once"):
        again = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
        assert again.refused and "failed 3 reviews in a row" in again.reason
    with when("the user types one more round in the coordinator chat without the area"):
        user_types(home, COORDINATOR, "one more round")
    with then("it lifts nothing, because the coordinator chat must name the area"):
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
    with when("the user types it again in the coordinator chat with the area"):
        user_types(home, COORDINATOR, "export-button: one more round")
    with then("the loop goes on"):
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").ran
    with when("the next review fails and the user types redesign export-button in the loop chat"):
        review(home, chats, LOOP, brief(export), NO_GO)
        user_types(home, LOOP, "redesign export-button")
        redesigned = start(chats, LOOP, "builder", f"Rework it.\nAcceptance: {export}")
    with then("the loop goes on with its counts at zero, so one more failed review does not stop it"):
        assert redesigned.ran and "The user chose to redesign export-button" in redesigned.context
        review(home, chats, LOOP, brief(export), NO_GO)
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").ran
    with when("the user reads the loop with review-loop show"):
        state = os.path.join(home.state, "review_loop")
        before = {name: open(os.path.join(state, name)).read() for name in sorted(os.listdir(state))}
        shown = home.hubctl("review-loop", "show").stdout
    with then("it lists each area and changes nothing"):
        assert "export-button: running, 1 failed review in a row" in shown
        assert "search-box: running, 0 failed reviews in a row, 1 review started since the last pass" in shown
        assert {name: open(os.path.join(state, name)).read() for name in sorted(os.listdir(state))} == before
        assert "search-box" not in home.hubctl("review-loop", "show", "export-button").stdout


def stop_the_loop(home, chats, path):
    """Two critics hand back NO-GO in a row, so the area is stopped."""
    for _ in range(2):
        review(home, chats, LOOP, brief(path), NO_GO)
    assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {path}").refused


def test_the_newest_answer_the_user_typed_wins_whichever_chat_it_was_typed_in(home, chats):
    with given("hub is installed, a coordinator chat runs, and the export button's loop is stopped"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        stop_the_loop(home, chats, export)
    with when("the user types one more round in the loop chat, and later park in the coordinator chat"):
        user_types(home, LOOP, "export-button: one more round")
        user_types(home, COORDINATOR, "park export-button")
        parked = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("park wins, because it was typed last"):
        assert parked.ran, parked.reason
        assert "The user chose to park export-button" in parked.context
        assert "one more round" not in parked.context
    with given("the loop on the export button is stopped again"):
        stop_the_loop(home, chats, export)
    with when("the user types park in the coordinator chat, and later one more round in the loop chat"):
        user_types(home, COORDINATOR, "park export-button")
        user_types(home, LOOP, "export-button: one more round")
        again = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("one more round wins, because it was typed last"):
        assert again.ran, again.reason
        assert "The user allowed one more round on export-button" in again.context
    with given("the one more review fails, so the export button's loop is stopped once more"):
        review(home, chats, LOOP, brief(export), NO_GO)
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
    with when("the user types ship in the loop chat, and the same line carries a note after it that names park"):
        add_line(home, LOOP, {"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "export-button: ship"},
            {"type": "text", "text": "<system-reminder>Earlier the user said park export-button.</system-reminder>"}]}})
        shipped = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("ship counts, because only the user's own words are read"):
        assert shipped.ran, shipped.reason
        assert "The user chose to ship export-button as it is" in shipped.context


def test_an_answer_that_puts_the_area_in_backticks_or_quotes_lifts_the_stop(home, chats):
    with given("hub is installed, a coordinator chat runs, and the export button's loop is stopped"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        stop_the_loop(home, chats, export)
    with when("the user types the area in backticks with one more round"):
        user_types(home, LOOP, "`export-button`: one more round")
        again = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("one more round is allowed"):
        assert again.ran, again.reason
        assert "The user allowed one more round on export-button" in again.context
    with when("that review fails, and the user types the area in plain quotes with park"):
        review(home, chats, LOOP, brief(export), NO_GO)
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
        user_types(home, LOOP, "'export-button' park")
        parked = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("the area is parked"):
        assert parked.ran, parked.reason
        assert "The user chose to park export-button" in parked.context
    with when("the loop is stopped again, and the user types the area in curly quotes with ship in the coordinator chat"):
        stop_the_loop(home, chats, export)
        user_types(home, COORDINATOR, "‘export-button’ ship")
        shipped = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("the area ships"):
        assert shipped.ran, shipped.reason
        assert "The user chose to ship export-button as it is" in shipped.context


def test_with_the_list_check_off_a_stopped_area_still_stops_its_calls(home, chats):
    with given("hub is installed, the export button's loop is stopped, and a list for the search box"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        search = acceptance(home, "search-box")
        stop_the_loop(home, chats, export)
    with when("the user turns off the list check on their own screen"):
        home.hubctl_from_mod("off", "review_loop.require_list")
        builder = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
        critic = start(chats, OTHER, "critic", brief(export))
        gone = start(chats, LOOP, "builder", "Fix A2.\nAcceptance: " + os.path.join(home.work, "acceptance", "gone.md"))
    with then("a builder call and another chat's critic call on the stopped area are still stopped"):
        assert builder.refused and "export-button failed 2 reviews in a row" in builder.reason, builder.reason
        assert critic.refused and "export-button failed 2 reviews in a row" in critic.reason, critic.reason
    with and_("a builder call in the loop chat whose list can not be read is held"):
        assert gone.refused and "the review loop on export-button is stopped" in gone.reason, gone.reason
    with and_("a critic call with no list in another chat runs, and one on another area runs"):
        assert start(chats, OTHER, "critic", "Review the docs.").ran
        assert start(chats, LOOP, "critic", brief(search)).ran
    with when("the user types ship for the area"):
        user_types(home, LOOP, "export-button: ship")
    with then("the builder call runs"):
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").ran


def test_a_coordinator_renamed_after_the_stop_lifts_nothing_and_the_one_at_the_stop_still_does(home, chats):
    with given("hub is installed, the coordinator chat HUB runs, and the export button's loop is stopped"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        stop_the_loop(home, chats, export)
    with when("the coordinator's name is changed to DOCS, a chat named DOCS runs, and the loop's next call is stopped"):
        home.hubctl("set", "coordinator.name", OTHER)
        chats.set_listing([chats.row(COORDINATOR, pid=os.getpid()), chats.row(OTHER, pid=os.getpid())])
        user_types(home, OTHER, "Hello.")
        assert start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}").refused
    with and_("the user types park for the area in DOCS"):
        user_types(home, OTHER, "park export-button")
        held = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("the stop holds, because DOCS was not the coordinator chat when the loop stopped"):
        assert held.refused and "export-button failed 2 reviews in a row" in held.reason, held.reason
    with when("the user types ship for the area in HUB, the coordinator chat at the stop"):
        user_types(home, COORDINATOR, "export-button: ship")
        shipped = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("the area ships"):
        assert shipped.ran, shipped.reason
        assert "The user chose to ship export-button as it is" in shipped.context


def test_a_review_loop_file_hub_can_not_read_stops_the_loops_calls_and_no_other(home, chats):
    with given("hub is installed and the export button's loop has one failed review"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        review(home, chats, LOOP, brief(export), NO_GO)
        state_file = os.path.join(home.state, "review_loop", "export-button.json")
        with open(state_file) as handle:
            state = json.load(handle)
        assert state["rounds"] and state["agents"]
        agent_id, agent = next(iter(state["agents"].items()))
        place = {"transcript": transcript(home, LOOP), "offset": 0, "role": "loop"}
    wrong_shapes = {
        "a stop whose position has no role": dict(state, stop={"reason": "streak", "positions": {
            SESSIONS[LOOP]: {key: value for key, value in place.items() if key != "role"}}}),
        "a stop with an unknown reason": dict(state, stop={"reason": "later", "positions": {SESSIONS[LOOP]: place}}),
        "rounds that are not a list": dict(state, rounds="1 failed"),
        "a round with no failed items": dict(state, rounds=[{"round": 1}]),
        "an agent with no counted mark": dict(state, agents={agent_id: {key: value for key, value in agent.items()
                                                                        if key != "counted"}}),
        "a streak that is text": dict(state, streak="one"),
        "no passed items": {key: value for key, value in state.items() if key != "passed"},
        "passed items that are text": dict(state, passed="A1"),
        "no field for the starts allowed": {key: value for key, value in state.items() if key != "allowed_starts"},
    }
    for broken, content in {"cut off half way": '{"area": "export-button", "stop": {"rea',
                            "valid JSON that is not an area's state": "[]",
                            **{shape: json.dumps(value) for shape, value in wrong_shapes.items()}}.items():
        with when(f"its file is {broken}"):
            with open(state_file, "w") as handle:
                handle.write(content)
            builder = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
            critic = start(chats, OTHER, "critic", "Review it.\nAcceptance: none, a small fix")
            scout = start(chats, LOOP, "scout", "Count the files.")
        with then("a builder call on the area and a critic call from any chat are stopped and name the file"):
            assert builder.refused and f"can not read the review loop file at {state_file}" in builder.reason, broken
            assert critic.refused and state_file in critic.reason, broken
        with and_("a call to an agent outside the loop runs"):
            assert scout.ran, scout.reason


def test_a_stop_hub_can_not_update_still_stops_the_loops_calls(home, chats):
    with given("hub is installed and the export button's loop is stopped"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        stop_the_loop(home, chats, export)
    with when("the area's lock file is a folder, so hub can not take the lock to update the stop"):
        lock = os.path.join(home.state, "review_loop", "export-button.lock")
        os.remove(lock)
        os.makedirs(lock)
        builder = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
        critic = start(chats, OTHER, "critic", brief(export))
        scout = start(chats, LOOP, "scout", "Count the files.")
    with then("the builder call and the other chat's critic call are still stopped, and name the review loop folder"):
        assert builder.refused and "can not read the review loop file at" in builder.reason, builder.reason
        assert critic.refused and os.path.join(home.state, "review_loop") in critic.reason, critic.reason
    with and_("a call to an agent outside the loop runs"):
        assert scout.ran, scout.reason
    with when("the lock is a file again"):
        os.rmdir(lock)
    with then("the builder call is stopped for the failed reviews again"):
        again = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
        assert again.refused and "export-button failed 2 reviews in a row" in again.reason


def test_a_hand_back_hub_can_not_record_is_refused_so_the_failed_review_still_counts(home, chats):
    with given("hub is installed, the export button's loop has one failed review, and a second critic runs"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        search = acceptance(home, "search-box")
        review(home, chats, LOOP, brief(export), NO_GO)
        assert start(chats, LOOP, "critic", brief(export)).ran
        agent_id = f"a{next(AGENT_IDS):016d}"
        agent_brief(home, LOOP, agent_id, brief(export))
        state_file = os.path.join(home.state, "review_loop", "export-button.json")
        with open(state_file) as handle:
            whole = handle.read()
    with when("the area's file is cut off half way and the critic hands back NO-GO"):
        with open(state_file, "w") as handle:
            handle.write('{"area": "export-button", "stop": {"rea')
        cut_off = hand_back(chats, LOOP, agent_id, NO_GO)
        builder = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
    with then("the hand-back is refused and names the file, and the next builder call is stopped too"):
        assert cut_off.refused and f"can not read the review loop file at {state_file}" in cut_off.reason, \
            cut_off.reason
        assert builder.refused and state_file in builder.reason, builder.reason
    with when("the file is whole again, but the area's lock is a folder, so hub can not write the count"):
        with open(state_file, "w") as handle:
            handle.write(whole)
        lock = os.path.join(home.state, "review_loop", "export-button.lock")
        os.remove(lock)
        os.makedirs(lock)
        locked = hand_back(chats, LOOP, agent_id, NO_GO)
    with then("the hand-back is refused and names the review loop folder"):
        assert locked.refused and os.path.join(home.state, "review_loop") in locked.reason, locked.reason
    with when("a critic starts on another area whose lock is a folder, so hub can not write the start"):
        os.makedirs(os.path.join(home.state, "review_loop", "search-box.lock"))
        other_start = start(chats, OTHER, "critic", brief(search))
    with then("the critic call is refused, so no start goes uncounted"):
        assert other_start.refused and os.path.join(home.state, "review_loop") in other_start.reason, \
            other_start.reason
    with when("the lock is a file again and the critic hands back the same report"):
        os.rmdir(lock)
        counted = hand_back(chats, LOOP, agent_id, NO_GO)
    with then("it goes through, and the loop is stopped after this second failed review, not one round late"):
        assert counted.ran, counted.reason
        stopped = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
        assert stopped.refused and "export-button failed 2 reviews in a row" in stopped.reason, stopped.reason


def test_an_agent_that_ends_while_hub_can_not_record_its_review_is_kept_going_once(home, chats):
    with given("hub is installed, the export button's loop has one failed review, and a second critic runs"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        review(home, chats, LOOP, brief(export), NO_GO)
        assert start(chats, LOOP, "critic", brief(export)).ran
        agent_id = f"a{next(AGENT_IDS):016d}"
        agent_brief(home, LOOP, agent_id, brief(export))
        state_file = os.path.join(home.state, "review_loop", "export-button.json")
        with open(state_file) as handle:
            whole = handle.read()
    with when("the area's file is cut off half way and the critic ends with NO-GO without handing back"):
        with open(state_file, "w") as handle:
            handle.write('{"area": "export-button", "stop": {"rea')
        first_end = agent_stops(chats, LOOP, agent_id, NO_GO)
    with then("it is kept going, and the reason names the file"):
        assert first_end.output.get("decision") == "block", first_end.output
        assert f"can not read the review loop file at {state_file}" in first_end.output.get("reason", "")
    with when("it ends again after a stop hook already kept it going"):
        second_end = agent_stops(chats, LOOP, agent_id, NO_GO, kept_going_before=True)
    with then("it may end, so it is never kept going without end"):
        assert second_end.output.get("decision") != "block", second_end.output
    with when("the file is whole again and the critic ends once more"):
        with open(state_file, "w") as handle:
            handle.write(whole)
        third_end = agent_stops(chats, LOOP, agent_id, NO_GO)
    with then("its review counts, and the loop is stopped after this second failed review"):
        assert third_end.output.get("decision") != "block", third_end.output
        stopped = start(chats, LOOP, "builder", f"Fix A2.\nAcceptance: {export}")
        assert stopped.refused and "export-button failed 2 reviews in a row" in stopped.reason, stopped.reason


def test_a_review_counts_only_with_verdict_lines_that_match_its_list(home, chats):
    with given("hub is installed and a list for the export button"):
        installed(home, chats)
        export = acceptance(home, "export-button")
    with when("a critic hands back a report with no verdict lines"):
        agent_id = "a1000000000000001"
        agent_brief(home, LOOP, agent_id, brief(export))
        first = hand_back(chats, LOOP, agent_id, "All looks fine to me.")
    with then("it is sent back with the lines to fill in"):
        assert first.refused and "does not end with the verdict lines for export-button" in first.reason
        assert "Verdict:" in first.reason and "A3:" in first.reason
    with and_("it is sent back once more, and the third time it goes through as a failed review"):
        assert hand_back(chats, LOOP, agent_id, "Still fine.").refused
        assert hand_back(chats, LOOP, agent_id, "Still fine.").ran
        told = start(chats, LOOP, "builder", f"Fix it.\nAcceptance: {export}")
        assert "still had no valid verdict lines after 2 tries" in told.context
    with when("critics hand back verdict lines that do not match the list"):
        bad = {
            "every item passes": "Verdict: NO-GO\nA1: PASS\nA2: PASS\nA3: PASS",
            "the verdict is GO, but A2 fails": "Verdict: GO\nA1: PASS\nA2: FAIL /srv/app/x.py:3 run. Bad.\nA3: PASS",
            "the line for A9 names an item that is not on the list":
                "Verdict: GO\nA1: PASS\nA2: PASS\nA3: PASS\nA9: PASS",
            "the verdict lines have no line for A3": "Verdict: GO\nA1: PASS\nA2: PASS",
            "the FAIL line for A2 gives no place": "Verdict: NO-GO\nA1: PASS\nA2: FAIL it breaks\nA3: PASS",
        }
        answers = {}
        for problem, report in bad.items():
            agent_id = f"a{next(AGENT_IDS):016d}"
            agent_brief(home, LOOP, agent_id, brief(export))
            answers[problem] = hand_back(chats, LOOP, agent_id, report)
    with then("each is sent back and names its problem"):
        for problem, answer in answers.items():
            assert answer.refused and problem in answer.reason, problem
    with when("a review passes every item, and a later one fails A2 with no failing test"):
        review(home, chats, LOOP, brief(export), GO)
        agent_id = f"a{next(AGENT_IDS):016d}"
        agent_brief(home, LOOP, agent_id, brief(export))
        reopened = hand_back(chats, LOOP, agent_id, NO_GO)
    with then("it is sent back, and with a failing test named it goes through"):
        assert reopened.refused and "A2 passed in an earlier round" in reopened.reason
        assert hand_back(chats, LOOP, agent_id, NO_GO.replace("second job.", "second job. test: test_second_click")).ran
    with when("a critic ends without handing back any verdict"):
        agent_id = f"a{next(AGENT_IDS):016d}"
        agent_brief(home, LOOP, agent_id, brief(export))
        first_stop = agent_stops(chats, LOOP, agent_id, "I ran out of steps.")
        second_stop = agent_stops(chats, LOOP, agent_id, "I ran out of steps.")
    with then("it is kept going once with the lines, and its second stop counts as a failed review"):
        assert first_stop.output.get("decision") == "block"
        assert "has no verdict lines for export-button yet" in first_stop.output.get("reason", "")
        assert second_stop.output.get("decision") != "block"
        stopped = start(chats, LOOP, "builder", f"Fix it.\nAcceptance: {export}")
        assert "ended twice without verdict lines" in stopped.context
        assert stopped.refused and "export-button failed 2 reviews in a row" in stopped.reason
    with given("a new area whose reviews never report a verdict"):
        lost = acceptance(home, "lost-verdicts")
    with when("three critics start on it and none of them hands back"):
        for _ in range(3):
            assert start(chats, LOOP, "critic", brief(lost)).ran
        fourth = start(chats, LOOP, "critic", brief(lost))
    with then("the fourth start is stopped, because no review ended with a GO"):
        assert fourth.refused and "lost-verdicts had 3 reviews since its last pass" in fourth.reason


def test_a_serious_problem_outside_the_list_is_put_to_the_user_and_blocks_nothing(home, chats):
    with given("hub is installed and a list for the export button"):
        installed(home, chats)
        export = acceptance(home, "export-button")
    with when("a review passes every item and names a High problem and a Low one outside the list"):
        report = GO + ("\nOutside the list:\n- High. The export file can be read by every user on the machine."
                       "\n- Low. The button label has no translation.")
        _, back = review(home, chats, LOOP, brief(export), report)
    with then("the report goes through"):
        assert back.ran
    with and_("the next call in the main chat runs, and it is told to ask the user about the High problem"):
        nxt = start(chats, LOOP, "builder", f"Polish it.\nAcceptance: {export}")
        assert nxt.ran
        assert "found a High problem outside the list. The export file can be read by every user" in nxt.context
        assert "1 smaller problems outside the list" in nxt.context
    with and_("the notes reach the main chat once"):
        assert "outside the list" not in start(chats, LOOP, "builder", f"Again.\nAcceptance: {export}").context


def test_a_review_call_names_a_list_with_a_threat_model_and_verdict_lines_or_says_why_it_has_none(home, chats):
    with given("hub is installed"):
        installed(home, chats)
        good = acceptance(home, "export-button")
    with then("a critic or reviewer call with no list is stopped and told the line to add"):
        for agent in ("critic", "reviewer"):
            refused = start(chats, LOOP, agent, "Review the export button.")
            assert refused.refused and f"this {agent} call, because its brief names no acceptance list" in \
                refused.reason, agent
    with and_("none without a reason is stopped, and none with a reason runs and records nothing"):
        assert start(chats, LOOP, "critic", "Review it.\nAcceptance: none").refused
        assert start(chats, LOOP, "critic", "Review it.\nAcceptance: none, a one-line typo fix").ran
        assert not os.path.isdir(os.path.join(home.state, "review_loop"))
    with and_("a list that does not exist, or that lacks a part, is stopped and named"):
        missing = os.path.join(home.work, "acceptance", "gone.md")
        assert f"can not read the acceptance list at {missing}" in start(chats, LOOP, "critic", brief(missing)).reason
        cases = {"has no `area:` line": acceptance(home, None),
                 "has no `area:` line with a letter from a to z or a digit":
                     acceptance(home, "---", file_name="dashes"),
                 "has no threat model with In scope and Out of scope": acceptance(home, "a", threat=False),
                 "has no items": acceptance(home, "b", items=()),
                 "has no Check for A2": acceptance(home, "c", check_for="A2")}
        for missing_part, path in cases.items():
            assert missing_part in start(chats, LOOP, "critic", brief(path)).reason, missing_part
        assert "has no Check for A2" in start(chats, LOOP, "builder", f"Build.\nAcceptance: {cases['has no Check for A2']}").reason
    with when("a critic brief names a good list but not the verdict lines"):
        refused = start(chats, LOOP, "critic", brief(good, shape=False))
    with then("it is stopped with the exact lines to paste in, and with them pasted it runs"):
        assert refused.refused and "not the verdict lines the report must end with" in refused.reason
        lines = refused.reason.split("start it again.", 1)[1].strip()
        assert lines.startswith("Verdict:") and all(f"\n{item}:" in lines for item in ITEMS)
        assert start(chats, LOOP, "critic", brief(good, shape=False) + "\n" + lines).ran
    with and_("a builder call with no list, and a call to another agent, run as before"):
        assert start(chats, LOOP, "builder", "Fix the typo.").ran
        assert start(chats, LOOP, "scout", "Count the files.").ran


def test_the_review_loop_holds_nothing_once_the_user_turns_it_off(home, chats):
    with given("hub is installed with the review loop on"):
        installed(home, chats)
    with when("the user turns it off on their own screen"):
        home.hubctl_from_mod("off", "review_loop")
    with then("a critic call with no list runs"):
        result = start(chats, LOOP, "critic", "Review the export button.")
        assert result.ran and not result.context


def test_a_negated_line_that_names_the_area_keeps_the_stop_over_an_older_answer(home, chats):
    with given("the loop on the export button is stopped after two failed reviews"):
        installed(home, chats)
        export = acceptance(home, "export-button")
        for task in ("Build it.", "Fix A2."):
            assert start(chats, LOOP, "builder", f"{task}\nAcceptance: {export}").ran
            assert review(home, chats, LOOP, brief(export), NO_GO)[1].ran
        assert start(chats, LOOP, "builder", f"Fix A2 again.\nAcceptance: {export}").refused
    with when("the user types park export-button, then don't park export-button, and the loop chat starts a builder"):
        user_types(home, LOOP, "park export-button")
        user_types(home, LOOP, "don't park export-button")
        taken_back = start(chats, LOOP, "builder", f"Fix A2 again.\nAcceptance: {export}")
    with then("the builder is still stopped with the rounds, since the newer line takes the answer back"):
        assert taken_back.refused and "export-button failed 2 reviews in a row" in taken_back.reason
    with when("the user types park export-button again, then asks in the coordinator chat whether to ship "
              "export-button"):
        user_types(home, LOOP, "park export-button")
        user_types(home, COORDINATOR, "should we ship export-button?")
        asked = start(chats, LOOP, "builder", f"Fix A2 again.\nAcceptance: {export}")
    with then("the builder is still stopped, since a question decides nothing and holds the older answer"):
        assert asked.refused and "export-button failed 2 reviews in a row" in asked.reason
    with when("the user types park export-button once more"):
        user_types(home, LOOP, "park export-button")
        parked = start(chats, LOOP, "builder", f"Fix A2 again.\nAcceptance: {export}")
    with then("the builder runs"):
        assert parked.ran, parked.reason
