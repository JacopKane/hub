"""Notices kept for one chat until it next starts or takes a turn.

A part leaves text here when it must tell a chat something while that chat
is down, or while a post to it can not be trusted. The dispatcher adds it as
context at the chat's next SessionStart or UserPromptSubmit, once. It does
this whichever parts run then, so a notice from a part that was turned off
still arrives. The files are <state>/chat_notices/<session id>.jsonl.
"""
import json
import os
import re
import time

from . import files

FOLDER = "chat_notices"
EVENTS = ("SessionStart", "UserPromptSubmit")
SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


def path_for(session):
    session = SAFE_ID.sub("", str(session or ""))
    return files.state_path(os.path.join(FOLDER, session + ".jsonl")) if session else None


def leave(session, text):
    """Keep text for the chat. Returns whether it was kept."""
    path = path_for(session)
    if path is None or not text:
        return False
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (json.dumps({"at": time.time(), "text": text}) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def take(session):
    """Every notice kept for the chat, oldest first, joined; and none of them is handed over twice."""
    path = path_for(session)
    if path is None:
        return ""
    taken = f"{path}.{os.getpid()}.taken"
    try:
        os.rename(path, taken)  # one hook takes them, even when two run at once
    except OSError:
        return ""
    texts = []
    try:
        with open(taken, encoding="utf-8") as handle:
            for line in handle:
                try:
                    texts.append(json.loads(line)["text"])
                except (ValueError, KeyError, TypeError):
                    continue
    finally:
        os.unlink(taken)
    return "\n\n".join(text for text in texts if isinstance(text, str) and text)
