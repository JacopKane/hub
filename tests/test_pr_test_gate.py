"""The PR test gate, end to end through hub's dispatcher.

Each scenario walks a real repo whose origin is a local bare repo in a folder
named github.com, so the gate sees GitHub and nothing leaves the machine.
A fake gh on PATH is the only stand-in: it says which branches have an open
PR and which commit a PR's head is. Test reports are written where hub keeps
them, in the repo's common git folder, in the shape the pr-tested step writes.
"""
import json
import os
import re
import shlex
import shutil
import time

from conftest import REPO, commit
from steps import and_, given, then, when

PART = "pr_test_gate"
CANONICAL = ("## What it is\n\nThe save button keeps the draft.\n\n### How it was tested\n\n"
             "Ran the save scenarios against a local server.\nNot tested: real GitHub, since a fake gh stands in.\n")
ONLY_PASSING = "## What it is\n\nA fix.\n\n## How it was tested\n\nAll 54 tests pass.\n"
NO_PART = "## What it is\n\nA fix.\n"
NEAR_MISS = "## What it is\n\nA fix.\n\n## Testing\n\nRan the scenarios.\nLeft out: the smoke run, the repo has none.\n"
HOW_THIS = "## What it is\n\nA fix.\n\n## How this was tested\n\nRan the scenarios.\nNot tested: Windows.\n"
HOW_TO_TEST = "## What it is\n\nA fix.\n\n## How to test\n\nRun the scenarios.\nNot tested: Windows.\n"
TEMPLATE = "## Summary\n\n## Test plan\n"
# The push form the push settings message shows, for most stops and for those that refuse a branch named alone
NAMED_EXAMPLE = "git push origin feature"
DESTINATION_EXAMPLE = "git push origin fix-a:feature, where feature is the branch on the remote"
TEMPLATE_BODY = "## Summary\n\nA fix.\n\n## Test plan\n\nRan the scenarios.\nNot covered: the slow network case.\n"
# gh's lookup of feature's open PR, in gh's own repo for the folder; the same words with -R ask another repo
OPEN_PR_LIST = ["pr", "list", "--head", "feature", "--state", "open", "--json", "number,baseRefName,headRefOid",
                "--limit", "1"]


def forked_from(repo):
    """gh's lookup of the repos a pushed repo is a fork of, its parent and the source at the root of its forks."""
    return ["api", f"repos/{repo}", "--jq", "[.parent.full_name, .source.full_name | strings]"]


# The repo, its remote and the fake gh


def github_folder(home):
    """The local bare repo that stands in for acme/demo on GitHub: its path names github.com."""
    return os.path.join(home.root, "github.com", "acme", "demo.git")


def with_github(home):
    """hub installed, a repo whose origin is a local bare repo in a folder named github.com, and the fake gh.
    It returns a second local bare repo, whose path names no GitHub host."""
    home.install()
    origin, elsewhere = github_folder(home), os.path.join(home.root, "other.git")
    for bare in (origin, elsewhere):
        home.git("init", "-q", "--bare", bare)
    home.git("remote", "add", "origin", origin)
    folder = os.path.join(home.root, "gh-bin")
    os.makedirs(folder)
    shutil.copy(os.path.join(REPO, "tests", "fakes", "fake_gh.py"), os.path.join(folder, "gh"))
    os.chmod(os.path.join(folder, "gh"), 0o755)
    home.env["PATH"] = folder + os.pathsep + home.env["PATH"]
    home.env["FAKE_GH_CONFIG"] = os.path.join(home.root, "gh.json")
    home.env["FAKE_GH_LOG"] = os.path.join(home.root, "gh-calls.jsonl")
    fake_gh(home)
    home.git("push", "-q", "-u", "origin", "main")
    home.git("remote", "set-head", "origin", "main")
    return elsewhere


def fake_gh(home, **config):
    home.write_json(home.env["FAKE_GH_CONFIG"], config)


def with_fake_ssh(home, **config):
    """A fake ssh on PATH, which answers ssh -G from config and never connects, so no test reads a real ssh config.
    git's ssh command is the fake too, so nothing in a scenario can start a real ssh. It returns the fake's path."""
    folder = os.path.join(home.root, "ssh-bin")
    os.makedirs(folder, exist_ok=True)
    fake = os.path.join(folder, "ssh")
    shutil.copy(os.path.join(REPO, "tests", "fakes", "fake_ssh.py"), fake)
    os.chmod(fake, 0o755)
    home.env["PATH"] = folder + os.pathsep + home.env["PATH"]
    home.env["FAKE_SSH_CONFIG"] = os.path.join(home.root, "ssh.json")
    home.env["FAKE_SSH_LOG"] = os.path.join(home.root, "ssh-calls.jsonl")
    home.env["GIT_SSH_COMMAND"] = fake
    home.write_json(home.env["FAKE_SSH_CONFIG"], config)
    return fake


def ssh_calls(home):
    path = home.env["FAKE_SSH_LOG"]
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle]


def gh_calls(home):
    path = home.env["FAKE_GH_LOG"]
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle]


def head(home, ref="HEAD", cwd=None):
    return home.git("rev-parse", ref, cwd=cwd).stdout.strip()


def change(home, name="app.py", message="Change the app", cwd=None):
    path = os.path.join(cwd or home.work, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        handle.write(f"# change {time.time_ns()}\n")
    home.git("add", name, cwd=cwd)
    home.git("commit", "-q", "-m", message, cwd=cwd)
    return head(home, cwd=cwd)


def pushed_branch(home, name="feature", start="main", cwd=None):
    """A branch with one commit, pushed to origin, and a new commit on it that is not pushed yet."""
    home.git("checkout", "-q", "-b", name, start, cwd=cwd)
    change(home, message=f"Start {name}", cwd=cwd)
    home.git("push", "-q", "-u", "origin", name, cwd=cwd)
    return change(home, cwd=cwd)


def worktree(home, name, *args):
    """A second worktree beside the repo; the last word is what it checks out, the others are options."""
    path = os.path.join(home.root, "work", name)
    home.git("worktree", "add", "-q", *args[:-1], path, args[-1])
    return path


def record(home, sha=None, cwd=None, **fields):
    """A passing code report for the commit, written where hub keeps reports, with fields changed."""
    cwd = cwd or home.work
    sha = sha or head(home, cwd=cwd)
    report = {"version": 1, "kind": "code", "head": sha, "tree": head(home, sha + "^{tree}", cwd=cwd),
              "branch": "feature", "clean": True, "made_at": "2026-11-12T10:00:00Z",
              "scenarios": [{"command": "pytest -q tests", "exit": 0, "seconds": 3.1, "tail": "12 passed"}],
              "smoke": {"command": "make smoke", "exit": 0, "seconds": 9.0, "tail": "ok"},
              "checks": [], "base": None, "files": [], "not_tested": ["Real GitHub, since a fake gh stands in"]}
    report.update(fields)
    path = os.path.join(reports_folder(home, cwd), sha + ".json")
    home.write_json(path, report)
    return path


def reports_folder(home, cwd=None):
    common = home.git("rev-parse", "--git-common-dir", cwd=cwd).stdout.strip()
    return os.path.join(cwd or home.work, common, "pr-test-reports")


def texts_report(home, sha=None, **fields):
    base = {"branch": "main", "sha": head(home, "origin/main")}
    texts = {"kind": "texts", "scenarios": [], "smoke": None, "base": base,
             "checks": [{"command": "make texts", "exit": 0, "seconds": 1.0, "tail": "ok"}]}
    return record(home, sha, **dict(texts, **fields))


def body_file(home, text, name="body.md"):
    path = os.path.join(home.root, "bodies", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(text)
    return path


# What the gate answers


def bash(home, command, cwd=None, **extra):
    data = home.hook_input("claude_code", "PreToolUse", command, cwd=cwd)
    data.update(extra)
    return home.send("claude_code", "PreToolUse", data)


def says(home, result, name, **values):
    """Whether the gate refused with that message, with its values and hub's step filled in."""
    with open(os.path.join(home.plugin, "parts", PART, "text", name)) as handle:
        template = handle.read().strip()
    pattern = re.escape(template).replace(re.escape("{{step}}"), r"python3 -I \S+")
    for key in re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", template):
        pattern = pattern.replace(re.escape("{" + key + "}"),
                                  re.escape(str(values[key])) if key in values else ".+?")
    return result.refused and re.fullmatch(pattern, result.reason) is not None


def body_sentence(home, name, **values):
    """A sentence of the pr part's body check, as pr-check says it."""
    with open(os.path.join(home.plugin, "parts", "pr", "text", name)) as handle:
        found = handle.read().strip()
    for key, value in values.items():
        found = found.replace("{" + key + "}", str(value))
    return found


def missing_part(home, heading="How it was tested"):
    return body_sentence(home, "check_missing_heading.md", heading=heading)


def blocked(home, child):
    return {"flag": f"{PART}.{child}", "signal": "blocked"} in [
        {"flag": line["flag"], "signal": line["signal"]} for line in home.signals()]


def quiet(result):
    return result.ran and result.output == {}


# The scenarios


def test_a_push_to_a_branch_with_an_open_pr_needs_a_passing_report_for_that_exact_commit_or_its_tree(home):
    with given("a GitHub repo whose branch feature has open PR 7 and a new commit that is not pushed yet"):
        with_github(home)
        first = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
    with when("Claude pushes it with no test report"):
        result = bash(home, "git push")
    with then("the gate refuses it, names the commit and the branch, and logs the refusal"):
        assert says(home, result, "no_report.md", short=first[:7], branch="feature"), result.reason
        assert blocked(home, "report")
    with when("a passing report is recorded for that commit"):
        record(home)
    with then("the same push runs"):
        assert quiet(bash(home, "git push"))
    with when("the commit is pushed, then reworded, so its tree stays the same"):
        home.git("push", "-q")
        home.git("commit", "-q", "--amend", "-m", "Reword the change")
    with then("the reworded commit keeps the report of its tree, and the push runs"):
        assert head(home) != first
        assert quiet(bash(home, "git push --force-with-lease"))
    with when("the code changes in a new commit"):
        home.git("push", "-q", "--force-with-lease")
        changed = change(home)
    with then("the old report does not count for it"):
        assert says(home, bash(home, "git push"), "no_report.md", short=changed[:7], branch="feature")
    with when("a report is recorded whose scenarios failed"):
        record(home, scenarios=[{"command": "pytest -q tests", "exit": 1, "seconds": 2.0, "tail": "1 failed"}])
    with then("the push is refused and names the failing step"):
        assert says(home, bash(home, "git push"), "failed.md", short=changed[:7], which="pytest -q tests", verb="run")


def test_a_new_line_ends_a_push_or_ready_step_as_the_shell_ends_it_unless_quotes_or_a_backslash_carry_it_on(home):
    with given("a GitHub repo whose branch feature has open PR 7, whose head is feature's new commit, and no "
               "test report"):
        with_github(home)
        first = pushed_branch(home)
        fake_gh(home, open={"feature": 7}, heads={"7": first, "8": first})
    two_lines = ["git push\ngit branch --delete old", "git push\ngit clean --dry-run",
                 "git push # send it\ngit branch -d old"]
    with when("Claude pushes feature, and the next line of the same call is another command with a delete or dry "
              "run option"):
        results = {push: bash(home, push) for push in two_lines}
    with then("the gate reads the push up to the end of its line and refuses it for the commit with no report"):
        for push, result in results.items():
            assert says(home, result, "no_report.md", short=first[:7], branch="feature"), (push, result.reason)
    one_push = ["git push -o 'note=one\ntwo --delete'", "git push -o $'it\\'s\ntwo --delete'",
                "git push origin \\\nfeature"]
    with when("Claude pushes feature with a new line inside quotes, even after an escaped quote in $'...', or with "
              "a backslash that carries the push on to the next line"):
        results = {push: bash(home, push) for push in one_push}
    with then("the gate reads one push of feature and refuses it the same way"):
        for push, result in results.items():
            assert says(home, result, "no_report.md", short=first[:7], branch="feature"), (push, result.reason)
    with when("Claude pushes feature between two echo commands, where the first echo's $'...' string holds an "
              "escaped quote and the second echo prints a quote"):
        hidden_push = bash(home, "echo $'\\'' ; git push ; echo \\'")
    with then("the gate finds the push the shell runs and refuses it, since another command runs before it"):
        assert says(home, hidden_push, "unreadable_push.md", why="another command runs before it on the same line"), \
            hidden_push.reason
    with when("Claude marks PR 7 ready, and the next line turns PR 8 back into a draft"):
        ready = bash(home, "gh pr ready 7\ngh pr ready 8 --undo")
    with then("the gate reads the ready step up to the end of its line and refuses it for PR 7's head"):
        assert says(home, ready, "no_report.md", short=first[:7]), ready.reason


def test_every_way_to_name_the_pushed_commit_finds_it_and_a_push_it_can_not_read_is_refused(home):
    with given("branch feature with open PR 7, a worktree on branch side with open PR 8, both with new "
               "commits, a tag on feature's new commit, and a worktree on a detached HEAD"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("tag", "t1")
        old = head(home, "origin/feature")
        side_folder = worktree(home, "side", "-b", "side", "main")
        change(home, message="Start side", cwd=side_folder)
        home.git("push", "-q", "-u", "origin", "side", cwd=side_folder)
        side = change(home, cwd=side_folder)
        loose = worktree(home, "loose", "--detach", "feature")
        fake_gh(home, open={"feature": 7, "side": 8})
    forms = ["git push", "git push -u origin feature", "git push origin HEAD:feature", "git push origin +feature",
             "git push origin refs/heads/feature", "git push --force-with-lease",
             "git push --force-with-lease=feature origin feature",
             f"git push --force-with-lease=feature:{old} origin feature", "git push -f origin feature",
             "git push --force origin feature", "git push --no-verify origin feature",
             "git push --recurse-submodules no origin feature", "git push -o ci.skip origin feature", "git push origin",
             f"cd {home.work} && git push", "GIT_TRACE=0 git push", "git push 2>&1 | tail -3",
             "git push origin feature 2>/dev/null", "git push origin HEAD:heads/feature",
             "git push origin heads/feature", "git push origin t1:feature",
             "git push origin refs/tags/t1:refs/heads/feature", "git push -onotify origin feature",
             "git push -odeploy origin feature", "git push -uo ci.skip origin feature",
             "git push --push-opt ci.skip origin feature", "git push --set-up origin feature",
             "git push -n --no-dry-run origin feature", "git push --dry --no-dry origin feature",
             "git push --del --no-del origin feature", "git push --mirror --no-mirror origin feature",
             "git --attr-source HEAD push origin feature", "git --attr-source=HEAD push origin feature"]
    side_forms = [f"git -C {side_folder} push", f"cd {side_folder} && git push",
                  f"git -C {side_folder} push origin side"]
    with when("Claude names feature's new commit in each way git allows, with no report"):
        results = {form: bash(home, form) for form in forms}
    with then("each is refused for that commit on feature"):
        for form, result in results.items():
            assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), (form, result.reason)
    with when("feature gets a passing report, and side has none"):
        record(home)
    with then("every form for feature runs, and the forms that push from the side worktree are refused for side"):
        for form in forms:
            assert quiet(bash(home, form)), form
        for form in side_forms:
            assert says(home, bash(home, form), "no_report.md", short=side[:7], branch="side"), form
    unreadable = {"git push origin $BRANCH": "a variable or a pattern names the branch",
                  f"git -C {loose} push": "HEAD is not on a branch",
                  "git push --all": "it pushes every branch",
                  "git push --mirror origin": "it pushes every branch",
                  "git push --al": "it pushes every branch",
                  "git push --mirr origin": "it pushes every branch",
                  "git push --branch origin": "it pushes every branch",
                  "git push --d origin feature": "a shortened option matches more than one option",
                  "git push origin 'feature": "the line does not parse",
                  "git status && git push": "another command runs before it on the same line",
                  "git fetch -q; git push origin feature": "another command runs before it on the same line",
                  "timeout 60 git push origin feature": "another command runs before it on the same line",
                  "echo $(git push origin feature)": "it runs inside another command",
                  "bash -c 'git push origin feature'": "it runs inside another command",
                  "printf feature | xargs git push origin": "xargs or parallel fills in its words",
                  "git -C $(pwd) push origin feature": "a variable or a pattern names the branch",
                  "git --git-dir=$(pwd)/.git push origin feature": "a variable or a pattern names the branch"}
    with when("Claude sends pushes whose commit the gate can not read: a variable, a detached HEAD, every branch "
              "in full or shortened, a shortened option git could read two ways, a broken quote, a step after "
              "another command or program, a substitution, a wrapper and xargs"):
        results = {form: bash(home, form) for form in unreadable}
    text_gate_first = ["git push origin $BRANCH", "bash -c 'git push origin feature'",
                       "printf feature | xargs git push origin", "git -C $(pwd) push origin feature",
                       "git --git-dir=$(pwd)/.git push origin feature", "git push --d origin feature",
                       "git push --all", "git push --mirror origin", "git push --al", "git push --mirr origin",
                       "git push --branch origin", "git push origin 'feature"]
    with then("the text gate answers first for the variable, the wrapper, xargs, the option git could read two "
              "ways, every branch and the broken quote, since it can not read their commits either, and every other "
              "form is asked "
              "to be written out as its own command"):
        for form, result in results.items():
            if form in text_gate_first:
                assert result.refused and result.findings == ["unreadable"], (form, result.reason)
            else:
                assert says(home, result, "unreadable_push.md", why=unreadable[form]), (form, result.reason)
    with when("the text gate's unreadable check is off and Claude sends the same pushes"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        results = {form: bash(home, form) for form in unreadable}
    with then("this gate refuses each one, says why it can not read it, and asks for it to be written out"):
        for form, result in results.items():
            assert says(home, result, "unreadable_push.md", why=unreadable[form]), (form, result.reason)
    with when("one push names feature and side together"):
        calls = len(gh_calls(home))
        both = bash(home, "git push origin feature side")
    with then("it is refused for side, and the gate asked gh nothing, since more than one branch counts as open"):
        assert says(home, both, "no_report.md", short=side[:7], branch="side"), both.reason
        assert len(gh_calls(home)) == calls


def test_a_push_whose_settings_send_more_than_the_current_branch_or_change_for_one_command_is_judged_or_refused(home):
    with given("branch feature with open PR 7 and a new commit, and a worktree on branch topic whose upstream is "
               "review, with open PR 9, and a new commit; neither has a report"):
        bare = with_github(home)
        feature = pushed_branch(home)
        topic_folder = worktree(home, "topic", "-b", "review", "main")
        change(home, message="Start review", cwd=topic_folder)
        home.git("push", "-q", "-u", "origin", "review", cwd=topic_folder)
        home.git("branch", "-m", "topic", cwd=topic_folder)
        topic = change(home, cwd=topic_folder)
        fake_gh(home, open={"feature": 7, "review": 9})
    with when("feature's own remote is a local folder while the repo's push remote is origin, and Claude pushes "
              "with no remote named"):
        home.git("remote", "add", "local", bare)
        home.git("config", "branch.feature.remote", "local")
        home.git("config", "remote.pushDefault", "origin")
        to_push_remote = bash(home, "git push")
        home.git("config", "branch.feature.remote", "origin")
        home.git("config", "--unset", "remote.pushDefault")
    with then("it is judged as the push to GitHub it is, and refused for feature's commit"):
        assert says(home, to_push_remote, "no_report.md", short=feature[:7], branch="feature"), to_push_remote.reason
    with when("push.default is upstream and Claude pushes topic with no branch named"):
        home.git("config", "push.default", "upstream")
        to_upstream = bash(home, "git push", cwd=topic_folder)
        home.git("config", "--unset", "push.default")
    with then("it is judged as the push to review it is, and refused for topic's commit on review"):
        assert says(home, to_upstream, "no_report.md", short=topic[:7], branch="review"), to_upstream.reason
    with when("feature gets a passing report"):
        record(home)
    settings = {"push.default": ("matching", "the repo's push.default sends every branch that has a match on "
                                             "the remote"),
                "remote.origin.push": ("refs/heads/*:refs/heads/*", "the remote has its own setting for what a "
                                                                    "push sends"),
                "remote.origin.mirror": ("true", "the remote is set to mirror every branch")}
    bare_forms = ["git push", "git push origin", "git push -u origin"]
    for name, (value, _why) in settings.items():
        with when(f"the repo sets {name} to {value} and Claude pushes with no branch named, then names it"):
            home.git("config", name, value)
            bare_pushes = {form: bash(home, form) for form in bare_forms}
            named = bash(home, "git push origin feature")
            home.git("config", "--unset", name)
        with then("the text gate refuses each push with no branch named first as unreadable, since it can not tell "
                  "which commits go out either, and the named push runs"):
            for form, result in bare_pushes.items():
                assert result.refused and result.findings == ["unreadable"], (name, form, result.reason)
            assert quiet(named), (name, named.reason)
    with when("the text gate's unreadable check is off"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
    for name, (value, why) in settings.items():
        with when(f"the repo sets {name} to {value} and Claude pushes with no branch named"):
            home.git("config", name, value)
            bare_pushes = {form: bash(home, form) for form in bare_forms}
            home.git("config", "--unset", name)
        with then("this gate refuses each one for that setting"):
            for form, result in bare_pushes.items():
                assert says(home, result, "push_config.md", why=why, example=NAMED_EXAMPLE), (name, form, result.reason)
    with when("the text gate's unreadable check is on again"):
        home.hubctl_from_mod("on", "public_text_gate.unreadable")
    with when("the repo sets remote.origin.mirror to off and Claude pushes with no branch named"):
        home.git("config", "remote.origin.mirror", "off")
        mirror_off = bash(home, "git push")
        home.git("config", "--unset", "remote.origin.mirror")
    with then("it runs, since only feature is sent"):
        assert quiet(mirror_off), mirror_off.reason
    one_command = ["git -c push.default=matching push",
                   "git -c Remote.origin.url=https://github.com/acme/other.git push origin feature",
                   "git -c url.https://github.com/acme/other.git.pushInsteadOf=https://github.com/acme/demo.git "
                   "push origin feature",
                   "git -c branch.feature.pushRemote=local push",
                   "git -c include.path=../other.cfg push origin feature",
                   "git --config-env push.default=PUSH_DEFAULT push",
                   "git --config-env=remote.origin.pushurl=OTHER_URL push origin feature",
                   "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=push.default GIT_CONFIG_VALUE_0=matching git push",
                   "GIT_CONFIG_PARAMETERS=\"'push.default'='matching'\" git push",
                   "GIT_CONFIG_GLOBAL=../other.cfg git push origin feature"]
    one_command_why = "git -c, --config-env or a GIT_CONFIG variable changes its push settings for this one command"
    with when("Claude changes push settings for one command with -c, --config-env or a GIT_CONFIG variable"):
        results = {form: bash(home, form) for form in one_command}
    text_gate_first = ["git -c push.default=matching push", "git -c branch.feature.pushRemote=local push",
                       *[form for form in one_command if form.startswith("GIT_CONFIG")]]
    with then("each is refused, since the gate's own lookups can not see that setting, self-protection answers "
              "first for --config-env, since it reads a value hub can not see, and the text gate answers first "
              "for a push with no branch named and for a GIT_CONFIG variable, since it can not tell which commits "
              "go out either"):
        for form, result in results.items():
            if "--config-env" in form:
                assert result.refused and "(self_protection.lowering)" in result.reason, (form, result.reason)
            elif form in text_gate_first:
                assert result.refused and result.findings == ["unreadable"], (form, result.reason)
            else:
                assert says(home, result, "push_config.md", why=one_command_why, example=NAMED_EXAMPLE), \
                    (form, result.reason)
    with when("self-protection and the text gate's unreadable check are off and Claude sends the same pushes"):
        home.hubctl_from_mod("off", "self_protection")
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        results = {form: bash(home, form) for form in one_command}
    with then("this gate refuses each one for its one-command setting"):
        for form, result in results.items():
            assert says(home, result, "push_config.md", why=one_command_why, example=NAMED_EXAMPLE), \
                    (form, result.reason)
    with and_("a one-command setting that does not touch pushes leaves the push to be judged as usual"):
        for form in ["git -c core.quotepath=off push origin feature", "git -c user.name=Ann push"]:
            assert quiet(bash(home, form)), form


def test_a_branch_named_in_a_push_is_judged_at_the_remote_branch_git_updates(home):
    with given("branch feature with open PR 7, a remote branch heads/x with open PR 8, and branch fix-a with a "
               "new commit and no report"):
        with_github(home)
        pushed_branch(home)
        home.git("push", "-q", "origin", "main:refs/heads/heads/x")
        home.git("checkout", "-q", "-b", "fix-a", "main")
        local = change(home)
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9})
    named = ["git push origin fix-a", "git push origin heads/fix-a", "git push origin refs/heads/fix-a",
             "git push -u origin fix-a"]
    with when("the repo has no push settings and Claude pushes fix-a by name"):
        plain = bash(home, "git push origin fix-a")
    with then("it runs, since fix-a has no PR"):
        assert quiet(plain), plain.reason
    with when("push.default is upstream, fix-a's upstream is main with no remote set, fix-a has open PR 10, and "
              "Claude pushes fix-a by name"):
        home.git("config", "push.default", "upstream")
        home.git("config", "branch.fix-a.merge", "refs/heads/main")
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9, "fix-a": 10})
        no_remote = bash(home, "git push origin fix-a")
        home.git("config", "branch.fix-a.remote", "")
        empty_remote = bash(home, "git push origin fix-a")
        home.git("config", "--unset", "branch.fix-a.remote")
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9})
    with then("it is refused for fix-a's commit on fix-a, since git maps a branch to its upstream only when the "
              "branch also has a remote, and an empty remote is refused, since git can not say where it goes"):
        assert says(home, no_remote, "no_report.md", short=local[:7], branch="fix-a"), no_remote.reason
        assert says(home, empty_remote, "push_config.md", why="the branch's upstream names no remote",
                    example=DESTINATION_EXAMPLE), empty_remote.reason
    with when("fix-a's upstream is feature on origin, and Claude names fix-a in each way"):
        home.git("config", "branch.fix-a.merge", "refs/heads/feature")
        home.git("config", "branch.fix-a.remote", "origin")
        upstream = {form: bash(home, form) for form in named}
    with then("each is refused for fix-a's commit on feature, the branch git updates"):
        for form, result in upstream.items():
            assert says(home, result, "no_report.md", short=local[:7], branch="feature"), (form, result.reason)
    with when("fix-a has a second upstream, so git sends it under its own name"):
        home.git("config", "--add", "branch.fix-a.merge", "refs/heads/other")
        two_upstreams = bash(home, "git push origin fix-a")
        home.git("config", "--unset-all", "branch.fix-a.merge")
    with then("it runs, since fix-a has no PR"):
        assert quiet(two_upstreams), two_upstreams.reason
    mapped = [(["refs/heads/fix-a:refs/heads/feature"], "feature"), (["+refs/heads/fix-a:feature"], "feature"),
              (["refs/heads/*:refs/heads/review/*"], "review/fix-a"),
              (["refs/heads/fix-a:refs/heads/feature", "refs/heads/*:refs/heads/review/*"], "feature")]
    for values, branch in mapped:
        with when(f"the remote's push settings are {values} and Claude pushes fix-a by name"):
            for value in values:
                home.git("config", "--add", "remote.origin.push", value)
            result = bash(home, "git push origin fix-a")
            home.git("config", "--unset-all", "remote.origin.push")
        with then(f"it is refused for fix-a's commit on {branch}, where the first setting that matches sends it"):
            assert says(home, result, "no_report.md", short=local[:7], branch=branch), (values, result.reason)
    with when("the remote's push setting holds two colons, fix-a has open PR 10, and Claude pushes fix-a by name"):
        home.git("config", "remote.origin.push", "refs/heads/fix-a:x:refs/heads/feature")
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9, "fix-a": 10})
        two_colons = bash(home, "git push origin fix-a")
        sent = home.git("push", "--dry-run", "--porcelain", "origin", "fix-a").stdout
        home.git("config", "--unset-all", "remote.origin.push")
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9})
    with then("it is refused for fix-a's commit on fix-a: git splits the setting at its last colon, so its source "
              "refs/heads/fix-a:x matches no branch and fix-a goes under its own name"):
        assert "refs/heads/fix-a:refs/heads/fix-a" in sent, sent
        assert says(home, two_colons, "no_report.md", short=local[:7], branch="fix-a"), two_colons.reason
    negative_why = "the remote's push settings leave branches out with ^, which changes where git sends a branch"
    for values in (["^refs/heads/fix-a", "refs/heads/main:refs/heads/z", "refs/heads/*:refs/heads/review/*"],
                   ["^refs/heads/fix-a", "refs/heads/*:refs/heads/*"]):
        with when(f"the remote's push settings are {values}, fix-a's upstream is feature again, and Claude pushes "
                  f"fix-a by name, then by name and destination"):
            home.git("config", "branch.fix-a.merge", "refs/heads/feature")
            for value in values:
                home.git("config", "--add", "remote.origin.push", value)
            by_name = bash(home, "git push origin fix-a")
            with_destination = bash(home, "git push origin fix-a:feature")
            home.git("config", "--unset-all", "remote.origin.push")
            home.git("config", "--unset", "branch.fix-a.merge")
        with then("the push by name is refused for the ^ entry and asked to name its destination, and the push "
                  "that names its destination is judged there as usual"):
            assert says(home, by_name, "push_config.md", why=negative_why, example=DESTINATION_EXAMPLE), \
                (values, by_name.reason)
            assert says(home, with_destination, "no_report.md", short=local[:7], branch="feature"), \
                (values, with_destination.reason)
    with when("the remote's push setting names fix-a without refs/heads/, which git does not match"):
        home.git("config", "remote.origin.push", "fix-a:refs/heads/feature")
        short_source = bash(home, "git push origin fix-a")
        home.git("config", "--unset-all", "remote.origin.push")
        home.git("config", "--unset", "push.default")
    with then("it runs, since git sends fix-a under its own name"):
        assert quiet(short_source), short_source.reason
    with when("Claude pushes fix-a's commit to heads/x by its short name and by its full name"):
        short_name = bash(home, "git push origin HEAD:heads/x")
        full_name = bash(home, "git push origin HEAD:refs/heads/heads/x")
    with then("both are refused for fix-a's commit: the short name updates x or heads/x, by what the remote has, "
              "so both count, and the full name is heads/x, where gh finds PR 8"):
        assert says(home, short_name, "no_report.md", short=local[:7]), short_name.reason
        assert says(home, full_name, "no_report.md", short=local[:7], branch="heads/x"), full_name.reason
    with when("tag v1 points at fix-a's commit, the remote's push setting sends refs/tags/v1 to feature, and "
              "Claude pushes v1 by name, then with the word tag"):
        home.git("tag", "v1", "fix-a")
        home.git("config", "remote.origin.push", "refs/tags/v1:refs/heads/feature")
        tag_by_name = bash(home, "git push origin v1")
        sent_by_name = home.git("push", "--dry-run", "--porcelain", "--force", "origin", "v1").stdout
        tag_word = bash(home, "git push origin tag v1")
        sent_by_word = home.git("push", "--dry-run", "--porcelain", "origin", "tag", "v1").stdout
        home.git("config", "--unset-all", "remote.origin.push")
    with then("the push by name is refused for fix-a's commit on feature, where git sends the tag, and the push "
              "with the word tag runs, since git sends it under its own name"):
        assert "refs/tags/v1:refs/heads/feature" in sent_by_name, sent_by_name
        assert says(home, tag_by_name, "no_report.md", short=local[:7], branch="feature"), tag_by_name.reason
        assert "refs/tags/v1:refs/tags/v1" in sent_by_word, sent_by_word
        assert quiet(tag_word), tag_word.reason
    with when("the remote's push setting sends every tag to a branch of the same name, v1 has open PR 11, and "
              "Claude pushes v1 by name"):
        home.git("config", "remote.origin.push", "refs/tags/*:refs/heads/*")
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9, "v1": 11})
        tag_pattern = bash(home, "git push origin v1")
        home.git("config", "--unset-all", "remote.origin.push")
        fake_gh(home, open={"feature": 7, "heads/x": 8, "review/fix-a": 9})
    with then("it is refused for fix-a's commit on v1"):
        assert says(home, tag_pattern, "no_report.md", short=local[:7], branch="v1"), tag_pattern.reason
    file_why = "a file in the repo's remotes or branches folder sets where pushes to this remote go"
    with when("an old-style remotes file names remote old, whose Push line sends fix-a to feature, and an old-style "
              "branches file names remote old2 at feature, and Claude pushes fix-a by name to old, with its "
              "destination to old, and with no branch to old2, also with the text gate's unreadable check off"):
        os.makedirs(os.path.join(home.work, ".git", "remotes"), exist_ok=True)
        os.makedirs(os.path.join(home.work, ".git", "branches"), exist_ok=True)
        with open(os.path.join(home.work, ".git", "remotes", "old"), "w") as handle:
            handle.write(f"URL: {github_folder(home)}\nPush: refs/heads/fix-a:refs/heads/feature\n")
        with open(os.path.join(home.work, ".git", "branches", "old2"), "w") as handle:
            handle.write(f"{github_folder(home)}#feature\n")
        from_file = bash(home, "git push old fix-a")
        sent_from_file = home.git("push", "--dry-run", "--porcelain", "--force", "old", "fix-a").stdout
        file_with_destination = bash(home, "git push old fix-a:feature")
        bare_to_branches_file = bash(home, "git push old2")
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        bare_past_the_text_gate = bash(home, "git push old2")
        home.hubctl_from_mod("on", "public_text_gate.unreadable")
        os.remove(os.path.join(home.work, ".git", "remotes", "old"))
        os.remove(os.path.join(home.work, ".git", "branches", "old2"))
    with then("the pushes that leave the destination to the file are refused and asked to name it, the text gate "
              "answers first for the push with no branch named, since it can not tell which commits go out either, "
              "and the push that names feature is judged there as usual"):
        assert "refs/heads/fix-a:refs/heads/feature" in sent_from_file, sent_from_file
        assert bare_to_branches_file.refused and bare_to_branches_file.findings == ["unreadable"], \
            bare_to_branches_file.reason
        for result in (from_file, bare_past_the_text_gate):
            assert says(home, result, "push_config.md", why=file_why, example=DESTINATION_EXAMPLE), result.reason
        assert says(home, file_with_destination, "no_report.md", short=local[:7], branch="feature"), \
            file_with_destination.reason
    with when("fix-a's commit gets a passing report, and push.default is upstream again"):
        record(home, branch="fix-a")
        home.git("config", "push.default", "upstream")
        home.git("config", "branch.fix-a.merge", "refs/heads/feature")
    with then("every one of those pushes runs"):
        for form in named + ["git push origin HEAD:heads/x", "git push origin HEAD:refs/heads/heads/x"]:
            assert quiet(bash(home, form)), form
    searches = ["git push origin ':/Change the app:feature'", "git push origin '+:/Change:refs/heads/feature'",
                "git push origin fix-a ':/Change:feature'"]
    with when("Claude pushes to feature a commit named by a search of commit messages, as in :/Change"):
        results = {form: bash(home, form) for form in searches}
    with then("the text gate refuses each one first as unreadable, since git splits it at the last colon and sends "
              "the newest commit whose message matches, which neither gate looks up"):
        for form, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (form, result.reason)
    with when("the text gate's unreadable check is off and Claude sends the same pushes"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        results = {form: bash(home, form) for form in searches}
    with then("this gate refuses each one and asks for it to be written out"):
        for form, result in results.items():
            assert says(home, result, "unreadable_push.md", why="a search of commit messages picks the commit"), \
                (form, result.reason)
    with when("release branch 1.0 has feature on origin as its upstream, tag v1.0 is at main's commit, which has no "
              "report, tag tree-v1 points at main's tree, not a commit, and Claude pushes v1.0 and tree-v1 by name, "
              "and tree-v1 to a new tag tree-v2"):
        home.git("branch", "1.0", "main")
        home.git("config", "branch.1.0.remote", "origin")
        home.git("config", "branch.1.0.merge", "refs/heads/feature")
        home.git("tag", "v1.0", "main")
        home.git("tag", "tree-v1", "main^{tree}")
        tags = {form: bash(home, form) for form in ("git push origin v1.0", "git push origin tree-v1",
                                                    "git push origin tree-v1:refs/tags/tree-v2")}
        sent = home.git("push", "--dry-run", "--porcelain", "origin", "v1.0").stdout
    with then("each runs, since git sends a tag under its own name, never to a branch's upstream, and a tag updates "
              "no branch, whatever it points at"):
        assert "refs/tags/v1.0:refs/tags/v1.0" in sent, sent
        for form, result in tags.items():
            assert quiet(result), (form, result.reason)


def test_a_pr_step_whose_folder_or_body_the_gate_can_not_read_is_refused_and_hub_logs_no_error(home):
    with given("branch feature with open PR 7, its pushed commit as PR 7's head, a new commit, no report, a body "
               "file with a tested part, and a folder that is not there"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("push", "-q")
        fake_gh(home, open={"feature": 7}, heads={"7": feature, "feature": feature})
        feature = change(home)
        good = body_file(home, CANONICAL)
        missing = os.path.join(home.root, "gone")
    steps = [f"gh pr create --title T --body-file {good}", f"gh pr new --title T --body-file {good}",
             f"gh pr edit 7 --body-file {good}", "gh pr ready", "git push origin feature"]
    lines = [place + step for place in (f"cd {missing}; ", "cd -; ", f"cd {missing} && ", "cd -; cd .; ")
             for step in steps]
    into_missing = f"git -C {missing} push origin feature"
    with when("Claude sends a new PR, gh's pr new, a body edit, a ready step and a push after a cd to a folder "
              "that is not there or back to the last folder, also with a cd from there, and a push into a folder "
              "that is not there"):
        results = {line: bash(home, line) for line in lines + [into_missing]}
    with then("each is refused as a step whose folder the gate can not find, the push into the missing folder "
              "first by the text gate, which can not list its commits, and hub logs no error"):
        for line in lines:
            assert says(home, results[line], "unreadable_push.md", why="the folder it runs in is missing or not "
                        "known"), (line, results[line].reason)
        assert results[into_missing].refused and results[into_missing].findings == ["unreadable"], \
            results[into_missing].reason
        assert home.errors() == ""
    with when("the text gate's unreadable check is off and a PR body comes from a variable or a substitution"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        hidden = {"new": bash(home, 'gh pr create --title T --body "$BODY"'),
                  "edit": bash(home, 'gh pr edit 7 --body "$(git log -1 --format=%b)"'),
                  "api": bash(home, 'gh api -X PATCH repos/acme/demo/pulls/7 -f body="$BODY"')}
        pushed_into_missing = bash(home, into_missing)
    with then("each is refused, since the gate can not check a body it can not read, and the push into the "
              "missing folder is refused by this gate as one whose folder it can not find"):
        for name, result in hidden.items():
            assert says(home, result, "unreadable_push.md", why="a variable or a substitution fills in the PR body"), \
                (name, result.reason)
        assert says(home, pushed_into_missing, "unreadable_push.md",
                    why="the folder it runs in is missing or not known"), pushed_into_missing.reason
    with when("feature gets a passing report and Claude sends the steps from the repo's own folder"):
        record(home)
        fake_gh(home, open={"feature": 7}, heads={"7": feature, "feature": feature})
        here = [f"cd {home.work}; " + step for step in steps] + [f"git -C {home.work} push origin feature"]
    with then("each runs"):
        for line in here:
            assert quiet(bash(home, line)), line


def graphql_call(query, *fields):
    return " ".join(["gh api graphql -f", shlex.quote("query=" + query), *fields])


def test_rarer_spellings_of_a_pr_step_get_the_answer_of_their_plain_form(home):
    with given("branch feature with open PR 7 and a new commit, branch side with a pushed commit, no reports, "
               "and new-PR requests for side and feature kept in JSON files"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("branch", "side", "main")
        side_folder = worktree(home, "side", "side")
        side = change(home, cwd=side_folder)
        home.git("push", "-q", "origin", "side", cwd=side_folder)
        fake_gh(home, open={"feature": 7})
        good = body_file(home, CANONICAL)
        side_request = body_file(home, json.dumps({"head": "side", "base": "main", "title": "T", "body": CANONICAL}),
                                 "side.json")
        bare_request = body_file(home, json.dumps({"head": "feature", "base": "main", "title": "T", "body": NO_PART}),
                                 "bare.json")
    create = graphql_call('mutation { opened: createPullRequest(input: {repositoryId: "R_1", baseRefName: "main", '
                          'headRefName: "side", title: "T", body: $body}) { pullRequest { number } } }',
                          f"-F body=@{good}")
    ready = graphql_call('mutation { done: markPullRequestReadyForReview(input: {pullRequestId: "PR_kw1"}) '
                         '{ clientMutationId } }')
    update = graphql_call('mutation { edited: updatePullRequest(input: {pullRequestId: "PR_kw1", '
                          'body: "All tests pass."}) { clientMutationId } }')
    feature_steps = ["g''it push", "'gi't push origin feature"]
    new_pr_steps = [f"gh pr new --title T --body-file {good}", f"g''h pr new --title T --body-file {good}",
                    f"gh pr -R acme/demo create --title T --body-file {good}",
                    f"gh -R acme/demo pr create --title T --body-file {good}",
                    f"gh --help=false pr create --title T --body-file {good}",
                    f"gh pr --repo=acme/demo new --title T --body-file {good}",
                    f"gh pr -Racme/demo new --title T --body-file {good}",
                    f"gh --help=0 pr --title T new --body-file {good}"]
    side_steps = [f"gh api repos/acme/demo/pulls --input {side_request}", create,
                  f"gh --help=false api repos/acme/demo/pulls --input {side_request}",
                  f"gh api https://api.github.com/repos/acme/demo/pulls --input {side_request}",
                  graphql_call('mutation { createPullRequest(input: {repositoryId: "R_1", baseRefName: "main", '
                               'headRefName: "side", title: "T", body: $body}) { pullRequest { number } } }',
                               f"-F body=@{good}").replace("gh api graphql", "gh api https://api.github.com/graphql"),
                  f"gh api repos/acme/demo/pulls -fhead=side -fbase=main -ftitle=T -Fbody=@{good}",
                  f"gh api repos/acme/demo/pulls -F=head=side -F=body=@{good}",
                  f"gh api -ifhead=side repos/acme/demo/pulls -F body=@{good}",
                  f"gh api -XPOST repos/acme/demo/pulls --raw-field=head=side --field body=@{good}"]
    with when("Claude pushes with a quoted program name, opens a PR with gh's pr new, sends a new PR through "
              "gh api --input or an aliased GraphQL mutation, by path or by full URL, and pushes every matching "
              "branch"):
        results = {step: bash(home, step) for step in feature_steps + new_pr_steps + side_steps}
        matching = {spec: bash(home, f"git push origin {spec}") for spec in (":", "+:")}
        bare = bash(home, f"gh api repos/acme/demo/pulls --input {bare_request}")
        glued_bare = bash(home, "gh api repos/acme/demo/pulls -fhead=feature -fbody=x")
        listing = bash(home, "gh api -X=GET repos/acme/demo/pulls -f head=side")
        patched = bash(home, f"gh api -X PATCH repos/acme/demo/pulls -f head=side -F body=@{good}")
        aliased = {"ready": bash(home, ready), "update": bash(home, update)}
    with then("each is refused as its plain form would be: for the missing report of feature or side, for a "
              "body with no tested part, for a ready step by node id, and by the text gate first for pushing every "
              "branch, since it can not tell which commits go out either"):
        for step in feature_steps:
            assert says(home, results[step], "no_report.md", short=feature[:7], branch="feature"), step
        for step in new_pr_steps:
            assert says(home, results[step], "no_report.md", short=feature[:7], branch="feature"), step
        for step in side_steps:
            assert says(home, results[step], "no_report.md", short=side[:7], branch="side"), step
        for spec, result in matching.items():
            assert result.refused and result.findings == ["unreadable"], (spec, result.reason)
        assert bare.refused and bare.reason == missing_part(home)
        assert glued_bare.refused and glued_bare.reason == missing_part(home), glued_bare.reason
        assert quiet(listing), listing.reason
        assert quiet(patched), patched.reason
        assert says(home, aliased["ready"], "unreadable_push.md", why="a node id names the PR, not its number")
        assert aliased["update"].refused and aliased["update"].reason == missing_part(home)
    with when("feature and side get passing reports"):
        record(home)
        record(home, sha=side, branch="side")
    with then("the same steps run with no answer"):
        for step in feature_steps + new_pr_steps + side_steps:
            assert quiet(bash(home, step)), step
    other = os.path.join(home.root, "other")
    elsewhere = [f"git --git-dir={other}/.git push origin feature", f"git --git-dir {other}/.git push",
                 f"git --work-tree={other} push", f"git --work-tree {other} push origin feature",
                 f"GIT_DIR={other}/.git git push", f"GIT_WORK_TREE={other} git push origin feature",
                 f"GIT_DIR={other}/.git gh pr create --title T --body-file {good}"]
    with when("Claude names another repo's git folder or work tree in an option or a variable, even with a report"):
        results = {step: bash(home, step) for step in elsewhere}
    with then("the text gate refuses each push first as unreadable, since it reads the commits of the folder git "
              "runs in, and the PR step is refused with a sentence that asks for the repo's own folder instead"):
        for step, result in results.items():
            if " push" in step:
                assert result.refused and result.findings == ["unreadable"], (step, result.reason)
            else:
                assert says(home, result, "other_repo.md"), (step, result.reason)
    with when("the text gate's unreadable check is off and Claude sends the same steps, and pushes every matching "
              "branch"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        results = {step: bash(home, step) for step in elsewhere}
        matching = {spec: bash(home, f"git push origin {spec}") for spec in (":", "+:")}
    with then("each step is refused with a sentence that asks for the repo's own folder instead, and each push of "
              "every matching branch for pushing every branch"):
        for step, result in results.items():
            assert says(home, result, "other_repo.md"), (step, result.reason)
        for spec, result in matching.items():
            assert says(home, result, "unreadable_push.md", why="it pushes every branch"), (spec, result.reason)
    with when("Claude pushes with a quoted program name inside bash -c"):
        wrapped = bash(home, "bash -c 'g\"\"it push origin feature'")
        unknown_option = bash(home, f"gh api repos/acme/demo/pulls --head-ref side -F body=@{good}")
        hidden_action = bash(home, f"gh pr $ACTION --title T --body-file {good}")
        hidden_git_action = bash(home, "git $ACTION origin feature")
        hidden_git_option = bash(home, "git $OPTS log")
    with then("this gate refuses it as a push inside another command, even with a report, refuses a gh api "
              "option it does not know, and refuses a gh or git action that a variable fills in, and a git "
              "option before the action, with a message that names the case and asks for the word written out"):
        assert says(home, wrapped, "unreadable_push.md", why="it runs inside another command"), wrapped.reason
        assert says(home, unknown_option, "unreadable_push.md", why="gh api has an option the gate does not know"), \
            unknown_option.reason
        assert says(home, hidden_action, "unreadable_push.md",
                    why="a variable or a substitution fills in a word gh may read as its command"), hidden_action.reason
        for result in (hidden_git_action, hidden_git_option):
            assert says(home, result, "git_word_unreadable.md"), result.reason


def test_without_gh_a_push_counts_as_having_an_open_pr_and_a_named_prs_head_is_unknown(home):
    with given("branch feature with a new commit and no report, and a PATH that holds git but no gh"):
        with_github(home)
        feature = pushed_branch(home)
        real_git = shutil.which("git", path=home.env["PATH"])
        folder = os.path.join(home.root, "git-only")
        os.makedirs(folder)
        with open(os.path.join(folder, "git"), "w") as handle:
            handle.write(f"#!/bin/sh\nexec {shlex.quote(real_git)} \"$@\"\n")
        os.chmod(os.path.join(folder, "git"), 0o755)
        assert shutil.which("gh", path=folder) is None
    with when("Claude pushes"):
        push, _took = timed(home, "git push", folder)
    with then("the push is refused for its missing report, as after any failed lookup, and hub logs no error"):
        assert says(home, push, "no_report.md", short=feature[:7], branch="feature"), push.reason
        assert home.errors() == ""
    with when("Claude marks PR 12 ready by number"):
        ready, _took = timed(home, "gh pr ready 12", folder)
    with then("it is refused because gh could not say the PR's head"):
        assert says(home, ready, "pr_head_unknown.md", number="12", why="gh could not start"), ready.reason
    with when("feature gets a passing report and Claude pushes again"):
        record(home)
        again, _took = timed(home, "git push", folder)
    with then("the push runs"):
        assert quiet(again), again.reason


def file_call(home, tool, cwd=None, **tool_input):
    return bash(home, "", cwd=cwd, tool_name=tool, tool_input=tool_input)


def guarded(result):
    return result.refused and "(self_protection.settings_files)" in result.reason


def test_a_report_comes_only_from_the_pr_tested_step_and_a_file_tool_or_shell_write_is_refused(home):
    with given("branch feature with open PR 7, a new commit and its report, and a link to the reports folder"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        kept = record(home)
        folder = os.path.dirname(kept)
        link = os.path.join(home.root, "reports-link")
        os.symlink(folder, link)
        fake = json.dumps({"version": 1, "kind": "code", "head": feature})
    with when("Claude writes or edits a report with a file tool, by its full path, its path in the repo, the "
              "link, or with the folder's letters in another case"):
        calls = {"write": file_call(home, "Write", file_path=os.path.join(folder, "0" * 40 + ".json"), content=fake),
                 "edit": file_call(home, "Edit", file_path=kept, old_string='"exit": 0', new_string='"exit": 1'),
                 "relative": file_call(home, "Write", file_path=os.path.join(".git", "pr-test-reports", "a.json"),
                                       content=fake),
                 "link": file_call(home, "Write", file_path=os.path.join(link, "a.json"), content=fake),
                 "folded": file_call(home, "Write", file_path=os.path.join(".git", "PR-Test-Reports", "a.json"),
                                     content=fake)}
    with and_("Claude writes one from the shell, by a redirect, a copy, a glob and a script, also with a variable "
              "in the name, a glob for a new file, and a script that joins the folder from its parts"):
        shell = {"redirect": bash(home, f"echo {shlex.quote(fake)} > .git/pr-test-reports/a.json"),
                 "copy": bash(home, f"cp {shlex.quote(kept)} {link}/b.json"),
                 "glob": bash(home, "rm .git/pr-test-*/*"),
                 "script": bash(home, "python3 -c \"open('.git/pr-test-reports/c.json', 'w').write('{}')\""),
                 "variable": bash(home, f"echo {shlex.quote(fake)} > .git/pr-test-reports/$NAME.json"),
                 "new by glob": bash(home, f"cp {shlex.quote(kept)} .git/pr-test-*/new.json"),
                 "joined": bash(home, "python3 -c \"import os; open(os.path.join('.git', 'pr-test-reports', "
                                      "'d.json'), 'w')\"")}
    with then("self-protection refuses each one"):
        for name, result in {**calls, **shell}.items():
            assert guarded(result), (name, result.reason)
    with and_("reading a report still runs, and the push runs on the report the test wrote"):
        assert quiet(bash(home, f"cat {shlex.quote(kept)}"))
        assert quiet(file_call(home, "Read", file_path=kept))
        assert quiet(bash(home, "git push"))


def test_steps_outside_the_gate_run_quietly(home):
    with given("branch feature with a new commit, a tag, a second remote that is not GitHub, "
               "and gh failing, so any lookup would count as an open PR"):
        bare = with_github(home)
        pushed_branch(home)
        home.git("tag", "v1")
        home.git("remote", "add", "mirror", bare)
        fake_gh(home, fail=True)
    steps = ["git push origin v1", "git push origin refs/tags/v1", "git push --tags", "git push origin --tags",
             "git push --delete origin feature", "git push -d origin feature", "git push origin :feature",
             "git push --dry-run", "git push -n origin feature", "git push mirror feature", "git log --oneline -3",
             "git push --dry origin feature", "git push --del origin feature", "git push -onotify -n origin feature",
             "git push origin v1:refs/tags/v2", "git push origin refs/tags/v1:refs/tags/v1",
             "git status", "gh pr view 7", "gh pr list", "gh pr comment 7 --body 'Looks good'",
             "gh pr review 7 --approve", "gh pr merge 7 --squash", "gh pr close 7", "gh pr edit 7 --add-label bug",
             "gh pr edit 7 --title 'Keep the draft'", "gh api repos/acme/demo/pulls/7",
             "gh api https://api.github.com/repos/acme/demo/pulls/7", 'echo "git push"',
             commit("Explain git push in the docs")]
    with when("Claude sends pushes of tags, deletes, dry runs, pushes to another host, reads and PR steps that "
              "change no commit"):
        results = {step: bash(home, step) for step in steps}
    with then("each runs with no answer, and the gate never asked gh"):
        for step, result in results.items():
            assert quiet(result), (step, result.reason)
        assert gh_calls(home) == []
    with when("gh works and feature has no open PR"):
        fake_gh(home, open={})
        result = bash(home, "git push")
    with then("the push runs after gh is asked for feature's PR in its own repo and in acme/demo, since the later "
              "new PR needs the report"):
        assert quiet(result)
        assert gh_calls(home) == [forked_from("acme/demo"), OPEN_PR_LIST,
                                  OPEN_PR_LIST + ["-R", "github.com/acme/demo"]]


def test_a_new_pr_needs_a_report_for_its_head_and_a_tested_part_that_says_what_was_not_tested(home):
    with given("branch feature pushed with no PR yet, and branch side with a pushed commit and no report"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("push", "-q")
        home.git("branch", "side", "main")
        side_folder = worktree(home, "side", "side")
        side = change(home, cwd=side_folder)
        home.git("push", "-q", "origin", "side", cwd=side_folder)
        good = body_file(home, CANONICAL)
    with when("Claude opens a PR with a canonical body and no report"):
        result = bash(home, f"gh pr create --draft --title 'Keep the draft' --body-file {good}")
    with then("it is refused for feature's head"):
        assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), result.reason
    with when("feature gets a passing report"):
        record(home)
    with then("the same step runs with no answer, and a PR for side named with --head is refused for side"):
        assert quiet(bash(home, f"gh pr create --draft --title 'Keep the draft' --body-file {good}"))
        other = bash(home, f"gh pr create --head acme:side --title Side --body-file {good}")
        assert says(home, other, "no_report.md", short=side[:7], branch="side"), other.reason
    with when("the body heads its part Testing or How this was tested"):
        near = bash(home, f"gh pr create --title T --body-file {body_file(home, NEAR_MISS)}")
        how_this = bash(home, f"gh pr create --title T --body-file {body_file(home, HOW_THIS)}")
    with then("each runs with a reminder that names the heading hub's rules use"):
        assert near.ran and "Testing" in near.context and "How it was tested" in near.context
        assert how_this.ran and "How this was tested" in how_this.context
    with when("the body's part says only that the tests pass, or the body has no part, or uses How to test"):
        passing = bash(home, f"gh pr create --title T --body-file {body_file(home, ONLY_PASSING)}")
        empty = bash(home, f"gh pr create --title T --body {shlex.quote(NO_PART)}")
        how_to = bash(home, f"gh pr create --title T --body-file {body_file(home, HOW_TO_TEST)}")
    with then("each is refused with pr-check's own sentence, and the refusal is logged as the body's"):
        assert passing.refused and passing.reason == body_sentence(home, "check_missing_not_tested.md", line=5)
        assert empty.refused and empty.reason == missing_part(home)
        assert how_to.refused and how_to.reason == missing_part(home)
        assert blocked(home, "body")
    with when("the body comes from a heredoc, once whole and once without its part"):
        heredoc = bash(home, f"gh pr create --title T -F - <<'EOF'\n{CANONICAL}EOF")
        bare_heredoc = bash(home, f"gh pr create --title T --body-file - <<'EOF'\n{NO_PART}EOF")
        cat_heredoc = bash(home, f"gh pr create --title T --body \"$(cat <<'EOF'\n{CANONICAL}EOF\n)\"")
    with then("the gate reads it: the whole body runs, also through $(cat <<'EOF' ...), and the other is refused"):
        assert quiet(heredoc), heredoc.reason
        assert quiet(cat_heredoc), cat_heredoc.reason
        assert bare_heredoc.refused and bare_heredoc.reason == missing_part(home)
    with when("the body is a $'...' string with its line breaks and quotes written as escapes"):
        written = "$'" + CANONICAL.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"
        dollar = bash(home, f"gh pr create --title T --body {written}")
    with then("the gate reads the lines the shell writes, finds the tested part and the step runs"):
        assert quiet(dollar), dollar.reason
    with when("Claude lets gh fill the body from the commits"):
        filled = bash(home, "gh pr create --fill")
    with then("it is refused, because a filled body has no tested part"):
        assert filled.refused and filled.reason == missing_part(home)
    with when("feature gets a new commit that is pushed with no report, then a local commit on top with a report"):
        pushed_only = change(home)
        home.git("push", "-q")
        change(home)
        record(home)
        good = body_file(home, CANONICAL, "canonical.md")
        named = bash(home, f"gh pr create --head feature --title T --body-file {good}")
        current = bash(home, f"gh pr create --title T --body-file {good}")
    with then("a new PR that names its head is refused for the pushed commit, since gh pushes nothing then; "
              "without --head gh pushes HEAD first or stops, so the local commit's report lets it run"):
        assert says(home, named, "no_report.md", short=pushed_only[:7], branch="feature"), named.reason
        assert quiet(current)
    with when("the tested commit is pushed too"):
        home.git("push", "-q")
    with then("the step that names its head runs"):
        assert quiet(bash(home, f"gh pr create --head feature --title T --body-file {good}"))
    with when("the repo's PR template heads its part Test plan, and the new head has a report"):
        os.makedirs(os.path.join(home.work, ".github"))
        with open(os.path.join(home.work, ".github", "pull_request_template.md"), "w") as handle:
            handle.write(TEMPLATE)
        home.git("add", ".github")
        home.git("commit", "-q", "-m", "Add the PR template")
        record(home)
        templated = bash(home, f"gh pr create --title T --body-file {body_file(home, TEMPLATE_BODY)}")
        bare_template = bash(home, f"gh pr create --title T --body-file {body_file(home, NO_PART)}")
    with then("a body with Test plan and a not-covered line runs with no answer, and a body with no part is "
              "asked for Test plan"):
        assert quiet(templated), templated.reason
        assert bare_template.refused and bare_template.reason == missing_part(home, "Test plan")


def test_ready_steps_need_the_prs_head_report_and_body_edits_get_the_body_check_only(home):
    with given("branch feature with open PR 7, whose head on GitHub is its pushed commit while a newer commit "
               "is only local, and side, whose head is PR 12's"):
        with_github(home)
        feature = pushed_branch(home)
        on_github = head(home, "origin/feature")
        home.git("branch", "side", "main")
        side_folder = worktree(home, "side", "side")
        side = change(home, cwd=side_folder)
        home.git("push", "-q", "origin", "side", cwd=side_folder)
        fake_gh(home, open={"feature": 7}, heads={"12": side, "13": feature, "feature": on_github})
    with when("Claude marks the current branch's PR ready while only the local commit has a report"):
        record(home)
        result = bash(home, "gh pr ready")
    with then("gh names the PR's head, and the step is refused for that commit until it has a report too"):
        assert says(home, result, "no_report.md", short=on_github[:7], branch="feature"), result.reason
        assert ["pr", "view", "--json", "headRefOid,baseRefName"] in gh_calls(home)
        record(home, on_github)
        assert quiet(bash(home, "gh pr ready"))
    with when("Claude marks PR 12 and PR 13 ready by number"):
        twelve = bash(home, "gh pr ready 12")
        thirteen = bash(home, "gh pr ready 13")
        spelled = [bash(home, step) for step in ("gh --help=false pr ready 12", "gh pr --help=0 ready 12")]
    with then("the gate reads each head from gh: PR 12's head has no report, PR 13's has, however gh's own "
              "options are placed"):
        assert says(home, twelve, "no_report.md", short=side[:7]), twelve.reason
        for result in spelled:
            assert says(home, result, "no_report.md", short=side[:7]), result.reason
        assert quiet(thirteen)
        assert ["pr", "view", "12", "--json", "headRefOid,baseRefName"] in gh_calls(home)
    with when("Claude marks PR 13 and PR 12 of another repo ready, naming the repo with -R or --repo, where "
              "PR 13's head is side's commit and PR 12's is feature's"):
        fake_gh(home, open={"feature": 7}, heads={"12": side, "13": feature, "acme/other#13": side,
                                                   "acme/other#12": feature})
        other_thirteen = {step: bash(home, step) for step in (
            "gh pr -R acme/other ready 13", "gh -R acme/other pr ready 13", "gh pr ready 13 --repo=acme/other",
            "gh pr ready -Racme/other 13", "gh pr ready --repo acme/other 13")}
        other_twelve = bash(home, "gh pr ready 12 -R acme/other")
    with then("the gate reads the number, not the repo, as the PR, asks gh for that PR in that repo, and judges "
              "its head: PR 13's has no report, PR 12's has"):
        for step, result in other_thirteen.items():
            assert says(home, result, "no_report.md", short=side[:7]), (step, result.reason)
        assert quiet(other_twelve), other_twelve.reason
        assert ["pr", "view", "12", "-R", "acme/other", "--json", "headRefOid,baseRefName"] in gh_calls(home)
    with when("Claude names the other repo with GH_REPO in front of gh instead, last one set, quoted, or under a "
              "-R that names it too, and also sets GH_REPO empty, which gh reads as no repo"):
        variable_thirteen = {step: bash(home, step) for step in (
            "GH_REPO=acme/other gh pr ready 13", "GH_REPO='acme/other' gh pr ready 13",
            "GH_REPO=acme/demo GH_REPO=acme/other gh pr ready 13", "GH_REPO= gh pr ready 13 -R acme/other",
            "GH_REPO=acme/demo gh pr ready 13 -R acme/other")}
        variable_twelve = bash(home, "GH_REPO=acme/other gh pr ready 12")
        empty_thirteen = bash(home, "GH_REPO= gh pr ready 13")
        set_before = {step: bash(home, step) for step in (
            "env GH_REPO=acme/other gh pr ready 13", "export GH_REPO=acme/other; gh pr ready 13")}
    with then("the gate asks gh in the repo gh itself picks: -R when it names one, else a GH_REPO that is not "
              "empty, else the current repo, and a GH_REPO set any other way is refused"):
        for step, result in variable_thirteen.items():
            assert says(home, result, "no_report.md", short=side[:7]), (step, result.reason)
        assert quiet(variable_twelve), variable_twelve.reason
        assert quiet(empty_thirteen), empty_thirteen.reason
        for step, result in set_before.items():
            assert result.refused, (step, result.reason)
    with when("gh fails"):
        fake_gh(home, fail=True)
        failed = bash(home, "gh pr ready 12")
    with then("the step is refused, because the head is unknown"):
        assert says(home, failed, "pr_head_unknown.md", number="12"), failed.reason
    with when("gh takes 3.5 seconds to name PR 12's head, which has a report: longer than the 3 seconds a gh call "
              "gets, but inside the gate's 5 seconds for all its lookups"):
        fake_gh(home, sleep=3.5, heads={"12": feature})
        started = time.monotonic()
        slow = bash(home, "gh pr ready 12")
        took = time.monotonic() - started
    with then("the gate stops waiting after its 3 seconds for gh and refuses, though the head has a report, well "
              "inside the hook's time"):
        assert says(home, slow, "pr_head_unknown.md", number="12"), slow.reason
        assert took < 6, took
    with when("Claude turns a PR back into a draft"):
        fake_gh(home, fail=True)
        undos = [bash(home, step) for step in ("gh pr ready 12 --undo", "gh pr ready 12 --undo=false --undo",
                                               "gh pr ready --undo=0 12 --undo=1", "gh pr ready 12 --undo=T")]
    with then("it runs, whenever the last --undo turns it on"):
        for result in undos:
            assert quiet(result), result.reason
    with when("the last --undo turns it off again, as gh reads its flags, or holds a value gh does not read as "
              "true, or a -- ends gh's flags before it"):
        fake_gh(home, heads={"12": side})
        marked = [bash(home, step) for step in ("gh pr ready 12 --undo --undo=false", "gh pr ready --undo 12 --undo=0",
                                                "gh pr ready 12 --undo=F", "gh pr ready 12 --undo=yes",
                                                "gh pr ready 12 -- --undo")]
        fake_gh(home, fail=True)
    with then("each one may mark PR 12 ready, so each needs its head's report"):
        for result in marked:
            assert says(home, result, "no_report.md", short=side[:7]), result.reason
    with when("--undo stands where gh reads it as the value of -R or --repo"):
        fake_gh(home, heads={"12": side})
        repo_values = [bash(home, step) for step in ("gh pr ready 12 -R --undo", "gh pr ready 12 --repo --undo")]
        fake_gh(home, fail=True)
    with then("each one may mark PR 12 of a repo named --undo ready, and is refused, since gh names no head there"):
        for result in repo_values:
            assert says(home, result, "pr_head_unknown.md", number="12"), result.reason
    with when("Claude edits PR 12's body, once without a tested part and once with one"):
        bad = bash(home, f"gh pr edit 12 --body-file {body_file(home, NO_PART)}")
        fine = bash(home, f"gh pr edit 12 --body-file {body_file(home, CANONICAL)}")
    with then("only the body is checked: the bad body is refused and the good one runs, though side has no report"):
        assert bad.refused and bad.reason == missing_part(home)
        assert quiet(fine)
    with when("gh works again, and Claude edits the body or opens a PR through gh api"):
        fake_gh(home, heads={"12": side})
        patch = bash(home, "gh api -X PATCH repos/acme/demo/pulls/12 -f body='All tests pass.'")
        full_url = bash(home, "gh api -X PATCH https://api.github.com/repos/acme/demo/pulls/12 "
                              "-f body='All tests pass.'")
        post = bash(home, f"gh api repos/acme/demo/pulls -f head=side -f base=main -f title=T "
                          f"-F body=@{body_file(home, CANONICAL)}")
        graphql = bash(home, "gh api graphql -f query='mutation { markPullRequestReadyForReview("
                             "input: {pullRequestId: \"PR_kw1\"}) { clientMutationId } }'")
    with then("the body edit is checked, the new PR needs side's report, and a ready step by node id is "
              "asked for its number"):
        assert patch.refused and patch.reason == missing_part(home)
        assert full_url.refused and full_url.reason == missing_part(home)
        assert says(home, post, "no_report.md", short=side[:7], branch="side"), post.reason
        assert says(home, graphql, "unreadable_push.md"), graphql.reason
    with when("Claude marks PR 12 ready in a repo the shell fills in, first as is and then with the text gate's "
              "unreadable check off"):
        fake_gh(home, open={"feature": 7}, heads={"12": side, "13": feature})
        hidden_repo = bash(home, 'gh pr ready 12 -R "$REPO"')
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        hidden_repo_alone = bash(home, 'gh pr ready 12 -R "$REPO"')
        hidden_variable = bash(home, 'GH_REPO="$REPO" gh pr ready 13')
    with then("all are refused, and this gate asks for the repo to be written out"):
        assert hidden_repo.refused, hidden_repo.reason
        for result in (hidden_repo_alone, hidden_variable):
            assert says(home, result, "unreadable_push.md", why="a variable or a pattern names the branch"), \
                result.reason


def test_a_texts_report_counts_only_while_the_branch_changes_nothing_but_text_files(home):
    with given("branch docs with open PR 9 that changes only a guide, with a texts report for its head"):
        with_github(home)
        home.git("checkout", "-q", "-b", "docs")
        home.git("push", "-q", "-u", "origin", "docs")
        change(home, "docs/guide.md")
        change(home, "locales/de.json")
        change(home, "docs/shot.png")
        change(home, "docs/notes.txt")
        fake_gh(home, open={"docs": 9})
        texts_report(home)
    with when("Claude pushes it"):
        result = bash(home, "git push")
    with then("it runs"):
        assert quiet(result), result.reason
    for name in ("app.py", "text/notes.py", "docs/site.js", "requirements.txt"):
        with when(f"the branch gains {name} and a texts report is made for that head anyway"):
            home.git("reset", "-q", "--hard", "origin/docs" if name == "app.py" else "HEAD~1")
            if name == "app.py":
                change(home, "docs/guide.md")
            sha = change(home, name)
            texts_report(home)
        with then(f"the gate lists the branch's files itself and refuses, naming {name}"):
            assert says(home, bash(home, "git push"), "texts_has_code.md", short=sha[:7], file=name), name
    with when("a texts report has a failing check"):
        home.git("reset", "-q", "--hard", "HEAD~1")
        texts_report(home, checks=[{"command": "make texts", "exit": 2, "seconds": 1.0, "tail": "bad"}])
    with then("the push is refused, names the check, and says to record a texts run again"):
        assert says(home, bash(home, "git push"), "failed.md", which="make texts", verb="texts")
    with when("the texts report passes, but PR 9 goes into release, not main"):
        texts_report(home)
        home.git("push", "-q", "origin", "main:release")
        fake_gh(home, open={"docs": 9}, bases={"docs": "release"})
        other_base = bash(home, "git push")
    with then("the push is refused, since the report lists the changes since main, not since release"):
        assert says(home, other_base, "texts_base.md", base="main", into="release"), other_base.reason
    with when("the texts report lists the changes since release"):
        texts_report(home, base={"branch": "release", "sha": head(home, "origin/main")})
    with then("the push runs"):
        assert quiet(bash(home, "git push"))
    with when("Claude opens a new PR for docs into release, spelling the base each way gh reads it"):
        good = body_file(home, CANONICAL)
        into_release = {spelling: bash(home, f"gh pr create {spelling} --title Guide --body-file {good}")
                        for spelling in ("--base release", "--base=release", "-B release", "-Brelease")}
    with then("each one runs"):
        assert all(quiet(result) for result in into_release.values()), \
            {spelling: result.reason for spelling, result in into_release.items()}
    with when("Claude opens it into main, or names no base so it goes into GitHub's default branch, main, while the "
              "local copy of origin's default branch says release"):
        home.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release")
        into_main = bash(home, f"gh pr create --base main --title Guide --body-file {good}")
        no_base = bash(home, f"gh pr create --title Guide --body-file {good}")
        last_wins = bash(home, f"gh pr create --base release --base main --title Guide --body-file {good}")
    with then("each is refused, since the report lists the changes since release, not since main"):
        for result in (into_main, no_base, last_wins):
            assert says(home, result, "texts_base.md", base="release", into="main"), result.reason
    with when("branch docs's gh-merge-base setting names release, which gh reads for a new PR with no base"):
        home.git("config", "branch.docs.gh-merge-base", "release")
    with then("the new PR with no base runs"):
        assert quiet(bash(home, f"gh pr create --title Guide --body-file {good}"))
    with when("Claude marks PR 9 ready, by number and as the current branch's PR, while gh says it goes into release"):
        docs_head = head(home, "HEAD")
        fake_gh(home, open={"docs": 9}, heads={"9": docs_head, "docs": docs_head},
                bases={"9": "release", "docs": "release"})
        ready_by_number, ready_here = bash(home, "gh pr ready 9"), bash(home, "gh pr ready")
    with then("both run"):
        assert quiet(ready_by_number), ready_by_number.reason
        assert quiet(ready_here), ready_here.reason
    with when("gh says PR 9 goes into main"):
        fake_gh(home, open={"docs": 9}, heads={"9": docs_head, "docs": docs_head})
        ready_into_main = bash(home, "gh pr ready 9")
    with then("the ready step is refused for main, the base gh gave"):
        assert says(home, ready_into_main, "texts_base.md", base="release", into="main"), ready_into_main.reason


def test_every_refusal_teaches_the_rule_and_a_subagent_gets_the_same_answer(home):
    with given("branch feature with open PR 7 and a new commit with no report"):
        with_github(home)
        pushed_branch(home)
        fake_gh(home, open={"feature": 7})
    with when("the main chat and a subagent push it"):
        main = bash(home, "git push")
        agent = bash(home, "git push", agent_id="agent_1", agent_type="worker")
    with then("both get the same refusal"):
        assert main.refused and agent.reason == main.reason
    with and_("it states the rule, both ways to record a report with hub's step filled in, and the tested part"):
        for words in ("draft or not", "outermost seam", "pr-tested run --scenario '<command>' (once for each scenario)", "pr-tested texts --base",
                      "--no-smoke", '"How it was tested"'):
            assert words in main.reason, words
        assert "{{step}}" not in main.reason and "python3 -I " in main.reason


def test_a_report_made_in_another_worktree_of_the_repo_counts(home):
    with given("branch side checked out in a second worktree, with open PR 8 and a new commit"):
        with_github(home)
        home.git("branch", "side", "main")
        side_folder = worktree(home, "side", "side")
        side = pushed_branch(home, "side-work", "side", cwd=side_folder)
        fake_gh(home, open={"side-work": 8})
    with then("a push from the second worktree is refused while no worktree has a report for it"):
        assert says(home, bash(home, "git push", cwd=side_folder), "no_report.md", short=side[:7],
                    branch="side-work")
    with when("its report is recorded from the main worktree"):
        record(home, side)
    with then("a push from the second worktree finds it"):
        assert quiet(bash(home, "git push", cwd=side_folder))


def test_a_broken_or_partial_report_is_refused_with_what_it_lacks(home):
    with given("branch feature with open PR 7 and a new commit"):
        with_github(home)
        sha = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
    cases = [("report_unreadable.md", None), ("dirty.md", {"clean": False}), ("no_smoke.md", {"smoke": None}),
             ("no_smoke.md", {"smoke": {"none": ""}}), ("no_not_tested.md", {"not_tested": []}),
             ("failed.md", {"scenarios": []}),
             ("failed.md", {"smoke": {"command": "make smoke", "exit": 3, "seconds": 1.0, "tail": "down"}})]
    for name, fields in cases:
        with when(f"the report is {fields or 'not valid JSON'}"):
            path = record(home, **(fields or {}))
            if fields is None:
                with open(path, "w") as handle:
                    handle.write('{"version": 1, "kind": ')
        with then(f"the push is refused with {name}"):
            result = bash(home, "git push")
            assert says(home, result, name, short=sha[:7], verb="run"), (name, fields, result.reason)
    with when("the smoke run has a reason why there is none"):
        record(home, smoke={"none": "the repo has no smoke run"})
    with then("the push runs"):
        assert quiet(bash(home, "git push"))


def test_a_handler_error_refuses_a_pr_step_and_nothing_else(home):
    with given("branch feature with open PR 7 and a new commit, and a report store that is a plain file"):
        with_github(home)
        pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        with open(reports_folder(home), "w") as handle:
            handle.write("not a folder\n")
    with when("Claude pushes, and lists a folder"):
        push = home.hook("claude_code", "PreToolUse", "git push", errors_expected=True)
        listing = home.hook("claude_code", "PreToolUse", "ls -la", errors_expected=True)
    with then("the push is refused through hub's error path, the listing runs, and errors.log holds the error"):
        assert push.refused and "could not read its settings or parts" in push.reason, push.reason
        assert quiet(listing)
        assert "pr_test_gate" in home.errors()


def test_it_runs_through_hubs_dispatcher_for_bash_and_monitor_calls_until_the_user_turns_it_off(home):
    with given("branch feature with open PR 7 and a new commit with no report"):
        with_github(home)
        pushed_branch(home)
        fake_gh(home, open={"feature": 7})
    with when("Claude pushes in a Bash call, and in a Monitor call, which reaches hub's Bash group"):
        plain = bash(home, "git push")
        monitor = bash(home, "git push", tool_input={"command": "git push", "description": "Watch the push"})
    with then("both are refused"):
        assert plain.refused and monitor.refused and monitor.reason == plain.reason
    with when("the user turns the gate off"):
        home.hubctl_from_mod("off", PART)
    with then("the push runs"):
        assert quiet(bash(home, "git push"))


def slow_git(home, seconds, log_seconds=None):
    """A git first on PATH that waits before it runs the real git, and git log waits its own time if given."""
    real = shutil.which("git", path=home.env["PATH"])
    folder = os.path.join(home.root, "slow-bin")
    os.makedirs(folder, exist_ok=True)
    wait = f"sleep {seconds}" if log_seconds is None else (
        f'if [ "$1" = log ]; then sleep {log_seconds}; else sleep {seconds}; fi')
    with open(os.path.join(folder, "git"), "w") as handle:
        handle.write(f"#!/bin/sh\n{wait}\nexec {shlex.quote(real)} \"$@\"\n")
    os.chmod(os.path.join(folder, "git"), 0o755)
    return folder + os.pathsep + home.env["PATH"]


def timed(home, command, path=None):
    """The answer to a Bash call, and the seconds it took, with PATH changed for that call only."""
    kept = home.env["PATH"]
    home.env["PATH"] = path or kept
    try:
        started = time.monotonic()
        result = bash(home, command)
        return result, time.monotonic() - started
    finally:
        home.env["PATH"] = kept


def test_a_slow_lookup_stays_inside_the_hooks_time(home):
    with given("branches feature and side with open PRs and new commits, both gates on, and no reports"):
        with_github(home)
        home.git("branch", "side", "main")
        side_folder = worktree(home, "side", "side")
        home.git("push", "-q", "-u", "origin", "side", cwd=side_folder)
        change(home, cwd=side_folder)
        pushed_branch(home)
    with when("gh takes 20 seconds to answer"):
        fake_gh(home, sleep=20)
        result, took = timed(home, "git push")
    with then("the gate stops waiting, counts the PR as open and refuses in under 12 seconds"):
        assert says(home, result, "no_report.md", branch="feature"), result.reason
        assert took < 12, took
    with when("every git call takes 3 seconds"):
        fake_gh(home, open={"feature": 7, "side": 8})
        result, took = timed(home, "git push", slow_git(home, 3))
    with then("the text gate answers first, since git did not list the pushed commits in its time, and says so, "
              "in under 12 seconds"):
        assert result.refused and result.findings == ["unreadable"], result.reason
        assert "git did not list the commits of this push in time" in result.reason, result.reason
        assert "comes from a variable, a pipe" not in result.reason, result.reason
        assert took < 12, took
    with when("the text gate's unreadable check is off, and every git call takes 3 seconds"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        result, took = timed(home, "git push", slow_git(home, 3))
    with then("the gate refuses because its lookups were too slow, in under 12 seconds"):
        assert says(home, result, "slow.md"), result.reason
        assert took < 12, took
    with when("every git call takes 1.5 seconds and one push names two branches"):
        result, took = timed(home, "git push origin feature side", slow_git(home, 1.5))
    with then("no single call is too slow, but the gate's own 5 seconds run out, so the answer comes in under "
              "9 seconds"):
        assert says(home, result, "slow.md"), result.reason
        assert took < 9, took
    with when("the text gate's git log takes 9 seconds before this gate starts, and every other git call 1.5"):
        result, took = timed(home, "git push", slow_git(home, 1.5, log_seconds=9))
    with then("this gate gets only the time the hook has left, refuses as too slow, and the whole answer still "
              "comes in under 12 seconds"):
        assert says(home, result, "slow.md"), result.reason
        assert took < 12, took


def test_the_gate_does_not_read_the_pr_skills_test_flag(home):
    with given("branch feature with a report, and the pr skill's testing section turned off"):
        with_github(home)
        pushed_branch(home)
        record(home)
        home.hubctl_from_mod("off", "pr.instructions.test_gate")
    with when("Claude opens a PR whose body has no tested part"):
        result = bash(home, f"gh pr create --title T --body-file {body_file(home, NO_PART)}")
    with then("the gate still asks for the part"):
        assert result.refused and result.reason == missing_part(home)


def test_pr_check_and_the_gate_find_the_same_tested_part_in_the_same_body(home):
    with given("branch feature with a report"):
        with_github(home)
        pushed_branch(home)
        record(home)
    bodies = {"canonical": CANONICAL, "near miss": NEAR_MISS, "how this": HOW_THIS, "only passing": ONLY_PASSING,
              "no part": NO_PART, "how to test": HOW_TO_TEST,
              "near miss with nothing left out": NEAR_MISS.replace("Left out: the smoke run, the repo has none.\n", "")}
    for name, text in bodies.items():
        path = body_file(home, text, name.replace(" ", "-") + ".md")
        with when(f"pr-check and the gate read the {name} body"):
            checked = home.hubctl("pr-check", path, "--json", check=False)
            found = [finding for finding in json.loads(checked.stdout)["findings"]
                     if finding["kind"] == "missing_not_tested"
                     or (finding["kind"] == "missing_heading" and finding["heading"] == "How it was tested")]
            said = home.hubctl("pr-check", path, check=False).stdout.strip().split("\n")
            result = bash(home, f"gh pr create --title T --body-file {path}")
        with then("the gate refuses exactly when pr-check finds the tested part lacking, with its sentence"):
            assert result.refused == bool(found), (name, result.reason, found)
            if found:
                assert result.reason in said, (name, result.reason, said)


def user_settings(home):
    """The user's hub settings file as text, or None while hub has not written one."""
    if not os.path.exists(home.user_file):
        return None
    with open(home.user_file) as handle:
        return handle.read()


def test_neither_a_model_nor_a_repos_settings_file_can_turn_the_gate_off(home):
    with given("a GitHub repo whose branch feature has open PR 7 and a new commit with no test report"):
        with_github(home)
        first = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        before = user_settings(home)
    with when("the model runs hub's off step for the gate in a tool call"):
        result = home.hook("claude_code", "PreToolUse", f"hubctl off {PART}")
    with then("self-protection refuses it and the settings stay"):
        assert result.refused and "(self_protection.lowering)" in result.reason, result.reason
        assert user_settings(home) == before
    with when("the repo commits a settings file that turns the gate and its report check off, and Claude pushes"):
        home.write_json(os.path.join(home.work, ".hub", "hub.json"), {"version": 1, "flags": {
            PART: {"by": "user", "value": "off"}, f"{PART}.report": {"by": "user", "value": "off"}}})
        pushed = bash(home, "git push")
    with then("the push is still refused for its missing report, since a repo's file can not lower the gate"):
        assert says(home, pushed, "no_report.md", short=first[:7], branch="feature"), pushed.reason
    with and_("hubctl refuses to write the gate off into the repo's file"):
        refused = home.hubctl("off", PART, "--scope", "project", check=False)
        assert refused.returncode == 1 and "privacy" in refused.stderr


# Review 7b: what gh really sends, where the PR opens and which remote a push reaches


def branches_tested_and_untested(home):
    """Branches tested, with a passing report, and untested, with none, both pushed; tested is checked out."""
    with_github(home)
    home.git("checkout", "-q", "-b", "untested", "main")
    untested = change(home, message="Start untested")
    home.git("push", "-q", "-u", "origin", "untested")
    home.git("checkout", "-q", "-b", "tested", "main")
    tested = change(home, message="Start tested")
    home.git("push", "-q", "-u", "origin", "tested")
    record(home)
    return tested, untested


def test_gh_pr_create_and_edit_words_are_read_the_way_gh_reads_its_flags(home):
    with given("branch tested with a report and branch untested with none, both pushed, and a good and a bad body"):
        _tested, untested = branches_tested_and_untested(home)
        good, bad = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
        good_here = os.path.relpath(good, home.work)  # a title the text gate does not read as a private path
    hidden_heads = [f"gh pr create --head untested --title --head=tested --body-file {good}",
                    f"gh pr create -H untested -t -Htested --body-file {good}",
                    f"gh pr create -dHuntested --title T --body-file {good}",
                    f"gh pr create --draft=true -H=untested -t T -F {good}"]
    with when("Claude opens a PR at untested, where a later word only looks like a --head, because --title or -t "
              "takes it as the title, or names untested inside a cluster of short flags"):
        results = {step: bash(home, step) for step in hidden_heads}
    with then("the gate reads the head gh opens the PR at and refuses each for untested's missing report"):
        for step, result in results.items():
            assert says(home, result, "no_report.md", short=untested[:7], branch="untested"), (step, result.reason)
            assert blocked(home, "report")
    hidden_bodies = [f"gh pr create --head tested --body-file {bad} --title --body-file={good_here}",
                     f"gh pr create --head tested --body {shlex.quote(CANONICAL)} --body-file {bad}",
                     f"gh pr edit 7 --body-file {bad} --title --body-file={good_here}",
                     f"gh pr edit 7 -t T -F={bad}"]
    with when("Claude sends a body with no tested part, behind a later word that --title takes, beside a good "
              "--body that gh's --body-file wins over, or written as -F=file"):
        results = {step: bash(home, step) for step in hidden_bodies}
    with then("the gate checks the body gh sends and refuses each with pr-check's sentence, as the body's refusal"):
        for step, result in results.items():
            assert result.refused and result.reason == missing_part(home), (step, result.reason)
        assert blocked(home, "body")
    unknown = [f"gh pr create --head tested --frobnicate x --body-file {good}",
               f"gh pr create -Z --head tested --body-file {good}",
               f"gh pr create --head tested --body-file {good} --title",
               f"gh pr edit 7 --frobnicate --body-file {bad}"]
    with when("a step has a flag gh does not know, or a flag that wants a value at the end of the line"):
        results = {step: bash(home, step) for step in unknown}
    with then("the gate refuses it as a step it can not read, naming the option"):
        for step, result in results.items():
            assert says(home, result, "unreadable_push.md",
                        why="gh has an option the gate does not know, or one with no value"), (step, result.reason)
    plain = [f"gh pr create -dtT -Htested -F {good}", f"gh pr create --draft=false --head=tested -F{good} -t T",
             f"gh pr create -R acme/demo --head tested --title T --body-file {good}",
             f"gh pr edit 7 --add-label docs -t 'New title'", f"gh pr edit 7 -b {shlex.quote(CANONICAL)}"]
    with when("Claude writes the same steps plainly, at tested with its report and a good body"):
        results = {step: bash(home, step) for step in plain}
    with then("each runs with no answer"):
        for step, result in results.items():
            assert quiet(result), (step, result.reason)


def test_the_push_urls_decide_whether_a_push_reaches_github(home):
    with given("branch feature with open PR 7 and a new commit with no report, and remotes whose fetch and push "
               "URLs point at different hosts"):
        elsewhere = with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        github = github_folder(home)
        home.git("remote", "add", "pub", elsewhere)
        home.git("config", "remote.pub.pushurl", github)
        home.git("remote", "add", "both", elsewhere)
        home.git("config", "remote.both.pushurl", elsewhere)
        home.git("config", "--add", "remote.both.pushurl", github)
        home.git("remote", "add", "fetchonly", github)
        home.git("config", "remote.fetchonly.pushurl", elsewhere)
        mapped = os.path.join(home.root, "mapped", "demo.git")
        home.git("config", f"url.{os.path.join(home.root, 'github.com', 'acme')}/.pushInsteadOf",
                 os.path.join(home.root, "mapped") + "/")
    to_github = {"a remote that fetches elsewhere and pushes to GitHub": "git push pub feature",
                 "a remote with a second push URL on GitHub": "git push both feature",
                 "a URL that url.*.pushInsteadOf sends to GitHub": f"git push {mapped} feature"}
    with when("Claude pushes feature where one of the push URLs is on GitHub"):
        results = {case: bash(home, step) for case, step in to_github.items()}
    with then("each is judged as the push to GitHub it is, and refused for feature's commit"):
        for case, result in results.items():
            assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), (case, result.reason)
    elsewhere_only = {"a remote that fetches from GitHub and pushes elsewhere": "git push fetchonly feature",
                      "a URL with no GitHub host": f"git push {elsewhere} feature"}
    with when("Claude pushes feature where no push URL is on GitHub"):
        results = {case: bash(home, step) for case, step in elsewhere_only.items()}
    with then("each runs with no answer"):
        for case, result in results.items():
            assert quiet(result), (case, result.reason)


def test_a_push_is_judged_by_an_open_pr_in_the_repo_it_goes_to_as_well_as_in_ghs_own_repo_for_the_folder(home):
    with given("branch feature with a new commit and no report, and open PR 7 for feature in acme/demo, which "
               "origin pushes to, while gh's own repo for the folder, as gh repo set-default may pick, has none"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={}, open_in={"github.com/acme/demo": {"feature": 7}})
    with when("Claude pushes feature to origin, by the remote's name and by its URL"):
        results = {step: bash(home, step) for step in ("git push", f"git push {github_folder(home)} feature")}
    with then("each is refused for feature's commit, after gh is asked in its own repo and in acme/demo"):
        for step, result in results.items():
            assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), (step, result.reason)
        assert gh_calls(home)[-6:] == [forked_from("acme/demo"), OPEN_PR_LIST,
                                       OPEN_PR_LIST + ["-R", "github.com/acme/demo"]] * 2
    with when("a second GitHub remote, acme/fork, holds the open PR instead, and Claude pushes feature there"):
        fork = os.path.join(home.root, "github.com", "acme", "fork.git")
        home.git("init", "-q", "--bare", fork)
        home.git("remote", "add", "fork", fork)
        fake_gh(home, open={}, open_in={"github.com/acme/fork": {"feature": 8}})
        to_fork = bash(home, "git push fork feature")
    with then("it is refused too, since PR 8 gets the new commit"):
        assert says(home, to_fork, "no_report.md", short=feature[:7], branch="feature"), to_fork.reason
    with when("no repo has an open PR for feature, and Claude pushes it to the fork"):
        fake_gh(home, open={}, open_in={})
        no_pr = bash(home, "git push fork feature")
    with then("it runs, after gh is asked whether acme/fork is a fork, then in its own repo and in acme/fork"):
        assert quiet(no_pr), no_pr.reason
        assert gh_calls(home)[-3:] == [forked_from("acme/fork"), OPEN_PR_LIST,
                                       OPEN_PR_LIST + ["-R", "github.com/acme/fork"]]
    with when("acme/fork is a fork of acme/upstream, which is no remote here and holds open PR 9 for feature, "
              "and Claude pushes feature to the fork"):
        fake_gh(home, open={}, open_in={"github.com/acme/upstream": {"feature": 9}},
                parents={"repos/acme/fork": ["acme/upstream", "acme/upstream"]})
        to_parent = bash(home, "git push fork feature")
    with then("it is refused, since PR 9 gets the new commit, after gh is asked in the fork's parent too"):
        assert says(home, to_parent, "no_report.md", short=feature[:7], branch="feature"), to_parent.reason
        assert gh_calls(home)[-4:] == [forked_from("acme/fork"), OPEN_PR_LIST,
                                       OPEN_PR_LIST + ["-R", "github.com/acme/fork"],
                                       OPEN_PR_LIST + ["-R", "github.com/acme/upstream"]]
    with when("neither the fork nor its parent has an open PR for feature"):
        fake_gh(home, open={}, open_in={}, parents={"repos/acme/fork": ["acme/upstream"]})
        no_pr_in_parent = bash(home, "git push fork feature")
    with then("the push runs, after gh is asked in all three repos"):
        assert quiet(no_pr_in_parent), no_pr_in_parent.reason
        assert gh_calls(home)[-4:] == [forked_from("acme/fork"), OPEN_PR_LIST,
                                       OPEN_PR_LIST + ["-R", "github.com/acme/fork"],
                                       OPEN_PR_LIST + ["-R", "github.com/acme/upstream"]]
    with when("GitHub can not say whether acme/fork is a fork, and gh can not reach GitHub at all"):
        fake_gh(home, open={}, open_in={}, unknown_forks=["repos/acme/fork"])
        unknown_fork = bash(home, "git push fork feature")
        fake_gh(home, fail=True)
        failed = bash(home, "git push fork feature")
    with then("each push is refused, as after any failed lookup, since a PR may be open in a repo the gate did "
              "not ask"):
        assert says(home, unknown_fork, "no_report.md", short=feature[:7], branch="feature"), unknown_fork.reason
        assert says(home, failed, "no_report.md", short=feature[:7], branch="feature"), failed.reason


SSH_HOST_WHY ="ssh could not say which host the remote's address names"


def test_a_push_to_any_github_host_name_or_an_ssh_alias_of_github_is_judged_and_other_hosts_stay_quiet(home):
    with given("branch feature with open PR 7 and a new commit with no report, ssh aliases github-work for "
               "github.com and lab-work for gitlab.com, and remotes that name hosts in different ways"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        with_fake_ssh(home, hosts={"github-work": "github.com", "lab-work": "gitlab.com"}, fail=["broken-alias"])
        # Joined from two parts so the shape scan does not take these addresses for email.
        ssh_user = "git" + "@"
        remotes = {"ssh443": f"ssh://{ssh_user}ssh.github.com:443/acme/demo.git",
                   "www": "https://www.github.com/acme/demo.git",
                   "upper": f"{ssh_user}GitHub.COM.:acme/demo.git",
                   "alias": f"{ssh_user}github-work:acme/demo.git",
                   "aliasurl": f"ssh://{ssh_user}github-work/acme/demo.git",
                   "lab": f"{ssh_user}lab-work:acme/demo.git",
                   "gitlab": "https://gitlab.com/acme/demo.git",
                   "lookalike": "https://github.com.example.org/acme/demo.git",
                   "notgithub": f"{ssh_user}notgithub.com:acme/demo.git",
                   "broken": f"{ssh_user}broken-alias:acme/demo.git"}
        for name, url in remotes.items():
            home.git("remote", "add", name, url)
    on_github = ["ssh443", "www", "upper", "alias", "aliasurl"]
    with when("Claude pushes feature to each remote that reaches GitHub"):
        results = {name: bash(home, f"git push {name} feature") for name in on_github}
    with then("each is judged as the push to GitHub it is, and refused for feature's commit"):
        for name, result in results.items():
            assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), (name, result.reason)
    with when("Claude pushes feature to each remote on another host"):
        calls_before = len(ssh_calls(home))
        results = {name: bash(home, f"git push {name} feature") for name in ("lab", "gitlab", "lookalike",
                                                                               "notgithub")}
    with then("each runs with no answer, and ssh is asked only about the ssh addresses"):
        for name, result in results.items():
            assert quiet(result), (name, result.reason)
        assert ssh_calls(home)[calls_before:] == [["-G", "--", "lab-work"], ["-G", "--", "notgithub.com"]]
    with when("ssh can not read its config for the alias a remote names"):
        broken = bash(home, "git push broken feature")
    with then("the push is refused, since the alias may name GitHub"):
        assert says(home, broken, "unreadable_push.md", why=SSH_HOST_WHY), broken.reason


SSH_COMMAND_WHY = "the gate can not read git's ssh command, so it can not tell which host the push reaches"


def test_an_ssh_alias_is_looked_up_with_the_ssh_config_that_gits_ssh_command_names(home):
    with given("branch feature with open PR 7 and a new commit with no report, an ssh config file that only git's "
               "ssh command names, where alias work-only is github.com and lab-only is gitlab.com, and remotes "
               "for both aliases"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        fake = with_fake_ssh(home)
        work_config = os.path.join(home.root, "work.json")
        home.write_json(work_config, {"hosts": {"work-only": "github.com", "lab-only": "gitlab.com"}})
        with open(work_config + ".name", "w") as handle:
            handle.write(work_config)  # so a substitution that reads it names a real config
        ssh_user = "git" + "@"
        home.git("remote", "add", "work", f"{ssh_user}work-only:acme/demo.git")
        home.git("remote", "add", "lab", f"{ssh_user}lab-only:acme/demo.git")
        del home.env["GIT_SSH_COMMAND"]  # git takes the variable over the repo's setting
    with when("the repo's core.sshCommand passes that file to ssh with -F, and Claude pushes feature to each alias"):
        home.git("config", "core.sshCommand", f"{fake} -F {work_config}")
        calls_before = len(ssh_calls(home))
        to_work, to_lab = bash(home, "git push work feature"), bash(home, "git push lab feature")
    with then("the push to work-only is judged as the push to GitHub it is, the one to lab-only runs, and ssh -G "
              "was given the same -F"):
        assert says(home, to_work, "no_report.md", short=feature[:7], branch="feature"), to_work.reason
        assert quiet(to_lab), to_lab.reason
        assert ssh_calls(home)[calls_before:] == [["-F", work_config, "-G", "--", "work-only"],
                                                  ["-F", work_config, "-G", "--", "lab-only"]]
    with when("the setting names ssh by its name and the file as ~/work.json"):
        home.git("config", "core.sshCommand", "ssh -F ~/work.json")
        calls_before = len(ssh_calls(home))
        by_name = bash(home, "git push work feature")
    with then("ssh on PATH is asked with ~ read as the shell reads it, and the push is refused"):
        assert says(home, by_name, "no_report.md", short=feature[:7], branch="feature"), by_name.reason
        assert ssh_calls(home)[calls_before:] == [["-F", work_config, "-G", "--", "work-only"]]
    with when("GIT_SSH_COMMAND sets HostName for every host in the hook's environment, then GIT_SSH_COMMAND and "
              "git's -c option name the file for one command, while the repo's setting is plain ssh"):
        home.git("config", "core.sshCommand", "ssh")
        home.env["GIT_SSH_COMMAND"] = f"{fake} -o HostName=github.com"
        from_environment = bash(home, "git push lab feature")
        del home.env["GIT_SSH_COMMAND"]
        for_one_command = bash(home, f"GIT_SSH_COMMAND='{fake} -F {work_config}' git push work feature")
        by_git_option = bash(home, f"git -c core.sshCommand='{fake} -F {work_config}' push work feature")
        plain = bash(home, "git push work feature")
    with then("the variable and git's -c option win over the repo's setting, as in git, so those pushes are "
              "refused, and plain ssh, which does not know work-only, lets the last push run"):
        assert says(home, from_environment, "no_report.md", short=feature[:7], branch="feature"), \
            from_environment.reason
        assert says(home, for_one_command, "no_report.md", short=feature[:7], branch="feature"), \
            for_one_command.reason
        assert says(home, by_git_option, "no_report.md", short=feature[:7], branch="feature"), by_git_option.reason
        assert quiet(plain), plain.reason
    with when("git's ssh command is another program, holds a substitution, or names a host of its own"):
        unreadable_steps = {}
        for case, setting in {"another program": "plink -batch",
                              "a substitution": f"{fake} -F $(cat {work_config}.name)",
                              "a host of its own": f"{fake} -F {work_config} jump-host"}.items():
            home.git("config", "core.sshCommand", setting)
            unreadable_steps[case] = bash(home, "git push work feature")
    with then("each push is refused, since the gate can not tell which ssh config git will read"):
        for case, result in unreadable_steps.items():
            assert says(home, result, "unreadable_push.md", why=SSH_COMMAND_WHY), (case, result.reason)


def test_github_not_the_local_copy_of_its_refs_says_what_a_push_or_a_new_pr_sends(home):
    with given("branch feature with open PR 7, pushed, and a new commit with no report"):
        with_github(home)
        feature = pushed_branch(home)
        on_github = head(home, "origin/feature")
        fake_gh(home, open={"feature": 7})
    with when("the local copy of origin's feature is moved to the new commit, and Claude pushes"):
        home.git("update-ref", "refs/remotes/origin/feature", "HEAD")
        moved = bash(home, "git push")
    with then("the push is refused for the new commit, since GitHub's feature is still the old one"):
        assert says(home, moved, "no_report.md", short=feature[:7], branch="feature"), moved.reason
    with when("the local copy of origin's default branch is pointed at feature, and Claude pushes feature"):
        home.git("update-ref", "refs/remotes/origin/feature", on_github)
        home.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feature")
        as_default = bash(home, "git push origin feature")
    with then("the push is refused too, since feature has an open PR whatever the local copy says"):
        assert says(home, as_default, "no_report.md", short=feature[:7], branch="feature"), as_default.reason
    with when("gh can not reach GitHub and Claude pushes the new commit to main"):
        home.git("remote", "set-head", "origin", "main")
        fake_gh(home, fail=True)
        to_main = {step: bash(home, step) for step in ("git push origin feature:main",
                                                       "git push origin HEAD:refs/heads/main")}
    with then("each is refused, since without GitHub's answer main may have an open PR"):
        for step, result in to_main.items():
            assert says(home, result, "no_report.md", short=feature[:7], branch="main"), (step, result.reason)
    with when("feature is pushed, gh works again, and the same push comes with nothing new for PR 7"):
        home.git("push", "-q")
        fake_gh(home, open={"feature": 7})
        again = bash(home, "git push")
    with then("it runs after gh reads the head of PR 7 from GitHub, in its own repo and in acme/demo"):
        assert quiet(again), again.reason
        assert gh_calls(home)[-3:] == [forked_from("acme/demo"), OPEN_PR_LIST,
                                       OPEN_PR_LIST + ["-R", "github.com/acme/demo"]]
    with when("branch side is pushed with no report, its next commit gets a report, and the local copy of "
              "origin's side is moved to that commit"):
        home.git("checkout", "-q", "-b", "side", "main")
        side = change(home, message="Start side")
        home.git("push", "-q", "-u", "origin", "side")
        tested = change(home)
        record(home)
        home.git("update-ref", "refs/remotes/origin/side", tested)
        good = body_file(home, CANONICAL)
        forged = bash(home, f"gh pr create --head side --title Side --body-file {good}")
    with then("a new PR that names side is refused for the commit GitHub has, since gh pushes nothing then"):
        assert says(home, forged, "no_report.md", short=side[:7], branch="side"), forged.reason
    with when("Claude opens a PR for a branch GitHub does not have"):
        missing = bash(home, f"gh pr create --head nowhere --title T --body-file {good}")
    with then("it is refused, since the gate can not read the commit the PR would open at"):
        assert says(home, missing, "branch_head_unknown.md", branch="nowhere"), missing.reason


def test_a_new_pr_with_no_head_named_is_judged_at_the_commit_github_has_when_gh_pushes_nothing(home):
    with given("branch feature pushed to GitHub at a commit with no report, and a new commit with a report"):
        elsewhere = with_github(home)
        tested = pushed_branch(home)
        on_github = head(home, "origin/feature")
        record(home)
        good = body_file(home, CANONICAL)
        step = f"gh pr create --title T --body-file {good}"
    with when("the local copy of origin's feature is moved to the tested commit, and Claude opens a PR"):
        home.git("update-ref", "refs/remotes/origin/feature", tested)
        moved = bash(home, step)
    with then("it is refused, since gh then pushes nothing and opens the PR at the commit GitHub has"):
        assert says(home, moved, "github_head_differs.md", branch="feature", short=tested[:7],
                    commit=on_github[:7]), moved.reason
        assert ["api", "repos/acme/demo/git/ref/heads/feature", "--jq", ".object.sha"] in gh_calls(home)
    with when("a local copy of a branch GitHub does not have is at the tested commit too"):
        home.git("update-ref", "refs/remotes/origin/feature", on_github)
        home.git("update-ref", "refs/remotes/origin/side", tested)
        unknown = bash(home, step)
    with then("it is refused, since the gate can not read the commit gh may open the PR at"):
        assert says(home, unknown, "branch_head_unknown.md", branch="side"), unknown.reason
    with when("that copy is gone, a remote off GitHub has a copy at the tested commit, and gh can not reach GitHub"):
        home.git("update-ref", "-d", "refs/remotes/origin/side")
        home.git("remote", "add", "backup", elsewhere)
        home.git("push", "-q", "backup", "feature")
        fake_gh(home, fail=True)
        calls_before = len(gh_calls(home))
        ahead = bash(home, step)
    with then("it runs, since gh would push the tested commit before it opens the PR, and no branch is looked up"):
        assert quiet(ahead), ahead.reason
        assert not [call for call in gh_calls(home)[calls_before:] if "/git/ref/heads/" in " ".join(call)]
    with when("feature is pushed, so GitHub has the tested commit, and Claude opens the PR"):
        fake_gh(home)
        home.git("push", "-q", "origin", "feature")
        pushed = bash(home, step)
    with then("it runs after GitHub says feature is at the tested commit"):
        assert quiet(pushed), pushed.reason
        assert ["api", "repos/acme/demo/git/ref/heads/feature", "--jq", ".object.sha"] in gh_calls(home)


HEAD_REPO_WHY = "it names the head's repo in a way the gate does not read"
DRAFT_WHY = ("GitHub's REST update for a PR takes no draft field and can not change the draft state, so use "
             "gh pr ready or gh pr ready --undo instead")
REMOTE_URL_WHY = "git could not say which URL the remote names"


def test_gh_api_pr_steps_are_judged_whichever_way_the_endpoint_names_the_repo(home):
    with given("branch side pushed with no report, its next commit with a report, a good and a bad body, and PR 7"):
        with_github(home)
        home.git("checkout", "-q", "-b", "side", "main")
        side = change(home, message="Start side")
        home.git("push", "-q", "-u", "origin", "side")
        change(home)
        record(home)
        good, bad = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
    creates = [f"gh api repositories/123/pulls -f head=side -f base=main -F body=@{good}",
               f"gh api https://api.github.com/repositories/123/pulls -f head=side -F body=@{good}",
               f"GH_REPO=acme/demo gh api 'repos/{{owner}}/{{repo}}/pulls' -f head=side -F body=@{good}",
               f"gh api repos/:owner/:repo/pulls -f head=side -F body=@{good}"]
    with when("Claude opens a PR at side through gh api, naming the repo by its numeric id or by the "
              "{owner}/{repo} and :owner/:repo places that gh fills from GH_REPO or the current repo"):
        results = {step: bash(home, step) for step in creates}
        filled_head = bash(home, f"gh api 'repos/{{owner}}/{{repo}}/pulls' -F 'head={{branch}}' -F body=@{good}")
    with then("each is refused for the commit GitHub has on side, and a head gh fills in is refused as one "
              "GitHub does not have"):
        for step, result in results.items():
            assert says(home, result, "no_report.md", short=side[:7], branch="side"), (step, result.reason)
        assert says(home, filled_head, "branch_head_unknown.md", branch="{branch}"), filled_head.reason
    with when("Claude names the head's repo with head_repo, or a fork's branch through the numeric id form"):
        head_repo = bash(home, f"gh api repos/acme/demo/pulls -f head=side -f head_repo=acme/demo -F body=@{good}")
        fork = bash(home, f"gh api repositories/123/pulls -f head=octo:side -F body=@{good}")
    with then("both are refused, since the gate does not read which repo the head is in"):
        for result in (head_repo, fork):
            assert says(home, result, "unreadable_push.md", why=HEAD_REPO_WHY), result.reason
    with when("Claude edits PR 7's body to one with no tested part by its numeric repo id"):
        edited = bash(home, f"gh api -X PATCH repositories/123/pulls/7 -F body=@{bad}")
    with then("it is refused with pr-check's sentence"):
        assert edited.refused and edited.reason == missing_part(home), edited.reason
    drafts = ["gh api -X PATCH repos/acme/demo/pulls/7 -F draft=false",
              "gh api -X PATCH repositories/123/pulls/7 -F draft=false"]
    with when("Claude changes PR 7's draft state through gh api, by owner and name or by numeric id"):
        results = {step: bash(home, step) for step in drafts}
    with then("each is refused with a reason that says GitHub's REST update can not change the draft state "
              "and names gh pr ready instead"):
        for step, result in results.items():
            assert says(home, result, "unreadable_push.md", why=DRAFT_WHY), (step, result.reason)
    create = graphql_call('mutation { createPullRequest(input: {repositoryId: "R_base", headRepositoryId: "R_head", '
                          'baseRefName: "main", headRefName: "side", title: "T", body: $body}) '
                          '{ pullRequest { number } } }', f"-F body=@{good}")
    with when("Claude opens a PR at side through GraphQL, naming the head's repo by its node id"):
        by_node = bash(home, create)
    with then("it is refused for the commit GitHub has on side, read from the head's repo"):
        assert says(home, by_node, "no_report.md", short=side[:7], branch="side"), by_node.reason
        assert any(call[:2] == ["api", "graphql"] and "id=R_head" in call for call in gh_calls(home))
    with when("the same mutation names no head repo and no base branch, or GitHub does not have the branch"):
        base_only = bash(home, create.replace('headRepositoryId: "R_head", ', "")
                         .replace('baseRefName: "main", ', ""))
        nowhere = bash(home, create.replace('headRefName: "side"', 'headRefName: "nowhere"'))
    with then("the first is judged in the base repo and refused for side, the second can not be read"):
        assert says(home, base_only, "no_report.md", short=side[:7], branch="side"), base_only.reason
        base_calls = [call for call in gh_calls(home) if call[:2] == ["api", "graphql"] and "id=R_base" in call]
        assert any("ref=refs/heads/side" in call for call in base_calls), base_calls
        assert any(any("defaultBranchRef" in word for word in call) for call in base_calls), base_calls
        assert says(home, nowhere, "branch_head_unknown.md", branch="nowhere"), nowhere.reason
    with when("the mutation names only the head's repo, written in the query or passed as a variable"):
        head_only = [bash(home, create.replace('repositoryId: "R_base", ', "")),
                     bash(home, create.replace('headRepositoryId: "R_head"', "headRepositoryId: $head")
                          .replace('repositoryId: "R_base", ', "") + " -f head=R_head")]
    with then("each is refused, since the head's repo id does not say which repo the PR goes into"):
        for result in head_only:
            assert says(home, result, "unreadable_push.md", why=HEAD_REPO_WHY), result.reason
    with when("side's tested commit is pushed"):
        home.git("push", "-q")
    with then("the same steps run with no answer"):
        for step in creates + [create]:
            assert quiet(bash(home, step)), step


QUERY_WHY = "the gate can not read the whole GraphQL query before the command runs"
BEFORE_WHY = "another command runs before it on the same line"
NODE_WHY = "a node id names the PR, not its number"


def test_a_graphql_query_the_gate_can_not_read_in_full_is_judged_as_a_write(home):
    with given("branch side pushed with no report, its next commit with a report, a good body, and a GraphQL file "
               "that only reads"):
        with_github(home)
        home.git("checkout", "-q", "-b", "side", "main")
        side = change(home, message="Start side")
        home.git("push", "-q", "-u", "origin", "side")
        change(home)
        record(home)
        good = body_file(home, CANONICAL, "good.md")
        reads = body_file(home, "query { viewer { login } }", "reads.graphql")
    create = ('mutation { createPullRequest(input: {repositoryId: "R_1", baseRefName: "main", headRefName: "side", '
              'title: "T", body: $body}) { pullRequest { number } } }')
    ready = 'mutation { markPullRequestReadyForReview(input: {pullRequestId: "PR_kw1"}) { clientMutationId } }'

    def hidden_forms(query):
        sent = shlex.quote(json.dumps({"query": query, "variables": {"body": CANONICAL}}))
        return {"a variable": f"Q={shlex.quote(query)}; gh api graphql -f query=\"$Q\" -F body=@{good}",
                "a pipe": f"printf '%s' {shlex.quote(query)} | gh api graphql -F query=@- -F body=@{good}",
                "an --input pipe": f"printf '%s' {sent} | gh api graphql --input -",
                "a field name the shell fills in":
                    f"K=query; gh api graphql -f \"$K\"={shlex.quote(query)} -F body=@{good}"}

    def written_first(query):
        return f"printf '%s' {shlex.quote(query)} > {reads} && gh api graphql -F query=@{reads} -F body=@{good}"

    def fragment_form(query):
        name, selection = query[len("mutation { "):-2].split("(", 1)
        return graphql_call(f"mutation {{ ...Sent }} fragment Sent on Mutation {{ {name}({selection} }}",
                            f"-F body=@{good}")
    hidden = {f"{kind} by {form}": step for kind, query in (("create", create), ("ready", ready))
              for form, step in hidden_forms(query).items()}
    with when("Claude opens a PR at side or marks PR 7 ready through GraphQL, with the query in a variable, a "
              "pipe or an --input pipe, or under a field name the shell fills in"):
        results = {case: bash(home, step) for case, step in hidden.items()}
    with then("each is refused, since the gate can not read what the query does"):
        assert [(case, result.reason) for case, result in results.items() if not result.refused] == []
    with when("the text gate's unreadable check is off and Claude sends the same steps"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        results = {case: bash(home, step) for case, step in hidden.items()}
    with then("this gate refuses each one, saying it can not read the whole query, or naming the field name the "
              "shell fills in"):
        named_field = "a field name the shell fills in"
        assert [(case, result.reason) for case, result in results.items()
                if not says(home, result, "unreadable_push.md",
                            why=API_FIELD_WHY if case.endswith(named_field) else QUERY_WHY)] == []
    with when("Claude writes the mutation over the reading GraphQL file and sends that file in the same call, and "
              "sends the mutations through a fragment on Mutation"):
        overwritten = {kind: bash(home, written_first(query)) for kind, query in (("create", create), ("ready", ready))}
        fragments = {kind: bash(home, fragment_form(query)) for kind, query in (("create", create), ("ready", ready))}
    with then("the file is refused, since it may change before gh reads it, and each fragment gets the answer of "
              "its plain mutation"):
        for kind, result in overwritten.items():
            assert says(home, result, "unreadable_push.md", why=BEFORE_WHY), (kind, result.reason)
        assert says(home, fragments["create"], "no_report.md", short=side[:7], branch="side"), \
            fragments["create"].reason
        assert says(home, fragments["ready"], "unreadable_push.md", why=NODE_WHY), fragments["ready"].reason
    with when("Claude reads through GraphQL with the query written out, or sent from the reading file alone"):
        plain_reads = [bash(home, graphql_call("query { viewer { login } }")),
                       bash(home, f"gh api graphql -F query=@{reads}")]
    with then("each runs, since the gate reads each query in full and neither holds a mutation"):
        for result in plain_reads:
            assert quiet(result), result.reason


def test_a_pr_body_edited_through_gh_api_gets_the_body_check_however_the_call_names_the_pr_or_the_body(home):
    with given("PR 7 and plain issue 12 on GitHub, and a good and a bad body"):
        with_github(home)
        fake_gh(home, issues=[12])
        good, bad = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
    issue_edits = {"by owner and name": "gh api -X PATCH repos/acme/demo/issues/{number} -F body=@{body}",
                   "by numeric id": "gh api --method PATCH repositories/123/issues/{number} -F body=@{body}",
                   "by full URL": "gh api -X PATCH https://api.github.com/repos/acme/demo/issues/{number} "
                                  "-F body=@{body}",
                   "in the current repo": "gh api -X PATCH 'repos/{{owner}}/{{repo}}/issues/{number}' "
                                          "-F body=@{body}"}
    with when("Claude edits PR 7's body to one with no tested part through the issues endpoint, as every PR is "
              "an issue to GitHub's REST API"):
        results = {case: bash(home, step.format(number=7, body=bad)) for case, step in issue_edits.items()}
    with then("each is refused with pr-check's sentence, after gh says 7 is a PR"):
        for case, result in results.items():
            assert result.refused and result.reason == missing_part(home), (case, result.reason)
        assert ["api", "repos/acme/demo/issues/7", "--jq", 'has("pull_request")'] in gh_calls(home)
    with when("Claude edits PR 7's body to a good one the same way, and plain issue 12's body to the bad one"):
        fine = {case: bash(home, step.format(number=7, body=good)) for case, step in issue_edits.items()}
        plain = {case: bash(home, step.format(number=12, body=bad)) for case, step in issue_edits.items()}
    with then("each runs, since the good body has its tested part and issue 12 is not a PR"):
        for case, result in {**fine, **plain}.items():
            assert quiet(result), (case, result.reason)
    by_input = "mutation($pr: UpdatePullRequestInput!) { updatePullRequest(input: $pr) { clientMutationId } }"
    by_body = ('mutation($text: String) { updatePullRequest(input: {pullRequestId: "PR_kw1", body: $text}) '
               "{ clientMutationId } }")

    def sent_file(query, variables, name):
        return body_file(home, json.dumps({"query": query, "variables": variables}), name)

    def object_edits(body, name):
        return {"by -f fields": graphql_call(by_input, shlex.quote("pr[pullRequestId]=PR_kw1"),
                                             "-F", shlex.quote(f"pr[body]=@{body_file(home, body, name + '.md')}")),
                "by an --input object": "gh api graphql --input "
                                        + sent_file(by_input, {"pr": {"pullRequestId": "PR_kw1", "body": body}},
                                                    name + "-object.json"),
                "by an --input variable": "gh api graphql --input "
                                          + sent_file(by_body, {"text": body}, name + "-variable.json")}
    with when("Claude edits PR 7's body through GraphQL with the body inside the input object, by -f fields or "
              "an --input file, or as a variable the --input file sets"):
        bad_objects = {case: bash(home, step) for case, step in object_edits(NO_PART, "bad-object").items()}
        good_objects = {case: bash(home, step) for case, step in object_edits(CANONICAL, "good-object").items()}
        title_only = bash(home, graphql_call(by_input, shlex.quote("pr[pullRequestId]=PR_kw1"),
                                             "-f", shlex.quote("pr[title]=New title")))
        no_input = bash(home, graphql_call(by_input))
    with then("a body with no tested part is refused with pr-check's sentence, a good body and a title edit run, "
              "and an input object the call does not send is refused, since it may hold a body"):
        for case, result in bad_objects.items():
            assert result.refused and result.reason == missing_part(home), (case, result.reason)
        for case, result in {**good_objects, "title only": title_only}.items():
            assert quiet(result), (case, result.reason)
        assert says(home, no_input, "unreadable_push.md", why="a variable or a substitution fills in the PR body"), \
            no_input.reason
    with when("gh can not reach GitHub, and Claude edits issue 12's body to the bad one"):
        fake_gh(home, fail=True)
        unknown = bash(home, issue_edits["by owner and name"].format(number=12, body=bad))
    with then("it is refused, since 12 may be a PR"):
        assert unknown.refused and unknown.reason == missing_part(home), unknown.reason


def test_a_graphql_pr_body_gets_the_body_check_wherever_the_query_sets_it(home):
    with given("PR 7 on GitHub, a good and a bad body as files and as GraphQL strings"):
        with_github(home)
        good_file, bad_file = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
        good, bad = json.dumps(CANONICAL), json.dumps(NO_PART)
    by_default = ('mutation($text: String = {default}) {{ updatePullRequest(input: {{pullRequestId: "PR_kw1", '
                  'body: $text}}) {{ clientMutationId }} }}')

    def edit(inside):
        return graphql_call(f'mutation {{ updatePullRequest(input: {{pullRequestId: "PR_kw1", {inside}}}) '
                            "{ clientMutationId } }")

    def sent_file(query, sent, name):
        return "gh api graphql --input " + body_file(home, json.dumps({"query": query, **sent}), name)
    second = ('mutation { first: updatePullRequest(input: {pullRequestId: "PR_kw1", body: %s}) { clientMutationId } '
              'second: updatePullRequest(input: {pullRequestId: "PR_kw2", body: %s}) { clientMutationId } }')
    created = ('mutation($text: String = %s) { createPullRequest(input: {repositoryId: "R_1", baseRefName: "main", '
               'headRefName: "side", title: "T", body: $text}) { pullRequest { number } } }')
    hidden = {"a default the call does not send": graphql_call(by_default.format(default=bad)),
              "a default when the --input file sends the value outside its variables":
                  sent_file(by_default.format(default=bad), {"text": CANONICAL, "variables": {}}, "outside.json"),
              "a default for a new PR": graphql_call(created % bad),
              "a body after a comment that holds a good one": edit(f"# body: {good}\n body: {bad}"),
              "a body after a comma": edit(f"body:, {bad}"),
              "a second edit after a good one": graphql_call(second % (good, bad)),
              "an input object named after a comment":
                  graphql_call("mutation($pr: UpdatePullRequestInput!) { updatePullRequest(# the edit\n input: $pr) "
                               "{ clientMutationId } }", shlex.quote("pr[pullRequestId]=PR_kw1"),
                               "-F", shlex.quote(f"pr[body]=@{bad_file}"))}
    with when("Claude sets PR 7's body to one with no tested part through GraphQL, by a variable's default, "
              "after a comment or a comma, in a second edit, or in an input object named after a comment"):
        results = {case: bash(home, step) for case, step in hidden.items()}
    with then("each is refused with pr-check's sentence, as GitHub sets that body"):
        assert [(case, result.reason) for case, result in results.items()
                if result.reason != missing_part(home)] == []
    with when("Claude sends a good default, a good value over a bad default, and a good body after a comment that "
              "holds a bad one"):
        fine = {"good default": bash(home, graphql_call(by_default.format(default=good))),
                "sent over a default": bash(home, graphql_call(by_default.format(default=bad),
                                                               f"-F text=@{good_file}")),
                "comment": bash(home, edit(f"# body: {bad}\n body: {good}"))}
        sent_bad = bash(home, graphql_call(by_default.format(default=good), f"-F text=@{bad_file}"))
    with then("each runs, and a bad value sent over a good default is refused"):
        assert [(case, result.reason) for case, result in fine.items() if not quiet(result)] == []
        assert sent_bad.refused and sent_bad.reason == missing_part(home), sent_bad.reason
    with when("Claude sets the body in a block string, which can hold a good body's text before the real one"):
        block = bash(home, edit(f'title: """body: {good}""", body: {bad}'))
    with then("it is refused, since the gate does not read block strings"):
        assert says(home, block, "unreadable_push.md", why=QUERY_WHY), block.reason


def test_a_graphql_call_gets_the_body_check_whatever_path_or_method_names_graphql(home):
    with given("PR 7 on GitHub, and a good and a bad body as files and as GraphQL strings"):
        with_github(home)
        good_file, bad_file = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
        good, bad = json.dumps(CANONICAL), json.dumps(NO_PART)
    by_variable = shlex.quote('query=mutation($t: String) { updatePullRequest(input: {pullRequestId: "PR_kw1", '
                              'body: $t}) { clientMutationId } }')

    def written(body):
        return shlex.quote('query=mutation { updatePullRequest(input: {pullRequestId: "PR_kw1", '
                           f"body: {body}}}) {{ clientMutationId }} }}")

    def variable(path):
        return "-F " + shlex.quote(f"variables[t]=@{path}")
    hidden = {"/graphql with a variables[t] field": f"gh api /graphql -f {by_variable} {variable(bad_file)}",
              "graphql/ with a variables field": f"gh api graphql/ -f {by_variable} -f "
                                                 + shlex.quote("variables=" + json.dumps({"t": NO_PART})),
              "the github.com URL with a variables[t] field":
                  f"gh api https://api.github.com/graphql -f {by_variable} {variable(bad_file)}",
              "-X GET graphql with a variables[t] field": f"gh api -X GET graphql -f {by_variable} "
                                                          f"{variable(bad_file)}",
              "--method GET /graphql": f"gh api --method GET /graphql -f {written(bad)}",
              "-X get on the github.com URL": f"gh api -X get https://api.github.com/graphql -f {written(bad)}",
              "an enterprise api/graphql URL": f"gh api https://ghe.example.com/api/graphql -f {written(bad)}",
              "an api/graphql path": f"gh api api/graphql -f {written(bad)}",
              "/graphql with an input object in variables[pr]":
                  "gh api /graphql -f " + shlex.quote("query=mutation($pr: UpdatePullRequestInput!) { "
                                                      "updatePullRequest(input: $pr) { clientMutationId } }")
                  + " -f 'variables[pr][pullRequestId]=PR_kw1' -F " + shlex.quote(f"variables[pr][body]=@{bad_file}"),
              "/graphql with --input and a variables[t] field gh puts in the URL":
                  "gh api /graphql --input " + body_file(home, json.dumps({
                      "query": by_variable[len("'query="):-1], "variables": {"t": CANONICAL}}), "sent.json")
                  + " " + variable(bad_file)}
    with when("Claude sets PR 7's body to one with no tested part through GraphQL by a path other than the bare "
              "word graphql, or with GET, where gh sends each field as it is and only variables[t] or a variables "
              "field is a variable"):
        results = {case: bash(home, step) for case, step in hidden.items()}
    with then("each is refused with pr-check's sentence, as the bare graphql call would be"):
        assert [(case, result.reason) for case, result in results.items()
                if result.reason != missing_part(home)] == []
    with when("Claude sends a good body the same ways, a bad top-level t field that gh does not send as a variable "
              "beside a good variables[t], and reads through GraphQL by GET and by URL"):
        fine = {"good variables[t]": f"gh api /graphql -f {by_variable} {variable(good_file)}",
                "good by GET": f"gh api --method GET /graphql -f {written(good)}",
                "read by GET": "gh api -X GET /graphql -f query='query { viewer { login } }'",
                "read by URL": "gh api https://api.github.com/graphql -f query='query { viewer { login } }'"}
        fine = {case: bash(home, step) for case, step in fine.items()}
        stray = bash(home, f"gh api /graphql -f {by_variable} {variable(good_file)} -F "
                           + shlex.quote(f"t=@{bad_file}"))
    with then("each runs, and the stray t field is still read, so the gate never reads less than GitHub may take"):
        assert [(case, result.reason) for case, result in fine.items() if not quiet(result)] == []
        assert stray.refused and stray.reason == missing_part(home), stray.reason
    with when("Claude sends variables as a field that is not a JSON object, as one, or beside a variables[t] field"):
        odd = [bash(home, f"gh api /graphql -f {by_variable} -f " + shlex.quote(variables))
               for variables in ("variables=[1]", "variables={", 'variables={"t": "x"}')]
        both = bash(home, f"gh api /graphql -f {by_variable} {variable(good_file)} -f 'variables={{}}'")
    with then("a field that is not an object and the mixed call are refused, since the gate can not tell what "
              "GitHub takes from them, and the object's t gets the body check"):
        assert [result.reason for result in [*odd[:2], both] if not says(home, result, "unreadable_push.md",
                                                                          why=QUERY_WHY)] == []
        assert odd[2].refused and odd[2].reason == missing_part(home), odd[2].reason


API_ENDPOINT_WHY = "a variable or a substitution fills in a word gh api may read as its endpoint"
API_METHOD_WHY = "a variable or a substitution fills in the gh api method"
API_FIELD_WHY = "a variable or a substitution fills in a gh api field name"


def test_a_gh_api_call_whose_endpoint_method_or_field_name_the_shell_fills_in_is_refused(home):
    with given("branch side pushed with no report, PR 7 on GitHub, a good and a bad body, and a GraphQL file "
               "that sets PR 7's body"):
        with_github(home)
        home.git("checkout", "-q", "-b", "side", "main")
        change(home, message="Start side")
        home.git("push", "-q", "-u", "origin", "side")
        good, bad = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
        query = body_file(home, 'mutation { updatePullRequest(input: {pullRequestId: "PR_kw1", body: "A fix."}) '
                                "{ clientMutationId } }", "edit.graphql")
    hidden = {f'gh api "$ENDPOINT" -f head=side -f base=main -F body=@{good}': API_ENDPOINT_WHY,
              f"gh api repos/acme/demo/$KIND -f head=side -F body=@{good}": API_ENDPOINT_WHY,
              f"gh api $(printf repos/acme/demo/pulls) -f head=side -F body=@{good}": API_ENDPOINT_WHY,
              f"gh api repos/acme/demo/pulls/7 $MORE -F body=@{good}": API_ENDPOINT_WHY,
              f'gh api repos/acme/demo/pulls -X "$METHOD" -f head=side -F body=@{good}': API_METHOD_WHY,
              f"gh api --method=$METHOD repos/acme/demo/pulls/7 -F body=@{bad}": API_METHOD_WHY,
              f'gh api -X PATCH repos/acme/demo/pulls/7 -F "$KEY"=@{bad}': API_FIELD_WHY,
              f'gh api -X PATCH repos/acme/demo/issues/7 -f "${{KEY}}=A fix."': API_FIELD_WHY,
              f'gh api graphql -F "$KEY"=@{query}': API_FIELD_WHY}
    with when("Claude opens a PR at side or edits PR 7's body through gh api, while a variable or a substitution "
              "fills in the endpoint or a part of it, an extra word, the method, or the name of a field"):
        results = {step: bash(home, step) for step in hidden}
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        alone = {step: bash(home, step) for step in hidden}
    with then("each is refused, and with the text gate's unreadable check off this gate refuses each one with a "
              "reason that names the part the shell fills in"):
        for step, result in results.items():
            assert result.refused, (step, result.reason)
        for step, result in alone.items():
            assert says(home, result, "unreadable_push.md", why=hidden[step]), (step, result.reason)
    reads = ["gh api repos/acme/demo/pulls/7", "gh api repos/acme/demo/pulls/7 --jq '.head.ref'",
             "gh api -X GET repos/acme/demo/pulls -f head=acme:side"]
    with when("Claude reads PR 7 and the open PRs at side through gh api, with each part written out"):
        read_results = {step: bash(home, step) for step in reads}
    with then("each runs, since a read with a written out endpoint changes no PR"):
        for step, result in read_results.items():
            assert quiet(result), (step, result.reason)


def test_a_pr_body_edited_with_gh_issue_edit_gets_the_body_check_and_a_plain_issue_does_not(home):
    with given("PR 7 and plain issue 12 on GitHub, plain issue 15 only in acme/other, and a good and a bad body"):
        with_github(home)
        fake_gh(home, issues=[12, "acme/other#15", "github.com/acme/other#15"])
        good, bad = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
    pr_edits = [f"gh issue edit 7 --body-file {bad}", f"gh issue edit '#7' -F {bad}", "gh issue edit 7 -b 'A fix.'",
                f"gh issue edit https://github.com/acme/demo/pull/7 --body-file={bad}",
                f"gh issue edit 12 7 --title T -F{bad}", f"gh -R acme/demo issue edit 7 -F {bad}",
                f"gh issue edit 7 --add-label bug --body-file {bad}"]
    with when("Claude edits PR 7's body to one with no tested part with gh issue edit, which gh sends to the PR "
              "when the number is a PR, by number, #number or URL, with other numbers or flags"):
        results = {step: bash(home, step) for step in pr_edits}
    with then("each is refused with pr-check's sentence, after gh says 7 is a PR"):
        for step, result in results.items():
            assert result.refused and result.reason == missing_part(home), (step, result.reason)
        assert ["api", "repos/{owner}/{repo}/issues/7", "--jq", 'has("pull_request")'] in gh_calls(home)
    quiet_steps = [f"gh issue edit 7 --body-file {good}", f"gh issue edit 12 --body-file {bad}",
                   "gh issue edit 7 --title 'All tests pass'", f"gh issue comment 7 --body-file {bad}",
                   f"gh issue edit 15 -R acme/other -F {bad}",
                   f"gh issue edit https://github.com/acme/other/issues/15 -F {bad}"]
    with when("Claude gives PR 7 a good body, gives plain issue 12 the bad one, edits only PR 7's title, comments "
              "on PR 7, and edits plain issue 15 in acme/other by -R or by URL"):
        quiet_results = {step: bash(home, step) for step in quiet_steps}
    with then("each runs, since only a PR's body gets the check and a comment does not change the body"):
        for step, result in quiet_results.items():
            assert quiet(result), (step, result.reason)
    with when("gh can not reach GitHub, and Claude edits issue 12's body to the bad one"):
        fake_gh(home, fail=True)
        unknown = bash(home, f"gh issue edit 12 --body-file {bad}")
    with then("it is refused, since 12 may be a PR"):
        assert unknown.refused and unknown.reason == missing_part(home), unknown.reason


def test_a_pr_named_by_a_link_gh_decodes_gets_the_body_check_and_a_link_the_gate_can_not_read_counts_as_a_pr(home):
    with given("PR 7 in acme/demo, plain issue 15 only in acme/other, and a bad body"):
        with_github(home)
        fake_gh(home, issues=["github.com/acme/other#15"])
        bad = body_file(home, NO_PART, "bad.md")
    encoded_edits = [f"gh issue edit https://github.com/acme/demo/%70ull/7 -F {bad}",
                     f"gh issue edit https://github.com/acme/demo/issue%73/7 -F {bad}",
                     f"gh issue edit 'https://github.com/acme/demo/pull/7?x=1#top' -F {bad}",
                     f"gh issue edit https://GitHub.com:443/acme/demo/pull/7 -F {bad}"]
    with when("Claude edits PR 7's body to the bad one, naming PR 7 by a link with a percent-encoded path, a query "
              "and fragment, or a capital host name and a port, all of which gh reads as PR 7"):
        encoded = {step: bash(home, step) for step in encoded_edits}
    with then("each is refused with pr-check's sentence, after gh says 7 in acme/demo is a PR"):
        for step, result in encoded.items():
            assert result.refused and result.reason == missing_part(home), (step, result.reason)
        assert ["api", "repos/acme/demo/issues/7", "--jq", 'has("pull_request")'] in gh_calls(home)
    odd_links = [f"gh issue edit https://github.com/acme/demo/PULL/7 -F {bad}",
                 f"gh issue edit 'https://[github.com/acme/demo/pull/7' -F {bad}",
                 f"gh issue edit https:///acme/demo/pull/7 -F {bad}",
                 f"gh issue edit HTTP:acme/demo/pull/7 -F {bad}"]
    with when("Claude edits a body by an http or https word the gate can not read as an issue link"):
        odd = {step: bash(home, step) for step in odd_links}
    with then("each is refused as if it named a PR, since the gate can not tell which issue gh would edit"):
        for step, result in odd.items():
            assert result.refused and result.reason == missing_part(home), (step, result.reason)
    plain_edits = [f"gh issue edit https://github.com/acme/other/%69ssues/15 -F {bad}",
                   f"gh issue edit https://github.com:443/acme/other/issues/15 -F {bad}"]
    with when("Claude edits plain issue 15 in acme/other by a link with a percent-encoded path, or with a port"):
        plain = {step: bash(home, step) for step in plain_edits}
    with then("each runs, since gh reads both links as plain issue 15 there, on the host without its port"):
        for step, result in plain.items():
            assert quiet(result), (step, result.reason)


def test_a_pr_body_edit_inside_a_wrapper_or_a_substitution_is_refused_as_a_step_inside_another_command(home):
    with given("PR 7 on GitHub, a bad body, and a GraphQL file whose aliased mutation sets PR 7's body"):
        with_github(home)
        fake_gh(home)
        good, bad = body_file(home, CANONICAL, "good.md"), body_file(home, NO_PART, "bad.md")
        aliased = body_file(home, 'mutation { edited: updatePullRequest(input: {pullRequestId: "PR_kw1", '
                                  'body: "A fix."}) { clientMutationId } }', "aliased.graphql")
    edits = [f"gh issue edit 7 --body-file {bad}", f"gh api -X PATCH repos/acme/demo/issues/7 -F body=@{bad}",
             f"gh api graphql -F query=@{aliased}"]
    with when("Claude sends each edit as its own command"):
        bare = {edit: bash(home, edit) for edit in edits}
    with then("each gets pr-check's sentence, so the gate reads each one"):
        for edit, result in bare.items():
            assert result.refused and result.reason == missing_part(home), (edit, result.reason)
    wrapped = [form.format(edit=edit) for edit in edits
               for form in ("bash -c '{edit}'", "sh -c '{edit}'", "zsh -c '{edit}'", "eval '{edit}'",
                            "echo $({edit})", "bash <<'EOF'\n{edit}\nEOF")]
    read_by_the_text_gate = [step for step in wrapped if step.startswith(("echo", "bash <<"))]
    with when("Claude sends the same edits inside bash -c, sh -c, zsh -c, eval, a substitution or a heredoc "
              "script"):
        results = {step: bash(home, step) for step in wrapped}
    with then("the text gate answers first where it can not read the text, and this gate refuses each edit it "
              "reads as a step inside another command, as it refuses a wrapped gh pr edit"):
        for step, result in results.items():
            if step in read_by_the_text_gate:
                assert says(home, result, "unreadable_push.md", why="it runs inside another command"), \
                    (step, result.reason)
            else:
                assert result.refused and result.findings == ["unreadable"], (step, result.reason)
    with when("the text gate's unreadable check is off and Claude sends the same edits"):
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        results = {step: bash(home, step) for step in wrapped}
    with then("this gate refuses each one as a step inside another command"):
        for step, result in results.items():
            assert says(home, result, "unreadable_push.md", why="it runs inside another command"), \
                (step, result.reason)
    others = [f"gh issue comment 7 --body-file {bad}", f"gh issue create --title T --body-file {good}",
              "gh api repos/acme/demo/issues/7"]
    with when("Claude comments on PR 7, opens an issue and reads PR 7 through the issues endpoint, inside bash -c "
              "and a heredoc script"):
        results = {step: bash(home, step) for other in others
                   for step in (f"bash -c '{other}'", f"bash <<'EOF'\n{other}\nEOF")}
    with then("each runs, since the gate does not read these steps when they stand alone either"):
        for step, result in results.items():
            assert quiet(result), (step, result.reason)


def test_a_step_inside_another_command_is_refused_however_many_steps_stand_beside_it(home):
    with given("PRs 5, 6 and 7 on GitHub, where PR 7's head has no report"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, heads={"5": feature, "6": feature, "7": feature})
    hiding = ["$X gh pr edit 5 --title t gh pr edit 6 --title u; bash -c 'gh pr ready 7'",
              "gh pr edit 5 --title t gh pr edit 6 --title u; bash -c 'gh pr ready 7'",
              "gh pr edit 5 --title t gh pr ready 7 --undo; eval 'gh pr ready 7'",
              "$X gh pr edit 5 --title t",
              "$X git push origin feature gh pr edit 6 --title u"]
    settings = {"the text gate's unreadable check off": ("off", "public_text_gate.unreadable"),
                "public_repos_only on in a repo the profile does not mark public":
                    ("on", "public_text_gate.public_repos_only")}
    with when("Claude sends gh pr ready 7 inside bash -c or eval after a command that holds two gh steps, or a "
              "command whose program the shell fills in before visible steps, with the text gate letting "
              "unreadable steps through in each of two ways"):
        results = {}
        for setting, (state, flag) in settings.items():
            home.hubctl_from_mod(state, flag)
            results.update({(setting, step): bash(home, step) for step in hiding})
            home.hubctl_from_mod("off" if state == "on" else "on", flag)
    with then("this gate refuses each one as a step inside another command, since the visible steps beside it "
              "do not stand for the step it can not see"):
        for (setting, step), result in results.items():
            assert says(home, result, "unreadable_push.md", why="it runs inside another command"), \
                (setting, step, result.reason)
    with when("Claude sends the two title edits on their own, and gh pr ready 7 as its own command"):
        titles = bash(home, "gh pr edit 5 --title t gh pr edit 6 --title u")
        written_out = bash(home, "gh pr ready 7")
    with then("the title edits run and the ready step is judged on PR 7's head"):
        assert quiet(titles), titles.reason
        assert says(home, written_out, "no_report.md", short=feature[:7]), written_out.reason


def failing_git(home, verb):
    """A PATH whose first git fails one verb, as a lookup can fail, and runs the real git for the rest."""
    real = shutil.which("git", path=home.env["PATH"])
    folder = os.path.join(home.root, "failing-bin")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "git"), "w") as handle:
        handle.write(f'#!/bin/sh\nif [ "$1" = {verb} ]; then exit 128; fi\nexec {shlex.quote(real)} "$@"\n')
    os.chmod(os.path.join(folder, "git"), 0o755)
    return folder + os.pathsep + home.env["PATH"]


def test_a_step_is_judged_where_git_or_gh_sends_it_and_a_lookup_that_fails_is_refused(home):
    with given("branch feature with open PR 7 and a new commit with no report, an origin off GitHub, a remote "
               "hubgh on GitHub, and a URL word that starts with a dash and url.*.insteadOf sends to GitHub"):
        elsewhere = with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        home.git("remote", "set-url", "origin", elsewhere)
        home.git("remote", "add", "hubgh", github_folder(home))
        home.git("config", f"url.{github_folder(home)}.insteadOf", "-gh")
    to_github = ["git push --repo=hubgh", "git push --repo hubgh", "git push --repo=origin --repo=hubgh",
                 "git push -- -gh feature"]
    with when("Claude pushes with --repo naming hubgh and no remote word, or names the dash URL after --"):
        results = {step: bash(home, step) for step in to_github}
        sent = {step: home.git("push", "--dry-run", "--porcelain", *shlex.split(step)[2:]).stdout
                for step in to_github}
    with then("git sends feature to GitHub each time, and each is refused for feature's commit"):
        for step, result in results.items():
            assert "refs/heads/feature:refs/heads/feature" in sent[step], (step, sent[step])
            assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), (step, result.reason)
    elsewhere_steps = ["git push", "git push origin --repo=hubgh", "git push --repo=hubgh --no-repo"]
    with when("a remote word comes before --repo, --no-repo clears it, or nothing names hubgh"):
        results = {step: bash(home, step) for step in elsewhere_steps}
    with then("git pushes to origin, off GitHub, and each runs with no answer"):
        for step, result in results.items():
            assert quiet(result), (step, result.reason)
    with when("the shell fills in the --repo remote, with the text gate's unreadable check on and then off"):
        filled = [bash(home, 'git push --repo="$REMOTE"')]
        home.hubctl_from_mod("off", "public_text_gate.unreadable")
        filled.append(bash(home, 'git push --repo="$REMOTE"'))
        home.hubctl_from_mod("on", "public_text_gate.unreadable")
    with then("the text gate refuses it first, and this gate refuses it too, since it can not tell where it goes"):
        assert filled[0].refused and filled[0].findings == ["unreadable"], filled[0].reason
        assert says(home, filled[1], "unreadable_push.md", why="a variable or a pattern names the branch"), \
            filled[1].reason
    with when("git can not say where the dash URL points"):
        failed, _took = timed(home, "git push -- -gh feature", failing_git(home, "ls-remote"))
    with then("the push is refused, since it may reach GitHub"):
        assert says(home, failed, "unreadable_push.md", why=REMOTE_URL_WHY), failed.reason
    with when("feature's commit gets a report, gh fails, and Claude marks the current branch's PR ready"):
        record(home)
        fake_gh(home, fail=True)
        ready = bash(home, "gh pr ready")
    with then("it is refused, since gh did not say which commit the PR is at"):
        assert says(home, ready, "branch_head_unknown.md", branch="feature"), ready.reason
    with when("gh answers again with the PR's head at the reported commit"):
        fake_gh(home, open={"feature": 7}, heads={"feature": feature})
        again = bash(home, "gh pr ready")
    with then("the step runs"):
        assert quiet(again), again.reason


def test_a_texts_report_is_counted_from_the_base_branch_github_has_not_the_local_copy_of_it(home):
    with given("branch docs with open PR 9 that changes a guide, then app.py, then notes, and a local copy of "
               "origin's main moved to the app.py commit"):
        with_github(home)
        home.git("checkout", "-q", "-b", "docs")
        home.git("push", "-q", "-u", "origin", "docs")
        change(home, "docs/guide.md")
        code = change(home, "app.py")
        sha = change(home, "docs/notes.txt")
        fake_gh(home, open={"docs": 9})
        home.git("update-ref", "refs/remotes/origin/main", code)
    with when("a texts report is made against that local main and Claude pushes"):
        texts_report(home)
        moved = bash(home, "git push")
    with then("the gate lists the changes since main as GitHub has it, and refuses, naming app.py"):
        assert says(home, moved, "texts_has_code.md", short=sha[:7], file="app.py"), moved.reason
    with when("main moves on GitHub to a commit this repo has not fetched, and docs changes only the guide again"):
        other = os.path.join(home.root, "other-clone")
        home.git("clone", "-q", github_folder(home), other)
        github_main = change(home, "README.md", cwd=other)
        home.git("push", "-q", "origin", "main", cwd=other)
        home.git("reset", "-q", "--hard", "origin/docs")
        texts_sha = change(home, "docs/guide.md")
        texts_report(home)
        unfetched = bash(home, "git push")
    with then("the push is refused until main is fetched, since the gate can not list the changes"):
        assert says(home, unfetched, "texts_base_missing.md", short=texts_sha[:7], base="main",
                    commit=github_main[:7]), unfetched.reason
    with when("Claude fetches origin and pushes again"):
        home.git("fetch", "-q", "origin")
        fetched = bash(home, "git push")
    with then("the push runs"):
        assert quiet(fetched), fetched.reason
    with when("Claude marks PR 9 ready in a repo named with -R, where GitHub has no main, and then in this repo"):
        fake_gh(home, open={"docs": 9}, heads={"someone/fork#9": texts_sha, "9": texts_sha},
                unknown_repos=["someone/fork"])
        steps = {step: bash(home, step) for step in ("gh pr ready 9 -R someone/fork", "gh pr ready 9")}
    with then("the first is refused, since the report is counted from main in the repo the step goes to, and the "
              "second runs"):
        assert says(home, steps["gh pr ready 9 -R someone/fork"], "branch_head_unknown.md", branch="main"), \
            steps["gh pr ready 9 -R someone/fork"].reason
        assert quiet(steps["gh pr ready 9"]), steps["gh pr ready 9"].reason
