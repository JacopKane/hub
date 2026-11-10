"""The commit check that CI's privacy job runs on a PR's commits.

Each scenario builds a real git repo in a temporary folder, with no global or
system git config, and runs tests/commit_check.py on a range as a process.
Addresses and paths that the shape scan would flag are put together at run
time, so this file holds none of them.
"""
import json
import os
import re
import subprocess
import sys

from conftest import REPO
from steps import and_, given, then, when

CHECK = os.path.join(REPO, "tests", "commit_check.py")
NOREPLY = "68406+JacopKane@users." + "noreply.github.com"
CONTRIBUTOR = "sam@example.com"
REAL_LOOKING = "zorblat" + "@" + "corp-mail.io"


class Repo:
    def __init__(self, root):
        self.root = os.path.join(root, "repo")
        os.makedirs(self.root)
        # Every git call gets a made-up identity. A merge needs one too, and git on Linux can not guess an email.
        self.env = {"PATH": os.environ["PATH"], "HOME": root, "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C",
                    "GIT_AUTHOR_NAME": "Sam", "GIT_AUTHOR_EMAIL": CONTRIBUTOR,
                    "GIT_COMMITTER_NAME": "Sam", "GIT_COMMITTER_EMAIL": CONTRIBUTOR}
        self.git("init", "-q", "-b", "main")
        self.base = self.commit("Start", {"README.md": "hub\n"})

    def git(self, *args, env=None):
        return subprocess.run(["git", *args], cwd=self.root, env={**self.env, **(env or {})},
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message, files=(), links=(), email=CONTRIBUTOR, offset="+0000", committer=None):
        for path, text in dict(files).items():
            full = os.path.join(self.root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as handle:
                handle.write(text)
        for path, target in dict(links).items():
            full = os.path.join(self.root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            os.symlink(target, full)
        self.git("add", "-A")
        when_made = "1700000000 " + offset
        self.git("commit", "-q", "--allow-empty", "-m", message, env={
            "GIT_AUTHOR_NAME": "Sam", "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": when_made,
            "GIT_COMMITTER_NAME": "Sam", "GIT_COMMITTER_EMAIL": committer or email,
            "GIT_COMMITTER_DATE": when_made})
        return self.git("rev-parse", "HEAD")

    def check(self, spec):
        result = subprocess.run([sys.executable, "-I", CHECK, spec], cwd=self.root, env=self.env,
                                capture_output=True, text=True)
        result.out = result.stdout + result.stderr
        return result


def short(sha):
    return sha[:7]


def test_the_noreply_address_must_carry_utc(tmp_path):
    with given("a PR range with one commit by the noreply address at +0100, after one at +0000"):
        repo = Repo(str(tmp_path))
        utc = repo.commit("Add notes", {"notes.md": "a\n"}, email=NOREPLY)
        local = repo.commit("Add more notes", {"notes.md": "b\n"}, email=NOREPLY, offset="+0100")
    with when("commit_check.py runs on the range"):
        result = repo.check(f"{repo.base}..HEAD")
    with then("it exits 1, names the commit and the offset, and prints no address"):
        assert result.returncode == 1
        assert f"{short(local)}: author_offset: +0100" in result.stdout
        assert f"{short(local)}: committer_offset: +0100" in result.stdout
        assert short(utc) not in result.stdout
        assert "@" not in result.out
    with and_("the commit at +0000 passes on its own"):
        assert repo.check(f"{utc}^!").returncode == 0


def test_a_real_address_in_a_trailer_fails(tmp_path):
    with given("a contributor commit at +0200 with made-up and noreply trailers, and one whose trailer "
               "holds a real-looking address"):
        repo = Repo(str(tmp_path))
        clean = repo.commit(f"Fix the band\n\nCo-authored-by: Alex <alex@example.org>\nCo-authored-by: K <{NOREPLY}>",
                            {"a.md": "a\n"}, offset="+0200")
        trailer = repo.commit(f"Fix the band again\n\nSigned-off-by: Zorblat <{REAL_LOOKING}>", {"a.md": "b\n"})
    with when("commit_check.py runs on each"):
        first, second = repo.check(f"{clean}^!"), repo.check(f"{trailer}^!")
    with then("the first passes, and the second fails and names the line, with no address"):
        assert first.returncode == 0, first.out
        assert second.returncode == 1
        assert f"{short(trailer)}: message line 3: email" in second.stdout
        assert "zorblat" not in second.out and "@" not in second.out


def test_a_message_with_a_home_path_fails_by_class(tmp_path):
    with given("a commit whose message holds a home path"):
        repo = Repo(str(tmp_path))
        planted = "/" + "Users/" + "zorblat/notes.md"
        bad = repo.commit(f"Move the notes\n\nThey were in {planted}", {"a.md": "a\n"})
    with when("commit_check.py runs"):
        result = repo.check(f"{repo.base}..HEAD")
    with then("it fails, names the commit, line and class, and prints no value"):
        assert result.returncode == 1
        assert f"{short(bad)}: message line 3: home_path" in result.stdout
        assert "zorblat" not in result.out


def test_purity_is_checked_per_commit(tmp_path):
    with given("a pure join commit next to a code commit"):
        repo = Repo(str(tmp_path))
        repo.commit("Add a key", {".hub/maintainers": "ssh-ed25519 AAAA\n"})
        repo.commit("Fix a hook", {"plugins/hub/hooks/hooks.json": "{}\n"})
        pair_tip = repo.git("rev-parse", "HEAD")
    with and_("one commit that changes .hub/maintainers and a hook"):
        mixed = repo.commit("Add a key and fix a hook", {".hub/maintainers": "ssh-ed25519 AAAA\nssh-ed25519 BBBB\n",
                                                          "plugins/hub/hooks/hooks.json": "{\"a\": 1}\n"})
    with when("commit_check.py runs on the pair, then on the mixed commit"):
        pair, alone = repo.check(f"{repo.base}..{pair_tip}"), repo.check(f"{mixed}^!")
    with then("the pair passes, and the mixed commit fails and names both files"):
        assert pair.returncode == 0, pair.out
        assert alone.returncode == 1
        assert f"{short(mixed)}: mixed_trust: .hub/maintainers, plugins/hub/hooks/hooks.json" in alone.stdout


def test_a_checker_commit_may_change_only_the_checker_and_the_version(tmp_path):
    version = "plugins/hub/.claude-plugin/plugin.json"
    checker = "plugins/hub/lib/hub/trust.py"
    with given("a tree whose .hub/checker-files names the checker's file"):
        repo = Repo(str(tmp_path))
        repo.commit("Add the checker", {".hub/checker-files": f"# the update checker\n{checker}\n",
                                        checker: "x = 1\n", version: "{}\n"})
    with and_("a checker commit with the version raise, one that also changes the README, "
              "and one that also adds a key"):
        pure = repo.commit("Check one more field", {checker: "x = 2\n", version: "{\"v\": 2}\n"})
        with_readme = repo.commit("Check and document", {checker: "x = 3\n", "README.md": "hub 2\n"})
        with_key = repo.commit("Check and add a key", {checker: "x = 4\n", ".hub/maintainers": "ssh-ed25519 CCCC\n"})
    with when("commit_check.py runs on each"):
        results = [repo.check(f"{sha}^!") for sha in (pure, with_readme, with_key)]
    with then("only the pure one passes, and each other names the file that does not belong"):
        assert results[0].returncode == 0, results[0].out
        assert f"{short(with_readme)}: mixed_checker: README.md" in results[1].stdout
        assert f"{short(with_key)}: mixed_trust: .hub/maintainers, {checker}" in results[2].stdout
        assert results[1].returncode == results[2].returncode == 1


def test_a_symlink_in_the_tree_fails(tmp_path):
    with given("a commit that adds one symlink"):
        repo = Repo(str(tmp_path))
        linked = repo.commit("Add a link", {"docs/a.md": "a\n"}, links={"docs/b.md": "a.md"})
    with when("commit_check.py runs"):
        result = repo.check(f"{repo.base}..HEAD")
    with then("it exits 1 and names the path"):
        assert result.returncode == 1
        assert f"{short(linked)}: symlink: docs/b.md" in result.stdout


def merge(repo, branch, files=(), links=()):
    """Merge the branch into the checked-out one, adding the files and links in the merge commit itself."""
    repo.git("merge", "-q", "--no-ff", "--no-commit", branch)
    for path, text in dict(files).items():
        with open(os.path.join(repo.root, path), "w") as handle:
            handle.write(text)
    for path, target in dict(links).items():
        os.symlink(target, os.path.join(repo.root, path))
    return repo.commit(f"Merge {branch}")


def test_a_merge_commit_is_checked_on_the_changes_it_makes_itself(tmp_path):
    with given("a PR branch that merged main after main took a pure trust commit"):
        repo = Repo(str(tmp_path))
        repo.git("switch", "-q", "-c", "pr")
        repo.commit("Fix code", {"code.py": "x = 1\n"})
        repo.git("switch", "-q", "main")
        repo.commit("Add a key", {".hub/maintainers": "ssh-ed25519 AAAA\n"})
        main_tip = repo.git("rev-parse", "HEAD")
        repo.git("switch", "-q", "pr")
        clean = merge(repo, "main")
    with and_("a second merge that adds a symlink and a revoked key in the merge commit itself"):
        repo.git("switch", "-q", "-c", "side", main_tip)
        repo.commit("Side notes", {"notes.md": "n\n"})
        repo.git("switch", "-q", "pr")
        planted = merge(repo, "side", {".hub/revoked": "ssh-ed25519 AAAA\n", "code.py": "x = 2\n"},
                        links={"linked.md": "README.md"})
    with when("commit_check.py runs on the first merge, then on the PR range from main"):
        first, whole = repo.check(f"{main_tip}..{clean}"), repo.check(f"{main_tip}..pr")
    with then("the clean merge passes, and the planted one is refused for both of its own changes"):
        assert first.returncode == 0, first.out
        assert whole.returncode == 1
        assert f"{short(planted)}: mixed_trust: .hub/revoked, code.py, linked.md" in whole.stdout
        assert f"{short(planted)}: symlink: linked.md" in whole.stdout
        assert short(clean) not in whole.stdout


def test_a_pr_is_also_checked_as_the_one_commit_a_squash_makes(tmp_path):
    with given("a PR with a pure trust commit and then a code commit, and a PR with two trust commits"):
        repo = Repo(str(tmp_path))
        repo.git("switch", "-q", "-c", "mixed")
        repo.commit("Add a key", {".hub/maintainers": "ssh-ed25519 AAAA\n"})
        repo.commit("Fix code", {"code.py": "x = 1\n"})
        repo.git("switch", "-q", "-c", "trust", repo.base)
        repo.commit("Add a key", {".hub/maintainers": "ssh-ed25519 AAAA\n"})
        repo.commit("Revoke a key", {".hub/revoked": "ssh-ed25519 BBBB\n"})
    with when("commit_check.py runs on each PR per commit, then with --squash"):
        per_commit = repo.check(f"{repo.base}..mixed")
        squashed = subprocess.run([sys.executable, "-I", CHECK, "--squash", f"{repo.base}..mixed"], cwd=repo.root,
                                  env=repo.env, capture_output=True, text=True)
        trust = subprocess.run([sys.executable, "-I", CHECK, "--squash", f"{repo.base}..trust"], cwd=repo.root,
                               env=repo.env, capture_output=True, text=True)
        single = subprocess.run([sys.executable, "-I", CHECK, "--squash", "trust^!"], cwd=repo.root,
                                env=repo.env, capture_output=True, text=True)
    with then("each commit of the mixed PR passes, but its squash is refused and names both files"):
        assert per_commit.returncode == 0, per_commit.stdout
        assert squashed.returncode == 1
        assert squashed.stdout.splitlines() == ["squash: mixed_trust: .hub/maintainers, code.py"]
    with and_("the trust-only PR passes as one commit, and --squash needs a range of the form BASE..HEAD"):
        assert trust.returncode == 0, trust.stdout
        assert single.returncode == 2


def test_a_clean_range_passes_and_a_bad_range_is_a_usage_error(tmp_path):
    with given("two clean contributor commits"):
        repo = Repo(str(tmp_path))
        repo.commit("One", {"a.md": "a\n"}, offset="-0500")
        repo.commit("Two", {"b.md": "b\n"})
    with when("commit_check.py runs on the range, on a range that does not exist, and with no range"):
        clean = repo.check(f"{repo.base}..HEAD")
        missing = repo.check("nothere..HEAD")
        bare = subprocess.run([sys.executable, "-I", CHECK], cwd=repo.root, env=repo.env, capture_output=True)
    with then("the clean range passes, and the other two exit 2"):
        assert clean.returncode == 0, clean.out
        assert missing.returncode == 2 and bare.returncode == 2


def privacy_steps():
    """The shell of each run step of the privacy job in ci.yml, in order."""
    with open(os.path.join(REPO, ".github", "workflows", "ci.yml")) as handle:
        job = handle.read().split("\n  privacy:\n", 1)[1]
    job = re.split(r"\n  [A-Za-z0-9_-]+:\n", job, maxsplit=1)[0]
    steps = []
    lines = job.splitlines()
    for index, line in enumerate(lines):
        found = re.match(r"^( +)run: (.*)$", line)
        if not found:
            continue
        if found.group(2) != "|":
            steps.append(found.group(2))
            continue
        body = []
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= len(found.group(1)):
                break
            body.append(following[len(found.group(1)) + 2:])
        steps.append("\n".join(body))
    return steps


def run_privacy_job(repo, runner, event, **values):
    """Run the privacy job's steps the way the runner does, each in bash -e, with GITHUB_ENV carried over."""
    env_file = os.path.join(runner, "github_env")
    open(env_file, "w").close()
    env = {**repo.env, "GITHUB_ENV": env_file, "RUNNER_TEMP": runner, "GITHUB_EVENT_NAME": event,
           "PR_BASE": "", "PR_HEAD": "", "PUSH_BEFORE": "", "PUSH_AFTER": "", **values}
    output = ""
    for step in privacy_steps():
        with open(env_file) as handle:
            env.update(line.split("=", 1) for line in handle.read().splitlines() if "=" in line)
        result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step], cwd=repo.root, env=env,
                                capture_output=True, text=True)
        output += result.stdout + result.stderr
        if result.returncode != 0:
            return result.returncode, output
    return 0, output


def test_the_privacy_job_checks_a_pr_as_its_squash_and_a_push_per_commit(tmp_path):
    runner = str(tmp_path / "runner")
    os.makedirs(runner)
    with given("main with the checks, and a PR with a pure trust commit and then a code commit"):
        repo = Repo(str(tmp_path))
        start = repo.commit("Add the checks", {f"tests/{name}": open(os.path.join(REPO, "tests", name)).read()
                                               for name in ("commit_check.py", "scrub_scan.py")})
        repo.git("switch", "-q", "-c", "pr")
        repo.commit("Add a key", {".hub/maintainers": "ssh-ed25519 AAAA\n"})
        head = repo.commit("Fix code", {"code.py": "x = 1\n"})
    with when("the privacy job runs on the PR event"):
        code, output = run_privacy_job(repo, runner, "pull_request", PR_BASE=start, PR_HEAD=head)
    with then("it fails on the squash and names both files"):
        assert code == 1, output
        assert "squash: mixed_trust: .hub/maintainers, code.py" in output
    with when("the PR still reaches main as one squashed commit, and the push runs the job"):
        repo.git("switch", "-q", "main")
        repo.git("merge", "-q", "--squash", "pr")
        squashed = repo.commit("Add a key and fix code")
        pushed = run_privacy_job(repo, runner, "push", PUSH_BEFORE=start, PUSH_AFTER=squashed)
    with then("the push job refuses that commit as well"):
        assert pushed[0] == 1, pushed[1]
        assert f"{short(squashed)}: mixed_trust: .hub/maintainers, code.py" in pushed[1]


def test_the_privacy_job_runs_the_checks_from_the_base_tree(tmp_path):
    runner = str(tmp_path / "runner")
    os.makedirs(runner)
    always_pass = "import sys\nsys.exit(0)\n"
    with given("main with the checks, and a PR that turns both into scripts that always pass"):
        repo = Repo(str(tmp_path))
        start = repo.commit("Add the checks", {f"tests/{name}": open(os.path.join(REPO, "tests", name)).read()
                                               for name in ("commit_check.py", "scrub_scan.py")})
        repo.git("switch", "-q", "-c", "pr")
        repo.commit("Loosen the checks", {"tests/commit_check.py": always_pass, "tests/scrub_scan.py": always_pass})
    with and_("a commit by the noreply address at +0100 whose file holds a home path"):
        planted = "/" + "Users/" + "zorblat/notes.md"
        head = repo.commit("Add notes", {"notes.md": f"see {planted}\n"}, email=NOREPLY, offset="+0100")
    with when("the privacy job runs on the PR event"):
        code, output = run_privacy_job(repo, runner, "pull_request", PR_BASE=start, PR_HEAD=head)
    with then("the checks from main find the home path, and the job fails without printing it"):
        assert code == 1, output
        assert "notes.md:1: home_path" in output and "zorblat" not in output
    with when("the shape scan is clean but the offset is not"):
        repo.commit("Drop the notes path", {"notes.md": "see the notes\n"}, email=NOREPLY)
        code, output = run_privacy_job(repo, runner, "pull_request", PR_BASE=start, PR_HEAD="pr")
    with then("the commit check from main names the offset"):
        assert code == 1 and f"{short(head)}: author_offset: +0100" in output, output
    with when("neither the base nor the PR has the checks"):
        bare = Repo(str(tmp_path / "bare"))
        bare.git("switch", "-q", "-c", "pr")
        bare_head = bare.commit("Add notes", {"notes.md": "notes\n"})
        code, output = run_privacy_job(bare, runner, "pull_request", PR_BASE=bare.base, PR_HEAD=bare_head)
    with then("the job fails and says the base has no checks"):
        assert code != 0 and "has no tests/" in output, output
        assert "no privacy checks yet" not in output


SKIPPED = "base has no privacy checks yet, skipped"


def test_the_job_skips_only_the_checks_the_base_lacks_and_never_runs_the_prs_copy(tmp_path):
    runner = str(tmp_path / "runner")
    os.makedirs(runner)
    marker = "the PR's own copy ran"
    own_copy = f"import sys\nprint({marker!r})\nsys.exit(1)\n"
    with given("main with no checks, and the PR that adds both, as scripts that print a marker and fail"):
        repo = Repo(str(tmp_path))
        repo.git("switch", "-q", "-c", "pr")
        head = repo.commit("Add the checks", {"tests/commit_check.py": own_copy, "tests/scrub_scan.py": own_copy})
    with when("the privacy job runs on the PR event"):
        code, output = run_privacy_job(repo, runner, "pull_request", PR_BASE=repo.base, PR_HEAD=head)
    with then("it passes, gives the notice for each check, and runs neither copy from the PR"):
        assert code == 0, output
        assert f"{SKIPPED} (tests/scrub_scan.py)" in output
        assert f"{SKIPPED} (tests/commit_check.py)" in output
        assert marker not in output
    with when("the PR is squashed onto main and the push runs the job"):
        repo.git("switch", "-q", "main")
        repo.git("merge", "-q", "--squash", "pr")
        squashed = repo.commit("Add the checks")
        pushed = run_privacy_job(repo, runner, "push", PUSH_BEFORE=repo.base, PUSH_AFTER=squashed)
    with then("the push run passes with the same notice"):
        assert pushed[0] == 0 and SKIPPED in pushed[1] and marker not in pushed[1], pushed[1]
    with given("a second main that already has the shape scan but not the commit check"):
        partial = Repo(str(tmp_path / "partial"))
        start = partial.commit("Add the shape scan",
                               {"tests/scrub_scan.py": open(os.path.join(REPO, "tests", "scrub_scan.py")).read()})
        partial.git("switch", "-q", "-c", "pr")
        planted = "/" + "Users/" + "zorblat/notes.md"
        partial_head = partial.commit("Add the commit check", {
            "tests/commit_check.py": own_copy, "notes.md": f"see {planted}\n"}, email=NOREPLY, offset="+0100")
    with when("the privacy job runs on the PR that adds the commit check and a home path"):
        code, output = run_privacy_job(partial, runner, "pull_request", PR_BASE=start, PR_HEAD=partial_head)
    with then("the shape scan from main still runs and finds the path, and the PR's commit check never runs"):
        assert code == 1, output
        assert "notes.md:1: home_path" in output and "zorblat" not in output
        assert marker not in output
    with when("the path is gone"):
        clean_head = partial.commit("Drop the notes path", {"notes.md": "see the notes\n"}, email=NOREPLY)
        code, output = run_privacy_job(partial, runner, "pull_request", PR_BASE=start, PR_HEAD=clean_head)
    with then("the job passes, skipping only the commit check with the notice"):
        assert code == 0, output
        assert f"{SKIPPED} (tests/commit_check.py)" in output
        assert f"{SKIPPED} (tests/scrub_scan.py)" not in output and marker not in output


def test_the_privacy_job_fails_when_the_base_commit_is_not_in_the_clone(tmp_path):
    runner = str(tmp_path / "runner")
    os.makedirs(runner)
    with given("a PR that adds both checks, and a base commit that exists only in another repo"):
        repo = Repo(str(tmp_path))
        repo.git("switch", "-q", "-c", "pr")
        head = repo.commit("Add the checks", {f"tests/{name}": open(os.path.join(REPO, "tests", name)).read()
                                              for name in ("commit_check.py", "scrub_scan.py")})
        elsewhere = Repo(str(tmp_path / "elsewhere")).commit("Only elsewhere", {"other.md": "other\n"})
    with when("the privacy job runs on the PR event with that base"):
        code, output = run_privacy_job(repo, runner, "pull_request", PR_BASE=elsewhere, PR_HEAD=head)
    with then("it fails, says the base is not in the clone, and skips nothing"):
        assert code == 1, output
        assert "is not in the clone" in output, output
        assert SKIPPED not in output


def test_the_privacy_job_scans_every_changed_file_as_a_path(tmp_path):
    runner = str(tmp_path / "runner")
    os.makedirs(runner)
    planted = "/" + "Users/" + "zorblat/notes.md"
    with given("main with the checks, and a PR whose one changed file has the name of a shape scan option"):
        repo = Repo(str(tmp_path))
        start = repo.commit("Add the checks", {f"tests/{name}": open(os.path.join(REPO, "tests", name)).read()
                                               for name in ("commit_check.py", "scrub_scan.py")})
        repo.git("switch", "-q", "-c", "pr")
        head = repo.commit("Add notes", {"--" + "staged": f"see {planted}\n"})
    with when("the privacy job runs on the PR event"):
        code, output = run_privacy_job(repo, runner, "pull_request", PR_BASE=start, PR_HEAD=head)
    with then("the shape scan reads that file and finds the home path, without printing it"):
        assert code == 1, output
        assert "--staged:1: home_path" in output and "zorblat" not in output


def ci_jobs():
    """The check name GitHub reports for each job.

    That is the job's name key when it has one, and its id otherwise. A matrix job reports one check per entry
    under other names, so it counts as unknown.
    """
    with open(os.path.join(REPO, ".github", "workflows", "ci.yml")) as handle:
        jobs = re.split(r"^  ([A-Za-z0-9_-]+):\n", handle.read().split("\njobs:\n", 1)[1], flags=re.M)[1:]
    checks = []
    for job_id, body in zip(jobs[::2], jobs[1::2]):
        named = re.search(r"^    name: *(.+?) *$", body, re.M)
        checks.append("<matrix>" if re.search(r"^    strategy:", body, re.M) else
                      named.group(1).strip("\"'") if named else job_id)
    return sorted(checks)


def ruleset(name):
    with open(os.path.join(REPO, ".github", "rulesets", name + ".json")) as handle:
        return json.load(handle)


def rules_by_type(rules):
    return {rule["type"]: rule.get("parameters") for rule in rules["rules"]}


def test_the_rulesets_hold_main_stable_and_release_tags():
    with given("the three ruleset files and the CI workflow"):
        main, stable, tags = ruleset("main"), ruleset("stable"), ruleset("tags")
    with then("main needs a PR with no approval, squash only, and every CI job as a strict check"):
        assert main["target"] == "branch" and main["conditions"]["ref_name"]["include"] == ["refs/heads/main"]
        assert main["bypass_actors"] == []
        rules = rules_by_type(main)
        assert {"deletion", "non_fast_forward"} <= rules.keys()
        assert rules["pull_request"]["required_approving_review_count"] == 0
        assert rules["pull_request"]["allowed_merge_methods"] == ["squash"]
        checks = rules["required_status_checks"]
        assert checks["strict_required_status_checks_policy"] is True
        assert sorted(check["context"] for check in checks["required_status_checks"]) == ci_jobs()
        assert ci_jobs() == ["linux", "macos", "privacy"]
    with and_("stable and the v tags can be made, moved or deleted only by a repository admin"):
        for rules, target, ref in ((stable, "branch", "refs/heads/stable"), (tags, "tag", "refs/tags/v*")):
            assert rules["target"] == target and rules["conditions"]["ref_name"]["include"] == [ref]
            assert rules["bypass_actors"] == [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
            assert {"creation", "update", "deletion", "non_fast_forward"} == rules_by_type(rules).keys()
        for rules in (main, stable, tags):
            assert rules["enforcement"] == "active"
            assert "required_signatures" not in rules_by_type(rules)
