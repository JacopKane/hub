"""The evals ship in evals/. They run by hand later with --no-publish, so the
scenarios only check that each eval is laid out the way `claude plugin eval`
reads it.
"""
import json
import os
import re
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
        assert "hubctl" in read(script) and "apply" in read(script)
    with then("the scaffold turns the tier rule on, because a new user has it off"):
        assert "hubctl\" on tier_rule" in read(script)
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
        assert prompt.split("---\n", 2)[2].strip() == f"/hubctl off {DASHES}"
        assert 'schema_version: "1.1"' in case and "name: hub_skill_without_mods" in case
        assert "evals/*/no-mods/" in read_case("..", "..", ".gitignore").splitlines()
    with when("make-copy.sh copies the plugin into a copy of the eval folder"):
        folder = tmp_path / "eval"
        shutil.copytree(WITHOUT_MODS, folder, ignore=shutil.ignore_patterns("no-mods", "results"))
        subprocess.run(["bash", str(folder / "make-copy.sh"), os.path.join(REPO, "plugins", "hub")], check=True)
        copy = folder / "no-mods" / "hub"
    with then("the copy has no mod folder, and its hooks file and manifest no longer name the mod"):
        assert (copy / "skills" / "hubctl" / "SKILL.md").is_file()
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
        done = subprocess.run(["python3", "-I", str(copy / "bin" / "hubctl"), "off", DASHES], cwd=workspace,
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
        assert graders["hubctl_ran"]["tool"] == "Bash" and "hubctl" in graders["hubctl_ran"]["input_match"]
        skill_text = read_case("..", "..", "plugins", "hub", "skills", "hubctl", "SKILL.md")
        assert graders["skill_loaded"]["target"] == "trace"
        assert grader_regex(graders["skill_loaded"]).search(skill_text)
    with then("no file uses the em dash"):
        for root, _folders, names in os.walk(WITHOUT_MODS):
            for name in names:
                with open(os.path.join(root, name)) as handle:
                    assert DASH not in handle.read(), name


def test_the_hub_skill_scaffold_refuses_a_home_that_is_not_a_temporary_folder(tmp_path):
    with given("a HOME outside every temporary folder, made for this test inside the repo"):
        home = os.path.join(REPO, "tests", f".home-not-temp-{os.getpid()}")
        roots = tuple(os.path.realpath(root) + os.sep for root in (tempfile.gettempdir(), "/tmp"))
        assert not os.path.realpath(REPO).startswith(roots), "this check needs the repo outside the temporary folders"
        os.mkdir(home)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
    try:
        with when("the scaffold runs with that HOME"):
            environment = {"PATH": os.environ["PATH"], "HOME": home, "TMPDIR": str(tmp_path), "TERM": "dumb"}
            done = subprocess.run(["bash", os.path.join(WITHOUT_MODS, "scaffold.sh")], cwd=workspace,
                                  env=environment, capture_output=True, text=True)
        with then("it stops with a line that says why, and writes nothing in HOME or the workspace"):
            assert done.returncode != 0
            assert "HOME" in done.stderr and "temporary folder" in done.stderr
            assert os.listdir(home) == [] and os.listdir(workspace) == []
    finally:
        shutil.rmtree(home)


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


FIRST_RUN = os.path.join(REPO, "evals", "first_run_questions")


def test_the_first_run_questions_eval_lets_the_setup_agent_ask_in_one_short_dialog():
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
        assert "hubctl" in text and "apply" in text and "temporary folder" in text
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
