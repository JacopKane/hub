"""The pr-tested verb records how a commit was tested, in the place the PR test gate reads.

Each scenario runs the verb through hubctl in a fake HOME, as Claude would, and
checks the gate's answer where it matters. The only mock is the fake gh the gate
asks about open PRs.
"""
import json
import os
import re
import shlex
import threading
import time

from steps import and_, given, then, when
from test_pr_test_gate import (bash, body_file, change, fake_gh, head, pushed_branch, quiet, reports_folder, says,
                               with_github, worktree)

PASSING = "printf '3 passed\\n'"
SMOKE = "printf 'smoke ok\\n'"
LEFT_OUT = "Windows, since no runner has it"


def pr_tested(home, *words, cwd=None):
    return home.hubctl("pr-tested", *words, cwd=cwd, check=False)


def run(home, *words, cwd=None):
    """pr-tested run with a passing scenario, a smoke run and a line on what was not tested, plus the words given."""
    return pr_tested(home, "run", "--scenario", PASSING, "--smoke", SMOKE, "--not-tested", LEFT_OUT, *words, cwd=cwd)


def report_of(home, sha, cwd=None):
    with open(os.path.join(reports_folder(home, cwd), sha + ".json")) as handle:
        return json.load(handle)


def test_a_recorded_run_prints_the_tested_part_and_lets_the_push_through(home):
    with given("branch feature with an open PR and a new commit that has no report"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        before = bash(home, "git push")
    with when("Claude records a run with a scenario, a smoke run and a line on what was not tested"):
        done = run(home)
    with then("it writes the report for that commit with each exit code, time and output tail"):
        assert done.returncode == 0, done.stderr
        report = report_of(home, feature)
        assert (report["version"], report["kind"], report["head"], report["branch"], report["clean"]) == \
            (1, "code", feature, "feature", True)
        assert report["tree"] == head(home, "HEAD^{tree}")
        assert [(item["command"], item["exit"], item["tail"]) for item in report["scenarios"]] == \
            [(PASSING, 0, "3 passed")]
        assert isinstance(report["scenarios"][0]["seconds"], float)
        assert (report["smoke"]["command"], report["smoke"]["exit"], report["smoke"]["tail"]) == (SMOKE, 0, "smoke ok")
        assert report["not_tested"] == [LEFT_OUT]
    with and_("the push the gate refused for want of a report now runs"):
        assert says(home, before, "no_report.md", short=feature[:7], branch="feature"), before.reason
        assert quiet(bash(home, "git push"))
    with when("Claude asks for the one-line summary and the tested part"):
        line = pr_tested(home, "show").stdout.strip()
        part = pr_tested(home, "show", "--body").stdout
    with then("the line says the report passes, and the part names each run and what was not tested"):
        assert line == f"{feature[:7]} on feature: code report, 1 run, passes"
        assert part.startswith("### How it was tested\n")
        assert f"`{PASSING}` passed" in part and f"`{SMOKE}` passed" in part and f"- {LEFT_OUT}" in part
    with when("that part goes into a PR body"):
        path = body_file(home, "## What it is\n\nThe save button keeps the draft.\n\n" + part)
        checked = json.loads(home.hubctl("pr-check", path, "--json", check=False).stdout)["findings"]
        created = bash(home, f"gh pr create --title T --body-file {path}")
    with then("pr-check finds the tested part whole, and the gate lets the new PR through"):
        assert not [finding for finding in checked
                    if finding["kind"] == "missing_not_tested" or finding.get("heading") == "How it was tested"]
        assert quiet(created), created.reason


def test_it_refuses_what_it_can_not_vouch_for_and_records_a_failing_scenario_as_failed(home):
    with given("branch feature with an open PR and a new commit"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
    with when("the tree has a change that is not committed, or a new file git does not track yet"):
        with open(os.path.join(home.work, "app.py"), "a") as handle:
            handle.write("# not committed\n")
        changed = run(home)
        home.git("checkout", "--", "app.py")
        with open(os.path.join(home.work, "notes.py"), "w") as handle:
            handle.write("# new\n")
        untracked = run(home)
        os.remove(os.path.join(home.work, "notes.py"))
    with then("each run is refused, names the file, and runs nothing"):
        assert changed.returncode == 1 and "app.py" in changed.stderr, changed.stderr
        assert untracked.returncode == 1 and "notes.py" in untracked.stderr, untracked.stderr
        assert not os.path.exists(os.path.join(reports_folder(home), feature + ".log"))
    with when("a run leaves out what was not tested, the smoke run and its reason, or gives a command that tests "
              "nothing"):
        refused = {
            "no not-tested line": pr_tested(home, "run", "--scenario", PASSING, "--smoke", SMOKE),
            "a blank not-tested line": pr_tested(home, "run", "--scenario", PASSING, "--smoke", SMOKE,
                                              "--not-tested", " "),
            "no smoke and no reason": pr_tested(home, "run", "--scenario", PASSING, "--not-tested", LEFT_OUT),
            "a blank smoke reason": pr_tested(home, "run", "--scenario", PASSING, "--no-smoke", " ",
                                           "--not-tested", LEFT_OUT),
            "no scenario": pr_tested(home, "run", "--smoke", SMOKE, "--not-tested", LEFT_OUT),
            "true": pr_tested(home, "run", "--scenario", "true", "--smoke", SMOKE, "--not-tested", LEFT_OUT),
            "echo": pr_tested(home, "run", "--scenario", "echo 3 passed", "--smoke", SMOKE, "--not-tested", LEFT_OUT),
            "an empty smoke run": pr_tested(home, "run", "--scenario", PASSING, "--smoke", ":", "--not-tested", LEFT_OUT),
        }
    with then("each one is refused and no report is written"):
        for name, result in refused.items():
            assert result.returncode != 0, (name, result.stdout)
        assert not os.path.exists(os.path.join(reports_folder(home), feature + ".json"))
    with when("a scenario fails"):
        failed = pr_tested(home, "run", "--scenario", PASSING, "--scenario", "printf 'boom\\n'; exit 3",
                        "--no-smoke", "the repo has no smoke run", "--not-tested", LEFT_OUT)
    with then("the run says so, the report records the failure, and the gate refuses the push for that scenario"):
        assert failed.returncode == 1
        report = report_of(home, feature)
        assert [item["exit"] for item in report["scenarios"]] == [0, 3]
        assert report["scenarios"][1]["tail"] == "boom"
        assert report["smoke"] == {"none": "the repo has no smoke run"}
        assert "failed" in failed.stdout
        assert says(home, bash(home, "git push"), "failed.md", which="printf 'boom\\n'; exit 3")
        assert pr_tested(home, "show").stdout.strip() == f"{feature[:7]} on feature: code report, 2 runs, " \
                                                      "does not pass (failed)"


def test_a_texts_run_records_its_base_and_refuses_a_code_file(home):
    with given("branch words from main with an open PR, changing only the README and a locale file"):
        with_github(home)
        home.git("checkout", "-q", "-b", "words", "main")
        change(home, name="README.md", message="Reword the README")
        words = change(home, name="locales/de.json", message="Translate the save button")
        fake_gh(home, open={"words": 9})
    with when("Claude records a texts run against main with a check"):
        done = pr_tested(home, "texts", "--base", "main", "--check", "printf 'texts ok\\n'", "--not-tested", LEFT_OUT)
    with then("the report names the base branch and its commit, the changed files and the check"):
        assert done.returncode == 0, done.stderr
        report = report_of(home, words)
        assert report["kind"] == "texts"
        assert report["base"] == {"branch": "main", "sha": head(home, "origin/main")}
        assert sorted(report["files"]) == ["README.md", "locales/de.json"]
        assert [(item["command"], item["exit"]) for item in report["checks"]] == [("printf 'texts ok\\n'", 0)]
    with and_("the gate takes it for the push"):
        assert quiet(bash(home, "git push -u origin words"))
    with when("the branch then changes a code file, and Claude records a texts run again"):
        code = change(home, name="app.py", message="Change the app")
        again = pr_tested(home, "texts", "--base", "origin/main", "--not-tested", LEFT_OUT)
    with then("it is refused, names the code file, and writes no report"):
        assert again.returncode == 1 and "app.py" in again.stderr, again.stderr
        assert not os.path.exists(os.path.join(reports_folder(home), code + ".json"))
    with when("the base does not exist on origin"):
        missing = pr_tested(home, "texts", "--base", "nowhere", "--not-tested", LEFT_OUT)
    with then("it is refused and names it"):
        assert missing.returncode == 1 and "origin/nowhere" in missing.stderr, missing.stderr


def test_a_scenario_that_changes_the_tree_or_commits_while_it_runs_is_caught(home):
    with given("branch feature with an open PR and a new commit"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
    with when("a scenario writes to a tracked file while it runs"):
        edited = pr_tested(home, "run", "--scenario", "printf x >> app.py", "--smoke", SMOKE, "--not-tested", LEFT_OUT)
    with then("the report says the tree was not clean, the run fails, and the gate refuses the push as dirty"):
        assert edited.returncode == 1
        assert report_of(home, feature)["clean"] is False
        assert says(home, bash(home, "git push"), "dirty.md")
    with when("the change is undone and a scenario commits while it runs"):
        home.git("checkout", "--", "app.py")
        os.remove(os.path.join(reports_folder(home), feature + ".json"))
        committed = pr_tested(home, "run", "--scenario", "git commit -q --allow-empty -m Mid", "--smoke", SMOKE,
                           "--not-tested", LEFT_OUT)
    with then("the report for the commit it started on says it was not clean, and the run fails"):
        assert committed.returncode == 1
        assert report_of(home, feature)["clean"] is False
        assert head(home) != feature


def test_reports_land_in_the_common_folder_whole_and_old_ones_are_pruned(home):
    with given("branch feature with a new commit, checked out in a second worktree, and two old reports"):
        with_github(home)
        feature = pushed_branch(home)
        home.git("checkout", "-q", "main")
        side = worktree(home, "side", "feature")
        folder = reports_folder(home)
        os.makedirs(folder, exist_ok=True)
        stale, recent = os.path.join(folder, "a" * 40 + ".json"), os.path.join(folder, "b" * 40 + ".json")
        for path, days in ((stale, 61), (recent, 59)):
            with open(path, "w") as handle:
                handle.write("{}")
            moment = time.time() - days * 86400
            os.utime(path, (moment, moment))
    with when("Claude records runs in the second worktree while a reader keeps reading the report"):
        broken, stop = [], threading.Event()
        target = os.path.join(folder, feature + ".json")

        def read():
            while not stop.is_set():
                try:
                    with open(target) as handle:
                        json.load(handle)
                except FileNotFoundError:
                    pass
                except ValueError as error:
                    broken.append(str(error))

        reader = threading.Thread(target=read)
        reader.start()
        try:
            long_list = [word for number in range(400) for word in ("--not-tested", f"case {number}, left for later")]
            results = [pr_tested(home, "run", "--scenario", PASSING, "--smoke", SMOKE, *long_list, cwd=side)
                       for _ in range(3)]
        finally:
            stop.set()
            reader.join()
    with then("each report lands in the repo's common folder, the main worktree reads it, and no read saw half "
              "a file"):
        assert [result.returncode for result in results] == [0, 0, 0], results[-1].stderr
        assert len(report_of(home, feature)["not_tested"]) == 400
        assert pr_tested(home, "show", feature).stdout.strip() == f"{feature[:7]} on feature: code report, 1 run, passes"
        assert broken == []
        assert sorted(os.listdir(folder)) == sorted(["b" * 40 + ".json", feature + ".json", feature + ".log"])
    with and_("the report older than 60 days is gone, and the newer one stays"):
        assert not os.path.exists(stale) and os.path.exists(recent)


def test_the_pr_skill_gives_the_pr_tested_steps_and_the_step_it_gives_records_the_report_the_push_needs(home):
    with given("branch feature with an open PR, a new commit that has no report, and hub's pr skill built"):
        with_github(home)
        feature = pushed_branch(home)
        fake_gh(home, open={"feature": 7})
        before = bash(home, "git push")
        skill = os.path.join(home.root, ".claude", "skills", "hub-pr", "SKILL.md")
    with when("Claude reads the tested steps in the built skill"):
        with open(skill) as handle:
            text = handle.read()
        found = re.search(r"`(\S+ -I \S+) pr-tested run --scenario '<command>' --smoke '<command>' "
                          r"--not-tested '<what, and why>'`", text)
    with then("they give the run step with hub's step filled in, the texts step and the step that prints the part"):
        assert found, text
        step = found.group(1)
        assert f"`{step} pr-tested texts --base <branch> --check '<command>' --not-tested '<what, and why>'`" in text
        assert f"`{step} pr-tested show --body`" in text
    with when("Claude runs the run step as the skill gives it, with its own commands"):
        done = home.run([*shlex.split(step), "pr-tested", "run", "--scenario", PASSING, "--smoke", SMOKE,
                         "--not-tested", LEFT_OUT], check=False)
        part = home.run([*shlex.split(step), "pr-tested", "show", "--body"], check=False).stdout
    with then("the push the gate refused now runs, and the show step prints the tested part"):
        assert done.returncode == 0, done.stderr
        assert says(home, before, "no_report.md", short=feature[:7], branch="feature"), before.reason
        assert quiet(bash(home, "git push"))
        assert part.startswith("### How it was tested\n") and f"- {LEFT_OUT}" in part
    with when("the user turns the test gate method off"):
        home.hubctl("off", "pr.instructions.test_gate")
    with then("the built skill no longer gives the pr-tested steps"):
        with open(skill) as handle:
            assert "pr-tested" not in handle.read()
