"""away_guard: while the user is away, every question still reaches them.

Away mode never refuses a question or a dialog to the user. PreToolUse on
AskUserQuestion lets the dialog open and adds one line for the chat: the
user is away and may answer late, so it keeps working on what does not need
the answer. A dialog that question_check will refuse is left to it, so it
gets no note. A typed prompt and a dialog answer mark the user active. After
a silent stretch they turn the silence into a set away state instead of
ending it. While the user is away, each typed prompt asks the chat to judge
from the user's words whether they are back. Prompts from other sessions and
from the harness count as nothing. The state and its files are in away.py.
Claude Code only.
"""
import importlib.util
import os

from hub import files

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "away_guard"
TOOL = "AskUserQuestion"
# Prompts that come from a session or the harness, not from the user's keyboard.
NOT_TYPED = ("<task-notification>", "<system-reminder>", "Another Claude session")

NOTE = ("{state} The question still reaches the user, but they may answer late. "
        "Keep working on what does not need the answer.")
BACK = (
    "The user has been away since {since}, {reason}. If their words show they are back at work (a task, not "
    "a note sent on the way), run " + files.step_command() + " away clear. Questions to the user are never held."
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def away_module():
    return load(os.path.join(HERE, "away.py"), "hub_part_away_guard_away")


def question_check_refuses(hook_input, flags):
    if not flags.runs("question_check"):
        return False
    check = load(os.path.join(files.PARTS_DIR, "question_check", "handler.py"), "hub_part_question_check")
    return check.refusal(hook_input, flags) is not None


def note_text(away, state, options):
    since = away.show(state["since"], options)
    if state["kind"] == "idle":
        now = (f"The user has been silent since {since}, with no prompt or answer for over "
               f"{options.idle_minutes:g} minutes.")
    elif state["kind"] == "quiet":
        now = f"The user is {state['reason']}, which count as away until {away.show(state['until'], options)}."
    else:
        now = f"The user has been away since {since}, {state['reason']}."
    return NOTE.format(state=now)


def to_flag(away, state):
    """A silent stretch becomes a set away state at the user's next prompt or answer."""
    if state and state["kind"] == "idle":
        away.set_flag("silent, with no prompt or answer", since=state["since"])
        return away.flag()
    return state


def handle(event, hook_input, flags, answer):
    away = away_module()
    options = away.Options.from_values(flags.value)
    if event == "PreToolUse" and hook_input.get("tool_name") == TOOL:
        if not flags.runs(f"{PART}.hold_not_urgent"):
            return
        state = away.current(options)
        if not state or question_check_refuses(hook_input, flags):
            return
        # A note only, never a refusal: a question to the user always reaches them
        answer.signal(f"{PART}.hold_not_urgent", "noted")
        answer.add_context(note_text(away, state, options))
    elif event == "PostToolUse" and hook_input.get("tool_name") == TOOL:
        if flags.runs(f"{PART}.release_on_return"):
            to_flag(away, away.current(options))
        away.mark_active(prompt=False)
    elif event == "UserPromptSubmit":
        prompt = hook_input.get("prompt")
        if not isinstance(prompt, str) or prompt.lstrip().startswith(NOT_TYPED):
            return
        if not flags.runs(f"{PART}.release_on_return"):
            away.mark_active(prompt=True)
            return
        state = to_flag(away, away.current(options))
        away.mark_active(prompt=True)
        if state and state["kind"] == "flag":
            answer.signal(f"{PART}.release_on_return", "reminded")
            answer.add_context(BACK.format(since=away.show(state["since"], options), reason=state["reason"]))
