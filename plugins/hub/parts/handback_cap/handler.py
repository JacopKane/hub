"""handback_cap: an agent's hand-back stays under a word cap.

A SubagentHandback message longer than handback_cap.words, plus the margin
in percent, is refused, so the agent shortens it. Each agent is refused at
most handback_cap.refuse_over.tries times, counted in the state folder by
its agent id. After that, or while refuse_over is off, the message goes
through with a warning. Claude Code only.
"""
import os

from hub import files

PART = "handback_cap"
REFUSE = "handback_cap.refuse_over"
COUNTS = "handback_cap"


def count_path(agent):
    safe = "".join(character for character in str(agent) if character.isalnum() or character in "-_") or "main"
    return files.state_path(os.path.join(COUNTS, safe + ".json"))


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or hook_input.get("tool_name") != "SubagentHandback":
        return
    message = (hook_input.get("tool_input") or {}).get("message") or ""
    cap = int(flags.value(f"{PART}.words") or 0)
    found = len(str(message).split())
    if cap <= 0 or found <= cap:
        return
    finding = f"Your hand-back has {found} words, the cap is {cap}."
    if not flags.runs(REFUSE):
        answer.warn(PART, finding, f"Keep the next one to {cap} words or fewer.")
        answer.signal(PART, "over_cap")
        return
    margin = float(flags.value(f"{REFUSE}.margin") or 0)
    if found <= cap * (1 + margin / 100):
        return
    path = count_path(hook_input.get("agent_id") or hook_input.get("session_id"))
    with files.locked(os.path.join(COUNTS, os.path.basename(path) + ".lock")):
        refused = (files.read_json(path, {}) or {}).get("refused", 0)
        if refused < int(flags.value(f"{REFUSE}.tries") or 0):
            files.write_json(path, {"refused": refused + 1})
            answer.signal(REFUSE, "refused")
            answer.refuse(f"{finding} Cut it to {cap} words or fewer and hand it back again. "
                          f"Keep what the main chat needs to act on and the absolute file paths. ({REFUSE})")
            return
    answer.warn(REFUSE, finding, f"It goes through, because it was sent back {refused} times.")
    answer.signal(REFUSE, "over_cap")
