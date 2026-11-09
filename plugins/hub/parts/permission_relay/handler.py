"""The permission relay, layer 1, in Claude Code. The records are in relay_store.py, beside this file.

- PermissionRequest keeps a record of the prompt that waits in this chat,
  and PermissionDenied one of the call auto mode blocked (list.blocks).
- PostToolUse of the same session, agent, tool and input clears a prompt,
  since the user answered it. Stop and UserPromptSubmit in the main thread
  clear the main thread's prompts. SessionEnd clears every record of the
  chat. A block stays until the user keeps it blocked, the chat ends or an
  hour passes.
- PreToolUse is the guard: in every chat and subagent, a Read, Grep, Glob,
  Bash, Edit, Write or NotebookEdit call that names the relay folder is
  refused, so one chat's command text never reaches a model through hub.

The hook never answers a prompt and never adds text for the model. Its only
answer is the guard's refusal.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(os.path.dirname(HERE)), "lib")
for folder in (LIB, HERE):
    if folder not in sys.path:
        sys.path.insert(0, folder)

from hub import files  # noqa: E402

import relay_store  # noqa: E402

PART = relay_store.PART
PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"}
SEARCH_TOOLS = {"Grep", "Glob"}
GLOB_CHARACTERS = set("*?[{")
NAME_CHARACTER = re.compile(r"[A-Za-z0-9_.-]")


# the guard

def forms(path):
    """The plain and the resolved form of a path, so a link in the state folder's path is no way around."""
    return {os.path.normpath(path), os.path.realpath(path)}


def full_path(path, cwd):
    if path == "~" or path.startswith("~/"):
        path = os.path.join(files.home(), path[2:])
    return os.path.normpath(os.path.join(cwd or "/", path))


def inside(path, folders):
    return any(form == folder or form.startswith(folder.rstrip(os.sep) + os.sep)
               for form in forms(path) for folder in folders)


def above_or_inside(path, folders):
    """Whether a search from path would reach into one of the folders."""
    return inside(path, folders) or any(folder.startswith(form.rstrip(os.sep) + os.sep)
                                        for form in forms(path) for folder in folders)


def pattern_root(pattern):
    """The folder a glob pattern starts in: the part before its first glob character."""
    words = []
    for word in pattern.split("/"):
        if GLOB_CHARACTERS & set(word):
            break
        words.append(word)
    root = "/".join(words)
    return root if root or not pattern.startswith("/") else "/"


def text_forms(path):
    """How a command may write a path: as it is, from ~, $HOME or ${HOME}."""
    found = set(forms(path))
    home = os.path.normpath(files.home())
    for form in list(found):
        if form.startswith(home + os.sep):
            rest = form[len(home) + 1:]
            found |= {"~/" + rest, "$HOME/" + rest, "${HOME}/" + rest}
    return found


def state_forms():
    found = text_forms(files.state_dir())
    found |= {"$XDG_STATE_HOME/hubctl", "${XDG_STATE_HOME}/hubctl"}
    return found


def names_relay(command):
    """Whether a shell command names the relay folder, or the state folder as a whole or through a glob."""
    relay = os.path.join(files.state_dir(), relay_store.FOLDER)
    relay_forms = text_forms(relay) | {form + "/" + relay_store.FOLDER for form in state_forms()}
    if any(form in command for form in relay_forms):
        return True
    for form in state_forms():
        start = 0
        while True:
            index = command.find(form, start)
            if index < 0:
                break
            start = index + 1
            after = command[index + len(form):]
            if not after or not NAME_CHARACTER.match(after[0]) and after[0] != "/":
                return True  # the state folder itself
            if after[0] == "/":
                word = re.match(r"/([^/\s\"';|&)<>]*)", after).group(1)
                if not word or word in (".", "..") or GLOB_CHARACTERS & set(word) or word.startswith("."):
                    return True
    return False


def refuses(hook_input):
    """Whether this call names the relay folder."""
    tool, tool_input = hook_input.get("tool_name"), hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    cwd = hook_input.get("cwd") or os.getcwd()
    relay = forms(os.path.join(files.state_dir(), relay_store.FOLDER))
    if tool in PATH_TOOLS:
        path = tool_input.get(PATH_TOOLS[tool]) or tool_input.get("file_path")
        return isinstance(path, str) and bool(path) and inside(full_path(path, cwd), relay)
    if tool in SEARCH_TOOLS:
        base = tool_input.get("path")
        base = full_path(base, cwd) if isinstance(base, str) and base else os.path.normpath(cwd)
        if above_or_inside(base, relay):
            return True
        pattern = tool_input.get("pattern") if tool == "Glob" else None
        root = pattern_root(pattern) if isinstance(pattern, str) else ""
        return bool(root) and above_or_inside(full_path(root, base), relay)
    if tool == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and names_relay(command)
    return False


def guard(hook_input, flags, answer):
    if flags.runs(f"{PART}.guard") and refuses(hook_input):
        answer.signal(f"{PART}.guard", "blocked")
        answer.refuse(flags.text(f"{PART}.guard") or "The permission relay keeps its folder private.")


# the records

def handle(event, hook_input, flags, answer):
    if event == "PreToolUse":
        guard(hook_input, flags, answer)
        return
    if not flags.runs(f"{PART}.list"):
        return
    session = hook_input.get("session_id")
    agent = str(hook_input.get("agent_id") or "")
    if event == "PermissionRequest":
        relay_store.add(relay_store.PROMPT, hook_input)
    elif event == "PermissionDenied":
        if flags.runs(f"{PART}.list.blocks"):
            relay_store.add(relay_store.BLOCK, hook_input, hook_input.get("reason") or "")
    elif event == "PostToolUse":
        tool = relay_store.clean(hook_input.get("tool_name") or "", 200)
        hashed = relay_store.input_hash(hook_input.get("tool_input"))
        relay_store.clear(session, keep=lambda record: not (
            record["kind"] == relay_store.PROMPT and record["agent"] == agent and record["tool"] == tool
            and record["input_hash"] == hashed))
    elif event in ("Stop", "UserPromptSubmit") and not agent:
        relay_store.clear(session, keep=lambda record: record["kind"] != relay_store.PROMPT or record["agent"])
    elif event == "SessionEnd":
        relay_store.clear(session)
