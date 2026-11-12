"""Test reports for the PR test gate: where they live and whether one passes.

A report is a JSON file named after the commit it tested, kept in the repo's
common git folder under pr-test-reports, so every worktree of the repo sees
it. When a commit has no report of its own, a report for a commit with the
same tree counts, so a reworded commit keeps its report.

The gate imports this file by path. Its git calls go through the gate's
run_git, which keeps them inside the hook's time.
"""
from __future__ import annotations

import json
import os

FOLDER = "pr-test-reports"
TEXT_SUFFIXES = {".md", ".mdx", ".txt", ".rst", ".adoc", ".po", ".pot"}
LOCALE_FOLDERS = {"locales", "locale", "translations", "i18n"}
LOCALE_SUFFIXES = {".json", ".yaml", ".yml", ".xliff", ".xlf", ".strings", ".stringsdict", ".arb", ".properties",
                   ".ftl"}
DOC_FOLDERS = {"docs", "doc"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def is_text_file(path):
    """Whether a changed file counts as text or docs, so a texts report may cover it."""
    folders = path.split("/")[:-1]
    suffix = os.path.splitext(path)[1].lower()
    if suffix in TEXT_SUFFIXES:
        return True
    if suffix in LOCALE_SUFFIXES and LOCALE_FOLDERS.intersection(folders):
        return True
    return suffix in IMAGE_SUFFIXES and bool(DOC_FOLDERS.intersection(folders))


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


def problem(report, cwd, sha, run_git):
    """(the gate's message name, its values) for what the report lacks, or None when it passes."""
    if report.get("version") != 1 or report.get("kind") not in ("code", "texts"):
        return "report_unreadable.md", {}
    if report["kind"] == "texts":
        return texts_problem(report, cwd, sha, run_git)
    if report.get("clean") is not True:
        return "dirty.md", {}
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


def texts_problem(report, cwd, sha, run_git):
    which = failing(report.get("checks") or [])
    if which is not None:
        return "failed.md", {"which": which}
    if not report.get("not_tested"):
        return "no_not_tested.md", {}
    base = (report.get("base") or {}).get("sha") if isinstance(report.get("base"), dict) else None
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
