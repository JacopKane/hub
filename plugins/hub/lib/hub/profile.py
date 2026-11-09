"""The situation profile, ~/.config/hubctl/profile.json.

    {"version": 1, "this_machine": "laptop",
     "machines": {"laptop": {"host", "os", "use", "sign_in", "tools", "terminal", "synced_folders"}},
     "repos": {"~/code/app": {"remote", "ci", "compose"}},
     "mcp_servers": [...], "own_rules": [...], "own_agents": [...], "pins": ["machines.laptop.use"]}

A fact the user typed is pinned, and a scan or the tuner never changes it.
Conditions in part.json read the profile through view(), which also puts
this machine's facts at the top, such as profile.tools.cursor, and counts
the machines.
"""
import copy

from . import files

VERSION = 1
MACHINE_FACTS = ("host", "os", "tools", "terminal", "sign_in", "synced_folders")
LIST_FACTS = ("mcp_servers", "own_rules", "own_agents")


class ProfileError(ValueError):
    pass


def read():
    data = files.read_json(files.profile_path(), {}) or {}
    return data if isinstance(data, dict) else {}


def write(data):
    files.write_json(files.profile_path(), dict(data, version=VERSION))


def get(data, path):
    node = data
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def put(data, path, value):
    keys = path.split(".")
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise ProfileError(f"{path} goes through a value that is not a group")
    if value is None:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value


def pinned(data, path):
    return any(path == pin or path.startswith(pin + ".") for pin in data.get("pins", []))


def merge(data, path, value):
    """Set a fact unless the user pinned it or a fact above it. Returns True when it changed."""
    if pinned(data, path) or get(data, path) == value:
        return False
    put(data, path, value)
    return True


def from_scan(found, data=None, name=None):
    """The profile with the scan's facts merged in. Pinned facts stay."""
    data = copy.deepcopy(data if data is not None else read())
    machine = name or data.get("this_machine") or found["host"]
    if not pinned(data, "this_machine"):
        data["this_machine"] = machine
    for fact in MACHINE_FACTS:
        merge(data, f"machines.{machine}.{fact}", found.get(fact))
    for fact in LIST_FACTS:
        merge(data, fact, found.get(fact, []))
    repo = found.get("repo")
    if repo:
        facts = {key: value for key, value in repo.items() if key != "path"}
        for key, value in facts.items():
            data.setdefault("repos", {}).setdefault(repo["path"], {})
            if not pinned(data, f"repos.{repo['path']}"):
                data["repos"][repo["path"]][key] = value
    return data


def set_fact(path, value, by="user"):
    """Write one fact. A fact the user sets is pinned. Returns (old, new)."""
    data = read()
    if by != "user" and pinned(data, path):
        raise ProfileError(f"the user set {path}, so only the user changes it")
    old = get(data, path)
    put(data, path, value)
    if by == "user" and path not in data.setdefault("pins", []):
        data["pins"].append(path)
    write(data)
    return old, value


def machine(data):
    return (data.get("machines") or {}).get(data.get("this_machine") or "", {})


def view(data):
    """The profile as conditions read it: this machine's facts on top, and the machine count."""
    shown = dict(data)
    for key, value in machine(data).items():
        shown.setdefault(key, value)
    shown.setdefault("machine_count", len(data.get("machines") or {}))
    return shown
