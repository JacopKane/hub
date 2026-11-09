"""chrome_lock: only the chat that holds the Chrome lock drives Chrome.

A Chrome tool call from any chat but the lock's owner is refused, and the
reason names the owner, its job and when it took the lock. A subagent sends
its main chat's session id, so a chat and its agents count as one owner. A
chat may not run the step's `browser-lock clear`, because only the user clears a
lock. The lock itself is in lock.py. Claude Code only.
"""
import importlib.util
import os
import re

from hub import files

HERE = os.path.dirname(os.path.abspath(__file__))
PART = "chrome_lock"
CLEAR = re.compile(r"\bbrowser-lock\s+clear\b|\block\.py\b[^;&|\n]*\bclear\b")
STALE = ("If that chat has ended or crashed, the lock is stale, and the user clears it in a terminal with "
         f"{files.step_command()} browser-lock clear.")
USER_ONLY = ("Only the user clears the Chrome lock, from a terminal, because a chat can not tell whether the "
             "chat that holds it is still working. Wait for the lock instead, or ask the user to clear it.")


def lock_module():
    spec = importlib.util.spec_from_file_location("hub_part_chrome_lock_lock", os.path.join(HERE, "lock.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def refusal(lock, me, wait):
    record = lock.owner()
    if record is None:
        return None
    if not record:
        seconds = lock.age() or 0
        if seconds <= lock.FRESH:
            return f"A chat is taking the Chrome lock right now. {wait}".strip()
        return f"The Chrome lock has no owner file and is {int(seconds)} s old, so it is stale. {STALE} {wait}".strip()
    if record["session"] == me:
        return None
    return (f'Chrome is in use by another chat, session {record["session"]}, job "{record["job"]}", since '
            f'{record["start"]}. {wait} {STALE}').replace("  ", " ")


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse":
        return
    tool = str(hook_input.get("tool_name") or "")
    if tool == "Bash":
        command = (hook_input.get("tool_input") or {}).get("command") if isinstance(
            hook_input.get("tool_input"), dict) else None
        if flags.runs(f"{PART}.user_clears") and isinstance(command, str) and CLEAR.search(command):
            answer.signal(f"{PART}.user_clears", "refused")
            answer.refuse(USER_ONLY)
        return
    prefix = flags.value(f"{PART}.tool_prefix") or ""
    if not prefix or not tool.startswith(prefix) or not flags.runs(f"{PART}.one_chat"):
        return
    reason = refusal(lock_module(), str(hook_input.get("session_id") or ""), flags.text(f"{PART}.wait"))
    if reason:
        answer.signal(f"{PART}.one_chat", "refused")
        answer.refuse(reason)
