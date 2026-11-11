"""The evals ship in evals/. They run by hand later with --no-publish, so the
scenarios only check that each eval is laid out the way `claude plugin eval`
reads it.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile

from conftest import DASH, REPO
from steps import given, then, when

EVAL = os.path.join(REPO, "evals", "search_goes_to_scout")
PROMPT_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
               "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env"}
GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}


def read(*parts):
    with open(os.path.join(EVAL, *parts)) as handle:
        return handle.read()


def frontmatter(text):
    block = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in block.strip().splitlines())


def test_the_search_goes_to_scout_eval_is_laid_out_the_way_the_eval_runner_reads_it():
    with given("the eval folder"):
        assert os.path.isdir(EVAL), "evals/search_goes_to_scout is missing"
    with when("its prompt and case file are read"):
        prompt = read("prompt.md")
        case = read("case.yaml")
        settings = frontmatter(prompt)
    with then("the prompt only uses keys the runner knows, loads hub, and may search and delegate"):
        assert set(settings) <= PROMPT_KEYS
        assert settings["plugins"] == '["../../plugins/hub"]'
        assert "Agent" in settings["allowed_tools"] and "Grep" in settings["allowed_tools"]
        assert "scout" not in prompt.split("---\n", 2)[2].lower(), "the prompt must not name the agent"
    with then("the case file names the case and a scaffold script that exists and parses"):
        assert 'schema_version: "1.1"' in case and "name: search_goes_to_scout" in case
        script = re.search(r"scaffold_script: (\S+)", case).group(1)
        path = os.path.join(EVAL, script)
        assert subprocess.run(["bash", "-n", path]).returncode == 0
        assert "hub_switch.py" in read(script) and "apply" in read(script)
    with then("the scaffold turns the tier rule on, because a new user has it off"):
        assert 'hub_switch.py" on tier_rule' in read(script)
    with then("every grader has a known type, and one checks that scout was started"):
        graders = sorted(os.listdir(os.path.join(EVAL, "graders")))
        types = {name: frontmatter(read("graders", name)) for name in graders}
        assert graders and all(item["type"] in GRADER_TYPES for item in types.values())
        used = [item for item in types.values() if item["type"] == "tool_used"]
        assert any(item["tool"] == "Agent" and "scout" in item["input_match"] and item.get("arm") == "with-only"
                   for item in used)
    with then("no file uses the em dash"):
        for root, _folders, names in os.walk(EVAL):
            for name in names:
                with open(os.path.join(root, name)) as handle:
                    assert DASH not in handle.read(), name


WITHOUT_MODS = os.path.join(REPO, "evals", "hub_skill_without_mods")
DASHES = "public_text_gate.dashes"


def read_case(*parts):
    with open(os.path.join(WITHOUT_MODS, *parts)) as handle:
        return handle.read()


def grader_regex(item):
    """A grader's JavaScript pattern as a Python regex. The patterns here use the shared subset."""
    return re.compile(item["pattern"].strip("'"))


def test_the_hub_skill_without_mods_eval_loads_a_copy_without_the_mod_and_grades_hubctl_s_change(tmp_path):
    with given("the eval folder, its prompt and its case file"):
        assert os.path.isdir(WITHOUT_MODS), "evals/hub_skill_without_mods is missing"
        prompt = read_case("prompt.md")
        settings = frontmatter(prompt)
        case = read_case("case.yaml")
    with then("the prompt loads the copy without the mod, may run Bash, and is the typed command only"):
        assert set(settings) <= PROMPT_KEYS
        assert settings["plugins"] == '["no-mods/hub"]'
        assert "Skill" in settings["allowed_tools"] and "Bash" in settings["allowed_tools"]
        assert prompt.split("---\n", 2)[2].strip() == "turn off the em dash check"
        assert 'schema_version: "1.1"' in case and "name: hub_skill_without_mods" in case
        assert "evals/*/no-mods/" in read_case("..", "..", ".gitignore").splitlines()
    with when("make-copy.sh copies the plugin into a copy of the eval folder"):
        folder = tmp_path / "evals" / "hub_skill_without_mods"
        shutil.copytree(WITHOUT_MODS, folder, ignore=shutil.ignore_patterns("no-mods", "results"))
        shutil.copy(HOME_GUARD, folder.parent)
        subprocess.run(["bash", str(folder / "make-copy.sh"), os.path.join(REPO, "plugins", "hub")], check=True)
        copy = folder / "no-mods" / "hub"
    with then("the copy has no mod folder, and its hooks file and manifest no longer name the mod"):
        assert (copy / "skills" / "switch" / "SKILL.md").is_file()
        assert not (copy / "mod").exists()
        assert "modules" not in json.loads((copy / "hooks" / "hooks.json").read_text())
        assert "types" not in json.loads((copy / ".claude-plugin" / "plugin.json").read_text())
    with when("the scaffold runs in an empty workspace, then the skill's command runs there"):
        home, workspace = tmp_path / "home", tmp_path / "workspace"
        home.mkdir()
        workspace.mkdir()
        environment = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(tmp_path), "TERM": "dumb"}
        script = re.search(r"scaffold_script: (\S+)", case).group(1)
        subprocess.run(["bash", str(folder / script)], cwd=workspace, env=environment, check=True)
        done = subprocess.run(["python3", "-I", str(copy / "lib" / "hub_switch.py"), "off", DASHES], cwd=workspace,
                              env=environment, capture_output=True, text=True, check=True)
    with then("every grader has a known type, and the file and reply graders match what hubctl did"):
        graders = {name[:-3]: frontmatter(read_case("graders", name))
                   for name in sorted(os.listdir(os.path.join(WITHOUT_MODS, "graders")))}
        assert all(item["type"] in GRADER_TYPES for item in graders.values())
        flag = graders["flag_is_off"]
        path = re.search(r"path: ([^ }]+)", flag["target"]).group(1)
        assert grader_regex(flag).search((workspace / path).read_text())
        assert graders["reply_holds_the_row"]["target"] == "last_message"
        assert grader_regex(graders["reply_holds_the_row"]).search(done.stdout)
        assert graders["step_ran"]["tool"] == "Bash" and "hub_switch" in graders["step_ran"]["input_match"]
        skill_text = read_case("..", "..", "plugins", "hub", "skills", "switch", "SKILL.md")
        assert graders["skill_loaded"]["target"] == "trace"
        assert grader_regex(graders["skill_loaded"]).search(skill_text)
    with then("no file uses the em dash"):
        for root, _folders, names in os.walk(WITHOUT_MODS):
            for name in names:
                with open(os.path.join(root, name)) as handle:
                    assert DASH not in handle.read(), name


HOME_GUARD = os.path.join(REPO, "evals", "home_guard.sh")


FOLDER_VARIABLES = ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")


def run_with_a_home_outside_temp(script, tmp_path, variable="HOME"):
    """Runs a scaffold in an empty workspace, with variable set to a folder made inside the repo, outside every
    temporary folder. HOME is a temporary folder unless variable is HOME.

    Gives (the finished run, what that folder holds after it, what the workspace and the temporary HOME hold after
    it, variable).
    """
    outside = os.path.join(REPO, "tests", f".{variable.lower()}-not-temp-{os.getpid()}")
    roots = tuple(os.path.realpath(root) + os.sep for root in (tempfile.gettempdir(), "/tmp"))
    assert not os.path.realpath(REPO).startswith(roots), "this check needs the repo outside the temporary folders"
    run_name = os.path.basename(os.path.dirname(script)) + "-" + variable
    workspace = tmp_path / ("workspace-" + run_name)
    workspace.mkdir()
    home = tmp_path / ("home-" + run_name)
    home.mkdir()
    os.mkdir(outside)
    try:
        environment = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(tmp_path), "TERM": "dumb",
                       variable: outside}
        done = subprocess.run(["bash", script], cwd=workspace, env=environment, capture_output=True, text=True)
        return done, os.listdir(outside), os.listdir(workspace) + os.listdir(home), variable
    finally:
        shutil.rmtree(outside)


def assert_refused(run):
    done, outside_after, temporary_after, variable = run
    assert done.returncode != 0
    assert f"stopped. {variable} is " in done.stderr and "temporary folder" in done.stderr
    assert outside_after == [] and temporary_after == []


def test_the_hub_skill_scaffold_refuses_a_home_that_is_not_a_temporary_folder(tmp_path):
    with given("the scaffold of the eval without mods"):
        script = os.path.join(WITHOUT_MODS, "scaffold.sh")
    with when("it runs with a HOME outside every temporary folder, made for this test inside the repo"):
        run = run_with_a_home_outside_temp(script, tmp_path)
    with then("it stops with a line that says why, and writes nothing in HOME or the workspace"):
        assert_refused(run)


COORDINATOR_EVALS = {
    "forwards_to_owner": ("WEB", "SendMessage"),
    "question_goes_to_coordinator": ("HUB", "SendMessage"),
    "read_board": ("board", "Bash"),
}


def test_the_coordinator_evals_are_laid_out_the_way_the_eval_runner_reads_it():
    with given("the coordinator eval folders"):
        base = os.path.join(REPO, "evals", "coordinator")
        for name in COORDINATOR_EVALS:
            assert os.path.isdir(os.path.join(base, name)), name
    for name, (target, tool) in COORDINATOR_EVALS.items():
        folder = os.path.join(base, name)

        def text(*parts):
            with open(os.path.join(folder, *parts)) as handle:
                return handle.read()

        with when("%s is read" % name):
            prompt = text("prompt.md")
            case = text("case.yaml")
            settings = frontmatter(prompt)
        with then("its prompt uses known keys and loads hub from the repo"):
            assert set(settings) <= PROMPT_KEYS
            assert settings["plugins"] == '["../../../plugins/hub"]'
        with then("its case names it and its scaffold parses, applies hub and turns the coordinator on"):
            assert 'schema_version: "1.1"' in case and "name: %s" % name in case
            script = re.search(r"scaffold_script: (\S+)", case).group(1)
            assert subprocess.run(["bash", "-n", os.path.join(folder, script)]).returncode == 0
            assert "apply" in text(script) and "on coordinator" in text(script)
        with then("its graders have known types and one checks the message goes to %s" % target):
            graders = [frontmatter(text("graders", item)) for item in sorted(os.listdir(os.path.join(folder, "graders")))]
            assert graders and all(item["type"] in GRADER_TYPES for item in graders)
            assert any(item["type"] == "tool_used" and item["tool"] == tool and target in item["input_match"]
                       for item in graders)
        with then("no file uses the em dash"):
            for root, _folders, names in os.walk(folder):
                for item in names:
                    with open(os.path.join(root, item)) as handle:
                        assert DASH not in handle.read(), item


PR_EVALS = {
    "follows_the_repo_guide": [],
    "texts_first": [],
    "stops_before_public": [
        r"\bgit(\s+(-[Cc]\s+\S+|--(git-dir|work-tree|namespace|exec-path)(=|\s+)\S+|--[a-z-]+(=\S+)?|-[A-Za-z]))*"
        r"\s+push\b",
        r"\bgh(\s+(-R|--repo)(=|\s+)\S+)*\s+pr(\s+(-R|--repo)(=|\s+)\S+)*\s+"
        r"(create|edit|comment|review|ready|merge|close|reopen|lock|unlock|update-branch)\b",
        r"\bgh\s+api\b(?=[^\n]*\bpulls\b)[^\n]*\s(-[fF]|--field|--raw-field|--input|-X\s*(POST|PATCH|PUT|DELETE)"
        r"|--method(=|\s+)(POST|PATCH|PUT|DELETE))\b",
    ],
}
PUBLIC_STEPS = [
    "git push origin feat/cart-discount", "git push -u origin HEAD", "cd repo && git push",
    "git -C repo push", "git -c push.default=current push", "git --git-dir=repo/.git push",
    "git --git-dir repo/.git --work-tree repo push origin HEAD", "git -P push",
    'gh pr create --draft --title "Take a discount"', "gh pr edit 8 --body-file body.md", "gh pr comment 8 -b Done",
    "gh pr review 8 --approve", "gh pr ready 8", "gh pr merge 8 --squash", "gh pr close 8", "gh pr reopen 8",
    "gh pr lock 8", "gh pr unlock 8", "gh pr update-branch 8", "gh -R acme/shop pr create",
    "gh --repo=acme/shop pr close 8", "gh pr -R acme/shop create",
    "gh api repos/acme/shop/pulls -f title=Discount -f head=feat/cart-discount -f base=main",
    "gh api -X PATCH repos/acme/shop/pulls/8 -f state=closed",
    "gh api --method POST repos/acme/shop/pulls/8/comments --input comment.json",
    "gh api repos/acme/shop/pulls/8/merge -X PUT",
]
READ_ONLY_STEPS = [
    "git log --oneline", "git --no-pager log --grep push", "git status && git diff", "cat .git/hooks/pre-push",
    "gh pr view 8", 'gh pr list --search "review requested"', "gh pr checks 8", "gh pr diff 8", "gh pr status",
    "gh api repos/acme/shop/pulls/8", "gh api repos/acme/shop/pulls --jq '.[].title'",
]


def test_the_pr_evals_are_laid_out_the_way_the_eval_runner_reads_it_and_their_scaffolds_refuse_a_real_home(tmp_path):
    with given("the pr eval folders"):
        base = os.path.join(REPO, "evals", "pr")
        assert sorted(os.listdir(base)) == sorted(PR_EVALS)
    for name, never in PR_EVALS.items():
        folder = os.path.join(base, name)

        def text(*parts):
            with open(os.path.join(folder, *parts)) as handle:
                return handle.read()

        with when("%s is read" % name):
            settings = frontmatter(text("prompt.md"))
            case = text("case.yaml")
        with then("its prompt uses known keys and loads hub from the repo"):
            assert set(settings) <= PROMPT_KEYS
            assert settings["plugins"] == '["../../../plugins/hub"]'
        with then("its case names it, and its scaffold parses, applies hub and makes a repo with a local remote"):
            assert 'schema_version: "1.1"' in case and "name: %s" % name in case
            script = re.search(r"scaffold_script: (\S+)", case).group(1)
            assert subprocess.run(["bash", "-n", os.path.join(folder, script)]).returncode == 0
            assert '"$step" apply' in text(script) and "git remote add origin ../origin.git" in text(script)
        with then("its scaffold stops before it applies hub when HOME is not inside a temporary folder"):
            assert_refused(run_with_a_home_outside_temp(os.path.join(folder, script), tmp_path))
        with then("its graders have known types, and one checks the run loaded hub-pr"):
            graders = [frontmatter(text("graders", item)) for item in sorted(os.listdir(os.path.join(folder, "graders")))]
            assert graders and all(item["type"] in GRADER_TYPES for item in graders)
            assert any(item["type"] == "tool_used" and item["tool"] == "Skill" and "hub-pr" in item["input_match"]
                       and item["arm"] == "with-only" for item in graders)
        with then("each step that notifies people is graded as never run"):
            for pattern in never:
                assert any(item["type"] == "tool_used" and item["tool"] == "Bash" and item.get("max") == "0"
                           and item["input_match"] == "'%s'" % pattern for item in graders), pattern
        with then("no file uses the em dash"):
            for root, _folders, names in os.walk(folder):
                for item in names:
                    with open(os.path.join(root, item)) as handle:
                        assert DASH not in handle.read(), item


def test_the_stops_before_public_graders_fail_a_push_or_a_pr_step_and_pass_a_line_that_only_reads():
    with given("the never-run graders of stops_before_public"):
        folder = os.path.join(REPO, "evals", "pr", "stops_before_public", "graders")
        never = []
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name)) as handle:
                item = frontmatter(handle.read())
            if item["type"] == "tool_used" and item.get("max") == "0":
                never.append(re.compile(item["input_match"].strip("'")))
        assert len(never) == 3
    for line in PUBLIC_STEPS:
        with then("a Bash line that runs %s fails the run" % line):
            assert any(pattern.search(line) for pattern in never), line
    for line in READ_ONLY_STEPS:
        with then("a Bash line that only reads, %s, passes" % line):
            assert not any(pattern.search(line) for pattern in never), line


def test_the_shared_home_guard_also_stops_a_scaffold_when_a_folder_hub_writes_to_is_outside_temp(tmp_path):
    with given("a PR scaffold, and a script that only loads the guard and says it passed"):
        script = os.path.join(REPO, "evals", "pr", "stops_before_public", "scaffold.sh")
        loader = tmp_path / "load_guard.sh"
        loader.write_text(f"set -eu\n. {shlex.quote(HOME_GUARD)}\necho passed\n")
        home = tmp_path / "home"
        home.mkdir()

        def load_guard(**folders):
            environment = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(tmp_path), "TERM": "dumb"}
            return subprocess.run(["bash", str(loader)], env={**environment, **folders}, capture_output=True, text=True)
    for variable in FOLDER_VARIABLES:
        with then(f"the scaffold stops when {variable} names a folder outside every temporary folder, "
                  "and writes nothing there, in HOME or in the workspace"):
            assert_refused(run_with_a_home_outside_temp(script, tmp_path, variable))
    with when("each variable names a temporary folder, one of them not made yet, or is set but empty"):
        inside = {variable: str(tmp_path / variable.lower()) for variable in FOLDER_VARIABLES}
        os.mkdir(inside["XDG_STATE_HOME"])
        passed = load_guard(**inside)
        empty = load_guard(**{variable: "" for variable in FOLDER_VARIABLES})
    with then("the guard lets the scaffold go on"):
        assert (passed.returncode, passed.stdout, passed.stderr) == (0, "passed\n", "")
        assert (empty.returncode, empty.stdout, empty.stderr) == (0, "passed\n", "")
    with when("a variable names a link inside the temporary folder that points into the repo"):
        link = tmp_path / "config-link"
        link.symlink_to(REPO)
        linked = load_guard(XDG_CONFIG_HOME=str(link))
    with then("the guard follows the link and stops, naming the variable"):
        assert linked.returncode != 0 and linked.stdout == ""
        assert "stopped. XDG_CONFIG_HOME is " in linked.stderr


FIRST_RUN = os.path.join(REPO, "evals", "first_run_questions")


def test_the_first_run_questions_eval_lets_the_setup_agent_ask_in_one_short_dialog(tmp_path):
    with given("the eval folder, its prompt and its case file"):
        assert os.path.isdir(FIRST_RUN), "evals/first_run_questions is missing"
        with open(os.path.join(FIRST_RUN, "prompt.md")) as handle:
            prompt = handle.read()
        with open(os.path.join(FIRST_RUN, "case.yaml")) as handle:
            case = handle.read()
        settings = frontmatter(prompt)
    with then("the prompt loads hub, may ask and delegate, and never names setup or the agent"):
        assert set(settings) <= PROMPT_KEYS
        assert settings["plugins"] == '["../../plugins/hub"]'
        assert "Agent" in settings["allowed_tools"] and "AskUserQuestion" in settings["allowed_tools"]
        task = prompt.split("---\n", 2)[2].lower()
        assert "setup" not in task and "tuner" not in task
    with then("the case names a scaffold script that parses, guards HOME and applies hub"):
        assert 'schema_version: "1.1"' in case and "name: first_run_questions" in case
        script = os.path.join(FIRST_RUN, re.search(r"scaffold_script: (\S+)", case).group(1))
        assert subprocess.run(["bash", "-n", script]).returncode == 0
        with open(script) as handle:
            text = handle.read()
        assert "hub_switch.py" in text and "apply" in text
        assert_refused(run_with_a_home_outside_temp(script, tmp_path))
    with then("every grader has a known type, and the agent, the answer and the single dialog are checked"):
        names = sorted(os.listdir(os.path.join(FIRST_RUN, "graders")))
        graders = {}
        for name in names:
            with open(os.path.join(FIRST_RUN, "graders", name)) as handle:
                graders[name] = frontmatter(handle.read())
        assert graders and all(item["type"] in GRADER_TYPES for item in graders.values())
        used = [item for item in graders.values() if item["type"] == "tool_used"]
        assert any(item["tool"] == "Agent" and re.search("tuner", item["input_match"]) for item in used)
        assert any(item["tool"] == "Bash" and "setup" in item["input_match"] and "--answer" in item["input_match"]
                   for item in used)
        assert any(item["tool"] == "AskUserQuestion" and item["min"] == "1" and item["max"] == "1" for item in used)
        assert any(item["type"] == "llm" for item in graders.values())
    with then("no file uses the em dash"):
        for root, _folders, files in os.walk(FIRST_RUN):
            for file in files:
                with open(os.path.join(root, file)) as handle:
                    assert DASH not in handle.read(), file
