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
another host and pushes with nothing new stay quiet. A branch named alone in
a push counts at the remote branch git sends it to, by the remote's push
settings or the branch's upstream.

A step the gate can not read, such as one inside a wrapper, a substitution
or after another command, is refused and asked to be written out. Its git
and gh lookups share one deadline, so the answer comes well inside the
hook's time.

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
# git push's long options, so a shortened one such as --mirr or --no-dry reads as the option git takes it for
PUSH_LONG_OPTIONS = ("all", "atomic", "branches", "delete", "dry-run", "exec", "follow-tags", "force",
                     "force-if-includes", "force-with-lease", "ipv4", "ipv6", "mirror", "no-verify", "porcelain",
                     "progress", "prune", "push-option", "quiet", "receive-pack", "recurse-submodules", "repo",
                     "set-upstream", "signed", "tags", "thin", "verbose")
NEVER_NEGATED = {"ipv4", "ipv6"}
PUSH_ALIASES = {"--branches": "--all"}
PUSH_SHORT_OPTIONS = {"d": "--delete", "n": "--dry-run", "o": "--push-option", "u": "--set-upstream", "f": "--force",
                      "q": "--quiet", "v": "--verbose", "4": "--ipv4", "6": "--ipv6"}
PUSH_VALUE_OPTIONS = {"--push-option", "--repo", "--receive-pack", "--exec", "--recurse-submodules"}
EVERY_BRANCH = ("--all", "--mirror")
BRANCH_PREFIXES = ("refs/heads/", "heads/")
FILL_OPTIONS = {"--fill", "-f", "--fill-first", "--fill-verbose", "--web", "-w"}
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
OTHER_REPO_OPTIONS = {"--git-dir", "--work-tree"}
OTHER_REPO_VARIABLES = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"}
GIT_OR_GH = re.compile(r"\b(?:git|gh)\b")
GH_ALIASES = {("pr", "new"): ("pr", "create")}
PR_MUTATIONS = ("createPullRequest", "updatePullRequest", "markPullRequestReadyForReview")
GITHUB = re.compile(r"(?:^|[@/])github\.com(?:[:/]|$)", re.I)
CONFIG_OPTIONS = {"-c", "--config-env"}
CONFIG_VARIABLE = re.compile(r"GIT_CONFIG(?:_[A-Z0-9_]+)?=")
PUSH_SETTING_SECTIONS = {"push", "remote", "url", "branch", "include", "includeif"}
PUSH_SETTINGS = r"^(push\.default|remote\..+\.(push|mirror)|branch\..+\.merge)$"
FALSE_WORDS = {"false", "no", "off", "0"}
PUSH_CONFIG_WHY = {
    "matching": "the repo's push.default sends every branch that has a match on the remote",
    "refspec": "the remote has its own setting for what a push sends",
    "mirror": "the remote is set to mirror every branch",
    "option": "git -c, --config-env or a GIT_CONFIG variable changes its push settings for this one command",
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


def push_settings_stop(why):
    return Stop("push_config.md", why=PUSH_CONFIG_WHY[why])


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
        if word in ("-C", "-c", "--config-env", "--git-dir", "--work-tree", "--namespace") and index + 1 < len(words):
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


def option_spellings(option):
    """(spelling, option, whether it turns the option on) for each way git writes a long option out in full."""
    if option.startswith("no-"):
        return [(option, option, True), (option[3:], option, False)]
    return [(option, option, True)] + ([] if option in NEVER_NEGATED else [("no-" + option, option, False)])


PUSH_SPELLINGS = [spelling for option in PUSH_LONG_OPTIONS for spelling in option_spellings(option)]


def full_push_option(name):
    """(the long option git reads for a word such as --mirr or --no-dry, whether the word turns it on). A word
    that starts only one option is that option, as in git, and a word that starts more than one is refused."""
    word = name[2:]
    exact = [(f"--{option}", on) for spelling, option, on in PUSH_SPELLINGS if spelling == word]
    shortened = {(f"--{option}", on) for spelling, option, on in PUSH_SPELLINGS if spelling.startswith(word)}
    if not exact and len(shortened) > 1:
        raise unreadable("option")
    option, on = (exact or sorted(shortened) or [(name, True)])[0]
    return PUSH_ALIASES.get(option, option), on


def push_items(words):
    """(the option in full, its value) for each of git push's words in order, or (None, word) for a positional
    word. A switch's value is whether it is on. A value option takes its value the way git does."""
    remaining = iter(words)
    for word in remaining:
        if word == "--":
            yield from ((None, positional) for positional in remaining)
        elif word.startswith("--"):
            name, equals, attached = word.partition("=")
            option, on = full_push_option(name)
            takes_next_word = option in PUSH_VALUE_OPTIONS and on and not equals
            yield option, next(remaining, "") if takes_next_word else attached if equals else on
        elif word.startswith("-") and len(word) > 1:
            yield from short_push_items(word[1:], remaining)
        else:
            yield None, word


def short_push_items(letters, remaining):
    """The options in a cluster such as -uo, where -o takes the rest of the word, or else the next word."""
    for position, letter in enumerate(letters):
        option = PUSH_SHORT_OPTIONS.get(letter, "-" + letter)
        if option in PUSH_VALUE_OPTIONS:
            yield option, letters[position + 1:] or next(remaining, "")
            return
        yield option, True


def last_switch(items, option):
    """Whether a switch is on after every word that names it, so -n --no-dry-run is not a dry run."""
    return next((value is not False for name, value in reversed(items) if name == option), False)


def push_words(rest):
    """(positional words, whether a reason that needs no state keeps it quiet, whether --tags is on)."""
    items = list(push_items(without_redirects(rest)))
    if any(last_switch(items, option) for option in EVERY_BRANCH):
        raise unreadable("every")
    positional = [value for option, value in items if option is None]
    return positional, last_switch(items, "--delete") or last_switch(items, "--dry-run"), last_switch(items, "--tags")


def pushed(step, lookups):
    """[(branch, commit, cwd, the remote's default branches)] for each GitHub branch other than the default that
    the push sends something new to."""
    cwd, words = git_place(step)
    if sets_push_settings_for_one_command(step, words):
        raise push_settings_stop("option")
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
        remote = push_remote(cwd, branch, lookups)
    if not GITHUB.search(lookups.git(cwd, "ls-remote", "--get-url", remote) or ""):
        return []
    defaults = default_branches(cwd, remote, lookups)
    sent = [pair for spec in specs or specs_of_a_bare_push(push_settings(cwd, lookups), remote, branch)
            for pair in sent_by(spec, cwd, branch, remote, lookups)]
    return [(target, commit, cwd, defaults) for target, commit in sent
            if not target.startswith("refs/") and target not in defaults
            and lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}") != commit]


def sent_by(spec, cwd, branch, remote, lookups):
    """[(a branch the refspec may update, the commit it sends)], or [] when it updates a tag or deletes. A tag sent
    to a branch counts as the commit it points to. A local branch named alone goes where git's push settings
    send it."""
    source, _colon, target = spec.partition(":")
    if not source or target.startswith("refs/tags/"):
        return []
    source = branch_name(source)
    if source == "HEAD":
        commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        if not (target or branch) or not commit:
            raise unreadable("detached")
        return [(name, commit) for name in remote_branch_names(target or branch)]
    commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{source}")
    if commit is None:
        if not target and is_a_tag(cwd, source, lookups):
            return []  # a tag sent under its own name
        commit = lookups.git(cwd, "rev-parse", "--verify", "--quiet", f"{source}^{{commit}}")
        if commit is None or not target:
            raise unreadable("unknown")
    if not target:
        target = mapped_destination(f"refs/heads/{source}", push_settings(cwd, lookups), remote)
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
    """Where git sends a local branch named with no destination: the first of the remote's push settings whose
    source matches it, else its one upstream branch when push.default is upstream, else its own name."""
    mapped = [destination for key, value in settings if key == f"remote.{remote}.push"
              for destination in [refspec_destination(value, source_ref)] if destination is not None]
    if mapped:
        return mapped[0] or source_ref  # an empty destination keeps the branch's own name
    branch = source_ref[len("refs/heads/"):]
    merges = [value for key, value in settings if key == f"branch.{branch}.merge"]
    if last_value(settings, "push.default") in ("upstream", "tracking") and len(merges) == 1:
        return merges[0]
    return source_ref


def refspec_destination(refspec, source_ref):
    """Where one push setting sends the full source ref, or None when it does not match. A ^ entry only takes refs
    out of a pattern, and git matches the source only by its full name, with at most one *."""
    if refspec.startswith("^") or ":" not in refspec:
        return None
    source, destination = refspec[1:].split(":", 1) if refspec.startswith("+") else refspec.split(":", 1)
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


def settings_set_by_options(options):
    """The name=value settings that git's -c and --config-env options set, read from git's own options."""
    separate = [value for option, value in zip(options, options[1:]) if option in CONFIG_OPTIONS]
    attached = [option.split("=", 1)[1] for option in options if option.startswith("--config-env=")]
    return separate + attached


def names_a_push_setting(setting):
    """Whether a setting such as push.default=matching may change where a push goes or what it sends."""
    return setting.split("=", 1)[0].split(".", 1)[0].lower() in PUSH_SETTING_SECTIONS


def sets_push_settings_for_one_command(step, words_after_options):
    """Whether git's own options or the step's VAR=value words change push settings that the lookups can not see."""
    options = step.words[1:len(step.words) - len(words_after_options)]
    return (any(map(names_a_push_setting, settings_set_by_options(options)))
            or any(map(CONFIG_VARIABLE.match, step.before)))


def push_settings(cwd, lookups):
    """[(name, value)] of the repo's settings that decide what a push with no branch named sends."""
    listed = lookups.git(cwd, "config", "--get-regexp", PUSH_SETTINGS) or ""
    return [tuple((line.split(" ", 1) + [""])[:2]) for line in listed.splitlines()]


def last_value(settings, name):
    return next((value for key, value in reversed(settings) if key == name), None)


def is_true(value):
    return value is not None and value.lower() not in FALSE_WORDS


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


def default_branches(cwd, remote, lookups):
    named = lookups.git(cwd, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if named and named.startswith(remote + "/"):
        return {named[len(remote) + 1:]}
    return {"main", "master"}


def with_open_pr(candidates, lookups):
    """The pushed branches that have an open PR, each with the branch its PR goes into. More than one branch, or a
    failed lookup, counts as open, with the remote's default branch as its base."""
    if len({branch for branch, _commit, _cwd, _bases in candidates}) != 1:
        return candidates
    branch, _commit, cwd, _bases = candidates[0]
    output, _why = lookups.gh(cwd, "pr", "list", "--head", branch, "--state", "open", "--json",
                              "number,baseRefName", "--limit", "1")
    listed = parsed_json(output)
    if not isinstance(listed, list):
        return candidates
    base = next((found.get("baseRefName") for found in listed if isinstance(found, dict)), None)
    return [(name, commit, here, {base} if base else bases) for name, commit, here, bases in candidates] \
        if listed else []


def parsed_json(output):
    try:
        return json.loads(output) if output is not None else None
    except ValueError:
        return None


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


def pr_head(cwd, branch, named, lookups):
    """The commit a new PR for the branch opens at. With --head, gh pushes nothing, so the PR opens at what was
    pushed, read from the remote-tracking branch when there is one. Without it, gh opens the PR only once HEAD
    matches a pushed branch, pushing HEAD first or stopping, so the local head is the PR's head."""
    if unreadable_mark() in branch:
        raise unreadable("word")
    pushed_commit = named and lookups.git(cwd, "rev-parse", "--verify", "--quiet",
                                          f"refs/remotes/{push_remote(cwd, branch, lookups)}/{branch}")
    return pushed_commit or head_of_branch(cwd, branch, lookups)


def pr_bases(named_base, cwd, branch, lookups):
    """The branches a new PR may go into: the one named, or else the remote's default. A base the shell fills in
    matches none, so no texts report counts for it."""
    if named_base is None:
        return default_branches(cwd, push_remote(cwd, branch, lookups), lookups)
    return set() if unreadable_mark() in named_base else {named_base}


def new_pr(step, branch, named_head, named_base, lookups):
    return [(branch, pr_head(step.cwd, branch, named_head, lookups), step.cwd,
             pr_bases(named_base, step.cwd, branch, lookups))]


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
        body = body_of(command, step, rest)
        body_stop("" if body is None else body, step.cwd, flags, answer)
        if not flags.runs(f"{PART}.report"):
            return []
        heads = gate().option_values(rest, ["--head", "-H"], attached=True)
        bases = gate().option_values(rest, ["--base", "-B"], attached=True)
        branch = heads[-1].split(":", 1)[-1] if heads else current_branch(step.cwd, lookups)
        return new_pr(step, branch, bool(heads), bases[-1] if bases else None, lookups)
    if action == ["pr", "edit"]:
        body = body_of(command, step, rest)
        if body is not None:
            written_out(step)
            body_stop(body, step.cwd, flags, answer)
        return []
    if action == ["pr", "ready"] and "--undo" not in rest and flags.runs(f"{PART}.report"):
        written_out(step)
        return ready_pr(step, [word for word in without_redirects(rest) if not word.startswith("-")][:1], lookups)
    return []


def ready_pr(step, named, lookups):
    """The PR a ready step marks, read from gh: its head commit and the branch it goes into. With no number, gh
    finds the current branch's PR, and the local head stands in when gh can not say."""
    if named and unreadable_mark() in named[0]:
        raise unreadable("word")
    output, why = lookups.gh(step.cwd, "pr", "view", *named, "--json", "headRefOid,baseRefName")
    viewed = parsed_json(output)
    viewed = viewed if isinstance(viewed, dict) else {}
    commit, base = viewed.get("headRefOid"), viewed.get("baseRefName")
    branch = None if named else lookups.git(step.cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    bases = {base} if base else default_branches(step.cwd, push_remote(step.cwd, branch, lookups) if branch
                                                 else "origin", lookups)
    if named:
        if not commit:
            raise Stop("pr_head_unknown.md", number=named[0], why=why or "gh gave no head commit")
        return [(f"PR {named[0]}", commit, step.cwd, bases)]
    commit = commit or lookups.git(step.cwd, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if commit is None:
        raise unreadable("detached")
    return [(branch or "HEAD", commit, step.cwd, bases)]


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
        return new_pr(step, fields["head"].split(":", 1)[-1], True, fields.get("base"), lookups)
    if re.fullmatch(r"repos/[^/]+/[^/]+/pulls/\d+", endpoint) and method != "GET" and "body" in fields:
        written_out(step)
        body_stop(fields["body"], step.cwd, flags, answer)
    return []


def api_path(endpoint):
    """gh api's endpoint as a path, so a full URL such as https://api.github.com/repos/... reads as repos/..."""
    return re.sub(r"^[a-z]+://[^/]+/(?:api/v3/)?", "", endpoint.split("?", 1)[0], flags=re.I).strip("/")


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
    return new_pr(step, branch, True, graphql_value(query, "baseRefName", fields), lookups)


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


def report_stop(branch, commit, cwd, bases, lookups):
    store = reports()
    short_sha = commit[:7]
    report, broken = store.find(cwd, commit, lookups.git)
    if broken:
        raise Stop("report_unreadable.md", short=short_sha)
    if report is None:
        raise Stop("no_report.md", short=short_sha, branch=branch)
    found = store.problem(report, cwd, commit, lookups.git, bases)
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
        for branch, commit, here, bases in needed:
            report_stop(branch, commit, here, bases, lookups)


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
