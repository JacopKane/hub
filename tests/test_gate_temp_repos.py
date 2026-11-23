"""Commits in a temp repo with no remote are checked when they are pushed.

A git repo inside a temp folder, that git knows no remote for, is throwaway
work. The gate lets its commits and tags through and checks them when they
leave the machine, by a push or by gh repo create --push. A repo that gains a
remote, a clone, a worktree of a repo outside a temp folder and a line that
points git somewhere else keep every check, and so does a repo in the home
folder.
"""
import os
import stat
import sys

import pytest

from conftest import PLUGIN, commit
from steps import and_, given, then, when

GATE = "public_text_gate"
HOME_PATH_MESSAGE = "Add notes from /home/sam/notes.md"


def scratch_temp_folder(home):
    """A temp folder beside the test home, set as the hook's TMPDIR, so a repo in it sits in a temp folder and
    outside HOME."""
    folder = os.path.join(os.path.dirname(home.root), "scratch-tmp")
    os.makedirs(folder, exist_ok=True)
    home.env["TMPDIR"] = folder
    return folder


def new_repo(home, folder, name):
    repo = os.path.join(folder, name)
    os.makedirs(repo)
    home.git("init", "-q", cwd=repo)
    home.git("commit", "-q", "--allow-empty", "-m", "start", cwd=repo)
    return repo


def test_a_commit_in_a_temp_repo_with_no_remote_goes_through_and_its_push_is_checked(home, tool):
    with given("hub is installed, with a git repo in a new temp folder that has no remote"):
        home.install()
        scratch = scratch_temp_folder(home)
        repo = new_repo(home, scratch, "throwaway")
    with when("Claude commits and tags with a home path in the message"):
        committed = home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=repo)
        tagged = home.hook(tool, "PreToolUse", "git tag -a v1 -m 'See /home/sam/tag.md'", cwd=repo)
        from_a_subfolder = os.path.join(repo, "docs")
        os.makedirs(from_a_subfolder)
        committed_below = home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=from_a_subfolder)
    with then("each one runs with no answer"):
        assert [committed.output, tagged.output, committed_below.output] == [{}, {}, {}]
    with when("the commit is made, the repo gets a local bare remote and Claude pushes"):
        home.git("commit", "-q", "--allow-empty", "-m", HOME_PATH_MESSAGE, cwd=repo)
        home.git("init", "-q", "--bare", os.path.join(scratch, "backup.git"), cwd=scratch)
        home.git("remote", "add", "backup", os.path.join(scratch, "backup.git"), cwd=repo)
        pushed = home.hook(tool, "PreToolUse", "git push backup main", cwd=repo)
    with then("the push is refused for the home path in the commit it sends"):
        assert pushed.findings == ["private_paths"] and "push" in pushed.reason
    with and_("a new commit in the repo, which now has a remote, is checked as before"):
        assert home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=repo).findings == ["private_paths"]
    with when("the user turns off temp repos"):
        home.hubctl_from_mod("off", f"{GATE}.temp_repos")
        other = new_repo(home, scratch, "second")
    with then("a commit in a temp repo with no remote is checked as before"):
        assert home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=other).findings == ["private_paths"]


def test_a_temp_repo_that_is_not_on_its_own_or_a_line_that_moves_git_keeps_every_check(home, tool):
    with given("hub is installed, with a temp folder outside the home"):
        home.install()
        scratch = scratch_temp_folder(home)
    with and_("a repo whose remote is named only in a file its config includes"):
        included = new_repo(home, scratch, "included")
        remote_file = os.path.join(scratch, "remote.cfg")
        with open(remote_file, "w") as handle:
            handle.write('[remote "origin"]\n\turl = https://example.com/demo.git\n')
        home.git("config", "include.path", remote_file, cwd=included)
    with and_("a repo with a remote file in its git folder, as old git versions kept them"):
        remote_folder_repo = new_repo(home, scratch, "remote-folder")
        os.makedirs(os.path.join(remote_folder_repo, ".git", "remotes"))
        with open(os.path.join(remote_folder_repo, ".git", "remotes", "backup"), "w") as handle:
            handle.write("URL: https://example.com/demo.git\n")
    with and_("a clone, and a worktree of the home's own repo, both in the temp folder"):
        clone = os.path.join(scratch, "clone")
        home.git("clone", "-q", home.work, clone, cwd=scratch)
        worktree = os.path.join(scratch, "worktree")
        home.git("worktree", "add", "-q", "-b", "side", worktree, cwd=home.work)
    with when("Claude commits a home path in each of them and in the home's own repo"):
        repos = {"included remote": included, "remote file": remote_folder_repo, "clone": clone,
                 "worktree": worktree, "home repo": home.work}
        results = {name: home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=repo)
                   for name, repo in repos.items()}
    with then("each commit is refused for the home path, as today"):
        assert {name: result.findings for name, result in results.items()} == {
            name: ["private_paths"] for name in repos}
    with when("Claude commits in a temp repo with no remote on a line that may point git somewhere else"):
        plain = new_repo(home, scratch, "plain")
        message = HOME_PATH_MESSAGE.replace("'", "")
        lines = [f"cd {home.work} && {commit(message)}", f"pushd {home.work} && {commit(message)}",
                 f"git -C {home.work} commit -m '{message}'", f"git -c core.bare=false commit -m '{message}'",
                 f"git --git-dir={home.work}/.git commit -m '{message}'",
                 f"git --work-tree={home.work} commit -m '{message}'",
                 f"GIT_DIR={home.work}/.git git commit -m '{message}'",
                 f"export GIT_CONFIG_GLOBAL=/dev/null; {commit(message)}"]
        moved = [home.hook(tool, "PreToolUse", line, cwd=plain) for line in lines]
    with then("each one is checked as today"):
        assert [result.findings for result in moved] == [["private_paths"]] * len(lines)


def test_the_gates_git_reads_start_no_program_the_repo_config_names(home, tool):
    with given("hub is installed, with a temp repo whose config names an fsmonitor program that leaves a marker"):
        home.install()
        scratch = scratch_temp_folder(home)
        repo = new_repo(home, scratch, "watched")
        marker = os.path.join(scratch, "fsmonitor-ran")
        program = os.path.join(scratch, "fsmonitor.sh")
        with open(program, "w") as handle:
            handle.write(f"#!/bin/sh\ntouch '{marker}'\n")
        os.chmod(program, os.stat(program).st_mode | stat.S_IXUSR)
        home.git("config", "core.fsmonitor", program, cwd=repo)
    with when("Claude commits a home path there"):
        result = home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=repo)
    with then("the commit runs with no answer, and the fsmonitor program never ran"):
        assert result.output == {}
        assert not os.path.exists(marker)


def test_gh_repo_create_with_push_checks_the_commits_it_pushes(home, tool):
    with given("hub is installed, with a temp repo with no remote whose commit holds a home path"):
        home.install()
        scratch = scratch_temp_folder(home)
        repo = new_repo(home, scratch, "to-publish")
        home.git("commit", "-q", "--allow-empty", "-m", HOME_PATH_MESSAGE, cwd=repo)
        clean = new_repo(home, scratch, "clean")
    with when("Claude creates a GitHub repo from it and pushes, in each spelling of the source"):
        lines = ["gh repo create demo --private --source . --push",
                 "gh repo create demo --private --source=. --push",
                 "gh repo create demo --private -s . --push"]
        results = [home.hook(tool, "PreToolUse", line, cwd=repo) for line in lines]
        from_outside = home.hook(tool, "PreToolUse",
                                 f"gh repo create demo --private --source {repo} --push", cwd=clean)
    with then("each one is refused for the home path in the commits it pushes"):
        assert [result.findings for result in results] == [["private_paths"]] * len(lines)
        assert from_outside.findings == ["private_paths"] and "push" in from_outside.reason
    with when("Claude creates the repo with no push, or pushes a repo with clean commits"):
        no_push = home.hook(tool, "PreToolUse", "gh repo create demo --private --source .", cwd=repo)
        clean_push = home.hook(tool, "PreToolUse", "gh repo create demo --private --source . --push", cwd=clean)
    with then("both run with no answer"):
        assert [no_push.output, clean_push.output] == [{}, {}]
    with when("the description holds a home path"):
        described = home.hook(tool, "PreToolUse",
                              "gh repo create demo --source . --push -d 'From /home/sam/x'", cwd=clean)
    with then("it is still refused"):
        assert described.findings == ["private_paths"]


def test_a_broken_hub_checks_commits_in_a_temp_repo(home, tool):
    with given("hub is installed, with a temp repo with no remote, and a settings file hub can not read"):
        home.install()
        repo = new_repo(home, scratch_temp_folder(home), "throwaway")
        os.makedirs(os.path.dirname(home.user_file), exist_ok=True)
        with open(home.user_file, "w") as handle:
            handle.write("{ not json")
    with when("Claude commits a home path there"):
        result = home.hook(tool, "PreToolUse", commit(HOME_PATH_MESSAGE), cwd=repo, errors_expected=True)
    with then("the commit is refused, because temp repos counts as off"):
        assert result.refused


@pytest.fixture
def files_module():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    from hub import files
    return files


@pytest.mark.parametrize("path, expected", [
    ("/tmp/run/repo", True),
    ("/TMP/Run/Repo", True),
    ("/tmp", False),
    ("/tmpfiles/repo", False),
    ("/srv/repo", False),
    ("/tmp/home/sam/repo", False),
    ("/tmp/home/sam", False),
    ("/tmp/home", False),
    ("/tmp/home/sam/.config/hub/x", False),
    ("/var/run/T/repo", True),
])
def test_a_folder_counts_as_temp_only_under_a_temp_root_and_away_from_the_home_and_hubs_folders(
        files_module, path, expected):
    roots = ["/tmp", "/var/run/T"]
    kept = ["/tmp/home/sam", "/tmp/home/sam/.config/hub"]
    assert files_module.is_temp_folder(path, roots, kept) is expected
