"""The restart part, in Claude Code.

It keeps hub's session records (sessions.note), which hold each chat's
terminal, arguments and folder. restart.py reads them to find a chat's tmux
pane or iTerm tab and to bring the chat back with its own flags. The
restart itself runs from the hub-restart skill, never from a hook.

doctor(flags) says what a restart would do now and how the newest run went.
A chat that a cut-off run left stopped gets a warning.
"""
import importlib.util
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import sessions  # noqa: E402


def handle(event, hook_input, flags, answer):
    if event in ("SessionStart", "UserPromptSubmit", "SessionEnd"):
        sessions.note(event, hook_input)


def load_restart():
    """restart.py, beside this file, by its path."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart.py")
    spec = importlib.util.spec_from_file_location("hub_part_restart_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def doctor(flags):
    restart = load_restart()
    values = {flag_id: flags.value(flag_id) for flag_id in
              (restart.PART, *(f"{restart.PART}.{child}" for child in ("ask_first", "upgrade", "terminal",
                                                                     "wait_minutes")))}
    return restart.doctor_lines(values)
