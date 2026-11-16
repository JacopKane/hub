"""The PR test gate. A PR step needs a passing test report for its commit.

Before a Bash call runs, it reads the command with the public text gate's
shell parser and stops three steps until the commit they send has a passing
report from hub's pr-tested step:

- a push to a GitHub branch that has an open PR, in gh's own repo for the folder or in the repo the push goes
  to;
- a new PR, through gh pr create or gh api, for the PR's head commit;
- marking a PR ready, for the PR's head commit.

A new PR and a body edit also need a body whose testing part says what was
not tested, checked by the pr part's own body check, so pr-check and the gate
agree. Tags, deletes, dry runs and pushes to another host stay quiet, and so
does a push that GitHub says sends nothing new to an open PR. GitHub, not the
local copy of its refs, also gives a PR's base and a named head's commit. A
branch named alone in
a push counts at the remote branch git sends it to, by the remote's push
settings or the branch's upstream.

A step the gate can not read is refused and asked to be written out. That
covers a step inside a wrapper or a substitution, after another command, in
a folder that is missing or not known, or with a body the shell fills in.
Its git and gh lookups share one deadline, so the answer comes well inside
the hook's time.

Errors are not caught here, because hub's dispatcher refuses PR steps on any error.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse

from hub import files

PART = "pr_test_gate"
PART_DIR = os.path.dirname(os.path.abspath(__file__))
GATE_MODULE = "hub_part_public_text_gate"
REPORT_MODULE = "hub_part_pr_test_gate_report"
CHECK_BODY_MODULE = "hub_part_pr_check_body"
DEADLINE = 5.0  # seconds for all lookups of one call
# Seconds from when hub's parts start answering until this gate's lookups must end. The hook may run
# for 15 seconds, and the gates before this one, such as the text gate's git log, use some of that.
PARTS_SECONDS = 10.0
GIT_SECONDS = 2.0
GH_SECONDS = 3.0
RELEVANT = {"push", "pr", "wrapped"}
CHECKED = "checked"  # a gh step this gate reads that the text gate names otherwise, such as gh issue edit
EVERY_BRANCH = ("--all", "--mirror")
BRANCH_PREFIXES = ("refs/heads/", "heads/")
# The flags that let gh fill the body from the commits or a web form
FILLS = ("fill", "fill-first", "fill-verbose", "web")
# gh's flags for pr create, pr edit and issue edit, as gh defines them: {long name: (short letter, whether it
# takes a value)}. Every pr and issue command also has -R or --repo, and --help.
GH_PR_SHARED_FLAGS = {"repo": ("R", True), "help": ("h", False)}
GH_PR_FLAGS = {
    "create": {"draft": ("d", False), "title": ("t", True), "body": ("b", True), "body-file": ("F", True),
               "base": ("B", True), "head": ("H", True), "editor": ("e", False), "web": ("w", False),
               "fill-verbose": ("", False), "fill": ("f", False), "fill-first": ("", False),
               "reviewer": ("r", True), "assignee": ("a", True), "label": ("l", True), "project": ("p", True),
               "milestone": ("m", True), "no-maintainer-edit": ("", False), "recover": ("", True),
               "template": ("T", True), "dry-run": ("", False)},
    "edit": {"title": ("t", True), "body": ("b", True), "body-file": ("F", True), "base": ("B", True),
             "milestone": ("m", True), "remove-milestone": ("", False),
             **{f"{change}-{what}": ("", True) for change in ("add", "remove")
                for what in ("assignee", "label", "project", "reviewer")}},
    "issue edit": {"title": ("t", True), "body": ("b", True), "body-file": ("F", True),
                   "milestone": ("m", True), "remove-milestone": ("", False),
                   **{f"{change}-{what}": ("", True) for change in ("add", "remove")
                      for what in ("assignee", "label", "project")}},
}
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
OTHER_REPO_OPTIONS = {"--git-dir", "--work-tree"}
OTHER_REPO_VARIABLES = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
GIT_OR_GH = re.compile(r"\b(?:git|gh)\b")
GH_ALIASES = {("pr", "new"): ("pr", "create")}
PR_REPO_OPTIONS = {"-R", "--repo"}
PR_MUTATIONS = ("createPullRequest", "updatePullRequest", "markPullRequestReadyForReview")
CURRENT_REPO = ("rest", "repos/{owner}/{repo}", {})  # the repo gh picks from the folder, as gh pr list does
REPO_PATH = r"(?:repos/[^/]+/[^/]+|repositories/\d+)"  # the two ways GitHub's REST API names a repo
FORKED_FROM = "[.parent.full_name, .source.full_name | strings]"  # the repos a repo is a fork of, as owner/name
HEAD_ON_NODE = ("query($id: ID!, $ref: String!) { node(id: $id) { ... on Repository { ref(qualifiedName: $ref) "
                "{ target { oid } } } } }")
DEFAULT_ON_NODE = "query($id: ID!) { node(id: $id) { ... on Repository { defaultBranchRef { name } } } }"
GITHUB = re.compile(r"(?:^|[@/])github\.com(?:[:/]|$)", re.I)
SSH_SCHEMES = {"ssh", "git+ssh", "ssh+git"}  # the URL schemes git reaches over ssh
SCP_LIKE = re.compile(r"(?:[^@/:]*@)?(\[[^\]/]*\]|[^:/]+):")  # [user@]host:path, where host may be [host:port]
CONFIG_VARIABLE = re.compile(r"GIT_CONFIG(?:_[A-Z0-9_]+)?=")
PUSH_SETTINGS = r"^(push\.default|remote\..+\.(push|mirror)|branch\..+\.(merge|remote))$"
# The words Go's strconv.ParseBool reads as true, which gh uses for a value such as --undo=true
TRUE_WORDS = {"1", "t", "T", "TRUE", "true", "True"}
PUSH_CONFIG_WHY = {
    "matching": "the repo's push.default sends every branch that has a match on the remote",
    "refspec": "the remote has its own setting for what a push sends",
    "mirror": "the remote is set to mirror every branch",
    "option": "git -c, --config-env or a GIT_CONFIG variable changes its push settings for this one command",
    "upstream": "the branch's upstream names no remote",
    "negative": "the remote's push settings leave branches out with ^, which changes where git sends a branch",
    "file": "a file in the repo's remotes or branches folder sets where pushes to this remote go",
}
# The push form the message asks for. A stop that refuses a branch named alone asks for its destination too.
PUSH_CONFIG_EXAMPLE = {
    "named": "git push origin feature",
    "upstream": "git push origin fix-a:feature, where feature is the branch on the remote",
    "negative": "git push origin fix-a:feature, where feature is the branch on the remote",
    "file": "git push origin fix-a:feature, where feature is the branch on the remote",
}
WHY = {
    "parse": "the line does not parse",
    "inside": "it runs inside another command",
    "feeder": "xargs or parallel fills in its words",
    "before": "another command runs before it on the same line",
    "word": "a variable or a pattern names the branch",
    "every": "it pushes every branch",
    "option": "a shortened option matches more than one option",
    "detached": "HEAD is not on a branch",
    "unknown": "the branch is not in this repo",
    "node": "a node id names the PR, not its number",
    "head": "the PR's head branch is not named",
    "api_option": "gh api has an option the gate does not know",
    "folder": "the folder it runs in is missing or not known",
    "body": "a variable or a substitution fills in the PR body",
    "gh_command": "a variable or a substitution fills in a word gh may read as its command",
    "search": "a search of commit messages picks the commit",
    "pr_option": "gh has an option the gate does not know, or one with no value",
    "head_repo": "it names the head's repo in a way the gate does not read",
    "draft": "GitHub's REST update for a PR takes no draft field and can not change the draft state, so use "
             "gh pr ready or gh pr ready --undo instead",
    "remote_url": "git could not say which URL the remote names",
    "copies": "git could not list its copies of the remote's branches",
    "ssh_host": "ssh could not say which host the remote's address names",
    "ssh_command": "the gate can not read git's ssh command, so it can not tell which host the push reaches",
    "query": "the gate can not read the whole GraphQL query before the command runs",
}
GH_SSH = ("ssh",)  # gh reads a host's ssh config with plain ssh -G
SSH_COMMAND_SETTING = "core.sshcommand"
# Shell marks that may start a program, pipe, redirect, expand ${...} or match files. A command with none of them
# can only quote and fill in a plain $NAME or a ~, so the shell that splits it into words runs nothing.
SSH_COMMAND_SHELL_MARKS = re.compile(r"[`;&|<>(){}*?\[\]\n\r]")
SSH_CONFIG_OPTIONS = {"-F", "-o"}  # the ssh options that change the config ssh -G prints for a host
# ssh's one-letter options, as its getopt string names them: those that take a value, and those that do not
SSH_VALUE_LETTERS = set("BDEFIJLOPQRSWbceilmopw")
SSH_SWITCH_LETTERS = set("1246ACGKMNTVXYafgknqstvxy")


class Stop(Exception):
    """The gate refuses the call with one of its messages."""

    def __init__(self, name, child="report", **values):
        super().__init__(name)
        self.name = name
        self.child = child
        self.values = values


def unreadable(why):
    return Stop("unreadable_push.md", why=WHY[why])


def push_settings_stop(why):
    return Stop("push_config.md", why=PUSH_CONFIG_WHY[why],
                example=PUSH_CONFIG_EXAMPLE.get(why, PUSH_CONFIG_EXAMPLE["named"]))


# Modules loaded by path, once per process


def loaded(name, path):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
    return module


def gate():
    return loaded(GATE_MODULE, os.path.join(files.PARTS_DIR, "public_text_gate", "handler.py"))


def reports():
    return loaded(REPORT_MODULE, os.path.join(PART_DIR, "report.py"))


def check_body():
    return loaded(CHECK_BODY_MODULE, os.path.join(files.PARTS_DIR, "pr", "check_body.py"))


def message(name, **values):
    with open(os.path.join(PART_DIR, "text", name), encoding="utf-8") as handle:
        found = handle.read().strip()
    for key, value in values.items():
        found = found.replace("{" + key + "}", str(value))
    return files.fill_step(found)


# Lookups that share one deadline


class Lookups:
    """git and gh calls for one tool call, all inside one deadline."""

    def __init__(self, seconds=DEADLINE):
        self.deadline = time.monotonic() + seconds
        self.cache = {}

    @classmethod
    def inside_the_hook(cls, parts_started):
        """Lookups whose deadline is the gate's own, or what the hook has left after the parts before it."""
        return cls(seconds_for_lookups(time.monotonic() - parts_started))

    def run(self, words, cwd, cap, variables=None):
        """(exit code, output, error), or None when the call took longer than it may. variables are set for this
        call on top of the hook's own."""
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Stop("slow.md")
        process = subprocess.Popen(words, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   errors="replace", start_new_session=True,
                                   env={**os.environ, **(variables or {}), "GIT_OPTIONAL_LOCKS": "0",
                                        "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1"})
        try:
            output, error = process.communicate(timeout=min(cap, left))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)  # the whole group, so a child holding the pipe ends too
            process.communicate()
            return None
        return process.returncode, output, error

    def git(self, cwd, *words):
        """git's output with its ends trimmed, or None when it fails. A git call that is too slow stops the gate."""
        key = (cwd, words)
        if key not in self.cache:
            try:
                done = self.run(["git", *words], cwd, GIT_SECONDS)
            except OSError:
                if os.path.isdir(cwd):
                    raise
                raise unreadable("folder") from None  # such as git -C with a folder that is not there
            if done is None:
                raise Stop("slow.md")
            self.cache[key] = done[1].strip() if done[0] == 0 else None
        return self.cache[key]

    def ssh_hostname(self, cwd, host, ssh_words=GH_SSH):
        """The host ssh connects to for a host or a Host alias, as ssh -G prints it after reading ssh's config,
        without connecting. ssh_words are the program and the config options to ask with. A lookup that fails may
        hide GitHub, so it stops the step."""
        key = ("ssh", tuple(ssh_words), host)
        if key not in self.cache:
            try:
                done = self.run([*ssh_words, "-G", "--", host], cwd, GIT_SECONDS)
            except OSError:
                done = None
            printed = done[1] if done and done[0] == 0 else ""
            found = [value.strip() for name, _space, value in (line.partition(" ") for line in printed.splitlines())
                     if name.lower() == "hostname" and value.strip()]
            if not found:
                raise unreadable("ssh_host")
            self.cache[key] = found[0]
        return self.cache[key]

    def git_ssh(self, cwd, options, assignments):
        """git_ssh_words for a push, asked once for each set of git options and VAR=value words."""
        key = ("git_ssh", cwd, tuple(options), tuple(sorted(assignments.items())))
        if key not in self.cache:
            self.cache[key] = git_ssh_words(cwd, options, assignments, self)
        return self.cache[key]

    def gh(self, cwd, *words, variables=None):
        """(gh's output or None, why it failed). A gh that can not start is a failed lookup like any other."""
        try:
            done = self.run(["gh", *words], cwd, GH_SECONDS, variables)
        except OSError:
            return None, "gh could not start"
        if done is None:
            return None, "gh did not answer in time"
        if done[0] != 0:
            return None, (done[2].strip().splitlines() or ["gh failed"])[0][:120]
        return done[1], ""


def seconds_for_lookups(parts_took):
    return min(DEADLINE, PARTS_SECONDS - parts_took)


# Reading the command


class Step:
    def __init__(self, words, before, earlier, cwd, target):
        self.words = words  # from the program word on
        self.before = before  # the words before it in its simple command
        self.earlier = earlier  # the simple commands before it on the line
        self.cwd = cwd
        self.target = target


def read_steps(line, cwd):
    """(the parsed command, its git and gh steps that may need the gate), refusing what it can not read."""
    parser = gate()
    named = [target for target, _text in parser.public_steps(line, cwd, log=False, read_step=step_reader(parser))
             if target in RELEVANT | {CHECKED}]
    command = parser.Command(line, cwd, read_log=False)
    segments = command.segments()
    if segments is None:
        if named or re.search(r"\bgit\b.*\bpush\b", command.flat):
            raise unreadable("parse")
        return command, []
    found, here = [], cwd
    for position, words in enumerate(segments):
        if len(words) == 2 and words[0] == "cd" and unreadable_mark() not in words[1]:
            here = folder_after_cd(here, words[1])
            continue
        for index, word in enumerate(words):
            program = os.path.basename(word)
            if program not in ("git", "gh"):
                continue
            command.cwd = here or cwd
            step_words = words[index:] if program == "git" else without_gh_alias(gh_words(parser, words[index:]))
            target = (parser.git_target if program == "git" else parser.gh_target)(step_words, command)
            if program == "git" and target and target[0] == parser.WRAPPED:
                raise Stop("git_word_unreadable.md")
            if not read_here(program, step_words, target):
                continue
            if any(os.path.basename(before) in parser.FEEDERS for before in words[:index]):
                raise unreadable("feeder")
            if here is None or not os.path.isdir(here):
                raise unreadable("folder")
            found.append(Step(step_words, words[:index], segments[:position], here, target[0]))
    if len(named) > len(found):
        raise unreadable("inside")
    return command, found


def read_here(program, step_words, target):
    """Whether the gate reads a git or gh step: a push, a PR step or one the shell fills in, a gh api call that
    writes, and gh issue edit, which may edit a PR's body."""
    return bool(target) and (target[0] in RELEVANT or (program == "gh" and (
        step_words[1:2] == ["api"] or step_words[1:3] == ["issue", "edit"])))


def step_reader(parser):
    """The text gate's step reader, with each step the gate reads named CHECKED when its target is not RELEVANT.
    The text gate reads the steps inside wrappers and substitutions with it, so a step there counts as one the gate
    reads whenever the same step on its own would be read."""
    def read_step(words, command):
        target = parser.step_target(words, command)
        program = os.path.basename(words[0]).casefold()
        ordered = parser.gh_command(words) if program == "gh" else words
        step_words = without_gh_alias(ordered) if ordered else words
        checked = read_here(program, step_words, target) and target[0] not in RELEVANT
        return (CHECKED, target[1]) if checked else target
    return read_step


def folder_after_cd(here, target):
    """The folder a cd on the line moves to, or None when the gate can not know it: cd - goes back to a folder the
    line does not show, and a relative cd from an unknown folder stays unknown."""
    if target == "-":
        return None
    folder = os.path.expanduser(target)
    if os.path.isabs(folder):
        return folder
    return None if here is None else os.path.join(here, folder)


def may_push_or_open_a_pr(line, cwd):
    """Whether the gate reads the line. It does when the line does not parse, when git or gh is in its text or
    parsed words (so g''it counts), or when the text gate finds a public step inside it, as in bash -c. A line
    that only a variable fills in, such as $CMD, is left to the text gate."""
    parser = gate()
    segments = parser.Command(line, cwd, read_log=False).segments()
    words = [word for segment in segments or [] for word in segment]
    return (segments is None or any(GIT_OR_GH.search(text) for text in [line, *words])
            or any(target != "wrapped" for target, _text in parser.public_steps(line, cwd, log=False)))


def gh_words(parser, words):
    """gh's words in the order gh reads them, from the text gate's reader, refusing a command the shell fills in."""
    ordered = parser.gh_command(words)
    if ordered is None:
        raise unreadable("gh_command")
    return ordered


def without_gh_alias(words):
    """gh's words with a built-in alias such as pr new spelled as the action it stands for."""
    action = tuple(words[1:3])
    return [words[0], *GH_ALIASES[action], *words[3:]] if action in GH_ALIASES else words


def unreadable_mark():
    """The mark the gate's parser puts where the shell fills in a word."""
    return gate().UNREADABLE


def written_out(step):
    """Refuse a step that needs the repo's state unless only cd and VAR=value come before it."""
    for earlier in step.earlier:
        if len(earlier) != 2 or earlier[0] != "cd" or unreadable_mark() in earlier[1]:
            raise unreadable("before")
    if any(not ASSIGNMENT.match(word) for word in step.before):
        raise unreadable("before")
    if any(word.split("=", 1)[0] in OTHER_REPO_VARIABLES for word in step.before):
        raise Stop("other_repo.md")


def git_place(step):
    """(the folder git runs in, the words after git's own options)."""
    cwd, words, index = step.cwd, step.words, 1
    while index < len(words):
        word = words[index]
        if word in ("-C", "-c", "--config-env", "--git-dir", "--work-tree", "--namespace", "--attr-source") \
                and index + 1 < len(words):
            if unreadable_mark() in words[index + 1]:
                raise unreadable("word")
            if word in OTHER_REPO_OPTIONS:
                raise Stop("other_repo.md")
            if word == "-C":
                cwd = os.path.join(cwd, os.path.expanduser(words[index + 1]))
            index += 2
        elif word.startswith("-"):
            if unreadable_mark() in word:
                raise unreadable("word")  # such as --git-dir=... that the shell fills in
            if word.split("=", 1)[0] in OTHER_REPO_OPTIONS:
                raise Stop("other_repo.md")
            index += 1
        else:
            break
    return cwd, words[index:]


# Pushes


def push_words(rest):
    """(positional words, whether a reason that needs no state keeps it quiet, whether --tags is on, the last
    --repo value or None). git pushes to --repo when no remote word is given, and --no-repo clears it."""
    shared = gate()
    items = list(shared.push_items(shared.without_redirects(rest)))
    if any(option == shared.AMBIGUOUS_OPTION for option, _value in items):
        raise unreadable("option")
    if any(shared.last_switch(items, option) for option in EVERY_BRANCH):
        raise unreadable("every")
    positional = [value for option, value in items if option is None]
    quiet = shared.last_switch(items, "--delete") or shared.last_switch(items, "--dry-run")
    repo = next((value for option, value in reversed(items) if option == "--repo"), False)
    return positional, quiet, shared.last_switch(items, "--tags"), repo or None


def pushed(step, lookups):
    """[(branch, commit, cwd, no known base, the GitHub repo it goes to)] for each GitHub branch the push may
    update, once for each GitHub repo it pushes to. Local refs do not show what GitHub has, so with_open_pr asks
    GitHub which of them bring an open PR something new."""
    cwd, words = git_place(step)
    if sets_push_settings_for_one_command(step, words):
        raise push_settings_stop("option")
    positional, quiet, tags, repo = push_words(words[1:])
    specs = [spec[1:] if spec.startswith("+") else spec
             for spec in gate().push_refspecs(positional[1:], deletes=False)]
    if not quiet and ":" in specs:
        raise unreadable("every")  # a lone colon pushes each branch that has a match on the remote
    if quiet or (tags and not specs) or (specs and all(map(is_a_delete, specs))):
        return []
    written_out(step)
    hidden_mark = unreadable_mark()
    if any(hidden_mark in word or "$" in word or "*" in word for word in positional + [repo or ""]):
        raise unreadable("word")
    branch = lookups.git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote = positional[0] if positional else repo
    if remote is None:
        if branch is None:
            raise unreadable("detached")
        remote = push_remote(cwd, branch, lookups)
    options = step.words[1:len(step.words) - len(words)]
    assignments = dict(word.split("=", 1) for word in step.before)
    repos = github_repos(cwd, remote, lookups, lambda: lookups.git_ssh(cwd, options, assignments))
    if not repos:
        return []
    sent = [pair for spec in specs or specs_of_a_bare_push(push_settings(cwd, remote, lookups), remote, branch)
            for pair in sent_by(spec, cwd, branch, remote, lookups)]
    return [(target, commit, cwd, set(), ("rest", repo, {})) for target, commit in sent
            if not target.startswith("refs/") for repo in repos]


def github_repos(cwd, remote, lookups, ssh_words):
    """The REST paths of the GitHub repos a push to the remote word reaches, one for each of git's push URLs on
    GitHub, or [] when none is on GitHub. ssh_words gives the ssh program and options git reaches ssh hosts with."""
    urls = [url for url in push_urls(cwd, remote, lookups) if is_a_github_url(url, cwd, lookups, ssh_words)]
    return list(dict.fromkeys(map(repo_path_of, urls)))


def is_a_github_url(url, cwd, lookups, ssh_words=lambda: GH_SSH):
    """Whether a remote URL may reach GitHub: it names github.com, its host is github.com or a host under it, such
    as ssh.github.com, or it goes over ssh to a host or Host alias whose HostName is one of those. ssh_words is
    asked only for an ssh host, so a URL that needs no ssh lookup needs no ssh command either."""
    if GITHUB.search(url):
        return True
    host, over_ssh = url_host(url)
    if host is None:
        return False
    return is_a_github_host(host) or (over_ssh and is_a_github_host(lookups.ssh_hostname(cwd, host, ssh_words())))


def git_ssh_words(cwd, options, assignments, lookups):
    """(the ssh program, then the -F and -o options it gets) for the ssh command git runs for a push. git takes
    GIT_SSH_COMMAND, else core.sshCommand with a git -c value first, else GIT_SSH as one word, else ssh, as git's
    connect.c picks them, and the variables come from the hook's environment and the step's VAR=value words. A
    command that is not ssh, or that the gate can not read, stops the step."""
    environment = {**os.environ, **assignments}
    sources = (lambda: environment.get("GIT_SSH_COMMAND"), lambda: one_command_ssh_command(options),
               lambda: lookups.git(cwd, "config", "core.sshCommand"))
    text = next((found for found in (source() for source in sources) if found is not None), None)
    words = [environment.get("GIT_SSH", "ssh")] if text is None else shell_words(text, assignments, cwd, lookups)
    if not words or any(unreadable_mark() in word for word in words) or os.path.basename(words[0]) != "ssh":
        raise unreadable("ssh_command")
    return (words[0], *ssh_config_options(words[1:]))


def one_command_ssh_command(options):
    """The ssh command that git's -c options set for this one command, the last one winning as in git, or None.
    --config-env takes it from a variable, and -c with no = sets no command, so either stops the step."""
    found, index = None, 0
    while index < len(options):
        option = options[index]
        if option in gate().CONFIG_OPTIONS and index + 1 < len(options):
            setting, index = options[index + 1], index + 2
        elif option.startswith("--config-env="):
            option, setting, index = "--config-env", option.split("=", 1)[1], index + 1
        else:
            index += 1
            continue
        key, equals, value = setting.partition("=")
        if key.lower() == SSH_COMMAND_SETTING:
            if option != "-c" or not equals:
                raise unreadable("ssh_command")
            found = value
    return found


def shell_words(text, assignments, cwd, lookups):
    """The ssh command split into words by the shell, as git runs it with sh -c, with each $NAME and ~ filled in
    from the push's variables. A shell mark that may run a program, a variable the shell fills in for the step
    that the command may read, or a shell that fails stops the step."""
    hidden_mark = unreadable_mark()
    may_read_a_variable = "$" in text or "~" in text
    if (SSH_COMMAND_SHELL_MARKS.search(text) or hidden_mark in text
            or (may_read_a_variable and any(hidden_mark in value for value in assignments.values()))):
        raise unreadable("ssh_command")
    done = lookups.run(["/bin/sh", "-c", "printf '%s\\0' " + text, text], cwd, GIT_SECONDS, assignments)
    if done is None:
        raise Stop("slow.md")
    if done[0] != 0:
        raise unreadable("ssh_command")
    return done[1].split("\0")[:-1]


def ssh_config_options(options):
    """The -F and -o options, with their values, among the options of git's ssh command, which ssh -G needs to
    print the config that ssh will read. ssh takes the first word that is not an option as its host, so such a
    word, or a letter ssh does not know, stops the step."""
    kept, index = [], 0
    while index < len(options):
        word = options[index]
        letters = word[1:] if word.startswith("-") and word not in ("-", "--") else ""
        value_at = next((place for place, letter in enumerate(letters) if letter in SSH_VALUE_LETTERS), len(letters))
        if not letters or any(letter not in SSH_SWITCH_LETTERS for letter in letters[:value_at]):
            raise unreadable("ssh_command")
        if value_at == len(letters):
            index += 1
            continue
        option, value = "-" + letters[value_at], letters[value_at + 1:]
        if not value and index + 1 == len(options):
            raise unreadable("ssh_command")
        value, index = (value, index + 1) if value else (options[index + 1], index + 2)
        kept += [option, value] if option in SSH_CONFIG_OPTIONS else []
    return kept


def url_host(url):
    """(the host a remote URL names, or None for a local path, whether git reaches it over ssh), as git reads a URL:
    scheme://[user@]host[:port]/path, or [user@]host:path when a colon comes before any slash."""
    scheme, separator, rest = url.partition("://")
    if separator and re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*", scheme):
        if scheme.lower() == "file":
            return None, False
        return bare_host(rest.split("/", 1)[0]), scheme.lower() in SSH_SCHEMES
    scp_like = SCP_LIKE.match(url)
    return (bare_host(scp_like.group(1)), True) if scp_like else (None, False)


def bare_host(authority):
    """The host of [user@]host[:port], [host] or [host:port]. Inside brackets more than one colon is an IPv6
    address, which stays whole."""
    host = authority.rpartition("@")[2]
    if host.startswith("["):
        inside = host[1:].partition("]")[0]
        return inside.partition(":")[0] if inside.count(":") == 1 else inside
    return host.partition(":")[0]


def is_a_github_host(host):
    name = host.lower().rstrip(".")
    return name == "github.com" or name.endswith(".github.com")


def push_urls(cwd, remote, lookups):
    """The URLs git pushes to for the remote word. A configured remote pushes to its push URLs, as git remote
    get-url --push shows them. git reads any other word as a URL, which url.<base>.pushInsteadOf rewrites, else
    url.<base>.insteadOf, as git ls-remote --get-url shows. A lookup that fails may hide a GitHub URL, so it
    stops the step."""
    listed = lookups.git(cwd, "remote", "get-url", "--push", "--all", "--", remote)
    if listed is not None:
        return listed.splitlines()
    rewritten = rewritten_urls(remote, push_rewrites(cwd, lookups))
    if rewritten:
        return rewritten
    url = lookups.git(cwd, "ls-remote", "--get-url", "--", remote)
    if url is None:
        raise unreadable("remote_url")
    return [url]


def push_rewrites(cwd, lookups):
    """[(the start of a URL, the base git puts in its place)] from the repo's url.<base>.pushInsteadOf settings."""
    listed = lookups.git(cwd, "config", "-z", "--get-regexp", r"^url\..*\.pushinsteadof$") or ""
    return [(start, key[len("url."):-len(".pushinsteadof")])
            for key, _newline, start in (entry.partition("\n") for entry in listed.split("\0") if entry)]


def rewritten_urls(url, rewrites):
    """The URL with its longest matching start replaced, as git's alias_url picks it, or [] when none matches.
    When two bases tie, each counts, so the check does not depend on which one git meets first."""
    longest = max((len(start) for start, _base in rewrites if url.startswith(start)), default=None)
    return [base + url[len(start):] for start, base in rewrites if url.startswith(start) and len(start) == longest]


def is_a_delete(spec):
    return gate().source_and_destination(spec)[0] == ""


def sent_by(spec, cwd, branch, remote, lookups):
    """[(a branch the refspec may update, the commit it sends)], or [] when it updates a tag or deletes. A tag sent
    to a branch counts as the commit it points to. A local branch or tag named alone goes where git's push
    settings send it. A source that searches commit messages, as :/text does, is refused."""
    source, target = gate().source_and_destination(spec)
    if not source or target.startswith("refs/tags/"):
        return []
    if gate().searches_commit_messages(source):
        raise unreadable("search")
    source = branch_name(source)
    if source == "HEAD":
        commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        if not (target or branch) or not commit:
            raise unreadable("detached")
        return [(name, commit) for name in remote_branch_names(target or branch)]
    commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{source}")
    if commit is None:
        if not target and is_a_tag(cwd, source, lookups):
            return sent_as_a_tag(source, cwd, remote, lookups)
        commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"{source}^{{commit}}")
        if commit is None or not target:
            raise unreadable("unknown")
    if not target:
        target = mapped_destination(f"refs/heads/{source}", push_settings(cwd, remote, lookups), remote)
    return [(name, commit) for name in remote_branch_names(target)]


def sent_as_a_tag(tag, cwd, remote, lookups):
    """[(a branch the tag may update, its commit)] for a tag named alone. git sends it where the remote's push
    settings send its full name, else under its own name, which updates no branch."""
    tag_ref = tag if tag.startswith("refs/tags/") else f"refs/tags/{tag}"
    target = mapped_destination(tag_ref, push_settings(cwd, remote, lookups), remote)
    if target.startswith("refs/tags/"):
        return []
    commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"{tag_ref}^{{commit}}")
    if commit is None:
        raise unreadable("unknown")
    return [(name, commit) for name in remote_branch_names(target)]


def remote_branch_names(target):
    """The remote branches a push target may update. A short heads/x updates heads/x when the remote has it and x
    otherwise, so it counts as both; refs/heads/x is exactly x."""
    if target.startswith("refs/heads/"):
        return [target[len("refs/heads/"):]]
    if target.startswith("heads/"):
        return [target[len("heads/"):], target]
    return [target]


def mapped_destination(source_ref, settings, remote):
    """Where git sends a local ref named with no destination: the first of the remote's push settings whose
    source matches it, else, for a branch, its one upstream branch when push.default is upstream and the branch
    has a remote, else its own name. A ^ entry in the remote's push settings is refused, since it changes which
    setting git uses."""
    remote_settings = [value for key, value in settings if key == f"remote.{remote}.push"]
    if any(value.startswith("^") for value in remote_settings):
        raise push_settings_stop("negative")
    mapped = [destination for value in remote_settings
              for destination in [refspec_destination(value, source_ref)] if destination is not None]
    if mapped:
        return mapped[0] or source_ref  # an empty destination keeps the ref's own name
    if not source_ref.startswith("refs/heads/"):
        return source_ref
    return upstream_destination(source_ref[len("refs/heads/"):], settings) or source_ref


def upstream_destination(branch, settings):
    """The branch's one upstream branch when push.default is upstream and the branch has a remote, as git maps it,
    else None. An empty remote is refused, since git can not say where the branch goes."""
    merges = [value for key, value in settings if key == f"branch.{branch}.merge"]
    if last_value(settings, "push.default") not in ("upstream", "tracking") or len(merges) != 1:
        return None
    branch_remote = last_value(settings, f"branch.{branch}.remote")
    if branch_remote == "":
        raise push_settings_stop("upstream")
    return merges[0] if branch_remote is not None else None


def refspec_destination(refspec, source_ref):
    """Where one push setting sends the full source ref, or None when it does not match. git matches the source
    only by its full name, with at most one *, and splits the setting at its last colon."""
    if ":" not in refspec:
        return None
    source, destination = gate().source_and_destination(refspec[1:] if refspec.startswith("+") else refspec)
    if "*" not in source:
        return destination if source == source_ref else None
    prefix, suffix = source.split("*", 1)
    fits = (len(source_ref) >= len(prefix) + len(suffix) and source_ref.startswith(prefix)
            and source_ref.endswith(suffix))
    return destination.replace("*", source_ref[len(prefix):len(source_ref) - len(suffix)], 1) if fits else None


def is_a_tag(cwd, ref, lookups):
    return ref.startswith("refs/tags/") or lookups.git(cwd, "rev-parse", "--verify", "--quiet",
                                                       f"refs/tags/{ref}") is not None


def push_remote(cwd, branch, lookups):
    """The remote a push with no remote named sends the branch to, as git picks it."""
    return (lookups.git(cwd, "config", f"branch.{branch}.pushRemote")
            or lookups.git(cwd, "config", "remote.pushDefault")
            or lookups.git(cwd, "config", f"branch.{branch}.remote") or "origin")


def sets_push_settings_for_one_command(step, words_after_options):
    """Whether git's own options or the step's VAR=value words change push settings that the lookups can not see."""
    options = step.words[1:len(step.words) - len(words_after_options)]
    return (any(map(gate().names_a_push_setting, gate().settings_set_by_options(options)))
            or any(map(CONFIG_VARIABLE.match, step.before)))


def push_settings(cwd, remote, lookups):
    """[(name, value)] of the repo's settings that decide what a push with no branch named sends. git also reads a
    remote from a file in the repo's remotes or branches folder, which the settings do not show, so such a file
    for the remote is refused."""
    if remote_file_exists(cwd, remote, lookups):
        raise push_settings_stop("file")
    listed = lookups.git(cwd, "config", "--get-regexp", PUSH_SETTINGS) or ""
    return [tuple((line.split(" ", 1) + [""])[:2]) for line in listed.splitlines()]


def remote_file_exists(cwd, remote, lookups):
    """Whether the repo's remotes or branches folder holds a file for the remote."""
    common = lookups.git(cwd, "rev-parse", "--git-common-dir")
    if common is None:
        raise unreadable("folder")
    return gate().has_remote_file(os.path.join(cwd, common), remote)


def last_value(settings, name):
    return next((value for key, value in reversed(settings) if key == name), None)


def is_true(value):
    return value is not None and value.lower() not in gate().FALSE_WORDS


def specs_of_a_bare_push(settings, remote, branch):
    """The refspecs a push that names no branch sends, by the repo's settings, or a stop when they send more."""
    if last_value(settings, f"remote.{remote}.push") is not None:
        raise push_settings_stop("refspec")
    if is_true(last_value(settings, f"remote.{remote}.mirror")):
        raise push_settings_stop("mirror")
    mode = last_value(settings, "push.default")
    if mode == "matching":
        raise push_settings_stop("matching")
    upstream = last_value(settings, f"branch.{branch}.merge") if mode in ("upstream", "tracking") else None
    return [f"HEAD:{branch_name(upstream)}"] if upstream else ["HEAD"]


def branch_name(ref):
    """A branch as the remote names it, so refs/heads/feature and heads/feature both read as feature."""
    return next((ref[len(prefix):] for prefix in BRANCH_PREFIXES if ref.startswith(prefix)), ref)


def with_open_pr(candidates, lookups):
    """The pushed branches that send something new to an open PR, each with the branch its PR goes into and the
    repo the PR is in, as GitHub says. gh is asked in its own repo for the folder, where a PR from a fork is, and
    in each repo the push goes to and in each repo that one is a fork of. A branch with no open PR, or whose PR's
    head is already the commit sent, drops out. More than one branch, or a failed lookup, keeps them all, with no
    known base."""
    if len({branch for branch, _commit, _cwd, _bases, _place in candidates}) != 1:
        return candidates
    branch, _commit, cwd, _bases, _place = candidates[0]
    pushed_to = {place[1] for *_start, place in candidates}
    forked_from = repos_forked_from(cwd, pushed_to, lookups)
    if forked_from is None:
        return candidates
    found = []
    for place, repo_words in pr_lists(pushed_to | forked_from):
        listed = parsed_json(lookups.gh(cwd, "pr", "list", "--head", branch, "--state", "open", "--json",
                                        "number,baseRefName,headRefOid", "--limit", "1", *repo_words)[0])
        if not isinstance(listed, list):
            return candidates
        found += [(pr if isinstance(pr, dict) else {}, place) for pr in listed[:1]]
    sent = list(dict.fromkeys((name, commit, here) for name, commit, here, _bases, _place in candidates))
    return [(name, commit, here, bases_of_pr(pr), place) for pr, place in found for name, commit, here in sent
            if commit != pr.get("headRefOid")]


def repos_forked_from(cwd, repos, lookups):
    """The REST paths of the repos that each GitHub repo a push goes to is a fork of: its parent and the source at
    the root of its forks. A PR from a fork may be open in one of those, and gh pr list with no -R looks only in the
    repo gh repo set-default picked or else the first remote, so GitHub is asked. None when a lookup fails."""
    listed = [parsed_json(lookups.gh(cwd, "api", repo, "--jq", FORKED_FROM)[0]) for repo in sorted(repos)]
    if not all(isinstance(names, list) and all(map(is_a_repo_name, names)) for names in listed):
        return None
    return {"repos/" + name for names in listed for name in names}


def is_a_repo_name(name):
    return isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9-]+/[\w.-]+", name) is not None


def pr_lists(repos):
    """[(the repo a PR that gh pr list finds is in, the -R words it is asked with)]: gh's own repo for the folder,
    then each GitHub repo by its REST path, in a fixed order."""
    return [(CURRENT_REPO, []), *((("rest", repo, {}), ["-R", "github.com/" + repo[len("repos/"):]])
                                  for repo in sorted(repos))]


def bases_of_pr(pr):
    base = pr.get("baseRefName")
    return {base} if isinstance(base, str) and base else set()


def parsed_json(output):
    try:
        return json.loads(output) if output is not None else None
    except ValueError:
        return None


# PR steps


def gh_pr_flags(words, action):
    """({long name: its values in order}, positional words) of a gh pr create or edit step, read the way gh's flag
    parser (pflag) reads them. A flag that takes a value takes the next word even when it starts with -, short flags
    stack as in -dH branch, and a value may be attached, as in -Hbranch, -H=branch or --head=branch. A flag gh does
    not know is refused, since gh fails on it, and so is a word the shell fills in where gh may read a flag."""
    known = {**GH_PR_SHARED_FLAGS, **GH_PR_FLAGS[action]}
    values, positional, index = {}, [], 0
    while index < len(words):
        word, index = words[index], index + 1
        if word == "--":
            positional += words[index:]
            break
        if len(word) < 2 or not word.startswith("-"):
            positional.append(word)
            continue
        read = long_gh_flag if word.startswith("--") else short_gh_flags
        pairs, index = read(word, words, index, known)
        for name, value in pairs:
            values.setdefault(name, []).append(value)
    if any(unreadable_mark() in word for word in positional):
        raise unreadable("word")
    return values, positional


def long_gh_flag(word, words, index, known):
    """([(name, value)], the index after it) for a word such as --head, --head=x or --draft."""
    name, equals, value = word[2:].partition("=")
    if name not in known:
        raise unreadable("pr_option")
    if equals or not known[name][1]:
        return [(name, value if equals else "true")], index
    if index >= len(words):
        raise unreadable("pr_option")
    return [(name, words[index])], index + 1


def short_gh_flags(word, words, index, known):
    """([(name, value)], the index after them) for a cluster such as -dHx: each bool letter is on, and the first
    letter that takes a value takes the rest of the word, or the next word when nothing is left. As in pflag, a
    letter followed by = and more takes what follows the =."""
    letters = {short: name for name, (short, _takes) in known.items() if short}
    pairs, cluster = [], word[1:]
    while cluster:
        letter, rest = cluster[0], cluster[1:]
        if letter not in letters:
            raise unreadable("pr_option")
        name = letters[letter]
        if len(rest) > 1 and rest[0] == "=":
            return pairs + [(name, rest[1:])], index
        if not known[name][1]:
            pairs, cluster = pairs + [(name, "true")], rest
            continue
        if rest:
            return pairs + [(name, rest)], index
        if index >= len(words):
            raise unreadable("pr_option")
        return pairs + [(name, words[index])], index + 1
    return pairs, index


def bodies_of(command, step, values):
    """The bodies a gh pr step may send, '' for one gh fills in. gh reads --body-file over --body, so both count."""
    command.cwd = step.cwd
    filled = [""] if any(values.get(name, ["false"])[-1] in TRUE_WORDS for name in FILLS) else []
    texts = [command.expand(values["body"][-1])] if "body" in values else []
    paths = [command.from_file(values["body-file"][-1])] if "body-file" in values else []
    return filled + texts + paths


def body_stop(body, cwd, flags, answer):
    """Refuse a body whose testing part is missing or says nothing about what was left out."""
    if body is None or not flags.runs(f"{PART}.body"):
        return
    if gate().UNREADABLE in body:
        raise unreadable("body")
    checker = check_body()
    found = checker.tested_part(body, cwd, expected=True)
    if found["findings"]:
        raise Stop("", child="body", text=" ".join(checker.sentences({"findings": found["findings"],
                                                                      "notice": None})))
    if found["near_miss"]:
        answer.add_context(message("near_miss.md", heading=found["heading"]))


def gh_repo_place(step, repo_values=()):
    """("rest", the repo's REST path, the variables gh runs with) for the repo a gh step works in. gh api fills
    {owner} and {repo} from GH_REPO, or else from the current repo, as gh pr picks its repo, so -R or --repo
    becomes GH_REPO."""
    variables = {"GH_REPO": repo_values[-1]} if repo_values else repo_variable(step)
    if any(unreadable_mark() in value for value in variables.values()):
        raise unreadable("word")
    return "rest", "repos/{owner}/{repo}", variables


def node_place(node, step):
    """("node", the GraphQL node id, the variables gh runs with) for a repo a mutation names by its node id."""
    if not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9_=-]+", node):
        raise unreadable("head_repo")
    return "node", node, gh_repo_place(step)[2]


def new_pr(step, head, named_head, named_base, places, lookups):
    """[(branch, the commit the PR opens at, cwd, the branches it may go into)] for a new PR at head. places is
    (the head's repo, the base's repo). A head that is named is read on GitHub, since gh then pushes nothing. With
    no head named, gh pushes nothing when its local copy of a GitHub branch is at HEAD, and opens the PR at the
    commit GitHub has for that branch, so each such copy must match GitHub. Otherwise gh pushes HEAD first or
    stops, and the local head is the PR's head."""
    head_place, base_place = places
    branch = head.split(":", 1)[-1]
    commit = (github_head(step.cwd, head, head_place, lookups) if named_head
              else head_of_branch(step.cwd, branch, lookups))
    if not named_head:
        check_github_copies(step.cwd, commit, lookups)
    return [(branch, commit, step.cwd, pr_bases(named_base, step.cwd, branch, base_place, lookups), base_place)]


def check_github_copies(cwd, commit, lookups):
    """Refuse a new PR whose head gh would take from a local copy of a GitHub branch at the commit, when GitHub has
    that branch at another commit or gives none. gh looks such a copy up as refs/remotes/<remote>/<branch>, and
    only for a remote on GitHub."""
    for remote, branch in remote_copies_at(cwd, commit, lookups):
        url = lookups.git(cwd, "remote", "get-url", "--", remote)
        if url is None:
            raise unreadable("remote_url")
        if not is_a_github_url(url, cwd, lookups):
            continue
        on_github = github_head(cwd, branch, ("rest", repo_path_of(url), {}), lookups)
        if on_github != commit:
            raise Stop("github_head_differs.md", branch=branch, short=commit[:7], commit=on_github[:7])


def remote_copies_at(cwd, commit, lookups):
    """[(remote, branch)] for each refs/remotes/<remote>/<branch> at the commit. A remote's name may hold a /, so
    a ref counts for every remote whose name it can start with."""
    listed = lookups.git(cwd, "for-each-ref", "--points-at", commit, "--format=%(refname)", "refs/remotes/")
    remotes = lookups.git(cwd, "remote")
    if listed is None or remotes is None:
        raise unreadable("copies")
    return [(remote, ref[len(prefix):]) for ref in listed.splitlines() for remote in remotes.splitlines()
            for prefix in [f"refs/remotes/{remote}/"] if ref.startswith(prefix) and ref[len(prefix):] != "HEAD"]


def repo_path_of(url):
    """The REST path of the GitHub repo a remote URL names, from its last two path parts, as in
    user@host:owner/name.git or https://github.com/owner/name."""
    parts = re.split(r"[/:]", re.sub(r"(?:\.git)?/*$", "", url))[-2:]
    if len(parts) != 2 or not (re.fullmatch(r"[A-Za-z0-9-]+", parts[0]) and re.fullmatch(r"[\w.-]+", parts[1])):
        raise unreadable("remote_url")
    return f"repos/{parts[0]}/{parts[1]}"


def github_head(cwd, head, place, lookups):
    """The commit a branch is at on GitHub, or a stop when GitHub gives none."""
    if any(unreadable_mark() in word for word in (head, place[1])):
        raise unreadable("word")
    lookup, path_to_commit = head_lookup(head, place)
    output, why = lookups.gh(cwd, "api", *lookup, "--jq", path_to_commit, variables=place[2])
    commit = (output or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Stop("branch_head_unknown.md", branch=head, why=why or "GitHub gave no commit")
    return commit


def head_lookup(head, place):
    """(the gh api words that ask GitHub for the commit a head is at, the jq path to it). owner:branch names the
    branch in the owner's repo of the same name, which only a repos/<owner>/<name> path can say."""
    kind, where, _variables = place
    fork, colon, after_colon = head.partition(":")
    branch = after_colon if colon else head
    if colon and not re.fullmatch(r"[A-Za-z0-9-]+", fork):
        raise unreadable("head")
    if colon and not (kind == "rest" and re.fullmatch(r"repos/[^/]+/[^/]+", where)):
        raise unreadable("head_repo")
    if kind == "node":
        return (["graphql", "-f", "query=" + HEAD_ON_NODE, "-f", f"id={where}", "-f", f"ref=refs/heads/{branch}"],
                ".data.node.ref.target.oid")
    path = f"repos/{fork}/{where.split('/')[2]}" if colon else where
    return [f"{path}/git/ref/heads/{urllib.parse.quote(branch, safe='/')}"], ".object.sha"


def pr_bases(named_base, cwd, branch, place, lookups):
    """The branches a new PR may go into, as gh picks it: the one named, else the branch's gh-merge-base setting,
    else the repo's default branch on GitHub. A base the shell fills in, or one GitHub does not give, matches none,
    so no texts report counts for it."""
    if named_base is not None:
        return set() if unreadable_mark() in named_base else {named_base}
    merge_base = lookups.git(cwd, "config", f"branch.{branch}.gh-merge-base")
    if merge_base:
        return {merge_base}
    output, _why = lookups.gh(cwd, "api", *default_lookup(place), variables=place[2])
    default = (output or "").strip()
    return {default} if default and default != "null" else set()


def default_lookup(place):
    """The gh api words that ask GitHub for a repo's default branch."""
    kind, where, _variables = place
    if kind == "node":
        return ["graphql", "-f", "query=" + DEFAULT_ON_NODE, "-f", f"id={where}", "--jq",
                ".data.node.defaultBranchRef.name"]
    return [where, "--jq", ".default_branch"]


def head_of_branch(cwd, name, lookups):
    if unreadable_mark() in name:
        raise unreadable("word")
    commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
    if commit is None:
        raise unreadable("unknown")
    return commit


def current_branch(cwd, lookups):
    branch = lookups.git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch is None:
        raise unreadable("detached")
    return branch


def pr_step(command, step, flags, answer, lookups):
    """[(branch or PR name, commit, cwd, the branches it may go into)] a gh step needs a report for, after its body
    check."""
    action, rest = step.words[1:3], step.words[3:]
    if step.words[1] == "api":
        return api_step(command, step, flags, answer, lookups)
    if action == ["pr", "create"]:
        written_out(step)
        values, _positional = gh_pr_flags(gate().without_redirects(rest), "create")
        for body in bodies_of(command, step, values) or [""]:
            body_stop(body, step.cwd, flags, answer)
        if not flags.runs(f"{PART}.report"):
            return []
        heads, bases = values.get("head"), values.get("base")
        head = heads[-1] if heads else current_branch(step.cwd, lookups)
        place = gh_repo_place(step, values.get("repo", ()))
        return new_pr(step, head, bool(heads), bases[-1] if bases else None, (place, place), lookups)
    if action == ["pr", "edit"]:
        values, _positional = gh_pr_flags(gate().without_redirects(rest), "edit")
        bodies = bodies_of(command, step, values)
        if bodies:
            written_out(step)
        for body in bodies:
            body_stop(body, step.cwd, flags, answer)
        return []
    if action == ["issue", "edit"] and flags.runs(f"{PART}.body"):
        values, positional = gh_pr_flags(gate().without_redirects(rest), "issue edit")
        bodies = bodies_of(command, step, values)
        places = [issue_place(word, values.get("repo", ()), step) for word in positional] if bodies else []
        if any(place and may_be_a_pr(step, *place, lookups) for place in places):
            written_out(step)
            for body in bodies:
                body_stop(body, step.cwd, flags, answer)
        return []
    if action == ["pr", "ready"] and flags.runs(f"{PART}.report"):
        words = gate().without_redirects(rest)
        if undoes_ready(words):
            return []
        written_out(step)
        return ready_pr(step, *ready_words(words), lookups)
    return []


def undoes_ready(words):
    """Whether gh pr ready turns the PR back into a draft. gh keeps the last --undo before a lone --, where the
    word after a lone -R or --repo is the repo. A bare --undo is true, and --undo=value is true only for the words
    Go's strconv.ParseBool reads as true."""
    undos, index = [], 0
    while index < len(words) and words[index] != "--":
        word = words[index]
        if word == "--undo" or word.startswith("--undo="):
            undos.append(word.partition("=")[2] if "=" in word else "true")
        index += 2 if word in PR_REPO_OPTIONS else 1
    return bool(undos) and undos[-1] in TRUE_WORDS


def ready_words(words):
    """(the PR a gh pr ready step names, as a list of at most one word, and its repo options as written). gh reads
    the word after a lone -R or --repo as the repo, so that word never names the PR."""
    named, repo, index = [], [], 0
    while index < len(words):
        word = words[index]
        width = 2 if word in PR_REPO_OPTIONS else 1
        if width == 2 or word.startswith("--repo=") or (word.startswith("-R") and len(word) > 2):
            repo.extend(words[index:index + width])
        elif not word.startswith("-"):
            named.append(word)
        index += width
    return named[:1], repo


def repo_variable(step):
    """{"GH_REPO": value} for the last GH_REPO set in front of gh, the one the shell passes on, else {}. gh reads
    it as the repo when no -R or --repo names one."""
    values = [word.split("=", 1)[1] for word in step.before if word.startswith("GH_REPO=")]
    return {"GH_REPO": values[-1]} if values else {}


def ready_pr(step, named, repo, lookups):
    """The PR a ready step marks, read from gh in the repo the step names with its options or GH_REPO: its head
    commit and the branch it goes into. With no number, gh finds the current branch's PR. When gh can not say the
    head, the step stops, with or without a number."""
    variables = repo_variable(step)
    if any(unreadable_mark() in word for word in named + repo + list(variables.values())):
        raise unreadable("word")
    output, why = lookups.gh(step.cwd, "pr", "view", *named, *repo, "--json", "headRefOid,baseRefName",
                             variables=variables)
    viewed = parsed_json(output)
    viewed = viewed if isinstance(viewed, dict) else {}
    commit, base = viewed.get("headRefOid"), viewed.get("baseRefName")
    branch = None if named else lookups.git(step.cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    bases = {base} if isinstance(base, str) and base else set()
    if named and not commit:
        raise Stop("pr_head_unknown.md", number=named[0], why=why or "gh gave no head commit")
    if not commit:
        raise Stop("branch_head_unknown.md", branch=branch or "HEAD", why=why or "gh gave no head commit")
    place = ("rest", "repos/{owner}/{repo}", {"GH_REPO": repo_value(repo)} if repo else variables)
    return [(f"PR {named[0]}" if named else branch or "HEAD", commit, step.cwd, bases, place)]


def repo_value(repo):
    """The repo the last of gh's -R or --repo options names, written as -R X, -RX, --repo X or --repo=X."""
    last = repo[-2:] if len(repo) > 1 and repo[-2] in PR_REPO_OPTIONS else repo[-1:]
    word = last[-1]
    if len(last) == 2:
        return word
    return word.split("=", 1)[1] if word.startswith("--repo=") else word[3:] if word.startswith("-R=") else word[2:]


def api_fields(command, items):
    """The fields a gh api command sends, by key, with -F's @file read. As in gh, -f fields come first and -F
    fields after them, and a later field with the same key wins."""
    fields = {}
    ordered = [item for kind in ("--raw-field", "--field") for item in items if item[0] == kind]
    for name, field in ordered:
        key, _equals, value = field.partition("=")
        if name == "--field" and value.startswith("@"):
            value = command.from_file(value[1:])
        fields[key] = command.expand(value)
    return fields


def api_step(command, step, flags, answer, lookups):
    """New PRs and body edits sent through gh api, by REST or GraphQL."""
    items = gate().gh_api_items(step.words[2:])
    if any(name == "unknown" for name, _ in items):
        raise unreadable("api_option")
    command.cwd = step.cwd
    fields = api_fields(command, items)
    endpoints = [value for name, value in items if name is None]
    endpoint = api_path(endpoints[0]) if endpoints else ""
    methods = [value for name, value in items if name == "--method"]
    inputs = [value for name, value in items if name == "--input"]
    sends = any(name in ("--field", "--raw-field") for name, _ in items) or inputs
    method = methods[-1].upper() if methods else ("POST" if sends else "GET")  # as gh picks it
    files = [sent_object(command.from_file(path), endpoint == "graphql") for path in inputs]
    if endpoint == "graphql":
        if flags.runs(f"{PART}.report") or flags.runs(f"{PART}.body"):
            query_read_in_full(step, gate().graphql_query_source(items, command))
        return graphql_step(graphql_sent(fields, files), step, flags, answer, lookups)
    fields = {**fields, **{key: value for sent in files for key, value in flat_fields(sent).items()}}
    new = re.fullmatch(f"({REPO_PATH})/pulls", endpoint)
    if new and method == "POST":
        written_out(step)
        body_stop(fields.get("body", ""), step.cwd, flags, answer)
        if not flags.runs(f"{PART}.report"):
            return []
        if not fields.get("head"):
            raise unreadable("head")
        if "head_repo" in fields:
            raise unreadable("head_repo")
        place = ("rest", new.group(1), gh_repo_place(step)[2])
        return new_pr(step, fields["head"], True, fields.get("base"), (place, place), lookups)
    if re.fullmatch(REPO_PATH + r"/pulls/\d+", endpoint) and method != "GET":
        if "draft" in fields and flags.runs(f"{PART}.report"):
            raise unreadable("draft")
        if "body" in fields:
            written_out(step)
            body_stop(fields["body"], step.cwd, flags, answer)
    if (re.fullmatch(REPO_PATH + r"/issues/\d+", endpoint) and method != "GET" and "body" in fields
            and flags.runs(f"{PART}.body") and may_be_a_pr(step, endpoint, gh_repo_place(step)[2], lookups)):
        written_out(step)
        body_stop(fields["body"], step.cwd, flags, answer)
    return []


def sent_object(text, graphql):
    """The JSON object an --input file sends, refusing a file that holds none."""
    try:
        sent = json.loads(text)
    except ValueError:
        sent = None
    if not isinstance(sent, dict):
        raise unreadable("query" if graphql else "head")
    return sent


def graphql_sent(fields, files):
    """(the query, the variables gh sends, {name: [value]} for the other values the call names) of a GraphQL
    call. gh makes every -f field but query and operationName a variable. With --input it sends the last file as
    it is and puts the -f fields in the URL, so only that file's variables are sent. The other values still get
    read, so the gate never reads less than GitHub may take."""
    plain = {key: value for key, value in fields.items() if key not in ("query", "operationName")}
    if not files:
        return fields.get("query", ""), plain, {key: [fields[key]] for key in ("operationName",) if key in fields}
    last = files[-1]
    query = last["query"] if isinstance(last.get("query"), str) else fields.get("query", "")
    loose = [plain, *({**flat_fields(sent), **file_variables(sent)} for sent in files[:-1]),
             flat_fields({key: value for key, value in last.items() if key not in ("query", "variables")})]
    names = {name for found in loose for name in found}
    return query, file_variables(last), {name: [found[name] for found in loose if name in found] for name in names}


def file_variables(sent):
    """The variables an --input file sends, named as gh's -f fields name them, as in pr[body]."""
    variables = sent.get("variables")
    if isinstance(variables, str):
        try:
            variables = json.loads(variables)
        except ValueError:
            variables = None
    return flat_fields(variables) if isinstance(variables, dict) else {}


def flat_fields(sent, prefix=""):
    """An object's values by gh's key[subkey] names, as -f pr[body]=x writes them. A value that is not text
    stays JSON text."""
    return dict(pair for key, value in sent.items()
                for pair in flat_pairs(f"{prefix}[{key}]" if prefix else key, value))


def flat_pairs(name, value):
    if isinstance(value, dict):
        return flat_fields(value, name).items()
    return [(name, value if isinstance(value, str) else json.dumps(value))]


def may_be_a_pr(step, issue_path, variables, lookups):
    """Whether an issue that an issues endpoint names may be a PR, since GitHub's REST API counts every PR as an
    issue and edits its body there too. Only GitHub's answer that it is a plain issue says no."""
    if unreadable_mark() in issue_path:
        return True
    output, _why = lookups.gh(step.cwd, "api", issue_path, "--jq", 'has("pull_request")', variables=variables)
    return (output or "").strip() != "false"


def issue_place(word, repo_values, step):
    """(the issues endpoint, the variables gh runs with) for an issue or PR that gh issue names by number, #number or
    URL, read as gh reads it, or None for a word gh refuses. A URL names its own repo, as in gh. An http or https
    word the gate can not read as an issue link gets the unreadable mark as its endpoint, so it counts as a PR."""
    if re.match(r"https?:", word, re.I):
        return linked_issue_place(word) or (unreadable_mark(), {})
    number = re.fullmatch(r"#?\+?(\d+)", word)
    if number:
        return f"repos/{{owner}}/{{repo}}/issues/{int(number.group(1))}", gh_repo_place(step, repo_values)[2]
    return None


def linked_issue_place(word):
    """(the issues endpoint, the variables gh runs with) for an issue link, or None. gh parses the link, decodes
    its path and matches /owner/name/issues/number or /owner/name/pull/number at the path's start, so
    .../%70ull/7 is PR 7 too. The decoded owner and name are encoded again, so the lookup asks for that repo only."""
    try:
        link = urllib.parse.urlsplit(word)
        host = link.hostname
    except ValueError:
        return None
    named = re.match(r"/([^/]+)/([^/]+)/(?:issues|pull)/([0-9]+)", urllib.parse.unquote(link.path))
    if not (host and named):
        return None
    owner, name, number = (urllib.parse.quote(part, safe="") for part in named.groups())
    return f"repos/{owner}/{name}/issues/{int(number)}", {"GH_REPO": f"{host}/{owner}/{name}"}


def api_path(endpoint):
    """gh api's endpoint as a path, so a full URL such as https://api.github.com/repos/... reads as repos/..."""
    return re.sub(r"^[a-z]+://[^/]+/(?:api/v3/)?", "", endpoint.split("?", 1)[0], flags=re.I).strip("/")


def pr_mutations(query):
    """The PR fields a GraphQL mutation names, aliased or not, so 'opened: createPullRequest' counts."""
    if not re.search(r"\bmutation\b", query):
        return set()
    return {name for name in PR_MUTATIONS if re.search(r"\b" + name + r"\b", query)}


def query_read_in_full(step, source):
    """Refuse a GraphQL call whose query the gate can not read in full. A query from a file, a heredoc or
    $(cat file) counts only when nothing on the line can change it first, so no command runs before the step
    and the shell fills in none of its words."""
    if source is None:
        raise unreadable("query")
    if source == "file":
        written_out(step)
        if any(unreadable_mark() in word for word in step.words):
            raise unreadable("query")


def graphql_step(sent_call, step, flags, answer, lookups):
    query, sent, others = sent_call
    names = pr_mutations(query)
    if "markPullRequestReadyForReview" in names and flags.runs(f"{PART}.report"):
        raise unreadable("node")
    if not names & {"createPullRequest", "updatePullRequest"}:
        return []
    written_out(step)
    code, strings = graphql_code(query)

    def values(key):
        return graphql_values(code, strings, key, sent, others)
    in_input = input_bodies(code, sent, others) if flags.runs(f"{PART}.body") else []
    for text in values("body") + in_input or ([""] if "createPullRequest" in names else []):
        body_stop(text, step.cwd, flags, answer)
    if "createPullRequest" not in names or not flags.runs(f"{PART}.report"):
        return []
    branch = one_value(values("headRefName"))
    if not branch:
        raise unreadable("head")
    base_place = node_place(one_value(values("repositoryId")), step)
    head_node = one_value(values("headRepositoryId"))
    head_place = base_place if head_node is None else node_place(head_node, step)
    return new_pr(step, branch, True, one_value(values("baseRefName")), (head_place, base_place), lookups)


def input_bodies(code, sent, others):
    """The bodies an updatePullRequest sets inside an input object variable, such as -f pr[body]=x for input: $pr.
    An input object the call does not send, or a field name the shell fills in, may hold a body."""
    names = re.findall(r"\bupdatePullRequest\s*\(\s*input\s*:\s*\$(\w+)", code)
    if names and any(unreadable_mark() in key for key in [*sent, *others]):
        raise unreadable("body")
    if any(not any(key.startswith(name + "[") for key in sent) for name in names):
        raise unreadable("body")
    return [value for name in names
            for value in ([sent[name + "[body]"]] if name + "[body]" in sent else []) + others.get(name + "[body]", [])]


# A GraphQL string, set aside so a key is never read inside one, or a comment, which runs to the end of its line
GRAPHQL_STRING_OR_COMMENT = re.compile(r'"(?:[^"\\\n\r]|\\.)*"|#[^\n\r]*')


def graphql_code(query):
    """(the query with each string set aside as "0", "1", ..., and its comments and commas read as spaces, the
    strings), as GitHub reads it, since GraphQL skips commas as it skips spaces. Refuses a block string or a quote
    mark the gate can not pair, which the gate does not read."""
    if '"""' in query:
        raise unreadable("query")
    strings = []

    def set_aside(found):
        if found.group().startswith("#"):
            return " "
        strings.append(found.group())
        return f'"{len(strings) - 1}"'
    code = GRAPHQL_STRING_OR_COMMENT.sub(set_aside, query).replace(",", " ").replace("\ufeff", " ")
    if code.count('"') != 2 * len(strings):
        raise unreadable("query")
    return code, strings


def graphql_values(code, strings, key, sent, others):
    """Every string the mutations set key to, written in the query or passed as a variable. A variable the call
    does not send takes its default, as on GitHub. The other values the call names for it count too."""
    written = [graphql_text(strings[int(index)]) for index in re.findall(r"\b" + key + r'\s*:\s*"(\d+)"', code)]
    variables = re.findall(r"\b" + key + r"\s*:\s*\$(\w+)", code)
    return written + [value for name in variables
                      for value in ([sent[name]] if name in sent else variable_default(code, strings, name))
                      + others.get(name, [])]


def variable_default(code, strings, name):
    """The default of a variable the call does not send, as a list: empty when it has none or a null one. Refuses a
    default that is not a plain string or null, or a second default for the same name."""
    defaults = re.findall(r"\$" + name + r'\s*:[^=$)]*=\s*("\d+"|null\b)?', code)
    if len(defaults) > 1 or "" in defaults:
        raise unreadable("query")
    return [graphql_text(strings[int(found[1:-1])]) for found in defaults if found != "null"]


def graphql_text(string):
    """A GraphQL string's text, which escapes as JSON does."""
    try:
        return json.loads(string)
    except ValueError:
        return string[1:-1]


def one_value(values):
    """The one value a mutation sets, or None. Refuses values that differ, since the gate can not tell which one
    GitHub uses."""
    if len(set(values)) > 1:
        raise unreadable("query")
    return values[0] if values else None


# The report check


def bases_on_github(report, bases, cwd, place, lookups):
    """{branch: the commit GitHub has it at, or None} for the branches the PR may go into. Only a texts report's
    own base is looked up, since its changes are counted from there."""
    base = report.get("base") if report.get("kind") == "texts" else None
    branch = base.get("branch") if isinstance(base, dict) else None
    return {name: github_head(cwd, name, place, lookups) if name == branch else None for name in bases}


def report_stop(branch, commit, cwd, bases, place, lookups):
    store = reports()
    short_sha = commit[:7]
    report, broken = store.find(cwd, commit, lookups.git)
    if broken:
        raise Stop("report_unreadable.md", short=short_sha)
    if report is None:
        raise Stop("no_report.md", short=short_sha, branch=branch)
    found = store.problem(report, cwd, commit, lookups.git, bases_on_github(report, bases, cwd, place, lookups))
    if found:
        name, values = found
        raise Stop(name, short=short_sha, **values)


def check(command_line, cwd, flags, answer):
    report_on = flags.runs(f"{PART}.report")
    command, steps = read_steps(command_line, cwd)
    lookups = Lookups.inside_the_hook(answer.started)
    needed, candidates = [], []
    for step in steps:
        if os.path.basename(step.words[0]) == "git":
            if report_on:
                candidates += pushed(step, lookups)
        else:
            needed += pr_step(command, step, flags, answer, lookups)
    if candidates:
        needed += with_open_pr(candidates, lookups)
    if report_on:
        for branch, commit, here, bases, place in needed:
            report_stop(branch, commit, here, bases, place, lookups)


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or hook_input.get("tool_name") != "Bash":
        return
    if not (flags.runs(f"{PART}.report") or flags.runs(f"{PART}.body")):
        return
    command_line = (hook_input.get("tool_input") or {}).get("command") or ""
    cwd = hook_input.get("cwd") or os.getcwd()
    if not may_push_or_open_a_pr(command_line, cwd):
        return
    try:
        check(command_line, cwd, flags, answer)
    except Stop as stop:
        answer.signal(f"{PART}.{stop.child}", "blocked")
        answer.refuse(stop.values["text"] if stop.child == "body" else message(stop.name, **stop.values))
