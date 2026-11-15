"""The PR test gate, end to end through hub's dispatcher.

Each scenario walks a real repo whose origin names GitHub while its pushes go
to a local bare repo, so the gate sees GitHub and nothing leaves the machine.
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
ORIGIN = "https://github.com/acme/demo.git"
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


# The repo, its remote and the fake gh


def with_github(home):
    """hub installed, a repo whose origin names GitHub and pushes to a local bare repo, and the fake gh."""
    home.install()
    bare = os.path.join(home.root, "remote.git")
    home.git("init", "-q", "--bare", bare)
    home.git("remote", "add", "origin", ORIGIN)
    home.git("config", f"url.{bare}.pushInsteadOf", ORIGIN)
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
    return bare


def fake_gh(home, **config):
    home.write_json(home.env["FAKE_GH_CONFIG"], config)


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
        assert says(home, bash(home, "git push"), "failed.md", short=changed[:7], which="pytest -q tests")


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
             "git push --del --no-del origin feature", "git push --mirror --no-mirror origin feature"]
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
                       "git push --branch origin"]
    with then("the text gate answers first for the variable, the wrapper, xargs, the option git could read two "
              "ways and every branch, since it can not read their commits either, and every other form is asked "
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
            handle.write(f"URL: {ORIGIN}\nPush: refs/heads/fix-a:refs/heads/feature\n")
        with open(os.path.join(home.work, ".git", "branches", "old2"), "w") as handle:
            handle.write(f"{ORIGIN}#feature\n")
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
    with given("branch feature with open PR 7 and a new commit, branch side with a commit, no reports, "
               "and new-PR requests for side and feature kept in JSON files"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("branch", "side", "main")
        side = change(home, cwd=worktree(home, "side", "side"))
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
    with then("this gate refuses it as a push inside another command, even with a report, and refuses a gh api "
              "option it does not know"):
        assert says(home, wrapped, "unreadable_push.md", why="it runs inside another command"), wrapped.reason
        assert says(home, unknown_option, "unreadable_push.md", why="gh api has an option the gate does not know"), \
            unknown_option.reason
        assert says(home, hidden_action, "unreadable_push.md",
                    why="a variable or a substitution fills in a word gh may read as its command"), hidden_action.reason


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
    steps = ["git push origin feature:main", "git push origin HEAD:refs/heads/main", "git push origin v1",
             "git push origin refs/tags/v1", "git push --tags", "git push origin --tags",
             "git push --delete origin feature", "git push -d origin feature", "git push origin :feature",
             "git push --dry-run", "git push -n origin feature", "git push mirror feature", "git log --oneline -3",
             "git push --dry origin feature", "git push --del origin feature", "git push -onotify -n origin feature",
             "git push origin v1:refs/tags/v2", "git push origin refs/tags/v1:refs/tags/v1",
             "git status", "gh pr view 7", "gh pr list", "gh pr comment 7 --body 'Looks good'",
             "gh pr review 7 --approve", "gh pr merge 7 --squash", "gh pr close 7", "gh pr edit 7 --add-label bug",
             "gh pr edit 7 --title 'Keep the draft'", "gh api repos/acme/demo/pulls/7",
             "gh api https://api.github.com/repos/acme/demo/pulls/7", 'echo "git push"',
             commit("Explain git push in the docs")]
    with when("Claude sends pushes to the default branch, tags, deletes, dry runs, another host, reads and "
              "PR steps that change no commit"):
        results = {step: bash(home, step) for step in steps}
    with then("each runs with no answer, and the gate never asked gh"):
        for step, result in results.items():
            assert quiet(result), (step, result.reason)
        assert gh_calls(home) == []
    with when("origin names no default branch"):
        home.git("remote", "set-head", "origin", "-d")
    with then("main and master count as default branches"):
        assert quiet(bash(home, "git push origin feature:master"))
        assert quiet(bash(home, "git push origin feature:main"))
    with when("gh works and feature has no open PR"):
        fake_gh(home, open={})
        result = bash(home, "git push")
    with then("the push runs after one lookup for feature, since the later new PR needs the report"):
        assert quiet(result)
        assert gh_calls(home) == [["pr", "list", "--head", "feature", "--state", "open", "--json",
                                   "number,baseRefName", "--limit", "1"]]
    with when("feature is pushed, and the same push comes again with nothing new"):
        home.git("push", "-q")
        fake_gh(home, fail=True)
        again = bash(home, "git push")
    with then("it runs with no lookup"):
        assert quiet(again) and len(gh_calls(home)) == 1


def test_a_new_pr_needs_a_report_for_its_head_and_a_tested_part_that_says_what_was_not_tested(home):
    with given("branch feature pushed with no PR yet, and branch side with a commit and no report"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("push", "-q")
        home.git("branch", "side", "main")
        side_folder = worktree(home, "side", "side")
        side = change(home, cwd=side_folder)
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
        side = change(home, cwd=worktree(home, "side", "side"))
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
    with when("gh takes 8 seconds to answer"):
        fake_gh(home, sleep=8, heads={"12": feature})
        started = time.monotonic()
        slow = bash(home, "gh pr ready 12")
        took = time.monotonic() - started
    with then("the gate stops waiting after its 3 seconds for gh and refuses, well inside the hook's time"):
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
    with when("Claude edits the body or opens a PR through gh api"):
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
    with then("the push is refused and names the check"):
        assert says(home, bash(home, "git push"), "failed.md", which="make texts")
    with when("the texts report passes, but PR 9 goes into release, not main"):
        texts_report(home)
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
    with when("Claude opens it into main, or names no base so it goes into the remote's default branch"):
        into_main = bash(home, f"gh pr create --base main --title Guide --body-file {good}")
        no_base = bash(home, f"gh pr create --title Guide --body-file {good}")
        last_wins = bash(home, f"gh pr create --base release --base main --title Guide --body-file {good}")
    with then("each is refused, since the report lists the changes since release, not since main"):
        for result in (into_main, no_base, last_wins):
            assert says(home, result, "texts_base.md", base="release", into="main"), result.reason
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
            assert says(home, result, name, short=sha[:7]), (name, fields, result.reason)
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
