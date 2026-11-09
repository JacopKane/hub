"""Built copies of agent and skill files.

A shipped agent.md or SKILL.md marks each instruction with one line before
and one after:

    <!-- hub:section builder.instructions.test_first -->
    Write the test first.
    <!-- hub:end -->

A marker can also name one value of a choice option, as in
`<!-- hub:section humanize.em_dash=warn -->`, and its section stays only
while the option runs with that value.

The other .md files of a skill, such as its references, can hold sections
too, and a built copy of the skill builds each of them the same way.

A copy keeps only the sections whose flags run, drops every marker, and
takes the part's options (model, effort) into its front matter. A file needs
a copy only when it changed: a section is out, or an option differs from the
shipped front matter. Back at every default, the link points at the shipped
file again.
"""
import re

SECTION = re.compile(r"^<!-- hub:section ([a-z0-9_.]+(?:=[a-z0-9_]+)?) -->$")
END = "<!-- hub:end -->"
FRONT_MATTER_OPTIONS = ("model", "effort")


class BuildError(ValueError):
    pass


def split(text):
    """(front matter lines, body) of a file that starts with ---, else ([], text)."""
    if not text.startswith("---\n"):
        return [], text
    head, _, body = text[4:].partition("\n---\n")
    return head.splitlines(), body


def front_matter(text):
    found = {}
    for line in split(text)[0]:
        key, colon, value = line.partition(":")
        if colon and key.strip() and not line.startswith((" ", "\t")):
            found[key.strip()] = value.strip()
    return found


def join(lines, body):
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def set_keys(lines, values):
    """Front matter lines with each key in values set, added at the end when missing."""
    lines, seen = list(lines), set()
    for index, line in enumerate(lines):
        key = line.partition(":")[0].strip()
        if key in values:
            lines[index] = f"{key}: {values[key]}"
            seen.add(key)
    lines += [f"{key}: {value}" for key, value in values.items() if key not in seen]
    return lines


def keep_only(lines, keys):
    return [line for line in lines if line.partition(":")[0].strip() in keys]


def sections(body, runs, where, offset=0):
    """(body without markers and without the sections that do not run, whether one was dropped).

    runs(marker, where) is called with "<file>:<line>" for the marker; offset is
    the number of lines in front of body in its file.
    """
    kept, stack, dropped = [], [], False
    for number, line in enumerate(body.split("\n"), offset + 1):
        match = SECTION.match(line.strip())
        if match:
            stack.append(runs(match.group(1), f"{where}:{number}"))
            dropped = dropped or not stack[-1]
            continue
        if line.strip() == END:
            if not stack:
                raise BuildError(f"{where}: {END} without a section before it")
            stack.pop()
            continue
        if all(stack):
            kept.append(line)
    if stack:
        raise BuildError(f"{where}: a section is not closed with {END}")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return text, dropped


def option_values(part, flags):
    """The front matter values a part's options give, such as {"model": "sonnet"}."""
    values = {}
    for name in FRONT_MATTER_OPTIONS:
        record = flags.get(f"{part.id}.{name}")
        if record is not None and record["value"] not in (None, "", "off"):
            values[name] = str(record["value"])
    return values


def section_runs(flags, where):
    """The check sections() calls for each marker, for one tool's flags."""
    def runs(marker, at):
        flag_id, _, wanted = marker.partition("=")
        record = flags.get(flag_id)
        if record is None:
            raise BuildError(f"{at}: no flag named {flag_id!r}")
        problem = marker_problem(marker, at)
        if problem:
            raise BuildError(problem)
        return record["runs"] and (not wanted or str(record["value"]) == wanted)
    return runs


def marker_problem(marker, at, nodes=None):
    """The sentence for a marker naming a value its option does not take, else None."""
    flag_id, _, wanted = marker.partition("=")
    if nodes is None:
        from . import files, tree as tree_module
        nodes = tree_module.load(files.PARTS_DIR).nodes
    node = nodes.get(flag_id)
    if wanted and node is not None and wanted not in node.allowed_values():
        return (f"{at}: {flag_id} has no value {wanted!r}. It takes one of "
                f"{', '.join(node.allowed_values())}.")
    return None


def marker_problems(folder):
    """One sentence for each marker under folder that names a value its option does not take."""
    import os
    from . import files, tree as tree_module
    nodes = tree_module.load(files.PARTS_DIR).nodes
    found = []
    for root, _folders, names in sorted(os.walk(folder)):
        for name in sorted(names):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            for number, line in enumerate((files.read_text(path) or "").split("\n"), 1):
                match = SECTION.match(line.strip())
                problem = match and marker_problem(match.group(1), f"{path}:{number}", nodes)
                if problem:
                    found.append(problem)
    return found


def body_only(text, flags, where):
    """(text without markers and without the sections that do not run, whether one was dropped).

    For a file a skill reads, such as references/tells-and-fixes.md. Any
    front matter stays as it is.
    """
    return sections(text, section_runs(flags, where), where)


def build(text, part, flags, where):
    """(built text, changed) for one tool's flags."""
    lines, body = split(text)
    shipped = front_matter(text)
    offset = text.count("\n") - body.count("\n")
    body, dropped = sections(body, section_runs(flags, where), where, offset)
    values = option_values(part, flags)
    changed = dropped or any(shipped.get(key) != value for key, value in values.items())
    return join(set_keys(lines, values), body) if lines else body, changed
