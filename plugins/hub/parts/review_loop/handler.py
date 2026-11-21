"""review_loop: keep a build and review loop on a written list, and stop it until the user decides.

An Agent call to a reviewer must name an acceptance list, or say why it has
none, and its brief must ask for the verdict lines. A reviewer's hand-back
under a list must end with verdict lines that match the list; it is sent
back up to verdict_check.tries times, then counts as a failed review. After
stop_after.rounds failed reviews in a row on one area, or rounds + 1 reviewer
starts with no pass, builder and reviewer calls on that area are stopped
from every chat, and the chat that ran the loop starts none without a list.
Only a line the user typed after the stop, in that chat or in the chat
that was the coordinator when the loop stopped, lifts it. A coordinator
name set later adds no chat. The line names the area, and the newest one
wins, so a newer question or negation that names the area keeps the stop.
While a review loop file can not be read or has the wrong shape, or
the check fails in any other way, builder and reviewer calls are refused,
since hub can not tell whether a stop is open.

The pure rules are in ledger.py. This file reads the lists, transcripts and
state files, and turns the rules' answers into refusals and notes. Claude
Code only. Design limit: it stops a model that loses track of a loop, and it
is not a sandbox.
"""
import glob
import importlib.util
import json
import os
import re

from hub import chat_notices, files, said, sessions, tasks

PART = "review_loop"
FOLDER = "review_loop"
TEXT = os.path.join(files.PARTS_DIR, PART, "text")
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
SAFE = re.compile(r"[^A-Za-z0-9_-]")
CHOICE_TEXTS = {"ship": "lifted_ship.md", "redesign": "lifted_redesign.md",
                "one_more_round": "lifted_one_more_round.md", "park": "lifted_park.md"}
HOLD = "hold"  # what a negated line that names the area says, so the stop stays
PROBLEM_TEXTS = {"unknown_item": "verdict_unknown_item.md", "missing_item": "verdict_missing_item.md",
                 "no_fail": "verdict_no_fail.md", "go_with_fail": "verdict_go_with_fail.md",
                 "fail_without_place": "verdict_fail_without_place.md",
                 "reopened_without_test": "verdict_reopened_without_test.md"}
UNREADABLE_ROUND = "with no valid verdict lines"
LOCK_WAIT = 5  # seconds an area's lock may stay held before the call is refused


def load_ledger():
    spec = importlib.util.spec_from_file_location("hub_review_loop_ledger",
                                                  os.path.join(files.PARTS_DIR, PART, "ledger.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = load_ledger()


# texts and settings

def message(name, **values):
    """A message file with its named values filled in."""
    with open(os.path.join(TEXT, name), encoding="utf-8") as handle:
        text = handle.read().strip()
    return VALUE.sub(lambda found: str(values.get(found.group(1), found.group(0))), text)


def joined(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def settings_of(flags):
    return {"reviewers": set(flags.value(f"{PART}.reviewers") or []),
            "builders": set(flags.value(f"{PART}.builders") or []),
            "require_list": flags.runs(f"{PART}.require_list"),
            "verdict_check": flags.runs(f"{PART}.verdict_check"),
            "tries": int(flags.value(f"{PART}.verdict_check.tries") or 2),
            "stop_after": flags.runs(f"{PART}.stop_after"),
            "rounds": int(flags.value(f"{PART}.stop_after.rounds") or 2),
            "serious_outside": flags.runs(f"{PART}.serious_outside"),
            "coordinator": flags.value("coordinator.name") if flags.runs("coordinator") else None}


def agent_name(value):
    return str(value or "").rsplit(":", 1)[-1]


# the state files

def safe_area(area):
    return SAFE.sub("-", area)[:80] or "area"


def state_path(area):
    return files.state_path(os.path.join(FOLDER, safe_area(area) + ".json"))


def lock_name(area):
    return os.path.join(FOLDER, safe_area(area) + ".lock")


class Unreadable(Exception):
    """A review loop file that is there but can not be read, so hub can not tell whether a stop is open."""

    def __init__(self, path):
        super().__init__(path)
        self.path = path


STATE_KEYS = frozenset(ledger.fresh("area"))
STOP_REASONS = ("streak", "starts")
ROLES = ("loop", "coordinator")


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_words(value):
    return isinstance(value, list) and all(isinstance(word, str) for word in value)


def is_round(value):
    return isinstance(value, dict) and is_count(value.get("round")) and is_words(value.get("failed"))


def is_place(value):
    return isinstance(value, dict) and isinstance(value.get("transcript"), str) and \
        is_count(value.get("offset")) and value.get("role") in ROLES


def is_stop(value):
    return value is None or (isinstance(value, dict) and value.get("reason") in STOP_REASONS and
                             isinstance(value.get("positions"), dict) and
                             all(isinstance(chat, str) and is_place(place)
                                 for chat, place in value["positions"].items()))


def is_agent(value):
    return isinstance(value, dict) and is_count(value.get("sent_back")) and is_count(value.get("stops")) and \
        isinstance(value.get("counted"), bool)


def is_state(value):
    """Whether a value has every field of an area's state, each of the shape ledger.py reads."""
    return isinstance(value, dict) and STATE_KEYS <= set(value) and isinstance(value["area"], str) and \
        (value.get("session") is None or isinstance(value.get("session"), str)) and \
        is_count(value.get("streak")) and is_count(value.get("starts")) and \
        (value.get("allowed_starts") is None or is_count(value.get("allowed_starts"))) and \
        isinstance(value.get("rounds"), list) and all(map(is_round, value["rounds"])) and \
        is_words(value.get("passed")) and is_stop(value["stop"]) and \
        isinstance(value.get("agents"), dict) and \
        all(isinstance(agent, str) and is_agent(entry) for agent, entry in value["agents"].items())


def state_in(path):
    """The state in one file, or None when there is no file. A file that is not a state raises Unreadable."""
    try:
        state = files.read_json(path)
    except (OSError, ValueError) as error:
        raise Unreadable(path) from error
    if state is not None and not is_state(state):
        raise Unreadable(path)
    return state


def read_state(area):
    return state_in(state_path(area)) or ledger.fresh(area)


def all_states():
    folder = files.state_path(FOLDER)
    try:
        names = sorted(name for name in os.listdir(folder) if name.endswith(".json"))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise Unreadable(folder) from error
    return [state for state in (state_in(os.path.join(folder, name)) for name in names) if state is not None]


def change_state(area, change):
    """Read, change and write one area's state under its lock. change returns (state, result).

    A lock held past LOCK_WAIT raises, so the call is refused inside the hook's 15 s instead of running.
    """
    with files.locked(lock_name(area), wait=LOCK_WAIT):
        state, result = change(read_state(area))
        files.write_json(state_path(area), state)
    return result


# lists and briefs

def read_list(line):
    """(parsed list, refusal) for an Acceptance line that names a path."""
    path = line[1]
    try:
        text = files.read_text(path) if os.path.isabs(path) and os.path.isfile(path) else None
    except (OSError, ValueError):
        text = None
    if text is None:
        return None, message("list_unreadable.md", path=path)
    parsed = ledger.parse_acceptance(text)
    if parsed["missing"]:
        return None, message("list_incomplete.md", path=path, missing=parsed["missing"])
    return parsed, None


def agent_brief(hook_input):
    """Record 0 of the agent's own transcript, which is its brief word for word, or ""."""
    path = hook_input.get("agent_transcript_path") or tasks.thread_transcript(hook_input)
    try:
        with open(path, encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, ValueError, TypeError):
        return ""
    return said.text_of(((first or {}).get("message") or {}).get("content")) if isinstance(first, dict) else ""


def list_of_brief(prompt):
    line = ledger.acceptance_line(prompt)
    if not line or line[0] != "path":
        return None
    parsed, _ = read_list(line)
    return parsed


# positions in the chats that may lift a stop

def size_of(path):
    try:
        return os.path.getsize(path)
    except (OSError, TypeError):
        return 0


def coordinator_transcript(hook_input, name):
    """(session, transcript path) of the live coordinator chat, or None."""
    if not name:
        return None
    chat = sessions.find_live(name, sessions.listing())
    transcript = hook_input.get("transcript_path")
    if not chat or not isinstance(transcript, str):
        return None
    session = SAFE.sub("", str(chat["session"]))
    root = os.path.dirname(os.path.dirname(transcript))
    found = sorted(glob.glob(os.path.join(root, "*", session + ".jsonl")))
    return session, found[0] if found else os.path.join(os.path.dirname(transcript), session + ".jsonl")


def place_at(transcript, role):
    transcript = transcript if isinstance(transcript, str) else ""
    return {"transcript": transcript, "offset": size_of(transcript), "role": role}


def fits(place):
    """Whether a recorded position still lies inside its file."""
    return os.path.isfile(place["transcript"]) and size_of(place["transcript"]) >= place["offset"]


def positions_at_stop(hook_input, settings):
    """Where the loop chat's transcript and the live coordinator chat's transcript end when a stop opens.

    These are the only chats whose typed lines may lift the stop, so a
    coordinator name that changes later adds no chat.
    """
    found = {hook_input.get("session_id"): place_at(hook_input.get("transcript_path"), "loop")}
    coordinator = coordinator_transcript(hook_input, settings["coordinator"])
    if coordinator and coordinator[0] not in found:
        found[coordinator[0]] = place_at(coordinator[1], "coordinator")
    return found


def positions_kept(kept):
    """The recorded positions: each one that still fits its file stays, and the others move to where it ends now.

    After a move only lines typed after it count.
    """
    return {chat: place if fits(place) else place_at(place["transcript"], place["role"])
            for chat, place in kept.items()}


# the user's answer

def negated(line):
    return "?" in line or bool(said.NEGATIONS.search(said.folded(line)))


def answerable_areas(state, open_states, chat, place):
    """The stopped areas a line in this chat may answer: every one in the coordinator chat, else the chat's own."""
    coordinator = place["role"] == "coordinator"
    areas = [other["area"] for other in open_states if coordinator or other.get("session") == chat]
    return areas if state["area"] in areas else areas + [state["area"]]


def line_choice(line, state, areas):
    """The choice one typed line makes for this area, HOLD when it keeps the stop, or None when it says nothing."""
    words = ledger.words_of(line)
    if ledger.holds(words, state["area"], negated(line)):
        return HOLD
    pick = ledger.decision_in(words, areas, True, negated(line))
    return pick[1] if pick and pick[0] == state["area"] else None


def newest_choice(state, open_states):
    """The choice the user typed last for this area after its stop, in any chat that may lift it, or None.

    Every answer names the area. The lines of both chats are put in order by
    their own time, and on the same time the loop chat's line counts as the
    later one. A newer question or negation that names the area keeps the
    stop and takes back an older answer.
    """
    ranked = sorted(state["stop"]["positions"].items(), key=lambda pair: pair[1]["role"])
    picks = [(stamp, rank, index, choice)
             for rank, (chat, place) in enumerate(ranked)
             for index, (stamp, line) in enumerate(said.typed_after(place["transcript"], place["offset"]))
             for choice in [line_choice(line, state, answerable_areas(state, open_states, chat, place))]
             if choice]
    newest = max(picks)[3] if picks else None
    return None if newest == HOLD else newest


def lift_stops(hook_input, answer):
    """Lift each open stop the user answered, and tell the chats."""
    open_states = [state for state in all_states() if state.get("stop")]
    for state in open_states:
        choice = newest_choice(state, open_states)
        if choice is None:
            continue

        def lift(current, choice=choice):
            if not current.get("stop"):
                return current, False
            return ledger.lifted(current, choice), True

        if change_state(state["area"], lift):
            text = message(CHOICE_TEXTS[choice], area=state["area"])
            answer.add_context(text)
            if state.get("session") and state["session"] != hook_input.get("session_id"):
                chat_notices.leave(state["session"], text)


# Agent calls

def stopped_text(state):
    if state["stop"]["reason"] == "starts":
        return message("stopped_no_verdicts.md", area=state["area"], starts=state["starts"])
    summary = " ".join(message("round_summary.md", round=entry["round"],
                               items=joined(entry["failed"]) if entry["failed"] else UNREADABLE_ROUND)
                       for entry in state["rounds"])
    return message("stopped.md", area=state["area"], rounds=state["streak"], summary=summary)


def refuse_for_stop(answer, area, text):
    """Refuse with text, and move each position that no longer fits its file to where it ends now."""
    def keep_positions(current):
        if not current.get("stop"):
            return current, None
        stop = dict(current["stop"], positions=positions_kept(current["stop"]["positions"]))
        return dict(current, stop=stop), None

    change_state(area, keep_positions)
    answer.signal(f"{PART}.stop_after", "stopped")
    answer.refuse(f"{text} ({PART}.stop_after)")


def path_of(error):
    """The review loop file an error is about, or the review loop folder when it names none."""
    return error.path if isinstance(error, Unreadable) else files.state_path(FOLDER)


def refuse_for_state(settings, answer, path):
    """Refuse the call while the stop check runs, since hub can not tell whether a stop is open."""
    if settings["stop_after"]:
        answer.signal(f"{PART}.stop_after", "unreadable")
        answer.refuse(message("state_unreadable.md", path=path) + f" ({PART}.stop_after)")


def refuse_unreadable(hook_input, settings, answer, path):
    """Fail closed: a builder or reviewer call is refused while hub can not read a review loop file."""
    name = agent_name((hook_input.get("tool_input") or {}).get("subagent_type"))
    if name in settings["reviewers"] or name in settings["builders"]:
        refuse_for_state(settings, answer, path)


def after_others(settings, answer, step):
    """Run step once every part has answered and none refused. An error refuses the call, so no count is lost."""
    def run(done):
        if done.refusal is not None:
            return
        try:
            step()
        except Exception as error:  # noqa: BLE001
            refuse_for_state(settings, done, path_of(error))

    answer.after_all(run)


def held_in_chat(settings, answer, session):
    """Refuse a call that names no readable list while this chat's own loop is stopped. Returns whether it did."""
    held = [state for state in all_states() if state.get("stop") and state.get("session") == session]
    if not (held and settings["stop_after"]):
        return False
    refuse_for_stop(answer, held[0]["area"], message("held_no_list.md", area=held[0]["area"]))
    return True


def agent_call(hook_input, settings, answer):
    tool_input = hook_input.get("tool_input") or {}
    name = agent_name(tool_input.get("subagent_type"))
    reviewer = name in settings["reviewers"]
    if not (reviewer or name in settings["builders"]):
        return
    prompt = str(tool_input.get("prompt") or "")
    session = hook_input.get("session_id")
    line = ledger.acceptance_line(prompt)
    if line is None or line[0] == "none":
        if held_in_chat(settings, answer, session):
            return
        if reviewer and settings["require_list"] and not (line and line[1]):
            answer.signal(f"{PART}.require_list", "no_list")
            answer.refuse(message("no_list.md", agent=name) + f" ({PART}.require_list)")
        return
    parsed, refusal = read_list(line)
    if refusal and settings["require_list"]:
        answer.signal(f"{PART}.require_list", "bad_list")
        answer.refuse(f"{refusal} ({PART}.require_list)")
        return
    if refusal:
        held_in_chat(settings, answer, session)
        return
    area = parsed["area"]
    state = read_state(area)
    if settings["stop_after"] and state.get("stop"):
        refuse_for_stop(answer, area, stopped_text(state))
        return
    if reviewer and settings["stop_after"] and ledger.backstop_due(state, settings["rounds"]):
        def open_stop(current):
            if current.get("stop"):
                return current, current
            current = ledger.with_stop(current, "starts", positions_at_stop(hook_input, settings))
            return current, current

        refuse_for_stop(answer, area, stopped_text(change_state(area, open_stop)))
        return
    if reviewer and settings["verdict_check"] and not ledger.brief_has_shape(prompt, parsed["items"]):
        answer.signal(f"{PART}.verdict_check", "no_shape")
        answer.refuse(message("no_verdict_shape.md", area=area, lines="\n" + ledger.shape_lines(parsed["items"])))
        return
    if reviewer:
        after_others(settings, answer, lambda: change_state(
            area, lambda current: (ledger.started(current, session), None)))


# reports

def notes_for(settings, state, judged):
    """The notes the main chat gets about a counted review's problems outside the list."""
    outside = judged["outside"]
    serious = [finding for finding in outside if ledger.serious(finding)] if settings["serious_outside"] else []
    smaller = [finding for finding in outside if finding not in serious]
    return [message("serious_outside_finding.md", area=state["area"], severity=severity, finding=text)
            for severity, text in serious] + \
        ([message("outside_findings.md", area=state["area"], count=len(smaller))] if smaller else [])


def count_review(hook_input, settings, area, agent, judged, unreadable_note=None):
    """Count one review, open the stop when it is due, and leave the notes for the main chat."""
    def record(current):
        if ledger.agent_of(current, agent)["counted"]:
            return current, None
        current, opens = ledger.counted(current, agent, judged["failed"], judged["passed"], settings["rounds"],
                                        unreadable=unreadable_note is not None)
        if opens and settings["stop_after"]:
            current = ledger.with_stop(current, "streak", positions_at_stop(hook_input, settings))
        return current, current

    state = change_state(area, record)
    if state is None:
        return
    notes = ([unreadable_note] if unreadable_note else []) + notes_for(settings, state, judged)
    for note in notes:
        chat_notices.leave(hook_input.get("session_id"), note)


def under_list(hook_input, settings):
    """(parsed list, agent id) when a reviewer agent's brief names a readable list, else None."""
    if agent_name(hook_input.get("agent_type")) not in settings["reviewers"] or not hook_input.get("agent_id"):
        return None
    parsed = list_of_brief(agent_brief(hook_input))
    return (parsed, str(hook_input["agent_id"])) if parsed else None


def refusal_for(judged, area, items, tries):
    kinds = [kind for kind, _ in judged["problems"]]
    if "missing" in kinds:
        return message("verdict_missing.md", area=area, tries=tries, lines="\n" + ledger.shape_lines(items))
    problem = " ".join(message(PROBLEM_TEXTS[kind], item=item) for kind, item in judged["problems"])
    return message("verdict_refused.md", problem=problem, tries=tries)


def hand_back(hook_input, settings, answer):
    found = under_list(hook_input, settings)
    if not found:
        return
    parsed, agent = found
    area, items = parsed["area"], parsed["items"]
    state = read_state(area)
    if ledger.agent_of(state, agent)["counted"]:
        return
    report = str((hook_input.get("tool_input") or {}).get("message") or "")
    judged = ledger.judge_report(report, items, state["passed"])
    if not judged["problems"]:
        after_others(settings, answer, lambda: count_review(hook_input, settings, area, agent, judged))
        return
    if not settings["verdict_check"]:
        return

    def send_back(current):
        sent = ledger.agent_of(current, agent)["sent_back"]
        if sent >= settings["tries"]:
            return current, False
        return ledger.with_agent(current, agent, sent_back=sent + 1), True

    if change_state(area, send_back):
        answer.signal(f"{PART}.verdict_check", "sent_back")
        answer.refuse(refusal_for(judged, area, items, settings["tries"]) + f" ({PART}.verdict_check)")
        return
    note = message("verdict_unreadable.md", agent=agent_name(hook_input.get("agent_type")), area=area,
                   tries=settings["tries"])
    after_others(settings, answer, lambda: count_review(
        hook_input, settings, area, agent, dict(judged, failed=[], passed=[], outside=[]), note))


def agent_stop(hook_input, settings, answer):
    found = under_list(hook_input, settings)
    if not found:
        return
    parsed, agent = found
    area, items = parsed["area"], parsed["items"]
    state = read_state(area)
    if ledger.agent_of(state, agent)["counted"]:
        return
    judged = ledger.judge_report(str(hook_input.get("last_assistant_message") or ""), items, state["passed"])
    if not judged["problems"]:
        count_review(hook_input, settings, area, agent, judged)
        return

    def stop_once(current):
        stops = ledger.agent_of(current, agent)["stops"] + 1
        return ledger.with_agent(current, agent, stops=stops), stops

    if change_state(area, stop_once) == 1 and settings["verdict_check"]:
        answer.signal(f"{PART}.verdict_check", "kept_going")
        answer.refuse(message("ended_without_verdict.md", area=area, lines="\n" + ledger.shape_lines(items)))
        return
    note = message("ended_twice.md", agent=agent_name(hook_input.get("agent_type")), area=area)
    count_review(hook_input, settings, area, agent, dict(judged, failed=[], passed=[], outside=[]), note)


def handle(event, hook_input, flags, answer):
    tool = hook_input.get("tool_name")
    settings = settings_of(flags)
    main_chat = not hook_input.get("agent_id")
    if event == "PreToolUse" and tool == "Agent":
        # Any failure here refuses builder and reviewer calls, so a stop never fails open.
        try:
            if main_chat:
                answer.add_context(chat_notices.take(hook_input.get("session_id")))
                lift_stops(hook_input, answer)
            agent_call(hook_input, settings, answer)
        except Unreadable as error:
            refuse_unreadable(hook_input, settings, answer, error.path)
        except Exception:  # noqa: BLE001
            refuse_unreadable(hook_input, settings, answer, files.state_path(FOLDER))
    elif event == "PostToolUse" and tool == "Agent" and main_chat:
        answer.add_context(chat_notices.take(hook_input.get("session_id")))
    elif event == "PreToolUse" and tool == "SubagentHandback":
        # An error refuses the hand-back, so a failed review is never lost and the stop opens on time.
        try:
            hand_back(hook_input, settings, answer)
        except Exception as error:  # noqa: BLE001
            refuse_for_state(settings, answer, path_of(error))
    elif event == "SubagentStop":
        # An error keeps the agent going, but only once, so an agent is never kept going without end.
        try:
            agent_stop(hook_input, settings, answer)
        except Exception as error:  # noqa: BLE001
            if not hook_input.get("stop_hook_active"):
                refuse_for_state(settings, answer, path_of(error))
