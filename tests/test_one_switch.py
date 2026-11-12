"""Slice 1: one switch, end to end.

Every scenario runs the real engine, hubctl and the dispatcher as processes
in a temporary HOME. Scenarios that feed hook input run once per tool.
"""
import json
import os
import resource
import statistics
import subprocess
import sys

from conftest import DASH, FIXTURES, PLUGIN, REPO, Home, commit, load_fixture
from steps import and_, given, then, when

GATE = "public_text_gate"


def gate_text(home, name):
    with open(os.path.join(home.plugin, "parts", GATE, "text", name)) as handle:
        return handle.read().strip()


def warned(home):
    return sorted(line["flag"] for line in home.signals() if line["signal"] == "warned")


def test_a_user_turns_off_one_check_of_the_gate_and_only_that_check_stops(home, tool):
    message = f"Add notes {DASH} see /home/sam/notes.md"
    with given("hub is installed in a fresh HOME, with the gate at its default, warn"):
        home.install()
    with when("Claude sends git commit with only an em dash in the message"):
        dash_by_default = home.hook(tool, "PreToolUse", commit(f"Add notes {DASH} see the notes"))
    with then("it runs, because the em dash check is off until the user turns it on"):
        assert dash_by_default.ran and dash_by_default.output == {}
        assert home.show()["flags"][f"{GATE}.dashes"]["value"] == "off"
    with when("the user runs hubctl on public_text_gate.dashes"):
        home.hubctl("on", f"{GATE}.dashes")
    with and_("Claude sends git commit with an em dash and a home path in the message"):
        first = home.hook(tool, "PreToolUse", commit(message))
    with then("the gate refuses it, naming both findings, with the hint"):
        assert first.refused
        assert first.findings == ["private_paths", "dashes"]
        assert gate_text(home, "hint.md") in first.reason
        assert gate_text(home, "after_warning.md") in first.reason
    with when("the same command comes again"):
        second = home.hook(tool, "PreToolUse", commit(message))
    with then("it is refused again for the home path only, because a private item is refused every time"):
        assert second.refused and second.findings == ["private_paths"]
    with when("the home path is taken out and the command comes twice"):
        dash_only = [home.hook(tool, "PreToolUse", commit(f"Add notes {DASH} see the notes")) for _ in range(2)]
    with then("the dash is refused once and then runs, and the signal log holds no text"):
        assert [result.refused for result in dash_only] == [True, False]
        assert warned(home) == [f"{GATE}.dashes", f"{GATE}.dashes"]
        assert [line["signal"] for line in home.signals() if line["flag"] == f"{GATE}.private_paths"] == ["blocked"] * 2
        with open(os.path.join(home.state, "signals.jsonl")) as handle:
            raw = handle.read()
        assert "notes" not in raw and "sam" not in raw and DASH not in raw
        assert all(set(line) == {"flag", "event", "signal", "time"} for line in home.signals())
    with when("the user runs hubctl off public_text_gate.dashes"):
        home.hubctl("off", f"{GATE}.dashes")
    with and_("a new commit message holds both again"):
        third = home.hook(tool, "PreToolUse", commit(f"Move notes {DASH} see /home/sam/todo.md"))
    with then("the gate names only the home path"):
        assert third.refused and third.findings == ["private_paths"]
    with and_("the user file holds one entry, public_text_gate.dashes off by user"):
        assert home.settings()["flags"] == {f"{GATE}.dashes": {"by": "user", "value": "off"}}
    with when("the user runs hubctl undo"):
        home.hubctl("undo")
        fourth = home.hook(tool, "PreToolUse", commit(f"Tidy {DASH} done"))
    with then("the dash check runs again, pinned on"):
        assert fourth.refused and fourth.findings == ["dashes"]
        assert home.settings()["flags"][f"{GATE}.dashes"] == {"by": "user", "value": "on"}
        assert [line["door"] for line in home.history()] == ["hubctl", "hubctl", "hubctl"]


def test_a_parts_mode_caps_its_children_and_turning_it_off_keeps_their_values(home, tool):
    with given("public_text_gate.dashes is set to block while the part is at warn"):
        home.install()
        home.hubctl("set", f"{GATE}.dashes", "block")
    with when("a message with a dash comes twice"):
        results = [home.hook(tool, "PreToolUse", commit(f"One {DASH} two")) for _ in range(2)]
    with then("it is refused once and then runs"):
        assert [result.refused for result in results] == [True, False]
    with when("the part is set to block"):
        home.hubctl("set", GATE, "block")
        results = [home.hook(tool, "PreToolUse", commit(f"Three {DASH} four")) for _ in range(3)]
    with then("the same message is refused every time"):
        assert [result.refused for result in results] == [True, True, True]
        assert gate_text(home, "after_warning.md") not in results[0].reason
    with when("the part is set to off and then back to warn"):
        home.hubctl_from_mod("off", GATE)
        while_off = home.hook(tool, "PreToolUse", commit(f"Five {DASH} six"))
        home.hubctl("set", GATE, "warn")
    with then("nothing runs while it is off, and afterwards dashes is still block"):
        assert while_off.ran and while_off.output == {}
        assert home.settings()["flags"][f"{GATE}.dashes"]["value"] == "block"
        assert home.show()["flags"][f"{GATE}.dashes"]["value"] == "block"


def test_the_highest_scope_wins_and_the_local_file_stays_out_of_git(home, tool):
    with given("a repo whose user file says warn, project file says block and local file says off"):
        home.install()
        home.hubctl("set", GATE, "warn")
        home.hubctl("set", GATE, "block", "--scope", "project")
        home.hubctl_from_mod("set", GATE, "off", "--scope", "local")
    with when("a commit message with a home path comes from that repo"):
        inside = home.hook(tool, "PreToolUse", commit("See /home/sam/plan.md"))
    with then("it runs, because the local scope wins"):
        assert inside.ran and inside.output == {}
    with and_("git status does not list .hub/hub.local.json"):
        status = home.git("status", "--porcelain", "--untracked-files=all").stdout
        assert ".hub/hub.json" in status and "hub.local.json" not in status
    with and_("the same commit from a folder outside the repo is refused by the user scope"):
        outside = os.path.join(home.root, "elsewhere")
        os.makedirs(outside)
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/plan.md"), cwd=outside).refused


def test_a_value_equal_to_the_default_is_dropped_unless_pinned(home, tool):
    with given("hub is installed"):
        home.install()
    with when("setup writes warn for the gate"):
        home.set_by("setup", GATE, "warn")
    with then("setup's entry is dropped"):
        assert home.settings()["flags"] == {}
    with when("the user then types hubctl set public_text_gate warn"):
        home.hubctl("set", GATE, "warn")
    with then("the user's entry stays, pinned"):
        assert home.settings()["flags"] == {GATE: {"by": "user", "value": "warn"}}
    with when("an update moves the gate's default to block and the dash check's default to on"):
        home.edit_manifest(GATE, lambda data: data.update(default="block"))
        home.edit_manifest(GATE, lambda data: next(c for c in data["children"] if c["id"] == "dashes").update(default="on"))
    with then("the gate stays at warn, the unpinned dash check follows its new default, and a path is refused every time"):
        assert home.show()["flags"][GATE]["value"] == "warn"
        assert home.hook(tool, "PreToolUse", commit(f"Only {DASH} here")).refused
        paths = [home.hook(tool, "PreToolUse", commit("See /home/sam/a.md")) for _ in range(2)]
        assert [result.refused for result in paths] == [True, True]


def test_a_part_that_is_new_in_an_update_arrives_as_an_offer(home):
    with given("apply has seen every part of a first plugin copy"):
        home.install()
        assert home.state_file("offers.json") == {"offers": []}
    with when("a second copy adds a part whose default is on, and a gate behavior whose default is on"):
        home.write_json(os.path.join(home.plugin, "parts", "demo_extra", "part.json"), {
            "id": "demo_extra", "family": "tools", "kind": "extra", "default": "on",
            "runs_in": ["claude_code", "cursor"], "does": "A part that arrives in an update",
        })
        home.edit_manifest(GATE, lambda data: data["children"].append(
            {"id": "new_check", "kind": "behavior", "default": "on", "does": "A check that arrives in an update"}))
        home.hubctl("apply")
    with then("the new part stays off and is in offers.json"):
        flags = home.show("--all")["flags"]
        assert flags["demo_extra"]["value"] == "off" and not flags["demo_extra"]["runs"]
        assert [offer["id"] for offer in home.state_file("offers.json")["offers"]] == ["demo_extra"]
    with and_("the new behavior runs and is in update-summary.json"):
        assert flags[f"{GATE}.new_check"]["runs"]
        assert [change["id"] for change in home.state_file("update-summary.json")["changes"]] == [f"{GATE}.new_check"]
    with and_("a second apply offers nothing twice"):
        home.hubctl("apply")
        assert len(home.state_file("offers.json")["offers"]) == 1
        assert len(home.state_file("update-summary.json")["changes"]) == 1
    with when("the user takes the offer"):
        home.hubctl("on", "demo_extra")
    with then("the part runs, pinned on"):
        assert home.show()["flags"]["demo_extra"]["runs"]
        assert home.settings()["flags"]["demo_extra"] == {"by": "user", "value": "on"}


def test_relevance_hides_and_never_turns_off(home, tool):
    with given("a profile whose repos have no remote, so the gate is not relevant"):
        home.install()
        home.write_json(os.path.join(home.config, "profile.json"), {
            "version": 1, "repos": {"~/work/demo": {"public": False, "remote": False}}})
    with when("the user runs hubctl show"):
        shown = home.show()["flags"]
        everything = home.show("--all")["flags"]
    with then("the gate is hidden, and hubctl show --all lists it with its reason"):
        assert GATE not in shown and f"{GATE}.dashes" not in shown
        assert "repos_with_remote" in everything[GATE]["hidden_because"]
        assert everything[GATE]["runs"]
        assert "hidden" in home.hubctl("show", "--all").stdout
    with and_("a commit with a home path is still refused"):
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/b.md")).refused
    with when("the user turns on public_repos_only"):
        home.hubctl_from_mod("on", f"{GATE}.public_repos_only")
    with then("a commit in a repo the profile does not mark public runs"):
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/c.md")).ran
    with and_("the same commit in a repo the profile marks public is refused"):
        home.write_json(os.path.join(home.config, "profile.json"), {
            "version": 1, "repos": {"~/work/demo": {"public": True, "remote": True}}})
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/d.md")).refused


def test_a_missing_word_file_is_skipped_and_one_that_is_there_feeds_the_gate(home, tool):
    with given("private_words_files names two files, and only one exists"):
        home.install()
        with open(os.path.join(home.root, "words.txt"), "w") as handle:
            handle.write("# words that never go public\nZephyrine\n")
        home.hubctl("set", "core.private_words_files", "~/words.txt", "~/missing.txt")
        home.write_json(os.path.join(home.config, "profile.json"), {
            "version": 1, "machines": {"desktop": {"host": "sams-tower", "use": "home"}}})
    with when("a commit message holds a word from the file that exists, a machine name and a token"):
        token = "gh" + "p_" + "A1b2C3d4" * 5
        result = home.hook(tool, "PreToolUse", commit(f"Fix zephyrine on sams-tower, key {token}"))
    with then("the gate names each one, and the missing file causes no error"):
        assert result.refused
        assert result.findings == ["secrets", "private_words", "machine_names"]
        assert token not in result.reason and "Zephyrine" not in result.reason
    with and_("a word typed into private_words feeds the gate too"):
        home.hubctl("set", "core.private_words", "Quillbrook")
        assert home.hook(tool, "PreToolUse", commit("Ship quillbrook")).findings == ["private_words"]
        assert home.settings()["private_words"] == ["Quillbrook"]


def test_cursors_shell_call_gets_the_same_refusal_in_cursors_own_keys(home):
    with given("the recorded Cursor input for a shell call that commits with a dash, and the em dash check on"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        data = load_fixture("cursor", "preToolUse.Shell.json")
        data.update(cwd=home.work, workspace_roots=[home.work])
        data["tool_input"] = {"command": commit(f"Ship {DASH} now"), "working_directory": home.work}
    with when("the adapter runs it"):
        cursor = home.send("cursor", "PreToolUse", data)
    with then("the answer is in Cursor's keys, with the same finding text as Claude Code's"):
        assert set(cursor.output) == {"permission", "user_message", "agent_message"}
        assert cursor.output["permission"] == "deny"
        claude = home.hook("claude_code", "PreToolUse", commit(f"Ship {DASH} now"))
        assert claude.refused and cursor.reason == claude.reason
        assert "hookSpecificOutput" not in cursor.output


def test_two_doors_change_two_flags_at_once_and_both_survive(home):
    behaviors = ["targets.commit", "targets.push", "targets.pr", "targets.issue", "targets.review_comment",
                 "private_paths", "secrets", "private_words", "machine_names", "dashes"]
    with given("hub is installed"):
        home.install()
    with when("ten hubctl processes start together, each changing a different flag, as the mod runs them for "
              "the user's clicks"):
        processes = [home.hubctl_from_mod_started("off", f"{GATE}.{name}") for name in behaviors]
        outputs = [process.communicate(timeout=60) for process in processes]
    with then("the user file parses and holds every entry, and the history has one line each"):
        assert [process.returncode for process in processes] == [0] * len(behaviors), outputs
        assert sorted(home.settings()["flags"]) == sorted(f"{GATE}.{name}" for name in behaviors)
        assert len(home.history()) == len(behaviors)


def test_a_linked_settings_file_stays_a_link(home):
    target = os.path.join(home.root, "dotfiles", "hub.json")
    with given("~/.config/hubctl/hub.json is a link into another folder"):
        home.install()
        home.write_json(target, {"version": 1, "flags": {}})
        os.makedirs(home.config, exist_ok=True)
        os.symlink(target, home.user_file)
    with when("the user runs hubctl off public_text_gate.dashes"):
        home.hubctl("off", f"{GATE}.dashes")
    with then("hub.json is still a link, and its target holds the change"):
        assert os.path.islink(home.user_file)
        with open(target) as handle:
            assert json.load(handle)["flags"] == {f"{GATE}.dashes": {"by": "user", "value": "off"}}


def test_edits_to_different_flags_on_two_machines_merge_without_a_conflict(home, tmp_path):
    other = Home(tmp_path / "other")
    bare = os.path.join(home.root, "settings.git")
    with given("two clones of one repo hold the settings file, each linked into its own HOME"):
        home.install()
        other.install()
        home.git("init", "-q", "--bare", bare)
        seed = os.path.join(home.root, "seed")
        home.git("clone", "-q", bare, seed)
        home.write_json(os.path.join(home.config, "hub.json"), {"version": 1, "flags": {}})
        home.hubctl("on", f"{GATE}.dashes")
        home.hubctl("on", f"{GATE}.secrets")
        os.replace(os.path.join(home.config, "hub.json"), os.path.join(seed, "hub.json"))
        home.git("add", "hub.json", cwd=seed)
        home.git("commit", "-q", "-m", "settings", cwd=seed)
        home.git("push", "-q", "origin", "main", cwd=seed)
        clones = []
        for each in (home, other):
            clone = os.path.join(each.root, "settings")
            each.git("clone", "-q", bare, clone)
            os.makedirs(each.config, exist_ok=True)
            os.symlink(os.path.join(clone, "hub.json"), each.user_file)
            clones.append(clone)
    with when("each HOME changes a different flag through hubctl and commits"):
        home.hubctl("off", f"{GATE}.dashes")
        other.hubctl_from_mod("off", f"{GATE}.secrets")
        for each, clone in zip((home, other), clones):
            each.git("commit", "-q", "-am", "change", cwd=clone)
        home.git("push", "-q", "origin", "main", cwd=clones[0])
        merge = other.run(["git", "pull", "-q", "--no-rebase", "origin", "main"], cwd=clones[1], check=False)
    with then("git merge ends with no conflict, and both values are present"):
        assert merge.returncode == 0, merge.stdout + merge.stderr
        with open(os.path.join(clones[1], "hub.json")) as handle:
            flags = json.load(handle)["flags"]
        assert flags[f"{GATE}.dashes"]["value"] == "off" and flags[f"{GATE}.secrets"]["value"] == "off"


def cpu_seconds_of_children():
    """CPU time of the finished child processes, theirs and their own children's. Waiting for a core is not in it."""
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def test_the_dispatcher_does_less_than_200_ms_of_work_with_every_part_on(home):
    with given("every part in the manifests is on"):
        home.install()
        parts = os.path.join(home.plugin, "parts")
        for part in sorted(os.listdir(parts)):
            if os.path.isfile(os.path.join(parts, part, "part.json")):
                home.hubctl("on", part)
        home.hubctl("set", "core.private_words", "Quillbrook")
        manifests = [name for name in os.listdir(parts) if os.path.isfile(os.path.join(parts, name, "part.json"))]
        header = json.loads(home.hubctl("screen", "--json").stdout)["header"]
        assert len(manifests) > 30 and header["parts_on"] == header["parts_total"] == len(manifests) - 1, header
        data = home.hook_input("claude_code", "PreToolUse", commit("A clean message"))
        home.send("claude_code", "PreToolUse", data)
    with when("the dispatcher runs 15 times as a real process on a recorded PreToolUse input"):
        work = []
        for number in range(15):
            fresh = dict(data, tool_use_id=f"call_timing_{number:03d}")
            before = cpu_seconds_of_children()
            result = home.send("claude_code", "PreToolUse", fresh)
            work.append(cpu_seconds_of_children() - before)
            assert result.ran
    with then("the median run uses less than 200 ms of CPU time, with every program it starts"):
        assert statistics.median(work) < 0.2, work


def test_the_scrub_scan_fails_on_planted_values_and_names_the_class(tmp_path):
    planted = {
        "home_path": "/" + "Users/" + "zorblat/notes.md",
        "email": "zorblat" + "@" + "corp-mail.io",
        "uuid": "1234abcd-" + "1234-4abc-8def-1234567890ab",
        "token": "sk-" + "ant-" + "Q" * 30,
        "em_dash": "a " + DASH + " b",
    }
    with given("a tree with a home path, an email, a UUID, a token and an em dash, planted at run time"):
        for index, (kind, value) in enumerate(planted.items()):
            (tmp_path / f"file{index}.md").write_text(f"clean line\n{value}\n")
    with when("scrub_scan.py runs on it"):
        scan = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tests", "scrub_scan.py"), str(tmp_path)],
                              capture_output=True, text=True)
    with then("it exits 1 and prints each file, line and class, and no planted value"):
        assert scan.returncode == 1
        for index, kind in enumerate(planted):
            assert f"file{index}.md:2: {kind}" in scan.stdout
        for value in planted.values():
            assert value not in scan.stdout + scan.stderr


def test_a_manifest_that_names_an_unknown_id_or_holds_code_fails_the_check(home):
    marker = os.path.join(home.root, "ran")
    with given("one manifest whose needs name an unknown id, and one whose relevant_if calls a function"):
        home.install()
        home.write_json(os.path.join(home.plugin, "parts", "bad_needs", "part.json"), {
            "id": "bad_needs", "family": "tools", "kind": "extra", "default": "on", "does": "x",
            "needs": ["nothing.here"]})
        home.write_json(os.path.join(home.plugin, "parts", "bad_code", "part.json"), {
            "id": "bad_code", "family": "tools", "kind": "extra", "default": "on", "does": "x",
            "relevant_if": f"__import__('os').system('touch {marker}')"})
    with when("the tree loads"):
        check = home.hubctl("apply", check=False)
    with then("each fails with its file and id, and the condition is never run"):
        assert check.returncode == 1
        assert os.path.join("bad_needs", "part.json") in check.stderr and "nothing.here" in check.stderr
        assert os.path.join("bad_code", "part.json") in check.stderr and "bad_code.relevant_if" in check.stderr
        assert not os.path.exists(marker)


def test_a_push_is_checked_through_the_commits_it_sends(home, tool):
    with given("a repo with a local bare remote and a new commit whose message has a dash, "
               "and the em dash check on"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        bare = os.path.join(home.root, "remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    with when("Claude sends git push"):
        first = home.hook(tool, "PreToolUse", "git push origin main")
    with then("the gate refuses it and names the dash"):
        assert first.refused and first.findings == ["dashes"]
    with when("the push target is turned off"):
        home.hubctl_from_mod("off", f"{GATE}.targets.push")
    with then("the same push runs, while a commit is still checked"):
        assert home.hook(tool, "PreToolUse", "git push origin main").ran
        assert home.hook(tool, "PreToolUse", commit(f"More {DASH} work")).refused


def test_pr_and_issue_texts_are_checked_by_their_own_targets(home, tool):
    with given("hub is installed with the em dash check on, and a PR body file holds a dash"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        with open(os.path.join(home.work, "body.md"), "w") as handle:
            handle.write(f"Adds the thing {DASH} finally\n")
    with when("Claude creates a PR from that file, comments on an issue and posts a review comment"):
        pr = home.hook(tool, "PreToolUse", "gh pr create --draft --title Thing --body-file body.md")
        issue = home.hook(tool, "PreToolUse", "gh issue comment 4 --body 'See /home/sam/log.txt'")
        review = home.hook(tool, "PreToolUse", f"gh api repos/o/r/pulls/1/comments -f body='Nit {DASH} here'")
        status = home.hook(tool, "PreToolUse", "gh pr view 3 --json title")
    with then("each is refused with its finding, and a read runs"):
        assert pr.findings == ["dashes"] and issue.findings == ["private_paths"] and review.findings == ["dashes"]
        assert status.ran and status.output == {}
    with when("the user turns off the PR target"):
        home.hubctl_from_mod("off", f"{GATE}.targets.pr")
    with then("a new PR text runs, and an issue text is still checked"):
        assert home.hook(tool, "PreToolUse", f"gh pr edit 3 --body 'New {DASH} text'").ran
        assert home.hook(tool, "PreToolUse", f"gh issue create --title 'Bug {DASH} here' --body x").refused


def test_a_second_run_of_the_same_tool_call_returns_the_first_answer(home, tool):
    with given("hub is installed with the em dash check on"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        data = home.hook_input(tool, "PreToolUse", commit(f"Twice {DASH} sent"))
    with when("the same tool call reaches the dispatcher twice, as when two plugin copies load"):
        first = home.send(tool, "PreToolUse", data)
        lines = len(home.signals())
        second = home.send(tool, "PreToolUse", data)
    with then("both get the same refusal, and the second logs nothing"):
        assert first.refused and second.output == first.output
        assert len(home.signals()) == lines


def test_the_manifests_schemas_and_hooks_files_agree():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    from hub import settings, tree

    def schema(name):
        with open(os.path.join(REPO, "schema", name)) as handle:
            return json.load(handle)

    with given("the shipped tree, the two schema files and both hooks files"):
        parts = tree.load(os.path.join(PLUGIN, "parts"))
        part_schema, hub_schema = schema("part.schema.json"), schema("hub.schema.json")
        with open(os.path.join(PLUGIN, "hooks", "hooks.json")) as handle:
            claude_events = set(json.load(handle)["hooks"])
        with open(os.path.join(REPO, "cursor", "hooks.json")) as handle:
            cursor_events = set(json.load(handle)["hooks"])
    with then("the schemas list the same fields the code checks"):
        assert set(part_schema["properties"]) == tree.PART_FIELDS
        assert set(part_schema["$defs"]["node"]["properties"]) == tree.NODE_FIELDS
        assert set(hub_schema["properties"]) == settings.TOP_KEYS
        assert set(hub_schema["$defs"]["entry"]["properties"]) == settings.ENTRY_KEYS
        assert set(part_schema["properties"]["handlers"]["propertyNames"]["enum"]) == set(tree.EVENTS)
    with and_("every event a part handles is in the hooks file of each tool the part runs in"):
        def handled_in(tool):
            return {event for part in parts.parts if tool in part.runs_in for event in part.handlers}
        assert handled_in("claude_code") and handled_in("claude_code") <= claude_events
        assert handled_in("cursor") and {tree.CURSOR_EVENT_NAMES[event] for event in handled_in("cursor")} <= cursor_events
    with and_("the marketplace names the one plugin"):
        with open(os.path.join(REPO, ".claude-plugin", "marketplace.json")) as handle:
            market = json.load(handle)
        assert [plugin["name"] for plugin in market["plugins"]] == ["hub"]
        assert os.path.isfile(os.path.join(REPO, "plugins", "hub", ".claude-plugin", "plugin.json"))
        assert os.path.isfile(os.path.join(REPO, ".cursor-plugin", "plugin.json"))
