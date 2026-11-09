"""hub setup: turn the setup agent's answer into a dialog, then apply the user's replies.

    setup --answer FILE    checks the agent's JSON answer, keeps it, prints the questions
    setup --reply JSON     {question id: option label}, with the ids --answer printed. Skip or a
                                  left-out id changes nothing, and an id setup did not ask is refused

The answer is {profile: {this_machine}, questions: [{id, text, options:
[{label, does, flags?, machine?}]}], instructions_off: [ids], offers: [ids]}.
Setup keeps only what the tree has: an unknown flag or a bad value is
dropped, then an option with nothing left, then a question with no option,
and questions past tuner.setup.questions. Setup also drops a flag change
that would weaken a protected flag (see protect.py) or turn on a beta flag,
since only the user turns one on. Every question gets
Skip as its last option and an id of hub's own. The agent's id stays as the
question's topic.

The reply scans this machine into the profile, applies the chosen options
by setup, turns off the instructions compare_rules found, writes hub.json
(so the first run is over), applies, and returns the summary.
"""
import json
import os
import secrets

from . import apply, changes, files, profile, protect, resolve, scan, screen, settings, tree as tree_module

PENDING = os.path.join("setup", "pending.json")
SKIP = {"label": "Skip", "does": "Changes nothing."}
# The facts about this machine a setup question may set, with the values each takes.
MACHINE_ANSWERS = {"use": {"work", "home", "both"}}
MAX_TEXT = 300
UNKNOWN_IDS = ("Nothing changed, because setup did not ask {unknown}. Use the question ids that "
               "setup --answer printed: {asked}.")


class SetupError(ValueError):
    pass


def text(value):
    return value.strip()[:MAX_TEXT] if isinstance(value, str) and value.strip() else None


def weakens_protected(node, value, flags):
    """Whether this value would weaken a protected flag below what it runs with now."""
    record = flags.get(node.id)
    return protect.is_protected(node) and (record is None or protect.lowers(node, value, record["set"]))


def valid_flags(tree, wanted, flags):
    kept = {}
    for flag_id, value in (wanted or {}).items() if isinstance(wanted, dict) else ():
        node = tree.get(flag_id)
        if node is None:
            continue
        try:
            value = changes.check_value(node, value)
        except changes.ChangeError:
            continue
        if node.stage == tree_module.BETA and resolve.is_on(node, value):
            continue  # only the user turns a beta flag on, so setup never offers it
        if not weakens_protected(node, value, flags):
            kept[flag_id] = value
    return kept


def valid_machine(wanted):
    if not isinstance(wanted, dict):
        return {}
    return {fact: value for fact, value in wanted.items() if value in MACHINE_ANSWERS.get(fact, ())}


def valid_option(tree, option, current):
    if not isinstance(option, dict) or not text(option.get("label")) or text(option["label"]) == SKIP["label"]:
        return None
    kept = {"label": text(option["label"]), "does": text(option.get("does")) or ""}
    flags, machine = valid_flags(tree, option.get("flags"), current), valid_machine(option.get("machine"))
    if flags:
        kept["flags"] = flags
    if machine:
        kept["machine"] = machine
    return kept if flags or machine else None


def valid_questions(tree, questions, limit, current):
    """The questions setup keeps. Each gets a random id of hub's own, and the agent's id becomes its topic."""
    kept, seen = [], set()
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            continue
        topic, wording = text(question.get("id")), text(question.get("text"))
        options = [found for found in (valid_option(tree, option, current)
                                       for option in question.get("options") or []) if found]
        if not topic or topic in seen or not wording or not options:
            continue
        seen.add(topic)
        kept.append({"id": secrets.token_hex(4), "topic": topic, "text": wording, "options": options})
    return kept[:limit]


def check_answer(answer, flags):
    """The agent's answer with everything the tree does not have dropped."""
    if not isinstance(answer, dict):
        raise SetupError("the setup answer must be one JSON object")
    tree = tree_module.load(files.PARTS_DIR)
    limit = int(flags["tuner.setup.questions"]["value"]) if flags["tuner.setup.questions"]["runs"] else 0
    wanted_profile = answer.get("profile") if isinstance(answer.get("profile"), dict) else {}
    machine = text(wanted_profile.get("this_machine"))
    off = []
    if flags["tuner.setup.compare_rules"]["runs"]:
        off = [flag_id for flag_id in answer.get("instructions_off") or []
               if isinstance(flag_id, str) and tree.get(flag_id) is not None
               and tree.get(flag_id).kind == "instruction" and not weakens_protected(tree.get(flag_id), "off", flags)]
    offers = [part_id for part_id in answer.get("offers") or []
              if isinstance(part_id, str) and tree.get(part_id) is not None and tree.get(part_id).parent is None
              and not flags[part_id]["runs"]]
    return {"this_machine": machine, "questions": valid_questions(tree, answer.get("questions"), limit, flags),
            "instructions_off": off, "offers": offers}


def take_answer(path, cwd):
    """Check and keep the agent's answer. Returns the dialog to show."""
    try:
        with open(path, encoding="utf-8") as handle:
            answer = json.load(handle)
    except (OSError, ValueError) as error:
        raise SetupError(f"hub could not read the setup answer {path} ({error})") from None
    flags = resolve.current(cwd)["tools"]["claude_code"]["flags"]
    pending = check_answer(answer, flags)
    files.write_json(files.state_path(PENDING), pending)
    return {"questions": [dict(question, options=[*({"label": option["label"], "does": option["does"]}
                                                    for option in question["options"]), SKIP])
                          for question in pending["questions"]]}


def chosen(pending, replies):
    """The options the user picked, one per question they did not skip."""
    for question in pending["questions"]:
        label = replies.get(question["id"])
        if label is None or label == SKIP["label"]:
            continue
        match = [option for option in question["options"] if option["label"] == label]
        if not match:
            raise SetupError(f"{question['id']} has no option {label!r}. Pick one of "
                             + ", ".join(option["label"] for option in question["options"]) + ", or Skip.")
        yield question, match[0]


def take_reply(raw, cwd):
    """Apply the user's replies, end the first run, and return the summary lines."""
    try:
        replies = json.loads(raw)
    except ValueError as error:
        raise SetupError(f"the reply must be JSON, such as {{\"use\": \"work\"}} ({error})") from None
    if not isinstance(replies, dict):
        raise SetupError("the reply must be one JSON object of question ids and option labels")
    pending = files.read_json(files.state_path(PENDING))
    if pending is None:
        raise SetupError("there is no setup answer yet. Run setup --answer <file> first.")
    asked = [question["id"] for question in pending["questions"]]
    unknown = sorted(reply_id for reply_id in replies if reply_id not in asked)
    if unknown:
        raise SetupError(UNKNOWN_IDS.format(unknown=", ".join(unknown), asked=", ".join(asked) or "none"))
    picks = list(chosen(pending, replies))
    data = profile.from_scan(scan.run(cwd), name=pending.get("this_machine"))
    machine = data["this_machine"]
    for _question, option in picks:
        for fact, value in option.get("machine", {}).items():
            profile.merge(data, f"machines.{machine}.{fact}", value)
    profile.write(data)
    changed = []
    for question, option in picks:
        for flag_id, value in option.get("flags", {}).items():
            line = changes.set_flag(flag_id, value, by="setup", door="setup", cwd=cwd,
                                    reason=f"you picked {option['label']} for {question.get('topic', question['id'])}")
            if line:
                changed.append(line)
    for flag_id in pending["instructions_off"]:
        line = changes.set_flag(flag_id, "off", by="setup", door="setup", cwd=cwd,
                                reason="your own rules already say this")
        if line:
            changed.append(line)
    with files.locked():
        path = files.user_settings_path()
        if not os.path.exists(path):
            settings.write(path, settings.empty())
        add_offers(pending["offers"])
        lines = apply.run()
    os.unlink(files.state_path(PENDING))
    return summary(cwd, changed) + lines


def add_offers(part_ids):
    if not part_ids:
        return
    tree = tree_module.load(files.PARTS_DIR)
    offers = files.read_json(files.state_path("offers.json"), {"offers": []})
    known = {offer["id"] for offer in offers["offers"]}
    for part_id in part_ids:
        if part_id not in known:
            offers["offers"].append({"id": part_id, "since": resolve.now(), "does": tree.get(part_id).does,
                                     "reason": "setup thinks you may need it"})
    files.write_json(files.state_path("offers.json"), offers)


def next_session(row):
    """Whether a part, or a child of it that runs, waits for the next session, such as start text or an agent."""
    return row["next_session"] or any(child["runs"] and next_session(child) for child in row.get("children", []))


def summary(cwd, changed):
    model = screen.model(cwd, everything=True)
    rows = [part for family in model["families"] for part in family["parts"] if part["runs"] and part["relevant"]]
    now = [row for row in rows if not next_session(row)]
    later = [row for row in rows if next_session(row)]
    lines = [f"hub is set up, with {len(rows)} parts on."]
    for heading, group in (("Works now", now), ("Starts next session", later)):
        lines += ["", heading + ":"] + [f"- {row['id']}: {row['does']}" for row in group]
    if changed:
        lines += ["", "Setup changed:"] + [f"- {line['flag']} is {json.dumps(line['new'])} now, because "
                                           f"{line['reason']}. Say \"undo {line['id']}\" to take it back."
                                           for line in changed]
    offered = [entry for entry in model["strip"] if entry["kind"] == "offer"]
    if offered:
        lines += ["", "Offered. These stay off until you say yes:"] + [f"- {entry['text']}" for entry in offered]
    lines += ["", "Ask for any change in plain words, such as \"turn off scout\"."]
    return lines
