"""away_guard: while the user is away, only urgent questions reach them.

PreToolUse on AskUserQuestion holds a dialog unless its metadata.source is
"urgent: <why>", and logs each urgent one. A dialog that question_check will
refuse is left to it, so it is never held or logged. A typed prompt and a
dialog answer mark the user active. After a silent stretch they turn the
silence into a set away state instead of ending it, so a short note sent on
the way does not release the questions. While the user is away, each typed
prompt asks the chat to judge from the user's words whether they are back.
Prompts from other sessions and from the harness count as nothing. The
state and its files are in away.py. Claude Code only.
"""
import importlib.util
import os

from hub import files

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "away_guard"
TOOL = "AskUserQuestion"
# Prompts that come from a session or the harness, not from the user's keyboard.
NOT_TYPED = ("<task-notification>", "<system-reminder>", "Another Claude session")

HOLD = (
    "{state} Only urgent questions reach the user now. A question is urgent when it involves a live risk to "
    "users or data, a public deadline that falls before the user is back, or a step that blocks every session. "
    "Keep any other question for when the user is back, say in one line that you are holding it, and go on "
    'with the work that does not need it. To send an urgent one, set metadata.source to "urgent: <why>". {end}'
)
BACK = (
    "The user has been away since {since}, {reason}. If their words show they are back at work (a task, not "
    "a note sent on the way), run hubctl away clear. Then ask the held questions in one dialog and name the "
    "{count} made while they were away. If not, keep holding them."
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


def held_text(away, state, options):
    since = away.show(state["since"], options)
    if state["kind"] == "idle":
        now = (f"The user has been silent since {since}, with no prompt or answer for over "
               f"{options.idle_minutes:g} minutes.")
        end = "It stays held until the user's words show they are back and a chat runs hubctl away clear."
    elif state["kind"] == "quiet":
        now = f"The user is {state['reason']}, which count as away."
        end = (f"The quiet hours end at {away.show(state['until'], options)}. A prompt the user types before "
               "then opens dialogs again.")
    else:
        now = f"The user has been away since {since}, {state['reason']}."
        end = "Only when the user's words show they are back, run hubctl away clear."
    return HOLD.format(state=now, end=end)


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
        tool_input = hook_input.get("tool_input")
        metadata = tool_input.get("metadata") if isinstance(tool_input, dict) else None
        why = away.urgent_reason(metadata.get("source") if isinstance(metadata, dict) else None)
        if why:
            away.log_urgent(why, hook_input.get("session_id"), hook_input.get("cwd"))
            answer.signal(f"{PART}.hold_not_urgent", "urgent")
            return
        answer.signal(f"{PART}.hold_not_urgent", "held")
        answer.refuse(held_text(away, state, options))
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
            count = away.urgent_count(state["since"])
            answer.signal(f"{PART}.release_on_return", "reminded")
            answer.add_context(BACK.format(since=away.show(state["since"], options), reason=state["reason"],
                                           count=f"{count} urgent pass" if count == 1 else f"{count} urgent passes"))
