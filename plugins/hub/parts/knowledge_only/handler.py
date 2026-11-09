"""knowledge_only: a prompt that only shares material gets a reminder to explain it, not act on it.

A prompt counts as shared material when it starts with a marker word, when
most of it is pasted, or when it is links and images with only a few words of
the user's own. A prompt whose own words ask for a change gets nothing, and so
do prompts from other sessions, the harness and slash commands. It only adds
context and never blocks. Claude Code only.
"""
import re

PART = "knowledge_only"
PASTE = re.compile(r"<pasted_content\b[^>]*>.*?(?:</pasted_content\b[^>]*>|\Z)", re.S)
IMAGE = re.compile(r"\[Image #\d+\]")
URL = re.compile(r"https?://\S+")
# Words that ask for a change. "check" and "look" stay out, because they ask to read.
ASK = re.compile(
    r"\b(fix|add|apply|change|update|make|write|implement|remove|delete|rename|move|create|build|save|"
    r"handle|refactor|revert|replace|put|run|commit|push|draft|send|merge|rebase|test|record)\b",
    re.I,
)
NOT_TYPED = ("/", "<task-notification>", "<system-reminder>", "Another Claude session")
PASTED_SHARE = 0.7
OWN_WORDS = 8


def starts_with_marker(prompt, markers):
    words = [re.escape(str(marker).strip()) for marker in markers or [] if str(marker).strip()]
    return bool(words) and bool(re.match(rf"\s*(?:{'|'.join(words)})\b", prompt, re.I))


def shared_only(prompt, markers):
    if prompt.lstrip().startswith(NOT_TYPED):
        return False
    if starts_with_marker(prompt, markers):
        return True
    own = URL.sub(" ", IMAGE.sub(" ", PASTE.sub(" ", prompt)))
    if ASK.search(own):
        return False
    pasted = sum(len(match.group(0)) for match in PASTE.finditer(prompt))
    if pasted > PASTED_SHARE * len(prompt.strip()):
        return True
    return bool(IMAGE.search(prompt) or URL.search(prompt)) and len(re.findall(r"\w+", own)) < OWN_WORDS


def handle(event, hook_input, flags, answer):
    if event != "UserPromptSubmit" or not flags.runs(f"{PART}.detect_shared_material"):
        return
    prompt = hook_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return
    if not shared_only(prompt, flags.value(f"{PART}.detect_shared_material.markers")):
        return
    reminder = flags.text(f"{PART}.reminder")
    if reminder:
        answer.signal(f"{PART}.detect_shared_material", "reminded")
        answer.add_context(f"{reminder} ({PART})")
