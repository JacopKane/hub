"""The start text, packed into SessionStart slots per tool.

Claude Code takes one string of at most 10,000 characters from each
SessionStart hook, and every string arrives. So hub ships eight slot hooks,
and apply writes what each one prints. Cursor gets its start text as one
string, so its set is one slot. A size is counted in UTF-16 code units, the
way the limit is counted, so a character outside the basic plane counts two.

    ~/.local/state/hubctl/slots/<tool>/1.md ... 8.md
    ~/.local/state/hubctl/slots/<tool>/left_out.json   ids that did not fit

The start text is every node with a text file and the cost "start text" that
runs, in tree order. A start-text node whose children are start text too is
one unit: its own text, then the text of each child that runs, one per line.
Such a unit is dropped when no child runs, as the tier table is when no agent
runs. {{flag.id}} in a text takes that flag's value in the tool. {{needs}}
takes the agents the node needs that run, joined with commas, and
{{needs.model}} or {{needs.effort}} their values: one value when all agree,
else one per agent in the same order, and "not set" for an agent without one.

Units fill the slots in order, whole, each slot under 10,000 characters. Once
the eight slots are full, every later unit stays out, so what is left out is
always the end of the tree order. A unit too long for any slot stays out on
its own.
"""
import json
import os
import re
import shutil

from . import files

COUNT = 8  # Claude Code's slots
COUNTS = {"claude_code": COUNT, "cursor": 1}
LIMIT = 10_000  # UTF-16 code units per slot, from Claude Code's hooks docs
START_TEXT = "start text"
JOIN = "\n\n"
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_.]+)\}\}")
NEEDS = "needs"  # {{needs}}, {{needs.model}}, {{needs.effort}}: see _needs_value
NOT_SET = "not set"


def count(tool):
    return COUNTS.get(tool, COUNT)


def size(text):
    """Length in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def folder(tool):
    return files.state_path(os.path.join("slots", tool))


def slot_path(tool, number):
    return os.path.join(folder(tool), f"{number}.md")


def is_start_text(node):
    return bool(node.text) and node.data.get("cost") == START_TEXT


def _shown(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return "" if value is None else str(value)


def _needs_value(node, flags, option):
    """{{needs}} or {{needs.<option>}}: the agents a row needs that run, or their option values."""
    running = [need for need in node.needs + node.needs_any if flags.get(need, {}).get("runs")]
    if option is None:
        return ", ".join(running)
    values = []
    for need in running:
        record = flags.get(f"{need}.{option}")
        value = record.get("value") if record else None
        values.append(NOT_SET if value in (None, "", "off") else str(value))
    if values and all(value == values[0] for value in values):
        return values[0]
    return ", ".join(values)


def render(node, flags):
    path = os.path.join(files.PARTS_DIR, node.part.id, node.text)
    with open(path, encoding="utf-8") as handle:
        text = handle.read().strip()

    def value(match):
        name = match.group(1)
        if name == "step":
            return files.step_command()
        if name == NEEDS or name.startswith(NEEDS + "."):
            return _needs_value(node, flags, name.partition(".")[2] or None)
        return _shown(flags.get(name, {}).get("value"))

    return PLACEHOLDER.sub(value, text)


def units(tree, flags):
    """[(node id, part id, text)] of the start text that runs, in tree order."""
    found = []

    def visit(node):
        if not is_start_text(node):
            for child in node.children:
                visit(child)
            return
        if not flags[node.id]["runs"]:
            return
        children = [child for child in node.children if is_start_text(child)]
        text = render(node, flags)
        if children:
            lines = [render(child, flags) for child in children if flags[child.id]["runs"]]
            if not lines:
                return
            text = "\n".join([text, *lines])
        found.append((node.id, node.part.id, text))

    for part in tree.parts:
        visit(part)
    return found


def pack(found, number=COUNT):
    """([slot texts], [placed units], [left out ids]) for a list of units."""
    slots, placed, left_out = [[] for _ in range(number)], [], []
    current, full = 0, False
    for unit in found:
        text = unit[2]
        if full or size(text) >= LIMIT:
            left_out.append(unit[0])
            continue
        while current < number and size(JOIN.join(slots[current] + [text])) >= LIMIT:
            current += 1
        if current == number:
            full = True
            left_out.append(unit[0])
            continue
        slots[current].append(text)
        placed.append(unit)
    return [JOIN.join(slot) for slot in slots], placed, left_out


def write(tool, tree, flags, first=()):
    """Write one tool's slot files. Returns (slot texts, placed units, left out ids).

    first holds units that go before the tree's start text, such as the
    output style that Cursor gets as rule text.
    """
    texts, placed, left_out = pack([*first, *units(tree, flags)], count(tool))
    for number, text in enumerate(texts, start=1):
        path = slot_path(tool, number)
        if files.read_text(path) != text:
            files.write_text(path, text)
    files.write_json(os.path.join(folder(tool), "left_out.json"), {"left_out": left_out})
    return texts, placed, left_out


def clear(tool):
    """Remove one tool's slot files, for a tool hub does not serve."""
    path = folder(tool)
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def start_text(texts):
    """The text a session gets, as the slot hooks' strings arrive joined."""
    return JOIN.join(text for text in texts if text)


def read(tool):
    return [files.read_text(slot_path(tool, number)) or "" for number in range(1, count(tool) + 1)]


def left_out(tool):
    return files.read_json(os.path.join(folder(tool), "left_out.json"), {"left_out": []})["left_out"]


def as_json(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}},
                      ensure_ascii=False)
