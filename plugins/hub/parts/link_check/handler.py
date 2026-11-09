"""link_check: a reply that names a PR, issue or ticket without a link goes back once.

At Stop it reads the reply (last_assistant_message, else the last assistant
text in the transcript), leaves out fenced code, inline code, links and URLs,
and looks for bare references. When it finds one, the reply is sent back
with the list, so Claude adds the links. A Stop that comes from such a
send-back (stop_hook_active) always passes, so it never loops.

A hex colour such as "colour: #123456" is not a PR, and a prefix in
link_check.tickets.skip, such as UTF in UTF-8, is not a ticket.
"""
import json
import re

PART = "link_check"
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
URL = re.compile(r"<?https?://[^\s>)\]]+>?")
PR = re.compile(r"(?<![\w/&#])#(\d{1,6})(?![\w-])")
PR_WORD = re.compile(r"\bPR\s+(\d+)\b")
ISSUE_REPO = re.compile(r"(?<![\w/])[\w.-]+/[\w.-]+#\d+\b")
ISSUE_WORD = re.compile(r"\b[Ii]ssue\s+#?(\d+)\b")
TICKET = re.compile(r"(?<![\w/-])([A-Z][A-Z0-9]{1,9})-(\d+)(?![\w-])")
COLOUR_BEFORE = re.compile(r"(colou?r|fill|stroke|background|border|bg|hex)\s*:?\s*$", re.I)


def last_reply(path):
    """The text of the last assistant message in a Claude Code transcript, or ""."""
    text = ""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
                    continue
                content = (entry.get("message") or {}).get("content") or []
                parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
                if any(part.strip() for part in parts):
                    text = "\n".join(parts)
    except (OSError, TypeError):
        return ""
    return text


def prose(text):
    """The reply without fenced code, inline code, links and URLs."""
    lines, fenced = [], None
    for line in text.splitlines():
        match = FENCE.match(line)
        if fenced:
            if match and match.group(1)[0] == fenced[0] and len(match.group(1)) >= len(fenced):
                fenced = None
            continue
        if match:
            fenced = match.group(1)
            continue
        lines.append(line)
    return URL.sub(" ", LINK.sub(" ", INLINE_CODE.sub(" ", "\n".join(lines))))


def bare_references(text, flags):
    """{behavior: [references]} for each behavior that runs and finds one, in the reply's order."""
    found = {}
    body = prose(text)
    if flags.runs(f"{PART}.issues"):
        found["issues"] = ISSUE_REPO.findall(body) + [match.group(0) for match in ISSUE_WORD.finditer(body)]
    if flags.runs(f"{PART}.prs"):
        numbers = []
        for match in PR.finditer(body):
            before = body[max(0, match.start() - 20):match.start()]
            if COLOUR_BEFORE.search(before) or ISSUE_WORD.search(before + match.group(0)):
                continue
            numbers.append(match.group(0))
        found["prs"] = numbers + [match.group(0) for match in PR_WORD.finditer(body)]
    if flags.runs(f"{PART}.tickets"):
        prefixes = flags.value(f"{PART}.tickets.prefixes") or []
        skip = set(flags.value(f"{PART}.tickets.skip") or [])
        found["tickets"] = [match.group(0) for match in TICKET.finditer(body)
                            if match.group(1) not in skip and (not prefixes or match.group(1) in prefixes)]
    return {name: list(dict.fromkeys(items)) for name, items in found.items() if items}


def handle(event, hook_input, flags, answer):
    if event != "Stop" or hook_input.get("stop_hook_active"):
        return
    text = hook_input.get("last_assistant_message")
    if not isinstance(text, str):
        text = last_reply(hook_input.get("transcript_path") or "")
    found = bare_references(text, flags)
    if not found:
        return
    for name in found:
        answer.signal(f"{PART}.{name}", "sent_back")
    references = list(dict.fromkeys(item for items in found.values() for item in items))
    answer.refuse("Link these before you finish: " + ", ".join(references) +
                  ". Then send only the lines you fixed, not the whole reply again. (" + PART + ")")
