"""The tuner part: setup on the first run, then tuning from the signals.

First run: a session starts and hub's settings file, hub.json, is missing. The
start text then holds the scan of this machine and tells the chat to run hub
setup before the user's task. The step's `setup` writes hub.json, so this runs
once per machine and covers both tools.

After that, each session start first shows the tuner's waiting one-line
notices, once per tool. When neither a system message nor a chat line shows
them, the start text holds them. A change then reaches the user even where no
mod runs. In Claude Code the start text leaves out a line the band showed,
and the band leaves out a line the start text told, so each shows once.
Then the rule filter runs when it is due. Where the
mod asked lately, its side call decides the candidates beside the chat.
Elsewhere the chat gets them and the command that decides them. At SubagentStop
a builder run that made no edit is logged, and a few of them offer researcher.
"""
import os
import sys

from hub import files

TEXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "text")
FIRST_RUN = os.path.join(TEXT, "first_run.md")
TUNE = os.path.join(TEXT, "tune.md")
NOTICES = os.path.join(TEXT, "notices.md")
HOW_TO_ASK = {"claude_code": "Ask them in one AskUserQuestion dialog.",
              "cursor": "Ask them in one plain message."}


def first_run_text(flags, cwd):
    from hub import scan
    found = scan.summary(scan.run(cwd)) if flags.runs("tuner.setup.scan") else "(the scan is off)"
    return fill(FIRST_RUN, {"step": files.step_command(), "how_to_ask": HOW_TO_ASK[flags.tool],
                            "scan": found})


def fill(path, values):
    text = files.read_text(path) or ""
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text.strip()


def tune_text(flags, found):
    from hub import tuner
    return fill(TUNE, {"step": files.step_command(), "candidates": tuner.candidate_lines(found)})


def shows_notices(flags):
    """Where this tool shows the tuner's line. With the band on, Claude Code shows it there, with Undo."""
    if flags.tool == "cursor":
        return flags.runs("cursor_link.tuner_chat_line")
    return flags.runs("tuner.notices.system_message") and not flags.runs("status_band.notices")


def is_first_run():
    return not os.path.exists(files.user_settings_path())


def handle(event, hook_input, flags, answer):
    if flags.tool == "cursor" and not flags.runs("cursor_link"):
        return  # what the tuner does in Cursor is part of the Cursor beta, which is off
    if event == "SubagentStop":
        from hub import tuner
        # Cursor's subagentStop fields are unverified, so only an input that names the agent type counts.
        tuner.builder_run(hook_input.get("cwd") or os.getcwd(), flags.tool, hook_input.get("agent_type"),
                          hook_input.get("agent_transcript_path"))
        return
    if event != "SessionStart":
        return
    cwd = hook_input.get("cwd") or os.getcwd()
    if is_first_run():
        if flags.runs("tuner.setup"):
            answer.add_context(first_run_text(flags, cwd))
        return
    from hub import tuner
    lines = tuner.take_notices(flags.tool)
    if shows_notices(flags):
        for line in lines:
            answer.notify(line)
    elif lines:
        # Nothing else shows them here, and the band needs a mod that may not run. So the chat passes them on.
        answer.add_context(fill(NOTICES, {"lines": "\n".join(lines)}))
    found = tuner.run_if_due(cwd, flags.tool)
    if found and tuner.mod_decides(flags):
        found = []
    # When the mod's side call gave up, this chat decides the list instead.
    found = found or tuner.take_given_back()
    if found:
        answer.add_context(tune_text(flags, found))


if __name__ == "__main__":
    sys.exit("the tuner handler runs from the dispatcher")
