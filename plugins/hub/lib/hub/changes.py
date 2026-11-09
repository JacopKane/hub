"""Set a flag in one scope, and undo the last change. Every door calls these.

A value set by the user is pinned: it stays in the file even when it equals
the default, and setup and the tuner leave it alone. hub sets who made a change from where it runs,
never from what the caller says: a change inside a tool call is set by the
model, and it is never a pin. A value from setup, the tuner or the model that
equals the default is dropped.

Only a user door may weaken a protected flag (see protect.py): setup and the
tuner leave it, a command the model runs is refused and told to ask the user,
and hubctl in a terminal is refused and pointed to /hub in Claude Code.
"""
import copy
import json
import os

from . import apply, files, history, links, protect, resolve, settings, tree as tree_module


class ChangeError(ValueError):
    pass


# set_flag's expected value when any value may be overwritten.
ANY = object()

PINNED_BY_USER = ("The user set {flag} by hand, so only the user can change it, with /hub in Claude Code. "
                  "A command the model runs can not.")
CHANGED_WHILE_RUNNING = ("Nothing changed, because {flag} changed while hubctl ran and this change would now "
                         "weaken it. Run it again to check against the new value.")
USER_ONLY_VERB = "Only the user can {verb}, with /hub in Claude Code. A command the model runs can not."


def refuse_in_tool_call(verb):
    if files.in_tool_call():
        raise ChangeError(USER_ONLY_VERB.format(verb=verb))


def settings_path(scope, repo):
    return files.user_settings_path() if scope == "user" else settings.scope_path(scope, repo=repo)


def scope_value(node, data):
    """The value a flag holds in one scope's file: its entry there, else its default."""
    stored = settings.entry(data, node.id)
    if stored is not None:
        return stored["value"]
    profile = resolve.load_profile()
    return resolve.default_value(node, profile, resolve.facts_for("claude_code", profile))


def holds(tree, line):
    """True when a history line's flag still holds the value that line set, in the line's scope."""
    node = tree.get(line["flag"])
    if node is None:
        return False
    scope = line.get("scope", "user")
    if scope != "user" and not os.path.exists(os.path.join(line.get("repo") or "", ".git")):
        return False
    data = settings.read(settings_path(scope, line.get("repo"))) or settings.empty()
    return scope_value(node, data) == line["new"]


def repo_for(scope, cwd, repo=None):
    """The repo a project or local value goes into, or None for the user scope."""
    if scope == "user":
        return None
    if repo is not None:
        if not os.path.exists(os.path.join(repo, ".git")):
            raise ChangeError(f"{repo} is no longer a git repo, so its {scope} value can not change")
        return repo
    found = settings.find_repo(cwd)
    if found is None:
        raise ChangeError(f"--scope {scope} needs a git repo, and {cwd} is not in one. "
                          "Run it inside the repo, or use --scope user.")
    return found


def check_value(node, value):
    if node.type == "list":
        if not isinstance(value, list):
            raise ChangeError(f"{node.id} takes a list")
        return value
    if node.type == "number":
        try:
            number = float(value) if "." in str(value) else int(value)
        except ValueError:
            raise ChangeError(f"{node.id} takes a number") from None
        bounds = node.data.get("range")
        if bounds and not bounds[0] <= number <= bounds[1]:
            raise ChangeError(f"{node.id} takes a number from {bounds[0]} to {bounds[1]}")
        return number
    if node.type == "text":
        value = str(value)
        problem = tree_module.pattern_problem(value) if node.data.get("check") == "pattern" else None
        if problem:
            raise ChangeError(f"{node.id} takes a pattern, and this one can not be used because {problem}")
        return value
    if value not in node.allowed_values():
        raise ChangeError(f"{node.id} takes one of {', '.join(node.allowed_values())}")
    return value


def on_value(node):
    """What `on` means for a node: on for a switch, the default mode for a mode part."""
    if node.type == "mode":
        default = node.data.get("default")
        return default if default in node.values and default != "off" else next(v for v in node.values if v != "off")
    if node.type in ("switch", "choice"):
        return "on" if "on" in node.allowed_values() else node.values[0]
    raise ChangeError(f"{node.id} is a {node.type}, so set it with a value")


def planned(data, node, value, by):
    """A copy of one scope's data with the change in it, as set_flag writes it."""
    changed = copy.deepcopy(data)
    profile = resolve.load_profile()
    if by != "user" and value == resolve.default_value(node, profile, resolve.facts_for("claude_code", profile)):
        settings.drop(changed, node.id)
    else:
        settings.put(changed, node.id, value, by)
    return changed


def resolved_files(tree, place):
    """What each tool runs with, read straight from the settings files. It takes no lock and applies nothing."""
    offers = {offer["id"] for offer in files.read_json(files.state_path("offers.json"), {"offers": []})["offers"]}
    return resolve.resolve(tree, settings.layers(place), resolve.load_profile(), offers)


def weakened(tree, node, value, by, scope, path, place):
    """The flag a change weakens, or None.

    It is the flag itself when the flag is protected and the change lowers it,
    either in its own scope or in what a tool runs with all scopes applied. It
    can also be another flag that runs now and would stop, such as
    tier_rule.guard when tier_rule is turned off while the guard runs. That
    other flag counts when it is protected, or when the user set it by hand
    and someone else makes the change. The user may stop their own value. A
    privacy option that loosens a check does not count, because stopping it
    makes the check stricter.
    """
    data = settings.read(path) or settings.empty()
    protected = protect.is_protected(node)
    if protected and protect.lowers(node, value, scope_value(node, data)):
        return node.id
    before = settings.layers(place)
    changed = planned(data, node, value, by)
    after = [(name, layer_path, changed if name == scope else layer_data) for name, layer_path, layer_data in before]
    if not any(name == scope for name, _path, _data in before):
        after.append((scope, path, changed))
        after.sort(key=lambda layer: settings.SCOPES.index(layer[0]))
    profile = resolve.load_profile()
    offers = {offer["id"] for offer in files.read_json(files.state_path("offers.json"), {"offers": []})["offers"]}
    old, new = resolve.resolve(tree, before, profile, offers), resolve.resolve(tree, after, profile, offers)
    if protected and any(protect.lowers(node, new[tool]["flags"][node.id]["set"], old[tool]["flags"][node.id]["set"])
                         for tool in old):
        return node.id
    for tool in old:
        for flag_id, record in old[tool]["flags"].items():
            other = tree.nodes[flag_id]
            if flag_id == node.id or not record["runs"] or new[tool]["flags"][flag_id]["runs"]:
                continue
            set_by_hand = record["by"] == "user" and by != "user"
            if set_by_hand or (protect.is_protected(other) and other.privacy != "loosens"):
                return flag_id
    return None


def pinned_elsewhere(flag_id, scope, place):
    """Whether the user set the flag by hand in another scope that applies here."""
    for name, _path, data in settings.layers(place):
        stored = settings.entry(data, flag_id)
        if name != scope and stored is not None and stored.get("by", "user") == "user":
            return True
    return False


GO, SKIP = "go", "skip"


def check_change(tree, node, value, by, scope, path, place, user_door, resolved_tools):
    """Whether a change may be written. Returns (verdict, weakened flag or None). The verdict is GO, or SKIP
    when setup or the tuner leaves the flag alone. Raises ChangeError for a refusal.

    user_door is None, or a function that answers the user's door. It is called only when a change by the
    user weakens a protected flag, since finding the door reads stdin. resolved_tools gives what each tool
    runs with now. It is called only for a privacy flag in a shared file.
    """
    flag_id = node.id
    if node.privacy != "none" and settings.shared(scope, path):
        below = resolved_tools()["claude_code"]["flags"][flag_id]["set"]
        if resolve.lowers(node, value, below):
            if scope == "project":
                raise ChangeError(f"{flag_id} is a privacy flag, so a project file can not lower it. "
                                  "Use --scope local or --scope user.")
            raise ChangeError(f"{flag_id} is a privacy flag, and git tracks {path}. A tracked file is shared "
                              "like a project file, so it can not lower that flag. Use --scope user, or stop "
                              "tracking the file with git rm --cached.")
    weakened_flag = weakened(tree, node, value, by, scope, path, place)
    if weakened_flag is not None:
        if by in ("setup", "tuner"):
            return SKIP, weakened_flag
        if by == "model":
            message = protect.ASK_THE_USER if weakened_flag == flag_id else protect.STOPS_ASK_THE_USER
            raise ChangeError(message.format(flag=flag_id, stopped=weakened_flag))
        if user_door is None or user_door() is None:
            message = protect.NO_DOOR if weakened_flag == flag_id else protect.STOPS_NO_DOOR
            raise ChangeError(message.format(flag=flag_id, stopped=weakened_flag))
    if by != "user" and pinned_elsewhere(flag_id, scope, place):
        if by == "model":
            raise ChangeError(PINNED_BY_USER.format(flag=flag_id))
        return SKIP, weakened_flag
    return GO, weakened_flag


def set_flag(flag_id, value, by="user", scope="user", door="hubctl", cwd=None, reason="", undoes=None,
             repo=None, notes=None, expected=ANY):
    """Write one value and apply. Returns the history line, or None when nothing changed.

    A project or local value needs a git repo: the one given, or else the one
    that holds cwd. The lines apply prints go into notes, when it is a list.
    When expected is given, the scope must still hold that value under the
    lock. Otherwise nothing is written.
    """
    tree = tree_module.load(files.PARTS_DIR)
    node = tree.get(flag_id)
    if node is None:
        raise ChangeError(f"no flag named {flag_id!r}")
    value = check_value(node, value)
    if by == "user" and files.in_tool_call():
        by = "model"
    if node.stage == tree_module.BETA and by != "user" and resolve.is_on(node, value):
        raise ChangeError(f"{flag_id} is in beta, so only the user turns it on")
    cwd = cwd or os.getcwd()
    repo = repo_for(scope, cwd, repo)
    path = settings_path(scope, repo)
    place = repo or cwd
    # The door is found at most once, and only when the change needs it, since finding it reads stdin.
    found = {}

    def user_door():
        if "door" not in found:
            found["door"] = protect.door()
        return found["door"]

    def door_found_before():
        if "door" not in found:
            raise ChangeError(CHANGED_WHILE_RUNNING.format(flag=flag_id))
        return found["door"]

    verdict, _weakened = check_change(tree, node, value, by, scope, path, place, user_door,
                                      lambda: resolve.current(repo)["tools"])
    if verdict == SKIP:
        return None
    if found.get("door") == protect.MOD_DOOR and door == "hubctl":
        door = protect.MOD_DOOR
    with files.locked():
        # The same checks again under the lock, so a file written after the first checks is seen. A change
        # that weakens a flag only now needed a door it did not show, so it is refused.
        # resolve.current takes this lock to apply, so the files are resolved here directly.
        verdict, _weakened = check_change(tree, node, value, by, scope, path, place, door_found_before,
                                          lambda: resolved_files(tree, place))
        if verdict == SKIP:
            return None
        data = settings.read(path) or settings.empty()
        stored = settings.entry(data, flag_id)
        if stored is not None and stored.get("by", "user") == "user" and by == "model":
            raise ChangeError(PINNED_BY_USER.format(flag=flag_id))
        if stored is not None and stored.get("by", "user") == "user" and by != "user":
            return None
        profile = resolve.load_profile()
        default = resolve.default_value(node, profile, resolve.facts_for("claude_code", profile))
        old = stored["value"] if stored is not None else default
        if expected is not ANY and old != expected:
            raise ChangeError(f"{undoes or flag_id} can not be undone, because {flag_id} changed since. It is "
                              f"{json.dumps(old, ensure_ascii=False)} now. Change it with hubctl set.")
        if by != "user" and value == default:
            settings.drop(data, flag_id)
        else:
            settings.put(data, flag_id, value, by)
        settings.write(path, data)
        line = history.add(flag_id, scope, old, value, by, door, reason, undoes, repo)
        acted_on(flag_id)
        lines = apply.run(tree)
    if notes is not None:
        notes.extend(lines)
    return line


def acted_on(flag_id):
    """A change to a flag answers its open offer and its update line, so both leave the strip."""
    offers_path, summary_path = files.state_path("offers.json"), files.state_path("update-summary.json")
    offers = files.read_json(offers_path)
    if offers and any(offer["id"] == flag_id for offer in offers.get("offers", [])):
        files.write_json(offers_path, {"offers": [offer for offer in offers["offers"] if offer["id"] != flag_id]})
    summary = files.read_json(summary_path)
    if summary and any(change["id"] == flag_id for change in summary.get("changes", [])):
        files.write_json(summary_path, {"changes": [change for change in summary["changes"] if change["id"] != flag_id]})


def decline_offer(part_id, door="hubctl"):
    """Say no to an offered part. It stays off, and undo brings the offer back."""
    refuse_in_tool_call("say no to an offer")
    with files.locked():
        offers_path = files.state_path("offers.json")
        offers = files.read_json(offers_path, {"offers": []})
        found = [offer for offer in offers["offers"] if offer["id"] == part_id]
        if not found:
            raise ChangeError(f"there is no open offer for {part_id!r}")
        files.write_json(offers_path, {"offers": [offer for offer in offers["offers"] if offer["id"] != part_id]})
        # Without the offer the part would take its default, so a no keeps it off with a pin.
        settings_path = files.user_settings_path()
        data = settings.read(settings_path) or settings.empty()
        pinned = settings.entry(data, part_id) is None
        if pinned:
            settings.put(data, part_id, "off", "user")
            settings.write(settings_path, data)
        history.add_action("decline_offer", door, part=part_id, offer=found[0], pinned=pinned)
        apply.run()
    return found[0]


def pin(flag_id, scope="user", door="hubctl", cwd=None, notes=None):
    """Write a flag's current value as set by the user, so setup and the tuner leave it."""
    refuse_in_tool_call("pin a flag")
    cwd = cwd or os.getcwd()
    record = resolve.current(repo_for(scope, cwd) or cwd)["tools"]["claude_code"]["flags"].get(flag_id)
    if record is None:
        raise ChangeError(f"no flag named {flag_id!r}")
    return set_flag(flag_id, record["set"], by="user", scope=scope, door=door, cwd=cwd, notes=notes)


def take_offer(part_id, door="hubctl", notes=None):
    """Hold the user's agent of the same name and make hub's link. Undo moves it back."""
    refuse_in_tool_call("swap their agent for hub's")
    with files.locked():
        try:
            line = links.take(part_id, door)
        except links.LinkError as error:
            raise ChangeError(str(error)) from None
        lines = apply.run()
    if notes is not None:
        notes.extend(lines)
    return line


def undo(door="hubctl", cwd=None, line_id=None):
    """Restore the old value of a change, pinned, in the repo it changed.

    With no id it undoes the newest change. When the change took an offer to
    switch, the user's file goes back. When it said no to an offer, the offer
    comes back.
    """
    if line_id is None:
        line = history.last_undoable()
        if line is None:
            raise ChangeError("there is nothing to undo")
    else:
        line = history.undoable(line_id)
    if line.get("action") in ("decline_offer", "take_offer"):
        refuse_in_tool_call("undo a choice about an offer")
    if line.get("action") == "decline_offer":
        with files.locked():
            offers_path = files.state_path("offers.json")
            offers = files.read_json(offers_path, {"offers": []})
            if not any(offer["id"] == line["part"] for offer in offers["offers"]):
                offers["offers"].append(line["offer"])
                files.write_json(offers_path, offers)
            if line.get("pinned"):
                settings_path = files.user_settings_path()
                data = settings.read(settings_path) or settings.empty()
                settings.drop(data, line["part"])
                settings.write(settings_path, data)
            history.add_action("undo_decline_offer", door, undoes=line["id"], part=line["part"])
            apply.run()
        return line
    if line.get("action") == "take_offer":
        with files.locked():
            try:
                links.undo_take(line)
            except links.LinkError as error:
                raise ChangeError(str(error)) from None
            history.add_action("undo_take_offer", door, undoes=line["id"], part=line["part"])
            apply.run()
        return line
    # Undo only while the flag still holds what this change set. Otherwise an older line
    # would overwrite a newer change.
    set_flag(line["flag"], line["old"], by="user", scope=line.get("scope", "user"), door=door, cwd=cwd,
             reason=f"undo of {line['id']}", undoes=line["id"], repo=line.get("repo"), expected=line["new"])
    return line
