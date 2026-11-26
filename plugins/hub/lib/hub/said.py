"""Whether the user asked for a change in the newest message they typed.

Self-protection lets a tool call lower a protection, or change a value the
user set by hand, only when this says yes. It reads the chat's transcript
and keeps the newest prompt the user typed. Tool results (so the files the
model read), skill text, summaries and notices are not typed prompts. A
message from another chat or an agent's report is newer than the user's
prompt, so after one there is no typed request until the user types again.

A message the user typed while Claude was working counts as typed, and a
message from another chat or an agent that arrived then ends the request.

The request must be an order aimed at the flag's name. A part is named by
its id or the plain words of its name. A flag under a part is named by its
full id, or by the part's name and its own name close together, so "the
guard" alone names no flag. Only small words such as "the" may stand between
the order and the name. To lower a protection, the order must turn it off or
pause it, as in "turn off self-protection", "turn self-protection
off", "disable self-protection" or "pause self-protection".
Uninstalling needs "uninstall hub". A sentence that only mentions the flag,
such as "remove what self-protection flagged", orders nothing. A
question, a negation, a refusal, a condition or a request for later asks for
nothing.

Design limit. This reads what the chat shows. It stops the model from
talking itself into a change, and it is not a sandbox.
"""
import json
import os
import re

READ_BYTES = 8 * 1024 * 1024
# How many words may stand between the flag's name and the change word.
NEAR = 4
NOT_TYPED = ("<system-reminder>", "<local-command-", "<command-name>", "<command-message>", "<bash-stdout>",
             "<bash-stderr>", "<user-prompt-submit-hook>")
# Text that arrives as a user line but is another sender's message.
FROM_OTHERS = ("<task-notification>", "<teammate-message", "<agent-message", "<cross-session-message")
# A block that opens with a tag such as <name ...>, known or not, which the user did not type.
WRAPPED = re.compile(r"\s*<[A-Za-z][\w.:-]*(?:\s[^<>]*)?/?>")
# Small words that may stand between an order and the name it is aimed at.
FILLER = {"the", "a", "my", "our", "this", "that", "its", "hub", "hub's", "hubs", "now", "please", "just"}
# Orders that lower the flag named right after them.
OFF_ORDERS = {"disable", "pause", "deactivate"}
# Orders that need "off" or "on", as in "turn off the gate" and "turn the gate off".
TURN_ORDERS = {"turn", "switch", "shut"}
CHANGE_ORDERS = OFF_ORDERS | TURN_ORDERS | {"enable", "activate", "set", "change", "use", "restore", "raise",
                                            "block", "reset", "unpin", "put"}
UNINSTALL_WORDS = {"uninstall"}
# Words that make a message a negation, a refusal, a question, a condition or a request for later.
NEGATIONS = re.compile(r"\b(?:\w+n't|no|not|never|dont|didnt|doesnt|isnt|wasnt|wont|cant|shouldnt|wouldnt|couldnt|"
                       r"do not|cannot|without|keep|leave|if|unless|whether|why|how|what|should|later|"
                       r"refus\w*|refrain\w*|avoid\w*|reject\w*|declin\w*|den(?:y|ies|ied|ying)|prevent\w*|"
                       r"forb[ia]d\w*|neither|nor)\b")
# Marks that people and keyboards type for an apostrophe, such as U+02BC in "don\u02bct".
APOSTROPHES = str.maketrans({mark: "'" for mark in "\u2019\u2018\u02bc\u2032\uff07`\u00b4"})
WORD = re.compile(r"[a-z0-9']+")


def newest_typed(transcript_path):
    """The newest prompt the user typed in this chat, or "" when there is none or another sender spoke after it."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return ""
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(max(0, os.path.getsize(transcript_path) - READ_BYTES))
            raw = handle.read()
    except OSError:
        return ""
    typed = ""
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "attachment":
            entry = queued_entry(entry)
        if entry is None or entry.get("type") != "user":
            continue
        kind = typed_kind(entry)
        if kind == "typed":
            typed = text_of(entry["message"]["content"])
        elif kind == "other sender":
            typed = ""
    return typed


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


def typed_kind(entry):
    """"typed", "other sender" or "skip" for one user line of the transcript."""
    origin = entry.get("origin")
    if isinstance(origin, dict) and origin.get("kind", "human") != "human":
        return "other sender"
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain") or "toolUseResult" in entry:
        return "skip"
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list) and any(isinstance(item, dict) and item.get("type") == "tool_result"
                                         for item in content):
        return "skip"
    text = text_of(content).lstrip()
    if not text or text.startswith(NOT_TYPED):
        return "skip"
    if text.startswith(FROM_OTHERS):
        return "other sender"
    return "typed"


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content
                         if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))
    return ""


def segment_words(segment):
    return segment.replace("_", " ").replace("-", " ").split()


def part_names(part_id):
    """The phrases that name a part: the plain words of its id, and without the first word when there are three."""
    words = segment_words(part_id)
    found = {" ".join(words)}
    if len(words) >= 3:
        found.add(" ".join(words[1:]))
    return found - {""}


def named_at(words, flag_id):
    """The spans of words that name a flag: its full id, or for a part, its name.

    A flag under a part is named by the part's name and the flag's own name
    with at most NEAR words between them, in either order.
    """
    segments = flag_id.split(".")
    found = spans(words, plain(flag_id))
    if len(segments) == 1:
        return found + [spot for name in part_names(flag_id) for spot in spans(words, plain(name))]
    leaf = spans(words, plain(" ".join(segment_words(segments[-1]))))
    for name in part_names(segments[0]):
        for part_start, part_end in spans(words, plain(name)):
            for leaf_start, leaf_end in leaf:
                gap = leaf_start - part_end - 1 if leaf_start > part_end else part_start - leaf_end - 1
                if 0 <= gap <= NEAR:
                    found.append((min(part_start, leaf_start), max(part_end, leaf_end)))
    return found


def folded(text):
    return text.casefold().translate(APOSTROPHES)


def plain(text):
    return WORD.findall(re.sub(r"[_\-./]", " ", folded(text)))


def spans(words, phrase):
    size = len(phrase)
    return [(start, start + size - 1) for start in range(len(words) - size + 1) if words[start:start + size] == phrase]


def asks_for(text, flag_id, lowering, wanted=None):
    """Whether text orders a change to flag_id, and to lower it when lowering is True.

    wanted replaces the orders, such as UNINSTALL_WORDS for hub itself.
    """
    if not text or "?" in text:
        return False
    if NEGATIONS.search(folded(text)):
        return False
    words = plain(text)
    return any(aimed(words, start, end, lowering, wanted) for start, end in named_at(words, flag_id))


def aimed(words, start, end, lowering, wanted):
    """Whether an order stands right before the name at words[start:end + 1], with only FILLER between."""
    before = start - 1
    while before >= 0 and words[before] in FILLER:
        before -= 1
    order = words[before] if before >= 0 else ""
    after = [word for word in words[end + 1:] if word not in FILLER]
    if wanted:
        return order in wanted
    particles = {"off"} if lowering else {"off", "on"}
    if order in particles and before > 0 and words[before - 1] in TURN_ORDERS:
        return True  # turn off self-protection
    if order in TURN_ORDERS and after[:1] and after[0] in particles:
        return True  # turn self-protection off
    if not order and len(after) == 1 and after[0] in particles:
        return True  # self-protection off
    return order in (OFF_ORDERS if lowering else CHANGE_ORDERS)


def user_asked(transcript_path, flag_id, lowering):
    return asks_for(newest_typed(transcript_path), flag_id, lowering)


def user_asked_to_uninstall(transcript_path):
    """Whether the newest message the user typed asks to uninstall hub, such as "uninstall hub"."""
    return asks_for(newest_typed(transcript_path), "hub", True, UNINSTALL_WORDS)
