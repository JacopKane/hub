"""The PR test gate. A PR step needs a passing test report for its commit.

Before a Bash call runs, it reads the command with the public text gate's
shell parser and stops three steps until the commit they send has a passing
report from hub's pr-tested step:

- a push to a GitHub branch that has an open PR (one gh lookup at most);
- a new PR, through gh pr create or gh api, for the PR's head commit;
- marking a PR ready, for the PR's head commit.

A new PR and a body edit also need a body whose testing part says what was
not tested, checked by the pr part's own body check, so pr-check and the gate
agree. Pushes to the default branch, tags, deletes, dry runs, pushes to
another host and pushes with nothing new stay quiet. A step the gate can not
read, such as one inside a wrapper, a substitution or after another command,
is refused and asked to be written out. Its git and gh lookups share one
deadline, so the answer comes well inside the hook's time.

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
PUSH_VALUE_OPTIONS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
EVERY_BRANCH = {"--all", "--mirror", "--branches"}
FILL_OPTIONS = {"--fill", "-f", "--fill-first", "--fill-verbose", "--web", "-w"}
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
OTHER_REPO_OPTIONS = {"--git-dir", "--work-tree"}
OTHER_REPO_VARIABLES = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
GIT_OR_GH = re.compile(r"\b(?:git|gh)\b")
GH_ALIASES = {("pr", "new"): ("pr", "create")}
PR_MUTATIONS = ("createPullRequest", "updatePullRequest", "markPullRequestReadyForReview")
GITHUB = re.compile(r"(?:^|[@/])github\.com(?:[:/]|$)", re.I)
WHY = {
    "parse": "the line does not parse",
    "inside": "it runs inside another command",
    "feeder": "xargs or parallel fills in its words",
    "before": "another command runs before it on the same line",
    "word": "a variable or a pattern names the branch",
    "every": "it pushes every branch",
    "detached": "HEAD is not on a branch",
    "unknown": "the branch is not in this repo",
    "node": "a node id names the PR, not its number",
    "head": "the PR's head branch is not named",
}


class Stop(Exception):
    """The gate refuses the call with one of its messages."""

    def __init__(self, name, child="report", **values):
        super().__init__(name)
        self.name = name
        self.child = child
        self.values = values


def unreadable(why):
    return Stop("unreadable_push.md", why=WHY[why])


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

    def run(self, words, cwd, cap):
        """(exit code, output, error), or None when the call took longer than it may."""
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise Stop("slow.md")
        process = subprocess.Popen(words, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   errors="replace", start_new_session=True,
                                   env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
                                            GH_PROMPT_DISABLED="1"))
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
            done = self.run(["git", *words], cwd, GIT_SECONDS)
            if done is None:
                raise Stop("slow.md")
            self.cache[key] = done[1].strip() if done[0] == 0 else None
        return self.cache[key]

    def gh(self, cwd, *words):
        """(gh's output or None, why it failed). A gh that can not start is a failed lookup like any other."""
        try:
            done = self.run(["gh", *words], cwd, GH_SECONDS)
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
    named = [target for target, _text in parser.public_steps(line, cwd, log=False) if target in RELEVANT]
    command = parser.Command(line, cwd, read_log=False)
    segments = command.segments()
    if segments is None:
        if named or re.search(r"\bgit\b.*\bpush\b", command.flat):
            raise unreadable("parse")
        return command, []
    found, here = [], cwd
    for position, words in enumerate(segments):
        if len(words) == 2 and words[0] == "cd" and unreadable_mark() not in words[1]:
            here = os.path.join(here, os.path.expanduser(words[1]))
            continue
        for index, word in enumerate(words):
            program = os.path.basename(word)
            if program not in ("git", "gh"):
                continue
            command.cwd = here
            step_words = words[index:] if program == "git" else without_gh_alias(words[index:])
            target = (parser.git_target if program == "git" else parser.gh_target)(step_words, command)
            api = program == "gh" and step_words[1:2] == ["api"]
            if not target or (target[0] not in RELEVANT and not api):
                continue
            if any(os.path.basename(before) in parser.FEEDERS for before in words[:index]):
                raise unreadable("feeder")
            found.append(Step(step_words, words[:index], segments[:position], here, target[0]))
    if len(named) > len([step for step in found if step.target in RELEVANT]):
        raise unreadable("inside")
    return command, found


def may_push_or_open_a_pr(line, cwd):
    """Whether the gate reads the line. It does when the line does not parse, when git or gh is in its text or
    parsed words (so g''it counts), or when the text gate finds a public step inside it, as in bash -c. A line
    that only a variable fills in, such as $CMD, is left to the text gate."""
    parser = gate()
    segments = parser.Command(line, cwd, read_log=False).segments()
    words = [word for segment in segments or [] for word in segment]
    return (segments is None or any(GIT_OR_GH.search(text) for text in [line, *words])
            or any(target != "wrapped" for target, _text in parser.public_steps(line, cwd, log=False)))


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
        if word in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and index + 1 < len(words):
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


def without_redirects(words):
    kept, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif gate().REDIRECT.fullmatch(word):
            skip = True
        elif not gate().REDIRECT.match(word):
            kept.append(word)
    return kept


# Pushes


def push_words(rest):
    """(positional words, whether a reason that needs no state keeps it quiet, whether --tags is on)."""
    positional, quiet, tags = [], False, False
    index = 0
    rest = without_redirects(rest)
    while index < len(rest):
        word = rest[index]
        if word in PUSH_VALUE_OPTIONS:
            index += 2
            continue
        if word == "--":
            positional += rest[index + 1:]
            break
        name = word.split("=", 1)[0]
        if name in EVERY_BRANCH:
            raise unreadable("every")
        if name == "--tags":
            tags = True
        elif name in ("--delete", "--dry-run"):
            quiet = True
        elif word.startswith("-") and not word.startswith("--") and len(word) > 1:
            quiet = quiet or bool(set(word[1:]) & {"d", "n"})
        elif not word.startswith("-"):
            positional.append(word)
        index += 1
    return positional, quiet, tags


def pushed(step, lookups):
    """[(branch, commit, cwd)] the push sends to a GitHub branch that is not the default, with something new."""
    cwd, words = git_place(step)
    positional, quiet, tags = push_words(words[1:])
    specs = [spec[1:] if spec.startswith("+") else spec for spec in positional[1:]]
    if not quiet and ":" in specs:
        raise unreadable("every")  # a lone colon pushes each branch that has a match on the remote
    if quiet or (tags and not specs) or (specs and all(spec.startswith(":") for spec in specs)):
        return []
    written_out(step)
    hidden_mark = unreadable_mark()
    if any(hidden_mark in word or "$" in word or "*" in word for word in positional):
        raise unreadable("word")
    branch = lookups.git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote = positional[0] if positional else None
    if remote is None:
        if branch is None:
            raise unreadable("detached")
        remote = (lookups.git(cwd, "config", f"branch.{branch}.pushRemote")
                  or lookups.git(cwd, "config", f"branch.{branch}.remote") or "origin")
    if not GITHUB.search(lookups.git(cwd, "ls-remote", "--get-url", remote) or ""):
        return []
    found = []
    for spec in specs or ["HEAD"]:
        source, _colon, target = spec.partition(":")
        if not source or "refs/tags/" in (source[:10], target[:10]):
            continue
        source, target = short(source), short(target)
        if source == "HEAD":
            commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
            target = target or branch
            if not target or not commit:
                raise unreadable("detached")
        else:
            commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{source}")
            if commit is None:
                if lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/tags/{source}"):
                    continue
                commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"{source}^{{commit}}")
                if commit is None or not target:
                    raise unreadable("unknown")
            target = target or source
        if target.startswith("refs/") or target in default_branches(cwd, remote, lookups):
            continue
        if lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}") == commit:
            continue
        found.append((target, commit, cwd))
    return found


def short(ref):
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


def default_branches(cwd, remote, lookups):
    named = lookups.git(cwd, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if named and named.startswith(remote + "/"):
        return {named[len(remote) + 1:]}
    return {"main", "master"}


def with_open_pr(candidates, lookups):
    """The pushed branches that have an open PR. More than one branch, or a failed lookup, counts as open."""
    if len({branch for branch, _commit, _cwd in candidates}) != 1:
        return candidates
    branch, _commit, cwd = candidates[0]
    output, _why = lookups.gh(cwd, "pr", "list", "--head", branch, "--state", "open", "--json", "number",
                              "--limit", "1")
    try:
        listed = json.loads(output) if output is not None else None
    except ValueError:
        listed = None
    return candidates if not isinstance(listed, list) or listed else []


# PR steps


def body_of(command, step, rest, text_options=("--body", "-b"), file_options=("--body-file", "-F")):
    """The body a gh pr step sends: '' when gh fills it in, None when it names none."""
    parser = gate()
    if any(word in FILL_OPTIONS for word in rest):
        return ""
    command.cwd = step.cwd
    texts = parser.option_values(rest, list(text_options), attached=True)
    paths = parser.option_values(rest, list(file_options), attached=True)
    if texts:
        return command.expand(texts[-1])
    if paths:
        return command.from_file(paths[-1])
    return None


def body_stop(body, cwd, flags, answer):
    """Refuse a body whose testing part is missing or says nothing about what was left out."""
    if body is None or gate().UNREADABLE in body or not flags.runs(f"{PART}.body"):
        return
    checker = check_body()
    found = checker.tested_part(body, cwd, expected=True)
    if found["findings"]:
        raise Stop("", child="body", text=" ".join(checker.sentences({"findings": found["findings"],
                                                                      "notice": None})))
    if found["near_miss"]:
        answer.add_context(message("near_miss.md", heading=found["heading"]))


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
    """[(branch or PR name, commit, cwd)] a gh step needs a report for, after its body check."""
    action, rest = step.words[1:3], step.words[3:]
    if step.words[1] == "api":
        return api_step(command, step, flags, answer, lookups)
    if action == ["pr", "create"]:
        written_out(step)
        body = body_of(command, step, rest)
        body_stop("" if body is None else body, step.cwd, flags, answer)
        if not flags.runs(f"{PART}.report"):
            return []
        heads = gate().option_values(rest, ["--head", "-H"], attached=True)
        branch = heads[-1].split(":", 1)[-1] if heads else current_branch(step.cwd, lookups)
        return [(branch, head_of_branch(step.cwd, branch, lookups), step.cwd)]
    if action == ["pr", "edit"]:
        body = body_of(command, step, rest)
        if body is not None:
            written_out(step)
            body_stop(body, step.cwd, flags, answer)
        return []
    if action == ["pr", "ready"] and "--undo" not in rest and flags.runs(f"{PART}.report"):
        written_out(step)
        named = [word for word in without_redirects(rest) if not word.startswith("-")]
        if not named:
            branch = lookups.git(step.cwd, "symbolic-ref", "--quiet", "--short", "HEAD") or "HEAD"
            commit = lookups.git(step.cwd, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
            if commit is None:
                raise unreadable("detached")
            return [(branch, commit, step.cwd)]
        if unreadable_mark() in named[0]:
            raise unreadable("word")
        output, why = lookups.gh(step.cwd, "pr", "view", named[0], "--json", "headRefOid")
        try:
            commit = json.loads(output).get("headRefOid") if output is not None else None
        except (ValueError, AttributeError):
            commit, why = None, "gh gave no head commit"
        if not commit:
            raise Stop("pr_head_unknown.md", number=named[0], why=why or "gh gave no head commit")
        return [(f"PR {named[0]}", commit, step.cwd)]
    return []


def api_fields(command, rest):
    fields = {}
    for name in ("-f", "--raw-field", "-F", "--field"):
        for field in gate().option_values(rest, [name]):
            key, _equals, value = field.partition("=")
            if name in ("-F", "--field") and value.startswith("@"):
                value = command.from_file(value[1:])
            fields[key] = command.expand(value)
    return fields


def api_step(command, step, flags, answer, lookups):
    """New PRs and body edits sent through gh api, by REST or GraphQL."""
    parser = gate()
    rest = step.words[2:]
    command.cwd = step.cwd
    fields = api_fields(command, rest)
    endpoints, skip = [], False
    for word in rest:
        if skip:
            skip = False
        elif word in parser.GH_API_VALUE_OPTIONS:
            skip = True
        elif not word.startswith("-"):
            endpoints.append(word)
    endpoint = endpoints[0].split("?", 1)[0].strip("/") if endpoints else ""
    methods = parser.option_values(rest, ["-X", "--method"])
    inputs = parser.option_values(rest, ["--input"])
    method = methods[-1].upper() if methods else ("POST" if fields or inputs else "GET")  # as gh picks it
    for path in inputs:
        try:
            sent = json.loads(command.from_file(path))
        except ValueError:
            sent = None
        if not isinstance(sent, dict):
            raise unreadable("head")
        fields.update({key: str(value) for key, value in sent.items()})
    if endpoint == "graphql":
        return graphql_step(fields, step, flags, answer, lookups)
    if re.fullmatch(r"repos/[^/]+/[^/]+/pulls", endpoint) and method == "POST":
        written_out(step)
        body_stop(fields.get("body", ""), step.cwd, flags, answer)
        if not flags.runs(f"{PART}.report"):
            return []
        if not fields.get("head"):
            raise unreadable("head")
        branch = fields["head"].split(":", 1)[-1]
        return [(branch, head_of_branch(step.cwd, branch, lookups), step.cwd)]
    if re.fullmatch(r"repos/[^/]+/[^/]+/pulls/\d+", endpoint) and method != "GET" and "body" in fields:
        written_out(step)
        body_stop(fields["body"], step.cwd, flags, answer)
    return []


def pr_mutations(query):
    """The PR fields a GraphQL mutation names, aliased or not, so 'opened: createPullRequest' counts."""
    if not re.search(r"\bmutation\b", query):
        return set()
    return {name for name in PR_MUTATIONS if re.search(r"\b" + name + r"\b", query)}


def graphql_step(fields, step, flags, answer, lookups):
    query = fields.get("query", "")
    names = pr_mutations(query)
    if "markPullRequestReadyForReview" in names and flags.runs(f"{PART}.report"):
        raise unreadable("node")
    if not names & {"createPullRequest", "updatePullRequest"}:
        return []
    written_out(step)
    body = graphql_value(query, "body", fields)
    if "createPullRequest" not in names:
        body_stop(body, step.cwd, flags, answer)
        return []
    body_stop(body or "", step.cwd, flags, answer)
    if not flags.runs(f"{PART}.report"):
        return []
    branch = graphql_value(query, "headRefName", fields)
    if not branch:
        raise unreadable("head")
    return [(branch, head_of_branch(step.cwd, branch, lookups), step.cwd)]


def graphql_value(query, key, fields):
    """A string the mutation sets, written in the query or passed as a variable, or None."""
    written = re.search(key + r'\s*:\s*"((?:[^"\\]|\\.)*)"', query)
    if written:
        try:
            return json.loads('"' + written.group(1) + '"')
        except ValueError:
            return written.group(1)
    variable = re.search(key + r"\s*:\s*\$(\w+)", query)
    return fields.get(variable.group(1)) if variable else None


# The report check


def report_stop(branch, commit, cwd, lookups):
    store = reports()
    short_sha = commit[:7]
    report, broken = store.find(cwd, commit, lookups.git)
    if broken:
        raise Stop("report_unreadable.md", short=short_sha)
    if report is None:
        raise Stop("no_report.md", short=short_sha, branch=branch)
    found = store.problem(report, cwd, commit, lookups.git)
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
        for branch, commit, here in needed:
            report_stop(branch, commit, here, lookups)


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
