"""Apply the run rule and write resolved.json, one section per tool.

A flag runs only when it, every parent and every need are on, in a tool that
runs it. A list or text flag counts as on when it is not empty. A behavior
under a mode part takes off, on or a mode, and the part's mode caps it, so
on follows the part. Relevance only hides a flag, it never turns it off.

A project file comes with every clone, and so does a local file that git
tracks, so neither may lower a privacy flag: its value is skipped when it is
weaker than the scopes below it. A guarded
list, such as the private words, adds up over every scope.

Apply also keeps seen.json. A part that is new in an update goes to
offers.json and stays off. A new behavior of a part that is on takes its
default and goes to update-summary.json.
"""
import calendar
import os
import time

from . import conditions, files, settings, tree as tree_module

PATH_FLAGS = {"core.private_words_files"}
RESOLVED = "resolved.json"
GATES_FAMILY = "gates"  # parts that check a call; they run after every other part
SELF_PROTECTION = "self_protection"  # the first of the gates


TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def epoch():
    """Seconds since 1970 in UTC. Tests fix the clock with HUB_TEST_NOW, an ISO UTC time."""
    fixed = os.environ.get("HUB_TEST_NOW")
    if fixed:
        return calendar.timegm(time.strptime(fixed, TIME_FORMAT))
    return time.time()


def now():
    return time.strftime(TIME_FORMAT, time.gmtime(epoch()))


def seconds(stamp):
    """An ISO UTC time from now() as seconds since 1970."""
    return calendar.timegm(time.strptime(stamp, TIME_FORMAT))


def load_profile():
    """The profile as conditions read it, with this machine's facts on top (profile.view)."""
    from . import profile
    return profile.view(profile.read())


def facts_for(tool, profile):
    repos = profile.get("repos")
    with_remote = None
    if isinstance(repos, dict):
        with_remote = sum(1 for repo in repos.values() if isinstance(repo, dict) and repo.get("remote"))
    return {"tool": tool, "mods_run": tool == "claude_code", "claude_code_version": None,
            "repos_with_remote": with_remote}


def default_value(node, profile, facts):
    if node.default_expr is not None:
        return conditions.evaluate(node.default_expr, profile, facts)
    if "default" in node.data:
        return node.data["default"]
    return {"list": [], "text": "", "number": 0}.get(node.type, "on" if node.parent else "off")


def is_on(node, value):
    if node.type in ("list", "text"):
        return bool(value)
    if node.type == "number":
        return value is not None
    return value not in ("off", None, False)


def strength(node, value):
    """How strict a value of a guarded flag is, or None when values do not compare."""
    if value in ("off", None, False):
        return 0
    if node.type == "mode":
        return node.values.index(value) if value in node.values else None
    if node.type == "switch":
        owner = node.mode_owner
        if owner is None or value == "on":
            return len(owner.values) if owner else 1  # on follows the part, so it is the strictest
        return owner.values.index(value) if value in owner.values else None
    return None


def lowers(node, new, old):
    """Whether new is a weaker setting than old for a privacy flag."""
    if node.privacy == "loosens":
        return new != "off" and old == "off"
    if node.privacy != "guard" or new == old:
        return False
    new_strength, old_strength = strength(node, new), strength(node, old)
    if new_strength is None or old_strength is None:
        return True  # a change that can not be compared counts as weaker
    return new_strength < old_strength


def merged(old, new):
    old = old if isinstance(old, list) else []
    return old + [item for item in (new if isinstance(new, list) else []) if item not in old]


def effective(node, value, values):
    """The value after the part's mode caps it."""
    owner = node.mode_owner
    if owner is None or node.type != "switch":
        return value
    cap = values.get(owner.id)
    if value == "off" or cap in ("off", None):
        return "off"
    if value == "on":
        return cap
    ladder = owner.values
    if value in ladder and cap in ladder:
        return ladder[min(ladder.index(value), ladder.index(cap))]
    return value


def signature(extra_paths=()):
    paths = tree_module.manifest_paths(files.PARTS_DIR)
    paths += [files.user_settings_path(), files.profile_path(), *extra_paths]
    return {"plugin": files.PLUGIN_ROOT, "files": {path: files.stamp(path) for path in paths}}


def resolve(tree, layer_list, profile, offers=()):
    """Resolve every flag for every tool. Returns {tool: {flag id: record}}."""
    result = {}
    shared = {name for name, path, _data in layer_list if settings.shared(name, path)}
    for tool in tree_module.TOOLS:
        facts = facts_for(tool, profile)
        values, records = {}, {}
        for node in tree.nodes.values():
            if node.parent is None and node.id in offers:
                value, by, scope = "off", "offer", "shipped"
            else:
                value, by, scope = default_value(node, profile, facts), "default", "shipped"
            for name, _path, data in layer_list:
                stored = settings.entry(data, node.id)
                if stored is None:
                    continue
                if node.type == "list" and node.privacy == "guard":
                    value = merged(value, stored["value"])
                elif name in shared and lowers(node, stored["value"], value):
                    continue
                else:
                    value = stored["value"]
                by, scope = stored.get("by", "user"), name
            if node.id in PATH_FLAGS and isinstance(value, list):
                value = [os.path.expanduser(path) for path in value]
            relevant, hidden_because = True, None
            if node.relevant_expr is not None and not conditions.evaluate(node.relevant_expr, profile, facts):
                relevant, hidden_because = False, f"{node.id}: {node.data['relevant_if']} is false"
            parent = records.get(node.parent.id) if node.parent else None
            if parent and not parent["relevant"]:
                relevant, hidden_because = False, parent["hidden_because"]
            values[node.id] = effective(node, value, values)
            records[node.id] = {"value": values[node.id], "set": value, "by": by, "scope": scope,
                                "relevant": relevant, "hidden_because": hidden_because}
        runs_cache = {}

        def runs(flag_id, visiting=()):
            if flag_id in runs_cache:
                return runs_cache[flag_id]
            if flag_id in visiting:
                return False
            node = tree.nodes[flag_id]
            path = visiting + (flag_id,)
            answer = (tool in node.runs_in and is_on(node, values[flag_id])
                      and (node.parent is None or runs(node.parent.id, path))
                      and all(runs(need, path) for need in node.needs)
                      and (not node.needs_any or any(runs(need, path) for need in node.needs_any)))
            runs_cache[flag_id] = answer
            return answer

        for flag_id, record in records.items():
            record["runs"] = runs(flag_id)
        handlers = {}
        # The gates run last, so they check a call after every part that may rewrite it.
        # self_protection runs first among them, so a call that touches hub gets its answer.
        for part in sorted(tree.parts, key=lambda part: (part.family == GATES_FAMILY, part.id != SELF_PROTECTION)):
            if not records[part.id]["runs"]:
                continue
            for event, spec in part.handlers.items():
                if not tree_module.handler_runs_in(spec, tool):
                    continue
                handlers.setdefault(event, []).append({"part": part.id, "tools": spec.get("tools", [])})
        result[tool] = {"flags": records, "handlers": handlers}
    return result


def new_offers(tree, seen, offered):
    """Ids of the parts an apply offers now: new since seen.json, not offered yet and not beta."""
    if seen is None:
        return []
    return [part.id for part in tree.parts
            if part.id not in seen["parts"] and part.id not in offered and tree.nodes[part.id].stage != tree_module.BETA]


def in_memory(cwd=None):
    """{tool: {"flags", "handlers"}} for a working folder, as current() gives them, without applying hub.

    It reads the settings of every scope, the profile and the offers, and
    writes no state file and no link. Reading the user's settings still moves
    their folder from its old name (files.config_dir), as every hub step does.
    """
    tree = tree_module.load(files.PARTS_DIR)
    recorded = {offer["id"] for offer in files.read_json(files.state_path("offers.json"), {"offers": []})["offers"]}
    offered = recorded | set(new_offers(tree, files.read_json(files.state_path("seen.json")), recorded))
    return resolve(tree, settings.layers(cwd), load_profile(), offered)


def texts(tree):
    return {node.id: os.path.join(files.PARTS_DIR, node.part.id, node.text)
            for node in tree.nodes.values() if node.text}


def apply(tree=None, write=True):
    """Resolve the user scope and write the state files. Returns the resolved data.

    With write=False the caller writes resolved.json itself, after the files
    built from it, so a hook that finds resolved.json fresh never reads a
    half-built set.
    """
    tree = tree or tree_module.load(files.PARTS_DIR)
    seen_path, offers_path = files.state_path("seen.json"), files.state_path("offers.json")
    summary_path = files.state_path("update-summary.json")
    seen = files.read_json(seen_path)
    offers = files.read_json(offers_path, {"offers": []})
    summary = files.read_json(summary_path, {"changes": []})
    part_ids = [part.id for part in tree.parts]
    child_ids = [node.id for node in tree.nodes.values() if node.parent is not None]
    new_children = []
    if seen is not None:
        offered = {offer["id"] for offer in offers["offers"]}
        offers["offers"] += [{"id": part_id, "since": now(), "does": tree.nodes[part_id].does}
                             for part_id in new_offers(tree, seen, offered)]
        new_children = [flag_id for flag_id in child_ids
                        if flag_id not in seen["nodes"] and tree.nodes[flag_id].part.id in seen["parts"]]
    offer_ids = {offer["id"] for offer in offers["offers"]}
    user = [(scope, path, data) for scope, path, data in settings.layers() if scope == "user"]
    sig = signature()
    resolved = resolve(tree, user, load_profile(), offer_ids)
    listed = {change["id"] for change in summary["changes"]}
    for flag_id in new_children:
        record = resolved["claude_code"]["flags"][flag_id]
        if record["runs"] and flag_id not in listed:
            summary["changes"].append({"id": flag_id, "value": record["value"], "since": now()})
    data = {"version": 1, "built": now(), "signature": sig, "texts": texts(tree), "tools": resolved}
    if write:
        files.write_json(files.state_path(RESOLVED), data)
    files.write_json(offers_path, offers)
    files.write_json(summary_path, summary)
    files.write_json(seen_path, {"parts": sorted(set(part_ids) | set((seen or {}).get("parts", []))),
                                 "nodes": sorted(set(child_ids) | set((seen or {}).get("nodes", [])))})
    return data


def current(cwd=None, session=None):
    """The resolved data for a working folder.

    It reads resolved.json, and applies again first when that file is missing
    or older than a settings file or a manifest, or a recorded link is gone
    (see apply.ensure_fresh). A repo with its own .hub/ files is resolved in
    memory with those scopes on top.
    """
    from .apply import ensure_fresh  # apply imports this module
    data = ensure_fresh(session)
    repo = settings.find_repo(cwd)
    if repo is None:
        return data
    layer_list = settings.layers(cwd)
    if not any(scope != "user" for scope, _path, _data in layer_list):
        return data
    tree = tree_module.load(files.PARTS_DIR)
    offers = {offer["id"] for offer in files.read_json(files.state_path("offers.json"), {"offers": []})["offers"]}
    return dict(data, tools=resolve(tree, layer_list, load_profile(), offers))
