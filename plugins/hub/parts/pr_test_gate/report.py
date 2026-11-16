"""Test reports for the PR test gate: where they live and whether one passes.

A report is a JSON file named after the commit it tested, kept in the repo's
common git folder under pr-test-reports, so every worktree of the repo sees
it. When a commit has no report of its own, a report for a commit with the
same tree counts, so a reworded commit keeps its report.

The gate imports this file by path. Its git calls go through the gate's
run_git, which keeps them inside the hook's time. Run as a script, the file
is hub's pr-tested command, which records a report.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

FOLDER = "pr-test-reports"
TEXT_SUFFIXES = {".md", ".mdx", ".rst", ".adoc", ".po", ".pot"}
LOCALE_FOLDERS = {"locales", "locale", "translations", "i18n"}
LOCALE_SUFFIXES = {".json", ".yaml", ".yml", ".xliff", ".xlf", ".strings", ".stringsdict", ".arb", ".properties",
                   ".ftl"}
DOC_FOLDERS = {"docs", "doc"}
# Images and plain .txt files count as text only in a docs folder, since requirements.txt and the like are code.
DOC_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".txt"}


def is_text_file(path):
    """Whether a changed file counts as text or docs, so a texts report may cover it."""
    folders = path.split("/")[:-1]
    suffix = os.path.splitext(path)[1].lower()
    if suffix in TEXT_SUFFIXES:
        return True
    if suffix in LOCALE_SUFFIXES and LOCALE_FOLDERS.intersection(folders):
        return True
    return suffix in DOC_SUFFIXES and bool(DOC_FOLDERS.intersection(folders))


def reports_folder(cwd, run_git):
    common = run_git(cwd, "rev-parse", "--git-common-dir")
    return os.path.join(cwd, common, FOLDER) if common else None


def find(cwd, sha, run_git):
    """(the report for the commit, or for a commit with its tree, or None; whether it was not valid JSON)."""
    folder = reports_folder(cwd, run_git)
    if folder is None:
        return None, False
    try:
        with open(os.path.join(folder, sha + ".json"), encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return same_tree(folder, cwd, sha, run_git), False
    try:
        report = json.loads(text)
    except ValueError:
        return None, True
    return (report, False) if isinstance(report, dict) else (None, True)


def same_tree(folder, cwd, sha, run_git):
    try:
        names = sorted(name for name in os.listdir(folder) if name.endswith(".json"))
    except FileNotFoundError:
        return None
    if not names:
        return None
    tree = run_git(cwd, "rev-parse", "--verify", "--quiet", sha + "^{tree}")
    if not tree:
        return None
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                report = json.load(handle)
        except (FileNotFoundError, ValueError):
            continue
        if isinstance(report, dict) and report.get("tree") == tree:
            return report
    return None


def failing(runs):
    """The command of the first run that did not exit 0, or None."""
    for run in runs:
        if not isinstance(run, dict) or run.get("exit") != 0:
            return run.get("command") if isinstance(run, dict) else str(run)
    return None


def problem(report, cwd, sha, run_git, bases=None):
    """(the gate's message name, its values) for what the report lacks, or None when it passes. bases, when given,
    are {branch: the commit GitHub has it at} for the branches the PR may go into. A texts report must name one of
    them, and its changes are then counted from that commit, not from the base the report saw."""
    if report.get("version") != 1 or report.get("kind") not in ("code", "texts"):
        return "report_unreadable.md", {}
    if report.get("clean") is not True:
        return "dirty.md", {}
    if report["kind"] == "texts":
        return texts_problem(report, cwd, sha, run_git, bases)
    scenarios = report.get("scenarios") or []
    if not scenarios:
        return "failed.md", {"which": "no scenario was recorded"}
    which = failing(scenarios)
    if which is not None:
        return "failed.md", {"which": which}
    smoke = report.get("smoke")
    if not isinstance(smoke, dict) or ("command" not in smoke and not str(smoke.get("none") or "").strip()):
        return "no_smoke.md", {}
    if "command" in smoke and smoke.get("exit") != 0:
        return "failed.md", {"which": smoke["command"]}
    if not report.get("not_tested"):
        return "no_not_tested.md", {}
    return None


def texts_problem(report, cwd, sha, run_git, bases=None):
    which = failing(report.get("checks") or [])
    if which is not None:
        return "failed.md", {"which": which}
    if not report.get("not_tested"):
        return "no_not_tested.md", {}
    base_branch = report["base"].get("branch") if isinstance(report.get("base"), dict) else None
    if bases is not None and base_branch not in bases:
        return "texts_base.md", {"base": base_branch, "into": " or ".join(sorted(bases)) or "a branch the gate "
                                                                                             "can not read"}
    base = (report.get("base") or {}).get("sha") if isinstance(report.get("base"), dict) else None
    if bases is not None:
        base = bases[base_branch]
        if base and run_git(cwd, "cat-file", "-e", base + "^{commit}") is None:
            return "texts_base_missing.md", {"base": base_branch, "commit": base[:7]}
    start = run_git(cwd, "merge-base", base, sha) if base else None
    if not start:
        return "report_unreadable.md", {}
    changed = run_git(cwd, "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", start, sha)
    if changed is None:
        return "report_unreadable.md", {}
    for path in changed.splitlines():
        if path and not is_text_file(path):
            return "texts_has_code.md", {"file": path}
    return None


# The pr-tested command records how a commit was tested, where the gate reads it.
# The checks and the report's shape are pure functions. Git, the runs and the file writes stay at the edges.


USAGE = ("usage: pr-tested run --scenario <command> [...] (--smoke <command> | --no-smoke <why>) "
         "--not-tested <what, why> [...] [-C <folder>]\n"
         "       pr-tested texts --base <branch> --not-tested <what, why> [...] [--check <command> ...] [-C <folder>]\n"
         "       pr-tested show [<commit>] [--body] [-C <folder>]")
TAIL_LINES = 20
KEEP_DAYS = 60
EMPTY_COMMANDS = {"", "true", ":", "exit 0", "echo"}


class Refused(Exception):
    """A run the verb can not vouch for. The message says what to change."""


# Pure parts


def tests_nothing(command):
    words = command.strip()
    return words in EMPTY_COMMANDS or words.startswith("echo ")


def option_refusals(options, kind):
    """What is wrong with the options before anything runs, as messages; empty when nothing is."""
    smoke = [] if kind != "code" or options.smoke is None else [options.smoke]
    commands = options.scenario + smoke if kind == "code" else options.check
    return ([] if any(line.strip() for line in options.not_tested)
            else ["Say what was not tested and why, with --not-tested."]) \
        + (["Give at least one --scenario command."] if kind == "code" and not options.scenario else []) \
        + (["Say why there is no smoke run, with --no-smoke."]
           if kind == "code" and options.no_smoke is not None and not options.no_smoke.strip() else []) \
        + [f"\"{command}\" tests nothing. Give the command that runs the tests." for command in commands
           if tests_nothing(command)]


def dirty_refusal(porcelain):
    if porcelain is None:
        return "git status failed, so the tree's state is unknown."
    first = next(iter(porcelain.splitlines()), "")[3:]
    return (f"The tree has changes that are not committed, such as {first}. "
            "Commit or stash them, then run the tests again.") if porcelain else None


def code_file_refusal(files):
    code = next((path for path in files if not is_text_file(path)), None)
    return None if code is None else (f"{code} is not a text file, so a texts report can not cover it. "
                                      "Record a code run with pr-tested run instead.")


def last_lines(output):
    return "\n".join(output.rstrip("\n").splitlines()[-TAIL_LINES:])


def new_report(kind, place, made_at, not_tested, runs, smoke, base=None, files=()):
    """A version 1 report. place is (head, tree, branch); runs maps scenarios or checks to their records."""
    head, tree, branch = place
    return {"version": 1, "kind": kind, "head": head, "tree": tree, "branch": branch, "clean": True,
            "made_at": made_at, "scenarios": runs.get("scenarios", []), "smoke": smoke,
            "checks": runs.get("checks", []), "base": base, "files": list(files),
            "not_tested": [line.strip() for line in not_tested if line.strip()]}


def run_line(record):
    return f"- `{record['command']}` {'passed' if record['exit'] == 0 else 'failed'} ({record['seconds']} s)"


def smoke_line(smoke):
    if not isinstance(smoke, dict):
        return []
    if "command" in smoke:
        return [f"- Smoke run `{smoke['command']}` {'passed' if smoke['exit'] == 0 else 'failed'}"]
    return [f"- No smoke run, because {smoke.get('none')}"]


def body(report):
    """The "How it was tested" part for a PR body."""
    runs = (report.get("scenarios") or []) + (report.get("checks") or [])
    return "\n".join(["### How it was tested", ""] + [run_line(record) for record in runs]
                     + smoke_line(report.get("smoke")) + ["", "Not tested:", ""]
                     + [f"- {line}" for line in report.get("not_tested") or []])


def summary(report, found):
    runs = len(report.get("scenarios") or report.get("checks") or [])
    verdict = "passes" if found is None else f"does not pass ({found[0][:-3].replace('_', ' ')})"
    return (f"{str(report.get('head'))[:7]} on {report.get('branch')}: {report.get('kind')} report, "
            f"{runs} run{'' if runs == 1 else 's'}, {verdict}")


def stale_names(entries, now):
    """The report and log names among (name, modified time) pairs that are older than KEEP_DAYS days."""
    return [name for name, modified in entries
            if name.endswith((".json", ".log")) and modified < now - KEEP_DAYS * 86400]


def base_name(base):
    return base[len("origin/"):] if base.startswith("origin/") else base


# Edges: git, the runs and the files


def git(cwd, *args):
    """git's output with its last line end taken off, or None when git fails."""
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace")
    return done.stdout.rstrip("\n") if done.returncode == 0 else None


def refuse_if(message):
    if message:
        raise Refused(message)


def porcelain(root):
    return git(root, "status", "--porcelain", "--untracked-files=all")


def run_command(command, root, log):
    """One command's record: what ran, its exit code, its seconds and the last lines it printed."""
    started = time.monotonic()
    done = subprocess.run(["sh", "-c", command], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace")
    seconds = round(time.monotonic() - started, 1)
    log.write(f"$ {command}\n{done.stdout}\n[exit {done.returncode} after {seconds} s]\n\n")
    return {"command": command, "exit": done.returncode, "seconds": seconds, "tail": last_lines(done.stdout)}


def write_whole(path, report):
    """Write the report to a temporary file beside it, then put it in place, so a reader never sees half of it."""
    handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def prune(folder):
    names = os.listdir(folder)
    entries = [(name, os.path.getmtime(os.path.join(folder, name))) for name in names]
    for name in stale_names(entries, time.time()):
        os.remove(os.path.join(folder, name))


def place_of(folder_option):
    """(the repo root, the reports folder, (HEAD, its tree, its branch)) for the folder the verb runs in."""
    cwd = os.path.abspath(folder_option or os.getcwd())
    root = git(cwd, "rev-parse", "--show-toplevel")
    refuse_if(None if root else f"{cwd} is not inside a git repo.")
    folder, head = reports_folder(root, git), git(root, "rev-parse", "--verify", "HEAD")
    refuse_if(None if folder and head else "The repo has no commit yet, so there is nothing to record a run for.")
    return root, folder, (head, git(root, "rev-parse", "HEAD^{tree}"), git(root, "rev-parse", "--abbrev-ref", "HEAD"))


def changed_since(root, base, head):
    since = git(root, "merge-base", base, head)
    files = git(root, "-c", "core.quotePath=false", "diff", "--name-only", "--no-renames", since, head) \
        if since else None
    return None if files is None else [path for path in files.splitlines() if path]


def record_run(options, kind):
    refuse_if(" ".join(option_refusals(options, kind)))
    root, folder, place = place_of(options.folder)
    head = place[0]
    refuse_if(dirty_refusal(porcelain(root)))
    base, files = None, []
    if kind == "texts":
        name = base_name(options.base)
        # The base comes from origin's local copy, so a code file is refused before anything runs, with no
        # network call. The gate never trusts this commit. For each step it counts the changes again from the
        # commit GitHub has for the base branch in the PR's repo, so a stale origin, or a fork as origin, can
        # not make a texts report count.
        sha = git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}")
        refuse_if(None if sha else f"origin/{name} does not exist. Fetch it, or name the branch the PR goes into.")
        files = changed_since(root, sha, head)
        refuse_if(f"git could not list the files changed since origin/{name}." if files is None else None)
        refuse_if(code_file_refusal(files))
        base = {"branch": name, "sha": sha}
    planned = {"checks": options.check} if kind == "texts" else \
        {"scenarios": options.scenario, "smoke": [] if options.smoke is None else [options.smoke]}
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, head + ".log"), "w", encoding="utf-8") as log:
        runs = {key: [run_command(command, root, log) for command in commands] for key, commands in planned.items()}
    smoke = None if kind == "texts" else \
        runs["smoke"][0] if options.smoke is not None else {"none": options.no_smoke.strip()}
    clean = git(root, "rev-parse", "--verify", "HEAD") == head and porcelain(root) == ""
    made_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = dict(new_report(kind, place, made_at, options.not_tested, runs, smoke, base, files), clean=clean)
    write_whole(os.path.join(folder, head + ".json"), report)
    prune(folder)
    print(body(report))
    if not clean:
        print("The tests changed the tree or HEAD while they ran, so this report does not count.", file=sys.stderr)
    return 0 if clean and problem(report, root, head, git) is None else 1


def show(options):
    cwd = os.path.abspath(options.folder or os.getcwd())
    sha = git(cwd, "rev-parse", "--verify", "--quiet", (options.commit or "HEAD") + "^{commit}")
    refuse_if(None if sha else f"{options.commit or 'HEAD'} is not a commit here.")
    report, broken = find(cwd, sha, git)
    refuse_if(None if report is not None else
              f"{sha[:7]} has {'a report that is not valid JSON' if broken else 'no test report'}. "
              "Record one with pr-tested run, or pr-tested texts for a texts-only branch.")
    print(body(report) if options.body else summary(report, problem(report, cwd, sha, git)))
    return 0


def arguments():
    parser = argparse.ArgumentParser(prog="pr-tested", usage=USAGE)
    parser.add_argument("-C", dest="folder")
    verbs = parser.add_subparsers(dest="verb", required=True)
    run = verbs.add_parser("run", usage=USAGE)
    run.add_argument("--scenario", action="append", default=[])
    smoke = run.add_mutually_exclusive_group(required=True)
    smoke.add_argument("--smoke")
    smoke.add_argument("--no-smoke")
    texts = verbs.add_parser("texts", usage=USAGE)
    texts.add_argument("--base", required=True)
    texts.add_argument("--check", action="append", default=[])
    shown = verbs.add_parser("show", usage=USAGE)
    shown.add_argument("commit", nargs="?")
    shown.add_argument("--body", action="store_true")
    for verb in (run, texts):
        verb.add_argument("--not-tested", action="append", default=[], required=True)
    for verb in (run, texts, shown):
        verb.add_argument("-C", dest="folder", default=argparse.SUPPRESS)
    return parser


def main(words):
    options = arguments().parse_args(words)
    try:
        return show(options) if options.verb == "show" else \
            record_run(options, "code" if options.verb == "run" else "texts")
    except Refused as refusal:
        print(f"pr-tested: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
