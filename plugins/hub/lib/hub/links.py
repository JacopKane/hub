"""Links and copies in the tools' folders, and the record of each one.

    ~/.claude/agents/hub-<id>.md        link to parts/<id>/agent.md or its built copy
    ~/.claude/skills/hub-<id>/          link to parts/<id>/skill/ or its built copy
    ~/.claude/output-styles/hub-style.md  link to parts/<id>/style.md or its built copy
    ~/.cursor/plugins/local/hub/        hub's own folder, its hooks call this copy
    ~/.cursor/agents/hub-<id>.md        a copy with Cursor's model name

Every entry goes into ~/.local/state/hubctl/links.json with the part, the
kind and the type (link, file or folder). Hub changes or removes only an
entry it recorded and that is still as hub left it. A path that is in the way
and is not hub's own stays as it is, and apply names it in one line.

When the user has an agent of the same plain name, such as builder.md, hub
makes no link, says why in one line, and offers to switch. Cursor reads both
~/.cursor/agents and ~/.claude/agents, so a user's agent of the same name in
either keeps hub from writing its Cursor copy. Every clash goes into
clashes.json, and hub's doctor names it. Taking the offer
moves the user's file to ~/.local/state/hubctl/held/agents/, and undo moves
it back. A hub link the user deleted turns its part off, pinned, as if the
user had turned it off. An old terminal link from an earlier hub is removed
with the rest of the record.

hub saves the record before and after each step, so an apply that stops
partway leaves a trace of what it was doing. Before it makes or changes an
entry, it saves the entry with state "planned", plus the entry before it if
there was one. Before it removes an entry, it saves it with state "removing".
The next apply or uninstall settles each marked entry first, by the same rule
as is_own. A planned link is hub's only while it points at the target hub
planned or had before, inside that target's root. Any other path drops out of
the record and stays as it is.
"""
import hashlib
import importlib.util
import os
import shutil

from . import build, files, history, settings

RECORD = "links.json"
OFFERS = "switch-offers.json"
CLASHES = "clashes.json"
RELOAD_SKILLS = "reload-skills"
AGENTS_FOLDER_MADE = "agents-folder-made.json"
PREFIX = "hub-"
PINNED_KINDS = ("agent", "skill", "style", "bin")
STYLE_FILE = "hub-style.md"
# The file each shell reads at start, by the name of $SHELL.
SHELL_FILES = {"zsh": "~/.zshrc", "bash": "~/.bashrc", "fish": "~/.config/fish/config.fish"}


class LinkError(ValueError):
    pass


def claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(files.home(), ".claude")


def cursor_dir():
    return os.path.join(files.home(), ".cursor")


def agent_path(part_id):
    return os.path.join(claude_dir(), "agents", f"{PREFIX}{part_id}.md")


def skill_path(part_id):
    return os.path.join(claude_dir(), "skills", f"{PREFIX}{part_id}")


def style_path():
    return os.path.join(claude_dir(), "output-styles", STYLE_FILE)


def built_path(kind, part_id):
    if kind == "agent":
        return files.state_path(os.path.join("built", "agents", f"{part_id}.md"))
    if kind == "style":
        return files.state_path(os.path.join("built", "output-styles", STYLE_FILE))
    return files.state_path(os.path.join("built", "skills", f"{PREFIX}{part_id}"))


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_record():
    return files.read_json(files.state_path(RECORD), {"links": []})["links"]


def write_record(entries):
    files.write_json(files.state_path(RECORD), {"links": sorted(entries, key=lambda entry: entry["path"])})


def inside(path, folder):
    return bool(folder) and path.startswith(folder.rstrip(os.sep) + os.sep)


def file_sha(path):
    """The sha of a plain file's text, or None for a link, a folder, a missing or unreadable file."""
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        text = files.read_text(path)
    except (OSError, UnicodeDecodeError):
        return None
    return None if text is None else sha(text)


def link_on_the_way(folder, name):
    """Whether the file name inside folder, or a folder between them, is a link."""
    path = folder
    for part in name.split("/"):
        path = os.path.join(path, part)
        if os.path.islink(path):
            return True
    return False


def folder_file_sha(folder, name):
    """The sha of a plain file inside a folder of hub's, or None when it is not one or a link is on the way."""
    return None if link_on_the_way(folder, name) else file_sha(os.path.join(folder, name))


def is_own(entry):
    """Whether a recorded entry is still as hub left it.

    A link is hub's only when it still points at the recorded target, inside
    the recorded root, which is hub's install folder or its state folder. A
    file is hub's only when its text has the recorded sha. A folder is hub's
    when it is a real folder; inside it, only the files the record names with
    their sha are hub's.
    """
    path = entry["path"]
    if entry["type"] == "link":
        target = entry.get("target")
        if not os.path.islink(path) or not inside(target or "", entry.get("root")):
            return False
        return os.readlink(path) == target
    if os.path.islink(path):
        return False
    if entry["type"] == "file":
        return file_sha(path) is not None and file_sha(path) == entry.get("sha")
    return os.path.isdir(path) and isinstance(entry.get("files"), dict)


def bare(entry):
    return {key: value for key, value in entry.items() if key not in ("state", "before")}


def settle(entry):
    """The plain record entry a marked one stands for now, or None when nothing of hub's is there."""
    state, before = entry.get("state"), entry.get("before")
    if not state:
        return entry
    entry = bare(entry)
    if state == "removing":
        return entry if is_own(entry) else None
    if not os.path.lexists(entry["path"]):
        return before
    if entry["type"] == "folder":
        if os.path.islink(entry["path"]) or not os.path.isdir(entry["path"]):
            return None
        planned, known = entry.get("files") or {}, (before or {}).get("files") or {}
        found = {}
        for name in sorted(set(planned) | set(known)):
            actual = folder_file_sha(entry["path"], name)
            if actual is not None and actual in (planned.get(name), known.get(name)):
                found[name] = actual
        return dict(entry, files=found)
    return next((candidate for candidate in (entry, before) if candidate and is_own(candidate)), None)


def repair():
    """Settle every entry a stopped apply left marked, save the record, and return it."""
    entries = read_record()
    if not any("state" in entry for entry in entries):
        return entries
    settled = [entry for entry in map(settle, entries) if entry is not None]
    write_record(settled)
    return settled


def plan(entry, before):
    """The record line saved before a step makes or changes an entry."""
    record = {key: entry[key] for key in ("path", "part", "kind", "type")}
    if entry["type"] == "link":
        record.update(target=entry["target"], root=link_root(entry))
    elif entry["type"] == "file":
        record["sha"] = sha(entry["content"])
    else:
        record["files"] = {name: sha(text) for name, text in entry["content"].items()}
    record["state"] = "planned"
    if before is not None:
        record["before"] = before
    return record


def remove_empty_folders(root, names):
    """Remove the folders under root that the named files sat in, deepest first, while they are empty."""
    folders = {root}
    for name in names:
        parent = os.path.dirname(os.path.join(root, name))
        while inside(parent, root):
            folders.add(parent)
            parent = os.path.dirname(parent)
    for folder in sorted(folders, key=len, reverse=True):
        try:
            os.rmdir(folder)
        except OSError:
            pass


def remove(entry, lines):
    """Remove an entry that is hub's. In a folder, only the recorded files that are unchanged go."""
    path = entry["path"]
    if entry["type"] != "folder":
        os.unlink(path)
        return
    recorded = entry.get("files") or {}
    for name, known in sorted(recorded.items()):
        if folder_file_sha(path, name) == known:
            os.unlink(os.path.join(path, name))
    remove_empty_folders(path, recorded)
    if os.path.lexists(path):
        lines.append(f"hub left {path} with the files hub did not make in it.")


def replace_link(path, target):
    """Point a link at target through a new link renamed over the old one, so the link itself is new."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = os.path.join(os.path.dirname(path), f".{PREFIX}{os.urandom(4).hex()}.tmp")
    os.symlink(target, temp)
    try:
        os.replace(temp, path)
    except OSError:
        os.unlink(temp)
        raise


def pin_deleted(lines):
    """Turn off, pinned, each part whose recorded agent or skill link the user deleted.

    It settles a stopped apply's marks first. So a link that hub was
    removing, or had not made yet, never counts as one the user deleted.
    """
    entries, kept, gone = repair(), [], []
    for entry in entries:
        if entry["kind"] in PINNED_KINDS and not os.path.lexists(entry["path"]):
            gone.append(entry)
        else:
            kept.append(entry)
    if not gone:
        return
    path = files.user_settings_path()
    data = settings.read(path) or settings.empty()
    for entry in gone:
        stored = settings.entry(data, entry["part"])
        settings.put(data, entry["part"], "off", "user")
        history.add(entry["part"], "user", stored["value"] if stored else "on", "off", "user", "apply",
                    f"{entry['path']} was deleted")
        lines.append(f"{entry['path']} was deleted, so {entry['part']} is now off. "
                     f"Say \"turn on {entry['part']}\" to bring it back.")
    settings.write(path, data)
    write_record(kept)


def user_agents(folder=None):
    """{plain name: path} of the user's own agents in a folder, by file stem and by name.

    The folder is ~/.claude/agents unless one is given.
    """
    folder = folder or os.path.join(claude_dir(), "agents")
    found = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return found
    for name in names:
        path = os.path.join(folder, name)
        if not name.endswith(".md") or name.startswith((PREFIX, ".")) or not os.path.isfile(path):
            continue
        found.setdefault(name[:-3], path)
        try:
            text = files.read_text(path) or ""
        except (OSError, UnicodeDecodeError):
            continue
        plain = build.front_matter(text).get("name")
        if plain:
            found.setdefault(plain, path)
    return found


def load_cursor_handler():
    path = os.path.join(files.PARTS_DIR, "cursor_link", "handler.py")
    spec = importlib.util.spec_from_file_location("hub_part_cursor_link", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cursor_model(values, shipped, effort_map):
    models = dict(item.split("=", 1) for item in effort_map or [] if "=" in item)
    model = values.get("model") or shipped.get("model")
    if not model:
        return None
    mapped = models.get(model, model)
    effort = values.get("effort")
    return f"{mapped}[effort={effort}]" if effort else mapped


def wanted(tree, resolved, lines):
    """The entries this apply should leave in place, and the offers. Builds the copies first."""
    claude = resolved["tools"]["claude_code"]["flags"]
    cursor = resolved["tools"]["cursor"]["flags"]
    entries, offers, listing, found = [], [], {}, []
    clashes = user_agents()
    cursor_clashes = dict(clashes, **user_agents(os.path.join(cursor_dir(), "agents")))
    for part in tree.parts:
        folder = os.path.join(files.PARTS_DIR, part.id)
        if part.kind == "agent" and os.path.isfile(os.path.join(folder, "agent.md")):
            shipped = os.path.join(folder, "agent.md")
            text = files.read_text(shipped)
            if claude[part.id]["runs"]:
                link = agent_path(part.id)
                if part.id in clashes:
                    offers.append({"part": part.id, "path": clashes[part.id], "link": link})
                    found.append({"part": part.id, "tool": "claude_code", "path": clashes[part.id], "skipped": link})
                    lines.append(f"hub did not make {link}, because your agent {clashes[part.id]} has the "
                                 f"name {part.id} too. Say \"show hub's offers\" to switch.")
                else:
                    built, changed = build.build(text, part, claude, shipped)
                    entries.append(copy_entry("agent", part, link, shipped, built, changed))
                    listing[part.id] = listing_cost(built)
            copies = cursor[part.id]["runs"] and cursor.get("cursor_link.agent_copies", {}).get("runs")
            path = os.path.join(cursor_dir(), "agents", f"{PREFIX}{part.id}.md")
            if copies and part.id in cursor_clashes:
                found.append({"part": part.id, "tool": "cursor", "path": cursor_clashes[part.id], "skipped": path})
                lines.append(f"hub did not write {path}, because your agent {cursor_clashes[part.id]} has the "
                             f"name {part.id} too, and Cursor reads it.")
            elif copies:
                built, _changed = build.build(text, part, cursor, shipped)
                lines_, body = build.split(built)
                head = build.keep_only(lines_, ("name", "description"))
                model = cursor_model(build.option_values(part, cursor), build.front_matter(text),
                                     cursor["cursor_link.agent_copies.effort_map"]["value"])
                if model:
                    head.append(f"model: {model}")
                entries.append({"path": path, "part": part.id, "kind": "cursor_agent", "type": "file",
                                "content": build.join(head, body)})
        if part.kind == "skill" and claude[part.id]["runs"] and os.path.isdir(os.path.join(folder, "skill")):
            shipped = os.path.join(folder, "skill")
            text = files.read_text(os.path.join(shipped, "SKILL.md")) or ""
            built, changed = build.build(text, part, claude, os.path.join(shipped, "SKILL.md"))
            others, others_changed = skill_files(shipped, claude)
            entry = copy_entry("skill", part, skill_path(part.id), shipped, built, changed or others_changed)
            if "built" in entry:
                entry["built_files"] = others
            entries.append(entry)
            listing[part.id] = listing_cost(built)
        if part.kind == "style" and claude[part.id]["runs"] and os.path.isfile(os.path.join(folder, "style.md")):
            shipped = os.path.join(folder, "style.md")
            built, changed = build.build(files.read_text(shipped) or "", part, claude, shipped)
            entries.append(copy_entry("style", part, style_path(), shipped, built, changed))
    if cursor.get("cursor_link.plugin", {}).get("runs"):
        rendered = load_cursor_handler().plugin_files(files.PLUGIN_ROOT, files.PLUGIN_ROOT)
        entries.append({"path": os.path.join(cursor_dir(), "plugins", "local", "hub"), "part": "cursor_link",
                        "kind": "cursor_plugin", "type": "folder", "content": rendered})
    files.write_json(files.state_path(CLASHES), {"clashes": found})
    return entries, offers, listing


def skill_files(shipped, flags):
    """({path in the skill: built text}, whether one dropped a section) for every .md file but SKILL.md."""
    built, dropped = {}, False
    for root, _folders, names in os.walk(shipped):
        for name in sorted(names):
            path = os.path.join(root, name)
            relative = os.path.relpath(path, shipped)
            if not name.endswith(".md") or relative == "SKILL.md":
                continue
            built[relative], changed = build.body_only(files.read_text(path) or "", flags, path)
            dropped = dropped or changed
    return built, dropped


def clashes():
    return files.read_json(files.state_path(CLASHES), {"clashes": []})["clashes"]


def listing_cost(text):
    """The characters a tool lists for an agent or skill: its name and description."""
    head = build.front_matter(text)
    return len(f"{head.get('name', '')}: {head.get('description', '')}")


def copy_entry(kind, part, path, shipped, built, changed):
    entry = {"path": path, "part": part.id, "kind": kind, "type": "link", "target": shipped}
    if changed:
        entry.update(target=built_path(kind, part.id), built=built, shipped=shipped)
    return entry


def write_built(entry):
    """Write a built copy. Returns whether its text changed."""
    target = entry["target"]
    if entry["kind"] in ("agent", "style"):
        if files.read_text(target) == entry["built"]:
            return False
        files.write_text(target, entry["built"])
        return True
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(entry["shipped"], target, symlinks=True)
    for relative, text in dict(entry.get("built_files") or {}, **{"SKILL.md": entry["built"]}).items():
        path = os.path.join(target, relative)
        os.unlink(path)
        files.write_text(path, text)
    return True


def link_root(entry):
    """The folder a link's target must stay inside: hub's state folder for a built copy, else the install."""
    return files.state_dir() if "built" in entry else files.PLUGIN_ROOT


def put(entry, before, lines, blocked):
    """Make one wanted entry over nothing or over hub's own entry before.

    Returns the record line, and whether a link changed. In a folder, a file
    that is there and is not hub's stays as it is, with one line, and a link
    or a folder where a file belongs is added to blocked.
    """
    path, record = entry["path"], {key: entry[key] for key in ("path", "part", "kind", "type")}
    if entry["type"] == "link":
        rebuilt = write_built(entry) if "built" in entry else False
        record.update(target=entry["target"], root=link_root(entry))
        if rebuilt or not os.path.islink(path) or os.readlink(path) != entry["target"]:
            replace_link(path, entry["target"])
            return record, True
        return record, False
    if entry["type"] == "file":
        record["sha"] = sha(entry["content"])
        if files.read_text(path) != entry["content"]:
            files.write_text(path, entry["content"])
        return record, False
    os.makedirs(path, exist_ok=True)
    known = (before or {}).get("files") or {}
    record["files"] = {}
    for name, text in sorted(entry["content"].items()):
        target = os.path.join(path, name)
        now = folder_file_sha(path, name)
        linked = link_on_the_way(path, name) or (os.path.lexists(target) and now is None)
        if linked or (os.path.lexists(target) and now != known.get(name) and now != sha(text)):
            lines.append(f"hub left {target} as it is, because it is not a file hub made.")
            if linked:
                blocked.append({"part": entry["part"], "tool": "cursor", "path": target, "kind": "not_a_file"})
            continue
        if now != sha(text):
            files.write_text(target, text)
        record["files"][name] = sha(text)
    for name, old in sorted(known.items()):
        if name not in entry["content"] and folder_file_sha(path, name) == old:
            os.unlink(os.path.join(path, name))
    return record, False


def clear_stale_built(entries):
    keep = {entry["target"] for entry in entries if "built" in entry}
    for kind in ("agents", "skills", "output-styles"):
        folder = files.state_path(os.path.join("built", kind))
        try:
            names = os.listdir(folder)
        except FileNotFoundError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if path not in keep:
                shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)


def reconcile(tree, resolved, lines):
    """Make every wanted link and copy, remove hub's own that are no longer wanted.

    Returns the listing cost of each linked agent and skill.
    """
    entries, offers, listing = wanted(tree, resolved, lines)
    old = {entry["path"]: entry for entry in repair()}
    record, skills_changed, blocked = [], False, []

    def save(*marked):
        write_record(record + list(old.values()) + list(marked))

    if any(entry["kind"] == "agent" for entry in entries):
        folder = os.path.join(claude_dir(), "agents")
        if not os.path.isdir(folder):
            os.makedirs(folder)
            files.write_json(files.state_path(AGENTS_FOLDER_MADE), {"made": folder})
    for entry in entries:
        path, before = entry["path"], old.pop(entry["path"], None)
        if before is None and os.path.lexists(path):
            lines.append(f"hub left {path} as it is, because it is not a file hub made.")
            listing.pop(entry["part"], None)
            continue
        if before is not None and not is_own(before):
            lines.append(f"hub left {path} as it is, because it changed since hub made it.")
            listing.pop(entry["part"], None)
            continue
        save(plan(entry, before))
        made, changed = put(entry, before, lines, blocked)
        record.append(made)
        save()
        skills_changed = skills_changed or (changed and entry["kind"] == "skill")
    for path, entry in list(old.items()):
        if is_own(entry):
            old[path] = dict(entry, state="removing")
            save()
            remove(entry, lines)
            skills_changed = skills_changed or entry["kind"] == "skill"
        elif os.path.lexists(path):
            lines.append(f"hub left {path} as it is, because it changed since hub made it.")
        del old[path]
        save()
    clear_stale_built(entries)
    write_record(record)
    if blocked:
        files.write_json(files.state_path(CLASHES), {"clashes": clashes() + blocked})
    files.write_json(files.state_path(OFFERS), {"offers": offers})
    if skills_changed:
        files.write_text(files.state_path(RELOAD_SKILLS), "1\n")
    return listing


def offers():
    return files.read_json(files.state_path(OFFERS), {"offers": []})["offers"]


def held_path(source):
    folder = files.state_path(os.path.join("held", "agents"))
    base, extension = os.path.splitext(os.path.basename(source))
    path, number = os.path.join(folder, base + extension), 1
    while os.path.lexists(path):
        path, number = os.path.join(folder, f"{base}-{number}{extension}"), number + 1
    return path


def take(part_id, door="step"):
    """Move the user's agent of the same name to the held folder, so hub's link can be made."""
    offer = next((offer for offer in offers() if offer["part"] == part_id), None)
    if offer is None:
        raise LinkError(f"there is no offer for {part_id}. Say \"show hub's offers\" to list them.")
    source = offer["path"]
    if not os.path.lexists(source):
        raise LinkError(f"{source} is gone, so there is nothing to hold. Say \"apply hub's settings\".")
    target = held_path(source)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(source, target)
    return history.add_action("take_offer", door, part=part_id, source=source, held=target)


def undo_take(line):
    """Move a held file back where it was."""
    source, target = line["source"], line["held"]
    if os.path.lexists(source):
        raise LinkError(f"{source} exists again, so hub can not move {target} back. Move it by hand.")
    if not os.path.lexists(target):
        raise LinkError(f"{target} is gone, so there is nothing to move back.")
    os.makedirs(os.path.dirname(source), exist_ok=True)
    shutil.move(target, source)
