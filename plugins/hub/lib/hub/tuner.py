"""The tuner: a rule filter over the signal log, then a cheap model decides.

    candidates(cwd, tool)  the changes the rules pick, with no model
    run_if_due(cwd, tool)  the filter at a session start, at most once per tuner.tune.every_hours
                           and only when the log has new lines since the last run
    decide(answer, cwd)    {"pick": [flag ids]} from the model; only candidates land, by the tuner
    ask(cwd)               the model request for the mod's side call, and the mod's mark
    mod_decides(flags)     whether the mod takes the candidates, so the chat does not get them
    give_back(since)       the mod's side call gave up on the list it tried, so the next chat gets it
    take_given_back()      that list, once, for the chat
    take_notices(tool)     the tuner's one-line notices a session start shows once
    band_read(model)       the band's read of the screen model: it notes the line the band shows
                           and hides the lines a chat already told, so each notice shows once
    builder_run(...)       logs a builder run that made no edit, at SubagentStop, and offers
                           researcher after a few. No model runs, since an offer turns nothing on

The filter never picks a flag the user pinned, a flag a project or local
file sets, a flag that is off already, or a protected flag (see protect.py). Each rule is a named constant, so
its test can name it.
"""
import json
import os

from . import build, changes, files, history, protect, resolve, signals, tree as tree_module

SIGNALS = "signals.jsonl"
FOLDER = "tuner"
PENDING = os.path.join(FOLDER, "pending.json")
LAST_RUN = os.path.join(FOLDER, "last-run.json")
NOTICES = os.path.join(FOLDER, "notices.json")
# The mod writes this mark each time it asks. With a fresh mark, the chat does not get the candidates.
SIDE_CALL = os.path.join(FOLDER, "side-call.json")
SIDE_CALL_FRESH_DAYS = 14
# The strip lines the band showed, and the ones a Claude Code start text told. A notice in one list never
# goes to the other, so the user sees it once, whichever comes first.
BAND = os.path.join(FOLDER, "band.json")
BAND_KEEP = 200
AGENT_TUNING = "## Tuning"

# A check the user went past often enough is turned off.
WENT_PAST_MIN_WARNINGS = 10
WENT_PAST_SHARE = 0.8
TOOLS = ("claude_code", "cursor")

# Builder runs that edit nothing are read-only work, which the cheaper researcher does.
BUILDER = "builder"
NO_EDIT_RUNS_FOR_OFFER = 4
READ_ONLY_OFFER = "researcher"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def signal_lines(keep_days=None):
    """The signal log. With keep_days, lines older than that are pruned from the file first."""
    path = files.state_path(SIGNALS)
    every = files.read_lines(path)
    if keep_days is None:
        return every
    oldest = resolve.epoch() - keep_days * 86400
    kept = [line for line in every if resolve.seconds(line.get("time")) >= oldest]
    if len(kept) != len(every):
        with files.locked("signals.lock"):
            files.write_text(path, "".join(json.dumps(line, sort_keys=True) + "\n" for line in kept))
    return kept


def counts(lines):
    found = {}
    for line in lines:
        flag = found.setdefault(line.get("flag", ""), {})
        flag[line.get("signal", "")] = flag.get(line.get("signal", ""), 0) + 1
    return found


def may_change(record, node):
    """Whether the tuner may change a flag. It must run, be unprotected and unpinned, and no project or local file may set it."""
    return (node is not None and not protect.is_protected(node) and record["runs"]
            and record["scope"] in ("shipped", "user") and record["by"] in ("default", "model", "setup", "tuner"))


def went_past(flag_id, signal_counts):
    warned, ahead = signal_counts.get("warned", 0), signal_counts.get("went_ahead", 0)
    if warned >= WENT_PAST_MIN_WARNINGS and ahead >= WENT_PAST_SHARE * warned:
        return {"flag": flag_id, "value": "off", "reason": f"you went past it {ahead} times"}
    return None


def tool_flags(cwd, tool):
    return resolve.current(cwd)["tools"][tool]["flags"]


def candidates(cwd, tool="claude_code"):
    flags = tool_flags(cwd, tool)
    if not flags.get("tuner.tune", {}).get("runs"):
        return []
    keep = flags.get("tuner.signals.keep_days", {}).get("value") if flags.get("tuner.signals", {}).get("runs") else None
    tree = tree_module.load(files.PARTS_DIR)
    found = []
    for flag_id, signal_counts in sorted(counts(signal_lines(keep)).items()):
        record = flags.get(flag_id)
        if record is None or not may_change(record, tree.get(flag_id)):
            continue
        if flags.get("tuner.tune.turn_off", {}).get("runs"):
            picked = went_past(flag_id, signal_counts)
            if picked:
                found.append(picked)
    return found


def run_if_due(cwd, tool):
    """Run the filter when it is due. Returns the candidates it wrote, or [] when it did not run."""
    flags = tool_flags(cwd, tool)
    if not flags.get("tuner.tune", {}).get("runs"):
        return []
    every_hours = flags.get("tuner.tune.every_hours", {}).get("value") or 24
    with files.locked("tuner.lock"):
        last = files.read_json(files.state_path(LAST_RUN), {}) or {}
        lines = len(signal_lines())
        if lines <= last.get("signals", 0) and "time" in last:
            return []
        if "time" in last and resolve.epoch() - resolve.seconds(last["time"]) < every_hours * 3600:
            return []
        found = candidates(cwd, tool)
        files.write_json(files.state_path(LAST_RUN), {"time": resolve.now(), "signals": len(signal_lines())})
        files.write_json(files.state_path(PENDING), {"time": resolve.now(), "candidates": found})
    return found


def candidate_lines(found):
    return "\n".join(f"- {candidate['flag']} {candidate['value']}: {candidate['reason']}" for candidate in found)


def waiting():
    return files.read_json(files.state_path(PENDING)) or {"candidates": []}


def screen_state(flags):
    """What the mod reads in the screen model: how many candidates wait for it, since when, and whether it decides.
    A list the mod gave back waits for the chat, so it counts none here."""
    pending = waiting()
    count = 0 if "given_back" in pending else len(pending["candidates"])
    return {"pending": count, "since": pending.get("time", ""),
            "side_call": bool(flags.get("tuner.tune.side_call", {}).get("runs"))}


def mod_decides(flags):
    """Whether the mod's side call takes the candidates: it is on, in Claude Code, and the mod asked lately."""
    if flags.tool != "claude_code" or not flags.runs("tuner.tune.side_call"):
        return False
    mark = files.read_json(files.state_path(SIDE_CALL)) or {}
    if not isinstance(mark.get("time"), str):
        return False
    return resolve.epoch() - resolve.seconds(mark["time"]) < SIDE_CALL_FRESH_DAYS * 86400


def tuning_text(flags):
    """The Tuning part of the tuner agent's text, built for these flags."""
    part = tree_module.load(files.PARTS_DIR).get("tuner")
    shipped = os.path.join(files.PARTS_DIR, "tuner", "agent.md")
    built, _changed = build.build(files.read_text(shipped) or "", part, flags, shipped)
    body = build.split(built)[1]
    return body[body.find(AGENT_TUNING):].strip() if AGENT_TUNING in body else body.strip()


def ask(cwd):
    """The request the mod sends to the model, and the mark that it asked."""
    flags = tool_flags(cwd, "claude_code")
    files.write_json(files.state_path(SIDE_CALL), {"time": resolve.now()})
    found = waiting()["candidates"]
    return {"model": flags["tuner.tune.model"]["value"], "system": tuning_text(flags),
            "prompt": "hub's rules found these changes:\n" + candidate_lines(found) if found else "",
            "candidates": found}


def give_back(since):
    """The mod could not decide the list it read at `since`. A newer list stays the mod's."""
    with files.locked("tuner-decide.lock"):
        pending = waiting()
        if not pending["candidates"] or pending.get("time") != since or "given_back" in pending:
            return False
        files.write_json(files.state_path(PENDING), {**pending, "given_back": resolve.now()})
    return True


def take_given_back():
    """The list the mod gave back, once. It stays pending until a decision clears it."""
    with files.locked("tuner-decide.lock"):
        pending = waiting()
        if "given_back" not in pending or pending.get("handed"):
            return []
        files.write_json(files.state_path(PENDING), {**pending, "handed": True})
    return pending["candidates"]


def decide(answer, cwd):
    """Make the changes the model picked from the candidates. Returns the history lines."""
    picked = answer.get("pick") if isinstance(answer, dict) else None
    if not isinstance(picked, list):
        raise changes.ChangeError('the tuner answer must be {"pick": [flag ids]}')
    with files.locked("tuner-decide.lock"):
        return make_picked(picked, cwd)


def make_picked(picked, cwd):
    """Under the lock, so a chat and the mod deciding at once make each change once."""
    pending = files.read_json(files.state_path(PENDING)) or {"candidates": []}
    flags = tool_flags(cwd, "claude_code")
    tree = tree_module.load(files.PARTS_DIR)
    made = []
    for candidate in pending["candidates"]:
        record = flags.get(candidate["flag"])
        if candidate["flag"] not in picked or record is None or not may_change(record, tree.get(candidate["flag"])):
            continue
        line = changes.set_flag(candidate["flag"], candidate["value"], by="tuner", door="tuner", cwd=cwd,
                                reason=candidate["reason"])
        if line:
            made.append(line)
            add_notice(line)
    files.write_json(files.state_path(PENDING), {"time": resolve.now(), "candidates": []})
    return made


def made_an_edit(transcript_path):
    """Whether a subagent transcript holds a call to an edit tool. Unreadable counts as an edit."""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if '"tool_use"' not in raw:
                    continue
                try:
                    content = (json.loads(raw).get("message") or {}).get("content") or []
                except ValueError:
                    continue
                if any(isinstance(block, dict) and block.get("type") == "tool_use"
                       and block.get("name") in EDIT_TOOLS for block in content):
                    return True
    except (OSError, TypeError):
        return True
    return False


def builder_run(cwd, tool, agent_type, transcript_path):
    """Log a builder run with no edit. Returns the offer it made, or None."""
    if agent_type != BUILDER or made_an_edit(transcript_path):
        return None
    flags = tool_flags(cwd, tool)
    if flags.get("tuner.signals", {}).get("runs"):
        signals.log(BUILDER, "SubagentStop", "no_edit")
    if not flags.get("tuner.tune.offers", {}).get("runs"):
        return None
    keep = flags.get("tuner.signals.keep_days", {}).get("value")
    runs = counts(signal_lines(keep)).get(BUILDER, {}).get("no_edit", 0)
    record = flags.get(READ_ONLY_OFFER)
    if runs < NO_EDIT_RUNS_FOR_OFFER or record is None or record["runs"] or record["by"] == "user":
        return None
    return offer(READ_ONLY_OFFER, f"{runs} builder runs made no edits")


def offer(part_id, reason):
    """Put a part in offers.json with the reason, once, and queue its one line."""
    from . import tree as tree_module
    with files.locked():
        path = files.state_path("offers.json")
        offers = files.read_json(path, {"offers": []}) or {"offers": []}
        if any(entry["id"] == part_id for entry in offers["offers"]):
            return None
        made = {"id": part_id, "since": resolve.now(), "does": tree_module.load(files.PARTS_DIR).nodes[part_id].does,
                "reason": reason, "by": "tuner"}
        offers["offers"].append(made)
        files.write_json(path, offers)
    queue_notice({"offer": part_id, "text": f"hub's tuner offers {part_id}, because {reason}. "
                                            f"hubctl on {part_id} turns it on."})
    return made


def notice_text(line):
    return (f"hub's tuner turned {line['flag']} {line['new']}, because {line['reason']}. "
            f"hubctl undo {line['id']} takes it back.")


def add_notice(line):
    queue_notice({"id": line["id"], "text": notice_text(line)})


def queue_notice(notice):
    with files.locked("tuner.lock"):
        notices = files.read_json(files.state_path(NOTICES), {}) or {}
        for tool in TOOLS:
            notices.setdefault(tool, []).append(notice)
        files.write_json(files.state_path(NOTICES), notices)


def take_notices(tool):
    """The notices this tool has not shown yet, whose change still holds. They are shown once."""
    from . import tree as tree_module
    with files.locked("tuner.lock"):
        notices = files.read_json(files.state_path(NOTICES), {}) or {}
        waiting = notices.pop(tool, [])
        if waiting:
            files.write_json(files.state_path(NOTICES), notices)
    if not waiting:
        return []
    tree = tree_module.load(files.PARTS_DIR)
    every = history.lines()
    lines = {line["id"]: line for line in every if "flag" in line}
    undone = {line.get("undoes") for line in every}
    offered = {entry["id"] for entry in (files.read_json(files.state_path("offers.json"), {}) or {}).get("offers", [])}

    def still_true(notice):
        if "offer" in notice:
            return notice["offer"] in offered
        line = lines.get(notice.get("id"))
        return line is not None and line["id"] not in undone and changes.holds(tree, line)

    waiting = [notice for notice in waiting if still_true(notice)]
    if tool == "claude_code":
        waiting = not_on_the_band(waiting)
    return [files.one_line(notice.get("text", "")) for notice in waiting]


def strip_id(notice):
    """The id of the screen's strip line for a notice: the history line, or the offer."""
    return f"offer:{notice['offer']}" if "offer" in notice else notice.get("id")


def band_state():
    state = files.read_json(files.state_path(BAND), {}) or {}
    return {key: [item for item in state.get(key, []) if isinstance(item, str)] if isinstance(state.get(key), list)
            else [] for key in ("shown", "told")}


def keep_band_state(state):
    files.write_json(files.state_path(BAND), {key: items[-BAND_KEEP:] for key, items in state.items()})


def not_on_the_band(notices):
    """The notices the band has not shown, noted as told. Under the lock band_read takes too."""
    with files.locked("tuner.lock"):
        state = band_state()
        shown = set(state["shown"])
        left = [notice for notice in notices if strip_id(notice) not in shown]
        if left:
            state["told"].extend(strip_id(notice) for notice in left if strip_id(notice))
            keep_band_state(state)
    return left


def band_line(model):
    """The strip line the band draws from the screen model, picked as register.tsx's shown() picks it."""
    flags = model.get("band") or {}
    if not flags.get("runs"):
        return None
    lines = [line for line in model.get("strip", []) if line.get("buttons")]
    if flags.get("notices"):
        for line in lines:
            if line.get("kind") in ("change", "update"):
                return line
    if flags.get("offers"):
        for line in lines:
            if line.get("kind") == "offer":
                return line
    return None


def band_read(model):
    """The model the band draws. A line a chat already told has no buttons, so the band skips it."""
    with files.locked("tuner.lock"):
        state = band_state()
        told = set(state["told"])
        for line in model.get("strip", []):
            if line.get("id") in told:
                line["buttons"] = []
        shown = band_line(model)
        if shown is not None and shown["id"] not in state["shown"]:
            state["shown"].append(shown["id"])
            keep_band_state(state)
    return model
