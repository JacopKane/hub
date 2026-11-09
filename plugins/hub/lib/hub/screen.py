"""The screen model every door prints: hubctl show, /hub, the band and the pane.

    header    the machine, the profile's use, the parts on out of all, the start text
    strip     the newest changes not undone, open offers and update lines, newest first,
              each with its reason and buttons; a button holds the hubctl arguments it runs.
              A change whose flag changed since has no undo button.
    families  one row per family that has a part, then a row per part and its children.
              The beta parts leave their family for a last group, labelled "Beta, untested".
    band      the status_band flags the band draws from: which zones run, its first digit, keep_hours
    hidden    how many flags are not relevant here
    command_clash  the names Claude Code refused the mod's command, or None (doctor.py)
    relay     whether permission_relay runs, so the mod asks it for waiting prompts
    doors     the folder the mod writes its door tickets to (protect.py)

Core has no family row and is left out of the counts. The header counts from
the manifests, so its total follows each new part. A line leaves the strip
when the user acts on it, or after status_band.notices.keep_hours.
"""
import calendar
import os
import socket
import time

from . import apply, board as board_module, changes as changes_module, doctor, files, history, protect, resolve, slots
from . import sessions, tree as tree_module, tuner

STRIP_CHANGES = 5
KEEP_HOURS = "status_band.notices.keep_hours"
AGENT_KINDS = {"agent", "skill"}


def seconds(stamp):
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0


def said(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none"
    return str(value)


def change_text(line):
    action = line.get("action")
    if action == "take_offer":
        return f"hub's {line['part']} took the place of your agent of the same name"
    if action == "decline_offer":
        return f"you said no to {line['part']}"
    if action == "undo_take_offer":
        return f"your agent {line['part']} is back"
    if action == "undo_decline_offer":
        return f"the offer of {line['part']} is back"
    flag, new = line["flag"], line["new"]
    if "undoes" in line:
        return f"undone, so {flag} is {said(new)} again"
    verb = f"turned {flag} {new}" if new in ("on", "off") else f"set {flag} to {said(new)}"
    return f"{line['by']} {verb}"


def mark(line):
    """+ turned something on, - turned it off, ~ changed a value or undid a change."""
    action = line.get("action")
    if action:
        return {"take_offer": "+", "decline_offer": "-"}.get(action, "~")
    if "undoes" in line:
        return "~"
    if line["new"] == "off":
        return "-"
    return "+" if line.get("old") == "off" else "~"


def offered(offer):
    does = offer.get("does", "").rstrip(".")
    return f"hub offers {offer['id']}, " + does[:1].lower() + does[1:] if does else f"hub offers {offer['id']}"


def strip(keep_hours, now=None):
    now = now or resolve.epoch()
    oldest = now - keep_hours * 3600
    lines = []
    every = history.lines()
    undone = {line.get("undoes") for line in every}
    changes = [line for line in every if line["id"] not in undone and seconds(line.get("time")) >= oldest]
    tree = tree_module.load(files.PARTS_DIR)
    for place, line in enumerate(changes[-STRIP_CHANGES:], start=1):
        entry = {"id": line["id"], "kind": "undo" if "undoes" in line else "change", "mark": mark(line),
                 "time": line.get("time"),
                 "place": place,
                 "text": change_text(line), "reason": line.get("reason", ""), "buttons": []}
        if "undoes" in line:
            entry["undoes"] = line["undoes"]
        # When the flag changed since, the line stays but has no undo. An undo would overwrite the newer value.
        elif "flag" not in line or changes_module.holds(tree, line):
            entry["buttons"] = [{"label": "Undo", "args": ["undo", line["id"]]}]
        lines.append(entry)
    for offer in files.read_json(files.state_path("offers.json"), {"offers": []}).get("offers", []):
        if seconds(offer.get("since")) < oldest:
            continue
        lines.append({"id": f"offer:{offer['id']}", "kind": "offer", "mark": "+", "time": offer.get("since"),
                      "text": offered(offer),
                      "reason": offer.get("reason", ""),
                      "buttons": [{"label": "Turn on", "args": ["on", offer["id"]]},
                                  {"label": "No", "args": ["no", offer["id"]]}]})
    for change in files.read_json(files.state_path("update-summary.json"), {"changes": []}).get("changes", []):
        if seconds(change.get("since")) < oldest:
            continue
        lines.append({"id": f"update:{change['id']}", "kind": "update", "mark": "+", "time": change.get("since"),
                      "text": f"this update added {change['id']}, which is {said(change.get('value'))}",
                      "reason": "", "buttons": [{"label": "Turn off", "args": ["off", change["id"]]}]})
    # Newest first. Within one second, a later history line is newer.
    lines.sort(key=lambda entry: (seconds(entry["time"]), entry.get("place", 0)), reverse=True)
    for entry in lines:
        entry.pop("place", None)
        # history and offers are files on disk, so a forged line still shows as one plain line
        entry["text"], entry["reason"] = files.one_line(entry["text"]), files.one_line(entry["reason"])
    return lines


def band(flags, keep_hours):
    def runs(flag_id):
        return bool(flags.get(flag_id, {}).get("runs"))
    digit = flags.get("status_band.first_digit", {}).get("value")
    return {"runs": runs("status_band"), "state": runs("status_band.state"),
            "notices": runs("status_band.notices"), "offers": runs("status_band.offers"),
            "chats": runs("status_band.chats"), "problems": runs("status_band.problems"),
            "first_digit": digit if isinstance(digit, int) else 1, "keep_hours": keep_hours}


def board_zones(flags, all_flags=None):
    """The band's view of the chat board: the other chats, the failed checks, and whether this chat is the
    coordinator. The coordinator's band rereads the board while the chat is idle."""
    all_flags = all_flags or {}
    chats = failed = None
    coordinator = False
    if flags["runs"] and flags["chats"]:
        viewer = board_module.own_session()
        chats = board_module.band_zone(board_module.load(), viewer)
        name = all_flags.get("coordinator.name", {}).get("value") or "HUB"
        record = sessions.record(viewer) if viewer else None
        coordinator = bool(all_flags.get("coordinator", {}).get("runs")) and isinstance(record, dict) and \
            sessions.same_name(record.get("title"), name)
    if flags["runs"] and flags["problems"]:
        failed = board_module.failed_checks()
    return {"chats": chats, "failed": failed or 0, "coordinator": coordinator}


def row(node, record):
    part = node.part
    name = node.id if node.parent is None else node.id[len(part.id) + 1:]
    return {"id": node.id, "name": name, "depth": node.id.count("."), "does": node.does,
            "value": record["set"], "effective": record["value"], "runs": record["runs"],
            "by": record["by"], "scope": record["scope"], "relevant": record["relevant"],
            "hidden_because": record["hidden_because"], "cost": node.data.get("cost", ""),
            "next_session": slots.is_start_text(node) or (node.parent is None and part.kind in AGENT_KINDS),
            "claude_code_only": node.runs_in == ["claude_code"], "stage": node.stage}


def model(cwd=None, tool="claude_code", everything=False):
    data = resolve.current(cwd or os.getcwd())
    flags = data["tools"][tool]["flags"]
    tree = tree_module.load(files.PARTS_DIR)
    profile = resolve.load_profile()
    parts = [part for part in tree.parts if part.family != "core"]
    keep = flags.get(KEEP_HOURS, {}).get("set")
    keep = keep if isinstance(keep, (int, float)) else 24
    families = []
    groups = [(family, [part for part in parts if part.family == family and part.stage != tree_module.BETA])
              for family in tree_module.FAMILIES]
    groups.append((tree_module.BETA, [part for part in parts if part.stage == tree_module.BETA]))
    for family, members in groups:
        if not members:
            continue
        rows = []
        for part in members:
            if not everything and not flags[part.id]["relevant"]:
                continue
            part_row = row(part, flags[part.id])
            part_row["children"] = [
                row(node, flags[node.id]) for node in part.walk() if node is not part
                and (everything or (flags[node.id]["relevant"] and flags[node.id]["by"] != "default"))]
            rows.append(part_row)
        if rows:
            group = {"family": family, "parts_on": sum(1 for part in members if flags[part.id]["runs"]),
                     "parts_total": len(members), "parts": rows}
            if family == tree_module.BETA:
                group["label"] = tree_module.BETA_LABEL
            families.append(group)
    return {
        "tool": tool,
        "header": {"machine": socket.gethostname().split(".")[0], "use": profile.get("use", ""),
                   "parts_on": sum(1 for part in parts if flags[part.id]["runs"]), "parts_total": len(parts),
                   "start_text": apply.cost(tool)["total"]["start_text"]},
        "strip": strip(keep),
        "band": band(flags, keep),
        "board": board_zones(band(flags, keep), flags),
        "relay": bool(flags.get("permission_relay", {}).get("runs")),
        "families": families,
        "hidden": sum(1 for record in flags.values() if not record["relevant"]),
        "everything": everything,
        "command_clash": doctor.command_clash(),
        "tuner": tuner.screen_state(flags),
        "doors": protect.doors_folder(),
    }


def row_for(flag_id, cwd=None, tool="claude_code"):
    """One flag's row, as /hub and hubctl print it after a change."""
    data = resolve.current(cwd or os.getcwd())
    node = tree_module.load(files.PARTS_DIR).get(flag_id)
    return row(node, data["tools"][tool]["flags"][flag_id])
