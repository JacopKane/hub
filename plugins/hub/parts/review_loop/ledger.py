"""The review loop's pure core: lists, verdict lines, choices and the state of one area.

Nothing here reads a file, a transcript or the clock. The handler reads them
and passes plain values in, and each function returns new values.

The state of one area is a dict:
    session         the chat whose reviewer calls ran the loop
    streak          failed reviews in a row
    starts          reviewer starts since the last pass or lift
    allowed_starts  the starts allowed before the backstop stops the loop, None for rounds + 1
    rounds          [{round, failed}] for the failed reviews of the streak
    passed          item ids that passed in a counted review
    stop            None, or {reason: "streak" or "starts", positions: {session: {transcript, offset, role}}}
    agents          {agent id: {sent_back, stops, counted}}
"""
import re

from hub import said

ACCEPTANCE = re.compile(r"^[ \t>*_-]*Acceptance:[ \t]*(.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
NONE = re.compile(r"^none\b[\s,;:.-]*(.*)$", re.IGNORECASE)
AREA = re.compile(r"^[ \t]*area:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
IN_SCOPE = re.compile(r"^[ \t]*In scope:", re.MULTILINE | re.IGNORECASE)
OUT_OF_SCOPE = re.compile(r"^[ \t]*Out of scope:", re.MULTILINE | re.IGNORECASE)
ITEM = re.compile(r"^[ \t]*[-*][ \t]+([A-Z]+[0-9]+)\.[ \t]+(.*)$", re.MULTILINE)
CHECK = re.compile(r"\bCheck:[ \t]*\S", re.IGNORECASE)
VERDICT = re.compile(r"^[ \t]*Verdict:[ \t]*(.*?)[ \t]*$", re.MULTILINE | re.IGNORECASE)
ITEM_LINE = re.compile(r"^[ \t]*([A-Za-z]+[0-9]+):[ \t]*(PASS|FAIL)\b[ \t.:,-]*(.*)$", re.IGNORECASE)
OUTSIDE = re.compile(r"^[ \t]*Outside the list:[ \t]*$", re.IGNORECASE)
FINDING = re.compile(r"^[ \t]*[-*][ \t]+(?:(Low|Medium|High|Critical)\b[ \t.:,-]*)?(.*)$", re.IGNORECASE)
PLACE = re.compile(r"\S+:[0-9]+[ \t]+[A-Za-z_][\w.]*")
NOT_CHECKED = re.compile(r"\bnot checked:", re.IGNORECASE)
TEST = re.compile(r"\btest:[ \t]*\S", re.IGNORECASE)
SERIOUS = {"high", "critical"}
CHOICES = {
    "ship": (("ship",),),
    "redesign": (("redesign",), ("new", "list")),
    "one_more_round": (("one", "more", "round"), ("another", "round")),
    "park": (("park",),),
}
MISSING_AREA = "`area:` line"
MISSING_AREA_WORD = "`area:` line with a letter from a to z or a digit"
MISSING_THREATS = "threat model with In scope and Out of scope"
MISSING_ITEMS = "items"


# the acceptance line and the list

def acceptance_line(prompt):
    """What a brief's first Acceptance line names: ("path", path), ("none", reason) or None."""
    found = ACCEPTANCE.search(prompt or "")
    if found is None:
        return None
    value = found.group(1).strip().strip("`\"'")
    none = NONE.match(value)
    return ("none", none.group(1).strip()) if none else ("path", value)


def parse_acceptance(text):
    """{area, items, missing} of a list's text. missing names the first part the list lacks, or None."""
    area = AREA.search(text)
    items = [(item, rest) for item, rest in ITEM.findall(text)]
    unchecked = [item for item, rest in items if not CHECK.search(rest)]
    missing = (MISSING_AREA if area is None
               else MISSING_AREA_WORD if not has_word(area.group(1))
               else MISSING_THREATS if not (IN_SCOPE.search(text) and OUT_OF_SCOPE.search(text))
               else MISSING_ITEMS if not items
               else f"Check for {unchecked[0]}" if unchecked
               else None)
    return {"area": area.group(1) if area else None, "items": [item for item, _ in items], "missing": missing}


def shape_lines(items):
    """The verdict lines a brief must ask for, to be filled in by the reviewer."""
    return "\n".join(["Verdict: GO or NO-GO",
                      *[f"{item}: PASS, or FAIL with path:line and function, or FAIL not checked: <reason>"
                        for item in items],
                      "Outside the list:",
                      "- <Low, Medium, High or Critical>. <one problem that is not on the list>"])


def brief_has_shape(prompt, items):
    """Whether a brief asks for a Verdict line and one line per item."""
    starts = {line.strip().split(":", 1)[0].strip().upper() for line in (prompt or "").splitlines() if ":" in line}
    return "VERDICT" in starts and all(item.upper() in starts for item in items)


# the verdict lines

def judge_report(report, items, passed_before):
    """What a report's verdict lines say about the list.

    Returns {verdict, failed, passed, problems, outside}. problems is a list of
    (kind, item) pairs, empty when the lines are valid; kind is one of
    missing, unknown_item, missing_item, no_fail, go_with_fail,
    fail_without_place and reopened_without_test.
    """
    lines = (report or "").splitlines()
    stated_at = [index for index, line in enumerate(lines) if VERDICT.match(line)]
    if not stated_at:
        return {"verdict": None, "failed": [], "passed": [], "problems": [("missing", None)], "outside": []}
    after = lines[stated_at[-1]:]
    stated = re.sub(r"[\s_-]", "", VERDICT.match(after[0]).group(1).upper())
    marks = [(found.group(1).upper(), found.group(2).upper(), found.group(3))
             for found in map(ITEM_LINE.match, after[1:]) if found]
    failed = [item for item, mark, _ in marks if mark == "FAIL" and item in items]
    passed = [item for item, mark, _ in marks if mark == "PASS" and item in items]
    named = {item for item, _, _ in marks}
    problems = ([("missing", None)] if stated not in ("GO", "NOGO") else []) + \
        [("unknown_item", item) for item, _, _ in marks if item not in items] + \
        [("missing_item", item) for item in items if item not in named] + \
        [("fail_without_place", item) for item, mark, rest in marks
         if mark == "FAIL" and item in items and not (PLACE.search(rest) or NOT_CHECKED.search(rest))] + \
        [("reopened_without_test", item) for item, mark, rest in marks
         if mark == "FAIL" and item in passed_before and not TEST.search(rest)] + \
        ([("no_fail", None)] if stated == "NOGO" and not failed else []) + \
        ([("go_with_fail", failed[0])] if stated == "GO" and failed else [])
    return {"verdict": "NO-GO" if failed else "GO", "failed": failed, "passed": passed, "problems": problems,
            "outside": outside_findings(after)}


def outside_findings(lines):
    """[(severity or None, text)] for the lines under "Outside the list:"."""
    starts = [index for index, line in enumerate(lines) if OUTSIDE.match(line)]
    if not starts:
        return []
    found = [FINDING.match(line) for line in lines[starts[-1] + 1:]]
    return [((match.group(1) or "").capitalize() or None, match.group(2).strip())
            for match in found if match and match.group(2).strip()]


def serious(finding):
    severity, _ = finding
    return (severity or "").lower() in SERIOUS


# the user's choice

def decision_in(words, open_areas, need_area, negated):
    """(area, choice) when the words choose one thing for one area, else None.

    words are the plain words of one typed line. Area words are taken out
    before the choice is looked for, so an area such as "park-map" chooses
    nothing. negated says the line is a question, a negation or a condition,
    which decides nothing.
    """
    if negated:
        return None
    named = [area for area in open_areas if spans_of(words, area_words(area))]
    area = named[0] if len(named) == 1 else (open_areas[0] if len(open_areas) == 1 and not named else None)
    if area is None or (need_area and not named):
        return None
    rest = without(words, [area_words(name) for name in open_areas])
    choices = [choice for choice, phrases in CHOICES.items() if any(spans_of(rest, phrase) for phrase in phrases)]
    return (area, choices[0]) if len(choices) == 1 else None


def holds(words, area, negated):
    """Whether a negated line still names the area, as "don't park export-button" does, so it keeps the stop."""
    return negated and bool(spans_of(words, area_words(area)))


def words_of(text):
    """The plain words of an area or a typed line, read the same way on both sides.

    Apostrophe marks are folded as said reads them, and the marks at a word's
    edges are taken off, so `export-button` and 'export-button' name export-button.
    """
    return tuple(word for word in (word.strip("'") for word in said.plain(text)) if word)


def area_words(area):
    return words_of(area)


def has_word(area):
    """Whether an area has a word with a letter from a to z or a digit, so a typed answer can name it."""
    return any(re.search(r"[a-z0-9]", word) for word in area_words(area))


def spans_of(words, phrase):
    size = len(phrase)
    return [start for start in range(len(words) - size + 1) if size and tuple(words[start:start + size]) == phrase]


def without(words, phrases):
    """The words with every span of the phrases taken out."""
    taken = {start + offset for phrase in phrases for start in spans_of(words, phrase) for offset in range(len(phrase))}
    return [word for index, word in enumerate(words) if index not in taken]


# the state of one area

def fresh(area):
    return {"area": area, "session": None, "streak": 0, "starts": 0, "allowed_starts": None, "rounds": [],
            "passed": [], "stop": None, "agents": {}}


def start_limit(state, rounds):
    return state["allowed_starts"] if state["allowed_starts"] is not None else rounds + 1


def backstop_due(state, rounds):
    return state["stop"] is None and state["starts"] >= start_limit(state, rounds)


def with_stop(state, reason, positions):
    return dict(state, stop={"reason": reason, "positions": positions})


def started(state, session):
    return dict(state, starts=state["starts"] + 1, session=session)


def with_agent(state, agent, **values):
    agents = dict(state["agents"])
    agents[agent] = dict(agents.get(agent, {"sent_back": 0, "stops": 0, "counted": False}), **values)
    return dict(state, agents=agents)


def agent_of(state, agent):
    return state["agents"].get(agent, {"sent_back": 0, "stops": 0, "counted": False})


def counted(state, agent, failed, passed, rounds, unreadable=False):
    """The state after one counted review, and whether the stop opens now.

    failed empty is a GO, unless the review is unreadable, which counts as a
    failed review with no item named.
    """
    state = with_agent(state, agent, counted=True)
    state = dict(state, passed=sorted(set(state["passed"]) | set(passed)))
    if not failed and not unreadable:
        return dict(state, streak=0, starts=0, allowed_starts=None, rounds=[]), False
    streak = state["streak"] + 1
    number = (state["rounds"][-1]["round"] if state["rounds"] else 0) + 1
    state = dict(state, streak=streak, rounds=state["rounds"] + [{"round": number, "failed": list(failed)}])
    return state, state["stop"] is None and streak >= rounds


def lifted(state, choice):
    """The state after the user's choice. One more round keeps the counts and allows one more start."""
    if choice == "one_more_round":
        return dict(state, stop=None, allowed_starts=state["starts"] + 1)
    return dict(state, stop=None, streak=0, starts=0, allowed_starts=None, rounds=[], passed=[])
