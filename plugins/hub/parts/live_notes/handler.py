"""The chat's live notes, in Claude Code.

- brief_names_file: an Agent call from the chat itself must name the chat's
  notes file in its brief, in full or from the home folder with ~. Otherwise
  it is refused, and the reason gives the path. Its child create_file makes
  the file with a header when it is missing, and size_warning says once per
  step when the file grows past 30 KB, then every 20 KB. Neither changes
  what the chat wrote.
- stay_line: a brief that orders background work and never says to stay
  until that work ends gets the stay line added at the end. Its child
  tell_sender tells the chat. A sentence that only says someone else runs a
  job in the background is no order, and neither is a wait for other work,
  such as a review.
- broadcast: after a SendMessage that carries a rule, the sender is reminded
  to add it to the notes and pass it to every running agent. A message to
  another chat's inbox (uds:) or to a remote session is skipped, because the
  coordinator passes rules between chats.

An Agent call from a subagent carries agent_id and passes, since its own
brief already named the notes. A subagent's messages get the reminder too. Any error lets the call run (the dispatcher logs it).
"""
import hashlib
import json
import os
import re
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files  # noqa: E402

PART = "live_notes"
DEFAULT_PATH = "{transcript_folder}/{session}/live_notes.md"
SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")
FIRST_WARNING = 30 * 1024
WARNING_STEP = 20 * 1024
WARNED = "live_notes"  # state folder of the size warnings, one small file per notes file

# background work

SENTENCES = re.compile(r"[.!?](?=\s|$)|\n")
CLAUSES = re.compile(r"[,;:()\[\]{}]|\s[-\u2013\u2014]+\s|[\u2013\u2014]")
BACKGROUND = re.compile(
    r"\brun_in_background\b|\bin the background\b|\bnohup\b|\bwaiters?\b"
    r"|\bbackground (?:jobs?|runs?|tasks?|tests?|watchers?|loops?|scripts?|commands?|waits?|builds?"
    r"|process(?:es)?)\b", re.I)
START = r"(?:start|run|launch|kick off|spawn|use|set up)"
LEAD = r"(?:(?:and|then|also|first|next|now|please|so|just|finally|or|but)\s+)*"
# An order to the agent: the verb opens the clause, or "you" is its subject.
ORDER = re.compile(
    rf"^[\W_]*{LEAD}{START}\b"
    rf"|\byou(?:['\u2019]ll|\s+(?:will|can|may|should|must|need to|have to))?\s+{LEAD}{START}\b", re.I)
DENIAL = re.compile(
    r"\b(?:no|not|cannot|never|nor|without|stop\w*|kill\w*|end(?:s|ed|ing)?|avoid\w*|leftovers?|alone"
    r"|\w+n['\u2019]t)\b", re.I)
# Stay or wait until "it", "they", "them", or something that names a job or process, ends.
STAY = re.compile(
    r"\b(?:stay|wait)(?:s|ing)?\b.{0,200}?\buntil\s+(?P<until>.{0,80}?)\s+(?:(?:is|are|has|have)\s+)?"
    r"(?:finish(?:es|ed)?|done|complete[sd]?|ends?)\b"
    r"|\bwait(?:s|ing)?\s+for\s+(?P<waited>.{0,80}?)\s+to\s+(?:finish|complete|end|be done)\b"
    r"|\buntil (?:it|they) (?:finish(?:es)?|ends?)\b|\bhand back once\b", re.I)
THE_WORK = re.compile(r"^(?:it|they|them)$|\bbackground\b|\bjobs?\b|\bprocess(?:es)?\b", re.I)

# rules in a message

RULE = re.compile(
    r"\b(?:never|must|rule|stop|do not|don't|from now on|instead|only use|not allowed|always"
    r"|correction|turned out|found that|is actually)\b", re.I)
REMOTE = re.compile(r"Claude session|Remote Control|cloud session")


def orders_background(prompt):
    """Whether one sentence has a background mark and an order to start, each in a clause with no denial."""
    for sentence in SENTENCES.split(prompt):
        clauses = [clause for clause in CLAUSES.split(sentence) if not DENIAL.search(clause)]
        if any(BACKGROUND.search(clause) for clause in clauses) and any(ORDER.search(clause) for clause in clauses):
            return True
    return False


def says_stay(prompt):
    """Whether a sentence says to stay or wait until the background work itself ends."""
    for sentence in SENTENCES.split(prompt):
        for match in STAY.finditer(sentence):
            waited = match.group("until") if match.group("until") is not None else match.group("waited")
            if waited is None or THE_WORK.search(waited.strip()):
                return True
    return False


# the notes file

def notes_path(hook_input, flags):
    """The chat's notes file, or None when its pattern needs a value the hook input lacks."""
    pattern = flags.value(f"{PART}.path") or DEFAULT_PATH
    session = hook_input.get("session_id")
    transcript = hook_input.get("transcript_path")
    if not isinstance(session, str) or not session or SAFE_ID.search(session):
        return None
    if "{transcript_folder}" in pattern:
        if not isinstance(transcript, str) or not os.path.isabs(transcript):
            return None
        pattern = pattern.replace("{transcript_folder}", os.path.dirname(transcript))
    path = pattern.replace("{session}", session)
    if path.startswith("~/"):
        path = os.path.join(files.home(), path[2:])
    return os.path.normpath(path) if os.path.isabs(path) else None


def names_file(prompt, path):
    home = files.home().rstrip(os.sep) + os.sep
    return path in prompt or (path.startswith(home) and "~/" + path[len(home):] in prompt)


def create(path, flags):
    if os.path.exists(path) or not flags.runs(f"{PART}.brief_names_file.create_file"):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(flags.text(f"{PART}.brief_names_file.create_file") + "\n\n")
    except FileExistsError:  # another hook made it first
        pass


def size_warning(path, flags):
    """The warning once per step past 30 KB, else ""."""
    flag_id = f"{PART}.brief_names_file.size_warning"
    if not flags.runs(flag_id):
        return ""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    if size < FIRST_WARNING:
        return ""
    step = FIRST_WARNING + WARNING_STEP * ((size - FIRST_WARNING) // WARNING_STEP)
    state = files.state_path(os.path.join(WARNED, hashlib.sha256(path.encode()).hexdigest()[:24] + ".json"))
    warned = (files.read_json(state, {}) or {}).get("warned", 0)
    if step <= warned:
        return ""
    files.write_json(state, {"path": path, "warned": step})
    return flags.text(flag_id).format(kb=step // 1024)


# the handlers

def before_agent(hook_input, flags, answer):
    tool_input = hook_input.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    prompt = tool_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    path = notes_path(hook_input, flags)
    if path is not None and flags.runs(f"{PART}.brief_names_file"):
        create(path, flags)
        if not names_file(prompt, path):
            answer.refuse(flags.text(f"{PART}.brief_names_file").format(path=path))
            return
        answer.add_context(size_warning(path, flags))
    if flags.runs(f"{PART}.stay_line") and orders_background(prompt) and not says_stay(prompt):
        line = flags.text(f"{PART}.stay_line")
        answer.rewrite(dict(tool_input, prompt=prompt.rstrip() + "\n\n" + line))
        if flags.runs(f"{PART}.stay_line.tell_sender"):
            answer.add_context(flags.text(f"{PART}.stay_line.tell_sender").format(line=line))


def after_message(hook_input, flags, answer):
    tool_input = hook_input.get("tool_input")
    if not flags.runs(f"{PART}.broadcast") or not isinstance(tool_input, dict):
        return
    message = tool_input.get("message")
    if not isinstance(message, str) or not RULE.search(message):
        return
    if str(tool_input.get("to") or "").startswith("uds:"):
        return
    if REMOTE.search(json.dumps(hook_input.get("tool_response"), ensure_ascii=False)):
        return
    answer.add_context(flags.text(f"{PART}.broadcast"))


def handle(event, hook_input, flags, answer):
    if event == "PreToolUse" and hook_input.get("tool_name") == "Agent" and not hook_input.get("agent_id"):
        before_agent(hook_input, flags, answer)
    elif event == "PostToolUse" and hook_input.get("tool_name") == "SendMessage":
        after_message(hook_input, flags, answer)


def doctor(flags):
    """What hubctl doctor says: where the notes are and whether briefs are checked."""
    pattern = flags.value(f"{PART}.path") or DEFAULT_PATH
    if flags.runs(f"{PART}.brief_names_file"):
        return [("note", f"every agent brief must name the chat's notes file, which is {pattern}.")]
    return [("note", f"agent briefs are not checked, because {PART}.brief_names_file is off. The notes file "
                     f"is {pattern}.")]
