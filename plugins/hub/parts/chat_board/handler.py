"""The chat board, in Claude Code.

states: each hook event of a chat sets its card in <state>/board/chats.
UserPromptSubmit and PreToolUse mean busy. Stop and the idle_prompt
notification mean idle. PermissionRequest, the notifications that wait for
the user, and a question dialog no part refused mean needs you. The card
needs you while any yes is open.

The chat's own thread and each subagent keep separate waits, and an event of
one thread changes only that thread. A subagent changes the card only when
it asks for a yes. That yes stays open until the agent's next event or its
SubagentStop, whatever the chat's own thread does meanwhile. A
permission_prompt notification has no agent id, so while a subagent waits it
counts as that agent's. A SessionStart in a new process drops the old
process's waits. SessionEnd removes the card and keeps the chat's task
ledger. A card is written only when it changes, so a busy chat writes once,
not on every tool call.

list_agents_mirror: a ListAgents result the chat already has is kept as the
board's listing, so the board needs no extra `claude agents --json` call.

The chats family runs before the question parts, so a dialog's state is set
after every part answered (Answer.after_all), and a refused dialog leaves the
chat busy.
"""
import json
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import board, files, sessions  # noqa: E402

PART = "chat_board"
MAX_WAITING = 20  # at most this many subagents on one card; the oldest go first
WAITING = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}


def question_of(tool_input):
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if isinstance(questions, list) and questions and isinstance(questions[0], dict):
        return board.clean(questions[0].get("question")) or None
    return None


def permission_question(hook_input):
    tool = board.clean(hook_input.get("tool_name"), 60) or "a tool"
    return f"for your yes to use {tool}"


def chat_name(hook_input, card):
    """The chat's title as one plain line, from hook input, its record, its card or its folder."""
    cwd = hook_input.get("cwd") if isinstance(hook_input.get("cwd"), str) else ""
    record = board.record_of(hook_input.get("session_id")) or {}
    for name in (hook_input.get("session_title"), record.get("title"), (card or {}).get("name"),
                 os.path.basename(os.path.normpath(cwd)) if cwd else ""):
        name = board.clean(name, cell_limit=board.NAME_CELLS) if isinstance(name, str) else ""
        if name:
            return name
    return "chat"


def card_of(hook_input):
    """The chat's card path and its card, or None for a missing, broken or linked card file."""
    session = hook_input.get("session_id")
    path = board.card_path(session) if isinstance(session, str) else None
    if not path:
        return None, None
    card = board.read(path)
    return path, card if isinstance(card, dict) and not os.path.islink(path) else None


def set_state(hook_input, state, question=None, waiting=None):
    """Write the card when its state, question, name or waiting agents changed.

    waiting lists the subagents that wait for the user's yes, each with its question, and the state and question
    from before the first one asked."""
    path, card = card_of(hook_input)
    if not path:
        return
    name = chat_name(hook_input, card)
    if card and card.get("state") == state and card.get("question") == question and card.get("name") == name and \
            card.get("waiting") == waiting:
        return
    new = {"session": hook_input.get("session_id"), "name": name, "machine": board.machine(), "state": state,
           "since": board.now(), "question": question,
           "cwd": hook_input.get("cwd") if isinstance(hook_input.get("cwd"), str) else None}
    if waiting:
        new["waiting"] = waiting
    board.write(path, new)


def waiting_of(card):
    """A card's waiting agents, its state from before they asked and that state's question. Each field is checked,
    since the file may be broken or old."""
    waiting = (card or {}).get("waiting")
    if not isinstance(waiting, dict) or not isinstance(waiting.get("agents"), dict):
        return {}, None, None
    agents = {agent: question for agent, question in waiting["agents"].items()
              if isinstance(agent, str) and isinstance(question, str)}
    before = waiting.get("before") if waiting.get("before") in ("busy", "idle", "needs_you") else "idle"
    question = waiting.get("before_question") if isinstance(waiting.get("before_question"), str) else None
    return agents, before, question


def own_state(card):
    """The state and question of the chat's own thread, read from under the subagent waits when some are open."""
    agents, before, before_question = waiting_of(card)
    if agents:
        return agents, before, before_question
    state = card.get("state") if card and card.get("state") in ("busy", "idle", "needs_you") else "idle"
    question = card.get("question") if card and isinstance(card.get("question"), str) else None
    return agents, state, question


def show(hook_input, state, question, agents):
    """Write the card from the chat's own state and its waiting subagents. While one waits, the card needs the
    user, with the chat's own question if it asks too, else the newest agent's."""
    if not agents:
        set_state(hook_input, state, question)
        return
    shown = question if state == "needs_you" else list(agents.values())[-1]
    set_state(hook_input, "needs_you", shown, {"agents": agents, "before": state, "before_question": question})


def own_event(hook_input, state, question=None, keep_agents=True):
    """Set the state of the chat's own thread and keep the subagents' waits."""
    _, card = card_of(hook_input)
    agents = own_state(card)[0] if keep_agents else {}
    show(hook_input, state, question, agents)


def agent_asks(hook_input, question):
    """A subagent waits for the user's yes, so its chat needs the user."""
    agent = board.clean(hook_input.get("agent_id"), 80)
    _, card = card_of(hook_input)
    agents, state, own_question = own_state(card)
    agents.pop(agent, None)
    agents[agent] = question
    show(hook_input, state, own_question, dict(list(agents.items())[-MAX_WAITING:]))


def agent_goes_on(hook_input):
    """A waiting subagent sent its next event, so its yes is no longer open."""
    agent = board.clean(hook_input.get("agent_id"), 80)
    _, card = card_of(hook_input)
    agents, state, own_question = own_state(card)
    if agent not in agents:
        return
    del agents[agent]
    show(hook_input, state, own_question, agents)


def listing_rows(response):
    """The rows of a ListAgents result: a list, a list under any key, or the same as JSON text."""
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return None
    if isinstance(response, dict):
        response = next((value for value in response.values() if isinstance(value, list)), None)
    if isinstance(response, list) and all(isinstance(row, dict) for row in response):
        return response
    return None


def handle(event, hook_input, flags, answer):
    if hook_input.get("agent_id"):
        if flags.runs(f"{PART}.states") and board.clean(hook_input.get("agent_id"), 80):
            if event == "PermissionRequest":
                agent_asks(hook_input, permission_question(hook_input))
            elif event != "Notification":
                agent_goes_on(hook_input)
        return
    if event in ("SessionStart", "UserPromptSubmit", "SessionEnd"):
        sessions.note(event, hook_input)
    if event == "PostToolUse" and hook_input.get("tool_name") == "ListAgents" and \
            flags.runs(f"{PART}.list_agents_mirror"):
        rows = listing_rows(hook_input.get("tool_response"))
        if rows is not None:
            board.keep_listing(rows)
    if event == "SessionEnd":
        board.remove_card(hook_input.get("session_id"))
    if not flags.runs(f"{PART}.states") or event == "SessionEnd":
        return
    if event == "SessionStart":
        # A new process has no subagents yet. After a clear or a compaction, the running ones stay.
        own_event(hook_input, "idle", keep_agents=hook_input.get("source") not in ("startup", "resume"))
    elif event == "Stop":
        own_event(hook_input, "idle")
    elif event == "Notification":
        kind = hook_input.get("notification_type")
        agents, state, _ = own_state(card_of(hook_input)[1])
        if kind == "permission_prompt" and agents and state != "needs_you":
            return  # This is the waiting subagent's prompt, and its PermissionRequest already set the card.
        if kind in WAITING:
            own_event(hook_input, "needs_you", board.clean(hook_input.get("message")) or None)
        elif kind == "idle_prompt":
            own_event(hook_input, "idle")
    elif event == "PermissionRequest":
        own_event(hook_input, "needs_you", permission_question(hook_input))
    elif event == "PreToolUse" and hook_input.get("tool_name") == "AskUserQuestion":
        question = question_of(hook_input.get("tool_input")) or "a question"
        answer.after_all(lambda final: own_event(hook_input, "busy" if final.refusal is not None else "needs_you",
                                                 None if final.refusal is not None else question))
    elif event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
        own_event(hook_input, "busy")


def doctor(flags):
    """What hubctl doctor says about the board. It reads files only and never runs the chat listing."""
    from hub import doctor as doctor_module  # only when the doctor runs
    lines = []
    if not any(gap["part"] == PART for gap in doctor_module.cursor_gaps()):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "part.json"), encoding="utf-8") as handle:
            reason = json.load(handle)["reason_not_in"]["cursor"]
        lines.append(("note", f"it runs only in Claude Code. {reason}"))
    kept = [copy for copy in (board.read(files.state_path(board.LISTING)),
                              board.read(files.state_path(sessions.LISTING)))
            if isinstance(copy, dict) and isinstance(copy.get("rows"), list) and board.is_number(copy.get("time"))]
    if kept:
        age = max(0, board.now() - max(copy.get("time", 0) for copy in kept))
        lines.append(("note", f"its chat listing is {board.since(age) if age >= 60 else 'under a minute'} old."))
    else:
        lines.append(("note", "it holds no chat listing yet, so the board runs claude agents --json when it opens."))
    lines.append(("note", "each chat writes its own task list. Claude Code's own task list is not copied."))
    return lines
