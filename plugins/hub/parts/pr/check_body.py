"""The step's `pr-check` verb: check a PR body before the user sees it.

    pr-check <file> [--json]    a body file, or - to read it from stdin

It names each finding by its kind and line, never by the text it found, and
never changes the body. The kinds:

- missing_heading: a heading of the body shape is not there. The shape comes
  from the repo's PR template when it has one, else from text/body.md. While
  pr.instructions.test_gate runs, tested_part() looks for the testing part
  instead. It takes the "How it was tested" heading of text/body.md, a near
  miss of it (see NEAR_MISSES) or the template's own testing heading (see
  TESTING_NAMES). When the body has none, the missing heading is the
  template's testing heading, else hub's.
- missing_not_tested: the testing part has no line on what was not tested.
  Its line is the heading's.
- mention: an @ before a name, outside code and links.
- bare_reference: a PR, issue or ticket without a link, found the way
  link_check finds one in a reply.
- em_dash: an em dash on any line, code too.

A body with more than 240 words outside code blocks, details blocks and
HTML comments, which is more than a minute's read, gets a notice. A notice
is not a finding. The exit code is 0 without findings, 1 with one and 2 on
wrong words.
"""
import importlib.util
import json
import os
import re
import sys

PART_DIR = os.path.dirname(os.path.abspath(__file__))
PARTS_DIR = os.path.dirname(PART_DIR)
LIB = os.path.join(os.path.dirname(PARTS_DIR), "lib")
for folder in (LIB, PART_DIR):
    if folder not in sys.path:
        sys.path.insert(0, folder)

from hub.settings import find_repo  # noqa: E402
import read_flags  # noqa: E402
from texts import text  # noqa: E402

USAGE = "usage: pr-check <file> [--json], where - reads the body from stdin"
WORDS_A_MINUTE = 240
DASH = chr(0x2014)
HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t#]*$")
# A template heading is its testing part when, without case and punctuation, it is one of these.
# A heading that only mentions tests, such as "Notes on the test data", is not.
TESTING_NAMES = {"testing", "tests", "test plan", "how to test", "how it was tested", "how has this been tested",
                 "qa", "verification", "how to verify", "how was this tested", "how was it tested", "tested",
                 "testing done", "testing performed", "testing instructions", "testing steps", "testing notes",
                 "test steps", "test instructions", "steps to test", "manual testing", "qa steps", "qa notes",
                 "qa instructions", "verification steps"}
# Body headings that pass as hub's "How it was tested", with a reminder to use hub's wording.
NEAR_MISSES = {"how it s tested", "how its tested", "how this was tested", "how was it tested", "how we tested",
               "tested", "testing", "tests", "test plan"}
# A line on what was not tested, searched in the testing part up to the next heading of its level or higher.
NOT_TESTED = re.compile(r"(?i)\b(not|un)[ -]?tested\b|\bleft out\b|\bnot (covered|run|checked)\b"
                        r"|\bno (smoke run|bdd|scenarios?)\b|\bdid(?: not|n['\u2019]?t) test\b")
# Unlike link_check's LINK, this one keeps the link text, so a mention in it still counts.
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
MENTION = re.compile(r"(?<![\w@.+/-])@[A-Za-z0-9][\w-]*")
WORD = re.compile(r"\S+")
TEMPLATE_NAME = "pull_request_template.md"
TEMPLATE_FOLDER = "pull_request_template"
TEMPLATE_PLACES = ("", ".github", "docs")


def link_check():
    """link_check's handler, read from its own folder, so both find the same bare references."""
    spec = importlib.util.spec_from_file_location("hub_link_check", os.path.join(PARTS_DIR, "link_check", "handler.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The check uses link_check's FENCE, INLINE_CODE and URL patterns and its bare_references,
# so both read a body the same way.
REFERENCES = link_check()


class TicketValues:
    """The flags link_check's bare_references reads: every kind on, and its ticket lists."""

    def __init__(self, flags):
        self.values = {}
        for name in ("prefixes", "skip"):
            record = flags.get(f"link_check.tickets.{name}") or {}
            self.values[f"link_check.tickets.{name}"] = record.get("value")
        if self.values.get("link_check.tickets.skip") is None:
            with open(os.path.join(PARTS_DIR, "link_check", "part.json"), encoding="utf-8") as handle:
                manifest = json.load(handle)
            tickets = next(child for child in manifest["children"] if child["id"] == "tickets")
            for child in tickets["children"]:
                self.values[f"link_check.tickets.{child['id']}"] = child["default"]

    def runs(self, _flag_id):
        return True

    def value(self, flag_id):
        return self.values.get(flag_id)


def plain_lines(body):
    """The body's lines with fenced code and HTML comments blanked, so line numbers stay."""
    lines, fenced, in_comment = [], None, False
    for line in body.split("\n"):
        match = REFERENCES.FENCE.match(line)
        if fenced:
            if match and match.group(1)[0] == fenced[0] and len(match.group(1)) >= len(fenced):
                fenced = None
            lines.append("")
            continue
        if not in_comment and match:
            fenced = match.group(1)
            lines.append("")
            continue
        kept = ""
        rest = line
        while rest:
            if in_comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                else:
                    in_comment, rest = False, rest[end + 3:]
            else:
                start = rest.find("<!--")
                if start < 0:
                    kept, rest = kept + rest, ""
                else:
                    kept, rest, in_comment = kept + rest[:start], rest[start + 4:], True
        lines.append(kept)
    return lines


def readable_lines(lines):
    """plain_lines without the lines inside <details> blocks, for the word count."""
    kept, depth = [], 0
    for line in lines:
        opened, closed = len(re.findall(r"<details\b", line, re.I)), len(re.findall(r"</details\s*>", line, re.I))
        if depth == 0 and not opened:
            kept.append(line)
        depth = max(0, depth + opened - closed)
    return kept


def headings_of(body):
    return [match.group(1).strip() for match in map(HEADING.match, plain_lines(body)) if match]


def headings_with_lines(body):
    """[(line number, level, heading)] of the body, outside code and comments."""
    found = []
    for number, line in enumerate(plain_lines(body), 1):
        match = HEADING.match(line)
        if match:
            found.append((number, len(line.strip()) - len(line.strip().lstrip("#")), match.group(1).strip()))
    return found


def same(heading):
    return " ".join(heading.lower().rstrip(":").split())


def words_of(heading):
    return " ".join(re.findall(r"[a-z0-9]+", heading.lower()))


def about_testing(heading):
    return words_of(heading) in TESTING_NAMES


def hub_shape():
    with open(os.path.join(PART_DIR, "text", "body.md"), encoding="utf-8") as handle:
        return headings_of(handle.read())


def templates(root):
    """[(path from the repo root, text)] of the repo's PR templates, in GitHub's places."""
    found = []
    for place in TEMPLATE_PLACES:
        folder = os.path.join(root, place)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if name.lower() == TEMPLATE_NAME and os.path.isfile(path):
                found.append(path)
            elif name.lower() == TEMPLATE_FOLDER and os.path.isdir(path):
                found += [os.path.join(path, inner) for inner in sorted(os.listdir(path))
                          if inner.lower().endswith(".md") and os.path.isfile(os.path.join(path, inner))]
    shapes = []
    for path in found:
        with open(path, encoding="utf-8", errors="replace") as handle:
            shapes.append((os.path.relpath(path, root), handle.read()))
    return shapes


def closest_shape(body, cwd, testing_needed):
    """(the headings the body lacks, where the shape came from, that shape's testing headings).

    While testing_needed, testing headings are left to tested_part(). Otherwise a template
    keeps its own, and hub's shape drops its "How it was tested".
    """
    have = {same(heading) for heading in headings_of(body)}
    root = find_repo(cwd)
    shapes = [(where, headings_of(shape)) for where, shape in templates(root)] if root else []
    if not shapes:
        shapes = [("hub", hub_shape())]
    best, best_rank = None, None
    for where, headings in shapes:
        testing = [heading for heading in headings if about_testing(heading)]
        left_out = testing if testing_needed or where == "hub" else []
        absent = [heading for heading in headings if same(heading) not in have]
        missing = [heading for heading in absent if heading not in left_out]
        # Count the testing headings too, so templates that differ only there do not tie.
        rank = (len(missing), len(absent))
        if best is None or rank < best_rank:
            best, best_rank = (missing, where, [] if where == "hub" else testing), rank
    return best


def missing_headings(body, cwd, testing_needed):
    """(the headings the body lacks, where the shape came from), without the testing part while testing_needed."""
    missing, where, _testing = closest_shape(body, cwd, testing_needed)
    return missing, where


def tested_part(body, cwd, expected):
    """{"findings": [...], "heading": the testing heading found or None, "near_miss": bool}.

    expected says whether the body needs a testing part at all. The part counts under hub's
    "How it was tested", a near miss of it, or the repo template's own testing heading, and
    runs to the next heading of its level or higher. It needs a line on what was not tested.
    """
    if not expected:
        return {"findings": [], "heading": None, "near_miss": False}
    hub_tested = next(heading for heading in hub_shape() if about_testing(heading))
    template_testing = closest_shape(body, cwd, True)[2]
    exact = {words_of(heading) for heading in [hub_tested] + template_testing}
    found = headings_with_lines(body)
    match = next(((index, False) for index, (_, _, heading) in enumerate(found) if words_of(heading) in exact),
                 None) or next(((index, True) for index, (_, _, heading) in enumerate(found)
                                if words_of(heading) in NEAR_MISSES), None)
    if match is None:
        missing = template_testing[0] if template_testing else hub_tested
        return {"findings": [{"kind": "missing_heading", "line": None, "heading": missing}],
                "heading": None, "near_miss": False}
    index, near_miss = match
    line, level, heading = found[index]
    end = next((number for number, next_level, _ in found[index + 1:] if next_level <= level), None)
    part = plain_lines(body)[line:None if end is None else end - 1]
    says_what_was_left = any(NOT_TESTED.search(part_line) for part_line in part)
    findings = [] if says_what_was_left else [{"kind": "missing_not_tested", "line": line}]
    return {"findings": findings, "heading": heading, "near_miss": near_miss}


def check(body, cwd):
    """{"findings": [{"kind", "line"}], "notice": a line or None, "headings_from": "hub" or a path,
    "tested": {"heading": the testing heading found or None, "near_miss": bool}}."""
    try:
        flags = read_flags.flags(cwd)
    except Exception:  # the check still runs with the shipped lists and every method on
        flags = {}
    testing_needed = (flags.get("pr.instructions.test_gate") or {"runs": True}).get("runs")
    findings = []
    missing, headings_from = missing_headings(body, cwd, testing_needed)
    findings += [{"kind": "missing_heading", "line": None, "heading": heading} for heading in missing]
    tested = tested_part(body, cwd, testing_needed)
    findings += tested["findings"]
    tickets = TicketValues(flags)
    lines = plain_lines(body)
    for number, line in enumerate(body.split("\n"), 1):
        plain = lines[number - 1]
        words = REFERENCES.URL.sub(" ", LINK.sub(lambda match: " " + match.group(1) + " ",
                                                 REFERENCES.INLINE_CODE.sub(" ", plain)))
        if MENTION.search(words):
            findings.append({"kind": "mention", "line": number})
        if REFERENCES.bare_references(plain, tickets):
            findings.append({"kind": "bare_reference", "line": number})
        if DASH in line:
            findings.append({"kind": "em_dash", "line": number})
    count = sum(len(WORD.findall(line)) for line in readable_lines(lines))
    notice = text("check_length.md", words=count) if count > WORDS_A_MINUTE else None
    return {"findings": findings, "notice": notice, "headings_from": headings_from,
            "tested": {"heading": tested["heading"], "near_miss": tested["near_miss"]}}


def sentences(result):
    found = []
    for finding in result["findings"]:
        if finding["kind"] == "missing_heading":
            found.append(text("check_missing_heading.md", heading=finding["heading"]))
        else:
            found.append(text(f"check_{finding['kind']}.md", line=finding["line"]))
    if not found:
        found.append(text("check_clean.md"))
    if result["notice"]:
        found.append(result["notice"])
    return found


def main(words):
    as_json = words[1:] == ["--json"]
    if len(words) != (2 if as_json else 1):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if words[0] == "-":
            body = sys.stdin.read()
        else:
            with open(words[0], encoding="utf-8") as handle:
                body = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        print(f"pr-check: {words[0]} can not be read ({error.__class__.__name__})", file=sys.stderr)
        return 2
    result = check(body, os.getcwd())
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print("\n".join(sentences(result)))
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
