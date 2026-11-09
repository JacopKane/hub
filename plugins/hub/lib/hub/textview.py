"""The screen model as text, for the switch skill's show and the /hub mod command.

Text has no buttons. Under each strip line, the next line names the commands
its buttons run.
"""
import time

from .screen import seconds

FAMILY_NAMES = {"setup": "setup", "tiers": "agents by tier", "chats": "chats", "questions": "questions",
                "gates": "gates", "rules": "rules", "learning": "learning", "housekeeping": "housekeeping",
                "screens": "screens", "tools": "tools", "notify": "notices", "beta": "beta, untested"}


def ago(stamp, now=None):
    passed = max(0, int((now or time.time()) - seconds(stamp)))
    if passed < 60:
        return "now"
    if passed < 3600:
        return f"{passed // 60}m ago"
    if passed < 86400:
        return f"{passed // 3600}h ago"
    return f"{passed // 86400}d ago"


def value_text(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none"
    return str(value)


UNDO_LINE = 'Say "undo that" to take it back.'
# What the user says for a button, by its verb. Each takes the button's words after the verb.
PHRASES = {"on": "turn on {words}", "off": "turn off {words}", "set": "set {words}", "pin": "keep {words} as it is",
           "no": "no to {words}", "take": "use hub's {words}", "apply": "apply hub's settings",
           "update": "update hub", "offers": "show hub's offers", "show": "show hub's parts",
           "doctor": "check hub's setup"}


def command(button):
    """The plain words the user says for a button, such as say "turn on researcher"."""
    verb, *rest = button["args"]
    if verb == "undo":
        phrase = f"undo {' '.join(rest)}" if rest else "undo that"
    else:
        phrase = PHRASES.get(verb, "hub {words}").format(words=" ".join(rest) if verb in PHRASES else
                                                              " ".join(button["args"]))
    return f'say "{phrase.strip()}"'



def header_line(header):
    where = f"hub on {header['machine']}"
    if header.get("use"):
        where += f" ({header['use']})"
    return (f"{where}. {header['parts_on']} of {header['parts_total']} parts on, "
            f"start text {header['start_text']:,} characters")


def row_line(row):
    line = f"{'  ' * (row['depth'] + 1)}{row['name']:<{max(30 - 2 * row['depth'], 12)}} " \
           f"{value_text(row['value']):<8} {'runs' if row['runs'] else 'stopped'}"
    if row["by"] == "offer":
        line += ", offered and waiting for your answer"
    elif row["by"] != "default":
        line += f", set by {row['by']} ({row['scope']})"
    if row["next_session"]:
        line += ", from the next session"
    if row["claude_code_only"] and row["depth"] == 0:
        line += ", Claude Code only"
    if row["stage"] == "beta" and row["depth"] == 0:
        line += ", beta, untested"
    if not row["relevant"]:
        line += f", hidden: {row['hidden_because']}"
    return line


def render(model):
    lines = [header_line(model["header"])]
    if model["strip"]:
        lines += ["", "Changes"]
        for entry in model["strip"]:
            text = f"  {ago(entry['time']):<8} {entry['text']}"
            if entry["reason"]:
                text += f", because {entry['reason']}"
            lines.append(text)
            if entry["buttons"]:
                lines.append(" " * 11 + ", or ".join(command(button) for button in entry["buttons"]))
    for family in model["families"]:
        lines += ["", f"{FAMILY_NAMES.get(family['family'], family['family'])}, "
                      f"{family['parts_on']} of {family['parts_total']} on"]
        for part in family["parts"]:
            lines.append(row_line(part))
            lines += [row_line(child) for child in part["children"]]
    lines.append("")
    if model["hidden"] and not model["everything"]:
        count = "1 flag is" if model["hidden"] == 1 else f"{model['hidden']} flags are"
        lines.append(f"{count} hidden here. Ask to show every flag to list them with the reason.")
    lines.append('Ask for a change in plain words, such as "turn off scout". Say "undo that" to take back the last change.')
    return "\n".join(lines) + "\n"
