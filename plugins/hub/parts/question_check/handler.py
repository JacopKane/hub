"""question_check: a question dialog opens only when it carries what the user needs to answer it.

Each behavior is one check on every question of an AskUserQuestion call:
context (the question and each option are long enough), paths (a code line
with its file path), example (an example, or an option with a preview), pick
(a soft note when the recommended option is not first) and plain_names (no item
named only by a code). A part that is a little short, within the near miss,
passes with one soft note. Input it can not read passes. Claude Code only.

away_guard calls refusal() too, so it never holds or logs a dialog that this
part will refuse.
"""
import re

PART = "question_check"
TOOL = "AskUserQuestion"
CODE = re.compile(r"`[^`\n]+`")
PATH = re.compile(r"/|\.(py|ts|tsx|js|jsx|svelte|md|json|sh|ya?ml|toml|html|css|sql|txt|go|rs)\b")
# A question is about code when it holds a code span or names code in words.
ABOUT_CODE = re.compile(r"\b(code|function|method|class|module|variable|endpoint|handler|diff|refactor|script)\b", re.I)
EXAMPLE = re.compile(r"\b(e\.g\.|for example|for instance|example|such as)", re.I)
RECOMMENDED = re.compile(r"\(recommended\)", re.I)
# Text that may hold digits and dots, removed before the code check.
SAFE = re.compile(
    r"`[^`\n]*`|https?://\S+|~?[\w.-]*/[\w./-]*|\b[\w-]+\.(?:py|ts|tsx|js|jsx|svelte|md|json|sh|ya?ml|toml|html|css|sql|txt|go|rs)\b"
    r"|\b\d+(?:\.\d+){2,}\b|\b[0-9a-f]{7,40}\b|[$€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:eur|usd|gbp|euro|euros|dollars?)\b"
    r"|\b\d{1,2}:\d{2}\b|\b\d+(?:\.\d+)?\s?(?:days?|weeks?|months?|years?|hours?|hrs?|minutes?|mins?|seconds?|secs?|ms|s|[kmgt]b|%|x)\b",
    re.I,
)
# A code in brackets right after a name, as in "revoke on disconnect (R7)".
NAMED = re.compile(r"(?<=\w) \((?:[QRTF]\d{1,3}[a-z]?|\d+(?:\.\d+)*[a-z]?)\)")
NUMBER = r"\d+(?:\.\d+)*[a-z]?"
# Only a dotted or lettered number is a code ("4.1", "8a"). A plain "step 3" is ordinary writing.
CODED_NUMBER = r"(?:\d+(?:\.\d+)+[a-z]?|\d+[a-z])"
KIND = r"(?:rows?|items?|steps?|tasks?|questions?|phases?|sections?|cases?|findings?)"
BARE_CODE = re.compile(
    rf"\b{KIND}\s+{CODED_NUMBER}\b(?:\s*(?:,|and|&|or)\s*{NUMBER}\b)*|\b[QRTF]\d{{1,3}}[a-z]?\b",
    re.I,
)
OPT_OUT = re.compile(r"\s*(skip|keep|keep as is|no change|drop|drop it|not now)\s*(\(recommended\))?\s*", re.I)


def number(flags, flag_id, default):
    try:
        return float(flags.value(flag_id))
    except (TypeError, ValueError):
        return default


def options_of(question):
    return [option for option in question.get("options") or [] if isinstance(option, dict)]


def all_text(question):
    texts = [question.get("question") or "", question.get("header") or ""]
    for option in options_of(question):
        texts += [option.get("label") or "", option.get("description") or "", option.get("preview") or ""]
    return [str(text) for text in texts]


def short_parts(questions, flags):
    """(short, near): the parts under the minimum, and those within the near miss."""
    question_min = number(flags, f"{PART}.context.question_min", 250)
    option_min = number(flags, f"{PART}.context.option_min", 60)
    margin = number(flags, f"{PART}.context.near_miss", 10) / 100
    short, near = [], []

    def place(length, minimum, name):
        if length >= minimum:
            return
        if length >= minimum * (1 - margin):
            near.append(f"{name} ({length} of {minimum:g} characters)")
        else:
            short.append(name)

    for index, question in enumerate(questions, 1):
        place(len(str(question.get("question") or "")), question_min, f"question {index}")
        for option in options_of(question):
            label = str(option.get("label") or "")
            if OPT_OUT.fullmatch(label):
                continue
            description = str(option.get("description") or "").strip()
            if (option.get("preview") or "").strip():
                continue
            if not description:
                short.append(f'option "{label}" in question {index}')
            else:
                place(len(description), option_min, f'option "{label}" in question {index}')
    return short, near


def missing(questions, check):
    return [f"question {index}" for index, question in enumerate(questions, 1) if not check(question)]


def about_code(question):
    return any(CODE.search(text) or ABOUT_CODE.search(text) for text in all_text(question))


def has_path(question):
    """A question that is not about code needs no path."""
    return not about_code(question) or any(PATH.search(text) for text in all_text(question))


def has_example(question):
    return any(EXAMPLE.search(text) for text in all_text(question)) or any(
        (option.get("preview") or "").strip() for option in options_of(question))


def pick_first(question):
    """True when the pick leads the real options. Skip-like options are ignored, so their place never counts."""
    options = [option for option in options_of(question) if not OPT_OUT.fullmatch(str(option.get("label") or ""))]
    if question.get("multiSelect") or len(options) < 2:
        return True
    return bool(RECOMMENDED.search(str(options[0].get("label") or "")))


def codes(questions):
    found = []
    for question in questions:
        for text in all_text(question):
            for match in BARE_CODE.finditer(NAMED.sub("", SAFE.sub(" ", text))):
                if match.group(0) not in found:
                    found.append(match.group(0))
    return found


def check(questions, flags):
    """(reasons, near): each reason ends with the flag that found it."""
    reasons, near = [], []
    if flags.runs(f"{PART}.context"):
        short, near = short_parts(questions, flags)
        if short:
            reasons.append(f"Too short: {', '.join(short)}. Add the context in plain product terms, and for each "
                           f"option what it leads to and what it costs. ({PART}.context)")
    if flags.runs(f"{PART}.paths"):
        found = missing(questions, has_path)
        if found:
            reasons.append(f"No code line with its path: {', '.join(found)}. Show the lines the choice touches, "
                           f"with their file path. ({PART}.paths)")
    if flags.runs(f"{PART}.example"):
        found = missing(questions, has_example)
        if found:
            reasons.append(f"No example: {', '.join(found)}. Say what the user would see or get, for example in "
                           f"an option's preview. ({PART}.example)")
    if flags.runs(f"{PART}.plain_names"):
        found = codes(questions)
        if found:
            joined = '", "'.join(found)
            reasons.append(f'Only a code: "{joined}". Name each item by what it is, with the code in brackets '
                           f"after it if a shared doc uses one. ({PART}.plain_names)")
    return reasons, near


def pick_notes(questions, flags):
    """The questions whose pick is not first. This never refuses, it only adds a soft note."""
    return missing(questions, pick_first) if flags.runs(f"{PART}.pick") else []


def refusal_text(reasons):
    return "This dialog does not carry what the user needs to answer it. " + " ".join(reasons) + " Then ask again."


def questions_of(hook_input):
    questions = (hook_input.get("tool_input") or {}).get("questions") if isinstance(
        hook_input.get("tool_input"), dict) else None
    if not isinstance(questions, list):
        return None
    return [question for question in questions if isinstance(question, dict)]


def refusal(hook_input, flags):
    """The refusal text for this dialog, or None when it opens."""
    questions = questions_of(hook_input)
    if not questions or not flags.runs(PART):
        return None
    reasons, _near = check(questions, flags)
    return refusal_text(reasons) if reasons else None


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or hook_input.get("tool_name") != TOOL:
        return
    questions = questions_of(hook_input)
    if not questions:
        return
    reasons, near = check(questions, flags)
    if reasons:
        for reason in reasons:
            answer.signal(reason.rsplit("(", 1)[-1].rstrip(")"), "refused")
        answer.refuse(refusal_text(reasons))
    else:
        noted = pick_notes(questions, flags)
        if noted:
            answer.signal(f"{PART}.pick", "noted")
            answer.warn(f"{PART}.pick", f"This dialog passed, but the recommended option is not first in: "
                        f"{', '.join(noted)}.", "Next time put the option you recommend first, and end its label "
                        "with (Recommended).")
    if not reasons and near:
        answer.signal(f"{PART}.context", "near_miss")
        answer.warn(f"{PART}.context", f"This dialog passed, but these parts are a little short: {', '.join(near)}.",
                    "Next time add more context, a code line with its path, an example or what the option leads to.")
