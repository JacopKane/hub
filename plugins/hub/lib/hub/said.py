"""What the user typed in a chat, read from the chat's transcript.

Tool results (so the files the model read), skill text, summaries and
notices are not typed prompts, and neither is a message from another chat or
an agent's report. A message the user typed while Claude was working counts
as typed.

Design limit. This reads what the chat shows, and it is not a sandbox.
"""
import json
import os
import re

READ_BYTES = 8 * 1024 * 1024
NOT_TYPED = ("<system-reminder>", "<local-command-", "<command-name>", "<command-message>", "<bash-stdout>",
             "<bash-stderr>", "<user-prompt-submit-hook>")
# Text that arrives as a user line but is another sender's message.
FROM_OTHERS = ("<task-notification>", "<teammate-message", "<agent-message", "<cross-session-message")
# A block that opens with a tag such as <name ...>, known or not, which the user did not type.
WRAPPED = re.compile(r"\s*<[A-Za-z][\w.:-]*(?:\s[^<>]*)?/?>")
# Words that make a message a negation, a refusal, a question, a condition or a request for later.
NEGATIONS = re.compile(r"\b(?:\w+n't|no|not|never|dont|didnt|doesnt|isnt|wasnt|wont|cant|shouldnt|wouldnt|couldnt|"
                       r"do not|cannot|without|keep|leave|if|unless|whether|why|how|what|should|later|"
                       r"refus\w*|refrain\w*|avoid\w*|reject\w*|declin\w*|den(?:y|ies|ied|ying)|prevent\w*|"
                       r"forb[ia]d\w*|neither|nor)\b")
# Marks that people and keyboards type for an apostrophe, such as U+02BC in "donʼt".
APOSTROPHES = str.maketrans({mark: "'" for mark in "’‘ʼ′＇`´"})
WORD = re.compile(r"[a-z0-9']+")


def typed_after(transcript_path, offset):
    """(timestamp, prompt) for every prompt the user typed at or after the byte offset, oldest first.

    The timestamp is the line's own UTC time as Claude Code writes it, or ""
    when the line has none, so the lines of two chats can be put in order. A
    message from another sender is skipped and cancels nothing. A missing
    file, or one shorter than the offset, gives no prompts, so a position
    taken on another file never counts.
    """
    try:
        size = os.path.getsize(transcript_path)
        if size < offset:
            return []
        with open(transcript_path, "rb") as handle:
            handle.seek(max(offset, size - READ_BYTES))
            raw = handle.read()
    except (OSError, TypeError):
        return []
    entries = [entry for entry in map(entry_of, raw.decode("utf-8", "replace").splitlines()) if entry is not None]
    typed = [(str(entry.get("timestamp") or ""), typed_text(entry)) for entry in entries]
    return [(stamp, text) for stamp, text in typed if text]


def typed_text(entry):
    """The words the user typed in one user line, or "".

    A line from another sender by its origin, a tool result or a meta line
    has none, and neither has a line whose first text block opens with a tag
    such as <name ...>. In any other line a block that opens with a tag is
    left out, and each other block counts only up to the first note, hook
    context, command output or other sender's message in it, so a note after
    the user's words does not join them.
    """
    origin = entry.get("origin")
    if origin is not None and not (isinstance(origin, dict) and origin.get("kind", "human") == "human"):
        return ""
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain") or "toolUseResult" in entry:
        return ""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    blocks =[content] if isinstance(content, str) else content if isinstance(content, list) else []
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in blocks):
        return ""
    texts = [text for text in (block if isinstance(block, str) else block.get("text") for block in blocks
                               if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text"))
             if isinstance(text, str) and text.strip()]
    if not texts or WRAPPED.match(texts[0]):
        return ""
    return "\n".join(own for own in (own_words(text) for text in texts if not WRAPPED.match(text)) if own)


def own_words(text):
    """A block's text up to the first mark of text the user did not type, stripped."""
    cut = min([found for found in (text.find(mark) for mark in NOT_TYPED + FROM_OTHERS) if found >= 0],
              default=len(text))
    return text[:cut].strip()


def entry_of(line):
    """One transcript line as a user line, or None for any other line."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if isinstance(entry, dict) and entry.get("type") == "attachment":
        entry = queued_entry(entry)
    return entry if isinstance(entry, dict) and entry.get("type") == "user" else None


def from_the_user(origin):
    """Whether a queued message's origin says the user typed it. A message with no origin did not come from them."""
    return isinstance(origin, dict) and origin.get("kind") == "human"


def queued_entry(entry):
    """A message that arrived while Claude was working, as a user line, or None for any other attachment.

    It counts as the user's only when its origin is of kind human. One with
    no origin, or with any other origin, is read as another sender's.
    """
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    origin = attachment.get("origin")
    line = {"type": "user", "message": {"role": "user", "content": attachment.get("prompt")},
            "origin": origin if from_the_user(origin) else {"kind": "not the user"}}
    if "timestamp" in entry:
        line["timestamp"] = entry["timestamp"]
    if attachment.get("isMeta") or attachment.get("commandMode", "prompt") != "prompt":
        line["isMeta"] = True
    return line


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content
                         if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))
    return ""


def folded(text):
    return text.casefold().translate(APOSTROPHES)


def plain(text):
    return WORD.findall(re.sub(r"[_\-./]", " ", folded(text)))
