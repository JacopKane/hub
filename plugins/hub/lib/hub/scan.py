"""What hub can learn about this machine in under a second, with no model.

Programs that may be slow, such as `claude auth status`, run in threads with
a time limit, so one stuck program can not stall a session start. The scan
keeps a short hash of the claude.ai account, never the email or the org id.
"""
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time

from . import files, settings

SLOW_CALL_SECONDS = 0.6
VERSION = re.compile(r"\d+(?:\.\d+)+")
SYNCED_FOLDERS = {
    "iCloud Drive": os.path.join("Library", "Mobile Documents", "com~apple~CloudDocs"),
    "Dropbox": "Dropbox",
    "Syncthing": os.path.join(".config", "syncthing"),
    "Google Drive": os.path.join("Library", "CloudStorage"),
}
CI_FILES = {"github-actions": os.path.join(".github", "workflows"), "gitlab-ci": ".gitlab-ci.yml",
            "circleci": ".circleci"}
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")


def program(args, cwd=None):
    """A program's output, or None when it is missing, fails to start or is too slow."""
    if shutil.which(args[0]) is None:
        return None
    try:
        done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=SLOW_CALL_SECONDS,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout


def version(text):
    found = VERSION.search(text or "")
    return found.group(0) if found else None


def account_hash(status):
    """A short hash that tells two machines on one claude.ai account apart from two on different ones."""
    email, org = status.get("email"), status.get("orgId")
    if not email and not org:
        return None
    return hashlib.sha256(f"{email or ''}\0{org or ''}".encode()).hexdigest()[:12]


def sign_in(text):
    try:
        status = json.loads(text or "")
    except ValueError:
        return {"method": "unknown"}
    found = {"method": status.get("authMethod", "unknown")}
    account = account_hash(status)
    if account:
        found["account_hash"] = account
    return found


def terminal():
    if os.environ.get("TMUX"):
        return "tmux"
    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return "iterm"
    return "plain"


def system():
    name = platform.system()
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(name, name.lower())


def repo_facts(cwd):
    repo = settings.find_repo(cwd)
    if repo is None:
        return None, None
    remote = (program(["git", "remote"], cwd=repo) or "").split()
    facts = {"remote": bool(remote)}
    ci = [name for name, path in CI_FILES.items() if os.path.exists(os.path.join(repo, path))]
    if ci:
        facts["ci"] = ci[0]
    facts["compose"] = any(os.path.exists(os.path.join(repo, name)) for name in COMPOSE_FILES)
    return repo, facts


def mcp_servers(home):
    data = files.read_json(os.path.join(home, ".claude.json"), {}) or {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return sorted(servers) if isinstance(servers, dict) else []


def own_agents(home):
    folder = os.path.join(home, ".claude", "agents")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(name[:-3] for name in names if name.endswith(".md") and not name.startswith("hub-")
                  and not os.path.islink(os.path.join(folder, name)))


def own_rules(home):
    found = [os.path.join("~", ".claude", "CLAUDE.md")] if os.path.isfile(
        os.path.join(home, ".claude", "CLAUDE.md")) else []
    folder = os.path.join(home, ".claude", "rules")
    if os.path.isdir(folder):
        found += [os.path.join("~", ".claude", "rules", name) for name in sorted(os.listdir(folder))
                  if name.endswith(".md")]
    return found


def run(cwd=None):
    """The scan as a dict, in the shape profile.from_scan reads."""
    home = files.home()
    cwd = cwd or os.getcwd()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        claude_version = pool.submit(program, ["claude", "--version"])
        auth = pool.submit(program, ["claude", "auth", "status"])
        cursor_version = pool.submit(program, ["cursor", "--version"])
        git_user = pool.submit(program, ["git", "config", "user.name"])
        repo = pool.submit(repo_facts, cwd)
        cursor = os.path.isdir(os.path.join(home, ".cursor")) or cursor_version.result() is not None
        tools = {"claude_code": version(claude_version.result())}
        if cursor:
            tools["cursor"] = version(cursor_version.result()) or "found"
        repo_path, repo_info = repo.result()
        found = {
            "host": socket.gethostname().split(".")[0],
            "os": system(),
            "tools": tools,
            "terminal": terminal(),
            "sign_in": sign_in(auth.result()),
            "git_user": bool((git_user.result() or "").strip()),
            "time_zone": time.strftime("%Z"),
            "synced_folders": [name for name, path in SYNCED_FOLDERS.items()
                               if os.path.exists(os.path.join(home, path))],
            "mcp_servers": mcp_servers(home),
            "own_rules": own_rules(home),
            "own_agents": own_agents(home),
        }
    if repo_path:
        found["repo"] = dict(repo_info, path=repo_path.replace(home, "~", 1))
    return found


def summary(found):
    """The scan as short lines for a chat's start text. It holds no email, id or path outside the home."""
    tools = ", ".join(f"{name} {value}" for name, value in found["tools"].items() if value) or "none found"
    lines = [f"Tools: {tools}", f"Terminal: {found['terminal']}", f"System: {found['os']}",
             f"Sign-in: {found['sign_in']['method']}", f"Time zone: {found['time_zone']}"]
    repo = found.get("repo")
    if repo:
        lines.append(f"Repo: {repo['path']}, remote {'yes' if repo['remote'] else 'no'}, "
                     f"CI {repo.get('ci', 'none')}, compose file {'yes' if repo['compose'] else 'no'}")
    for label, key in (("Synced folders", "synced_folders"), ("MCP servers", "mcp_servers"),
                       ("Your own rules", "own_rules"), ("Your own agents", "own_agents")):
        lines.append(f"{label}: {', '.join(found[key]) or 'none'}")
    return "\n".join(lines)
