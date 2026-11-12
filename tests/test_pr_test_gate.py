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


def test_every_way_to_name_the_pushed_commit_finds_it_and_a_push_it_can_not_read_is_refused(home):
    with given("branch feature with open PR 7, a worktree on branch side with open PR 8, both with new "
               "commits, and a worktree on a detached HEAD"):
        with_github(home)
        feature = pushed_branch(home)
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
             "git push --no-verify origin feature", "git push -o ci.skip origin feature", "git push origin",
             f"cd {home.work} && git push", "GIT_TRACE=0 git push", "git push 2>&1 | tail -3",
             "git push origin feature 2>/dev/null"]
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
                  "git push origin 'feature": "the line does not parse",
                  "git status && git push": "another command runs before it on the same line",
                  "git fetch -q; git push origin feature": "another command runs before it on the same line",
                  "timeout 60 git push origin feature": "another command runs before it on the same line",
                  "echo $(git push origin feature)": "it runs inside another command",
                  "bash -c 'git push origin feature'": "it runs inside another command",
                  "printf feature | xargs git push origin": "xargs or parallel fills in its words"}
    with when("Claude sends pushes whose commit the gate can not read: a variable, a detached HEAD, every branch, "
              "a broken quote, a step after another command or program, a substitution, a wrapper and xargs"):
        results = {form: bash(home, form) for form in unreadable}
    text_gate_first = ["git push origin $BRANCH", "bash -c 'git push origin feature'",
                       "printf feature | xargs git push origin"]
    with then("the text gate answers first for the variable, the wrapper and xargs, since it can not read "
              "their commits either, and every other form is asked to be written out as its own command"):
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
             "git status", "gh pr view 7", "gh pr list", "gh pr comment 7 --body 'Looks good'",
             "gh pr review 7 --approve", "gh pr merge 7 --squash", "gh pr close 7", "gh pr edit 7 --add-label bug",
             "gh pr edit 7 --title 'Keep the draft'", "gh api repos/acme/demo/pulls/7", 'echo "git push"',
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
        assert gh_calls(home) == [["pr", "list", "--head", "feature", "--state", "open", "--json", "number",
                                   "--limit", "1"]]
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
    with then("the gate reads it: the whole body runs and the other is refused"):
        assert quiet(heredoc), heredoc.reason
        assert bare_heredoc.refused and bare_heredoc.reason == missing_part(home)
    with when("Claude lets gh fill the body from the commits"):
        filled = bash(home, "gh pr create --fill")
    with then("it is refused, because a filled body has no tested part"):
        assert filled.refused and filled.reason == missing_part(home)
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
    with given("branch feature with open PR 7 and a new commit, and side, whose head is PR 12's"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("branch", "side", "main")
        side = change(home, cwd=worktree(home, "side", "side"))
        fake_gh(home, open={"feature": 7}, heads={"12": side, "13": feature})
    with when("Claude marks the current branch's PR ready with no report"):
        result = bash(home, "gh pr ready")
    with then("it is refused, and it runs once feature has a report"):
        assert says(home, result, "no_report.md", short=feature[:7], branch="feature"), result.reason
        record(home)
        assert quiet(bash(home, "gh pr ready"))
    with when("Claude marks PR 12 and PR 13 ready by number"):
        twelve = bash(home, "gh pr ready 12")
        thirteen = bash(home, "gh pr ready 13")
    with then("the gate reads each head from gh: PR 12's head has no report, PR 13's has"):
        assert says(home, twelve, "no_report.md", short=side[:7]), twelve.reason
        assert quiet(thirteen)
        assert ["pr", "view", "12", "--json", "headRefOid"] in gh_calls(home)
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
        undo = bash(home, "gh pr ready 12 --undo")
    with then("it runs"):
        assert quiet(undo)
    with when("Claude edits PR 12's body, once without a tested part and once with one"):
        bad = bash(home, f"gh pr edit 12 --body-file {body_file(home, NO_PART)}")
        fine = bash(home, f"gh pr edit 12 --body-file {body_file(home, CANONICAL)}")
    with then("only the body is checked: the bad body is refused and the good one runs, though side has no report"):
        assert bad.refused and bad.reason == missing_part(home)
        assert quiet(fine)
    with when("Claude edits the body or opens a PR through gh api"):
        patch = bash(home, "gh api -X PATCH repos/acme/demo/pulls/12 -f body='All tests pass.'")
        post = bash(home, f"gh api repos/acme/demo/pulls -f head=side -f base=main -f title=T "
                          f"-F body=@{body_file(home, CANONICAL)}")
        graphql = bash(home, "gh api graphql -f query='mutation { markPullRequestReadyForReview("
                             "input: {pullRequestId: \"PR_kw1\"}) { clientMutationId } }'")
    with then("the body edit is checked, the new PR needs side's report, and a ready step by node id is "
              "asked for its number"):
        assert patch.refused and patch.reason == missing_part(home)
        assert says(home, post, "no_report.md", short=side[:7], branch="side"), post.reason
        assert says(home, graphql, "unreadable_push.md"), graphql.reason


def test_a_texts_report_counts_only_while_the_branch_changes_nothing_but_text_files(home):
    with given("branch docs with open PR 9 that changes only a guide, with a texts report for its head"):
        with_github(home)
        home.git("checkout", "-q", "-b", "docs")
        home.git("push", "-q", "-u", "origin", "docs")
        change(home, "docs/guide.md")
        change(home, "locales/de.json")
        change(home, "docs/shot.png")
        fake_gh(home, open={"docs": 9})
        texts_report(home)
    with when("Claude pushes it"):
        result = bash(home, "git push")
    with then("it runs"):
        assert quiet(result), result.reason
    for name in ("app.py", "text/notes.py", "docs/site.js"):
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
        for words in ("draft or not", "outermost seam", "pr-tested run --scenarios", "pr-tested texts --base",
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


def slow_git(home, seconds):
    """A git first on PATH that waits before it runs the real git."""
    real = shutil.which("git", path=home.env["PATH"])
    folder = os.path.join(home.root, "slow-bin")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "git"), "w") as handle:
        handle.write(f"#!/bin/sh\nsleep {seconds}\nexec {shlex.quote(real)} \"$@\"\n")
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
    with then("the gate refuses because its lookups were too slow, in under 12 seconds"):
        assert says(home, result, "slow.md"), result.reason
        assert took < 12, took
    with when("every git call takes 1.5 seconds and one push names two branches"):
        result, took = timed(home, "git push origin feature side", slow_git(home, 1.5))
    with then("no single call is too slow, but the shared time runs out, and the answer still comes in under "
              "12 seconds"):
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
