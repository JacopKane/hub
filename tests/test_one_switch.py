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

from conftest import DASH, PLUGIN, REPO, Home, commit, load_fixture
from steps import and_, given, then, when

# The made-up guard part from tests/fixtures/demo_gate, which every test home installs. It has a mode, children and
# options, but no handler.
GATE = "demo_gate"
# A command that self-protection refuses in both tools: it clears the marker that says an agent runs the command.
CLEARS_THE_MARKER = "AI_AGENT= python3 tool.py"


def flags(home, cwd=None):
    """Every flag with its effective value, as hubctl show --json --all works it out in cwd."""
    return json.loads(home.hubctl("show", "--json", "--all", cwd=cwd or home.work).stdout)["flags"]


def test_a_parts_mode_caps_its_children_and_turning_it_off_keeps_their_values(home):
    with given("demo_gate.dashes is set to block while the part is at warn"):
        home.install()
        home.hubctl("set", f"{GATE}.dashes", "block")
    with when("hub works out the flags"):
        found = flags(home)
    with then("the dash check runs at warn, because the part's mode caps it, and keeps block as its own value"):
        assert found[f"{GATE}.dashes"]["effective"] == "warn"
        assert found[f"{GATE}.dashes"]["value"] == "block"
    with when("the part is set to block"):
        home.hubctl("set", GATE, "block")
    with then("the dash check runs at block"):
        assert flags(home)[f"{GATE}.dashes"]["effective"] == "block"
    with when("the part is set to off"):
        home.hubctl_from_mod("off", GATE)
        while_off = flags(home)
    with then("no child runs while it is off"):
        assert while_off[f"{GATE}.dashes"]["effective"] == "off" and not while_off[f"{GATE}.dashes"]["runs"]
        assert not while_off[f"{GATE}.private_paths"]["runs"]
    with when("the part is set back to warn"):
        home.hubctl("set", GATE, "warn")
    with then("dashes is still block, in the file and in hubctl show"):
        assert home.settings()["flags"][f"{GATE}.dashes"]["value"] == "block"
        assert home.show()["flags"][f"{GATE}.dashes"]["value"] == "block"


def test_the_highest_scope_wins_and_the_local_file_stays_out_of_git(home):
    with given("a repo whose user file says warn, project file says block and local file says off"):
        home.install()
        home.hubctl("set", GATE, "warn")
        home.hubctl("set", GATE, "block", "--scope", "project")
        home.hubctl_from_mod("set", GATE, "off", "--scope", "local")
    with when("hub works out the flags in that repo"):
        inside = flags(home)
    with then("the part is off, because the local scope wins"):
        assert inside[GATE]["effective"] == "off" and not inside[GATE]["runs"]
    with and_("git status does not list .hub/hub.local.json"):
        status = home.git("status", "--porcelain", "--untracked-files=all").stdout
        assert ".hub/hub.json" in status and "hub.local.json" not in status
    with and_("in a folder outside the repo the user scope applies, so the part is at warn"):
        outside = os.path.join(home.root, "elsewhere")
        os.makedirs(outside)
        assert flags(home, cwd=outside)[GATE]["effective"] == "warn"


def test_a_value_equal_to_the_default_is_dropped_unless_pinned(home):
    with given("hub is installed"):
        home.install()
    with when("setup writes warn for the gate"):
        home.set_by("setup", GATE, "warn")
    with then("setup's entry is dropped"):
        assert home.settings()["flags"] == {}
    with when("the user then types hubctl set demo_gate warn"):
        home.hubctl("set", GATE, "warn")
    with then("the user's entry stays, pinned"):
        assert home.settings()["flags"] == {GATE: {"by": "user", "value": "warn"}}
    with when("an update moves the gate's default to block and the dash check's default to on"):
        home.edit_manifest(GATE, lambda data: data.update(default="block"))
        home.edit_manifest(GATE, lambda data: next(c for c in data["children"] if c["id"] == "dashes").update(default="on"))
    with then("the gate stays at warn, and the unpinned dash check follows its new default, capped at warn"):
        found = flags(home)
        assert found[GATE]["value"] == "warn"
        assert found[f"{GATE}.dashes"]["value"] == "on" and found[f"{GATE}.dashes"]["effective"] == "warn"


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


def test_relevance_hides_and_never_turns_off(home):
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
    with and_("its checks still run while it is hidden"):
        assert everything[f"{GATE}.private_paths"]["runs"]
    with when("the profile gains a repo with a remote"):
        home.write_json(os.path.join(home.config, "profile.json"), {
            "version": 1, "repos": {"~/work/demo": {"public": True, "remote": True}}})
    with then("hubctl show lists the gate again"):
        assert GATE in home.show()["flags"]


def test_two_doors_change_two_flags_at_once_and_both_survive(home):
    behaviors = ["targets.commit", "targets.push", "targets.pr", "private_paths", "secrets", "private_words",
                 "unreadable", "dashes"]
    with given("hub is installed"):
        home.install()
    with when("eight hubctl processes start together, each changing a different flag, as the mod runs them for "
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
    with when("the user runs hubctl off demo_gate.dashes"):
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


def test_the_scrub_scan_finds_links_into_private_pages_but_not_made_up_ones(tmp_path):
    links = ["https://claude." + "ai/artifact/" + "Q7f3kLmN2pXz",
             "see www.notion." + "so/" + "Team-notes-4f1c9a7e2b",
             "https://drive.google." + "com/file/d/" + "1aB2cD3eF4gH/view",
             "team.notion." + "site/" + "Plan-9e8d7c",
             "https://docs.google." + "com/document/d/" + "1xYz9WvU/edit"]
    made_up = ["https://claude." + "ai/artifact/" + "00000000",
               "https://docs.google." + "com/document/d/" + "00000000/edit",
               "signed in to claude." + "ai, no link here"]
    with given("a doc with five links into private pages, and one with made-up ids and a bare host name"):
        (tmp_path / "links.md").write_text("\n".join(["clean line", *links]) + "\n")
        (tmp_path / "made_up.md").write_text("\n".join(made_up) + "\n")
    with when("scrub_scan.py runs on both"):
        scan = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tests", "scrub_scan.py"), str(tmp_path)],
                              capture_output=True, text=True)
    with then("it names each private link by line and class, passes the made-up ones and prints no link"):
        assert scan.returncode == 1
        assert scan.stdout.splitlines() == [f"{tmp_path / 'links.md'}:{number}: private_link" for number in range(2, 7)]
        for link in links:
            assert link.split("/")[-1] not in scan.stdout + scan.stderr


def scan_lines(folder, name, lines):
    (folder / name).write_text("\n".join(lines) + "\n")
    scan = subprocess.run([sys.executable, "-I", os.path.join(REPO, "tests", "scrub_scan.py"), str(folder / name)],
                          capture_output=True, text=True)
    return scan, [int(line.split(":")[1]) for line in scan.stdout.splitlines()]


def test_a_page_link_passes_as_made_up_only_when_its_id_is_zeros(tmp_path):
    claude, notion, docs = "claude." + "ai", "notion." + "so", "docs.google." + "com"
    with given("links that hold eight zeros next to a real-looking id, a query, a fragment or a workspace name"):
        wide = [f"https://{claude}/share/Q7f3kLmN2pXz?from=" + "00000000",
                f"https://{claude}/chat/4f1c9a7e2b/" + "00000000",
                f"https://{claude}/artifact/" + "00000000#Q7f3kLmN",
                f"https://{claude}/artifact/" + "00000000Q7f3kLmN",
                "https://zorblat.notion." + "site/Page-" + "00000000"]
    with and_("links whose only id is zeros, inside markdown and quotes, and a bare host name"):
        made_up = [f"`https://{claude}/artifact/" + "00000000`",
                   f"[a page](https://{docs}/document/d/" + "00000000/edit).",
                   f"\"www.{notion}/Team-notes-" + "00000000\",",
                   f"https://{claude}/code/session_" + "00000000",
                   f"https://acme.notion." + "site/" + "00000000-0000-0000-0000-000000000000",
                   f"signed in to {claude}, no link here"]
    with when("scrub_scan.py runs on each file"):
        wide_scan, wide_hits = scan_lines(tmp_path, "wide.md", wide)
        made_up_scan, made_up_hits = scan_lines(tmp_path, "made_up.md", made_up)
    with then("every wide link is a private_link hit, and every made-up one passes"):
        assert wide_hits == list(range(1, len(wide) + 1)), wide_scan.stdout
        assert all(line.endswith(": private_link") for line in wide_scan.stdout.splitlines())
        assert made_up_scan.returncode == 0 and made_up_hits == [], made_up_scan.stdout


def test_page_hosts_are_found_in_any_case(tmp_path):
    with given("links into private pages with the host written in capitals or mixed case"):
        links = ["https://Claude." + "AI/artifact/Q7f3kLmN2pXz",
                 "DOCS.GOOGLE." + "COM/document/d/1xYz9WvU/edit",
                 "https://www.Notion." + "So/Team-notes-4f1c9a7e2b"]
    with when("scrub_scan.py runs"):
        scan, hits = scan_lines(tmp_path, "links.md", links)
    with then("each line is a private_link hit"):
        assert scan.returncode == 1 and hits == [1, 2, 3], scan.stdout
        assert all(line.endswith(": private_link") for line in scan.stdout.splitlines())


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


def test_a_caller_that_needs_only_the_steps_can_skip_reading_the_pushed_commits():
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        with given("a git first on PATH that logs each call before it runs the real git"):
            log = os.path.join(folder, "calls.txt")
            real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
            with open(os.path.join(folder, "git"), "w") as handle:
                handle.write(f"#!/bin/sh\necho \"$*\" >> '{log}'\nexec '{real}' \"$@\"\n")
            os.chmod(os.path.join(folder, "git"), 0o755)
            code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from hub import command_steps; "
                    "print(json.dumps(command_steps.public_steps('git push origin main && bash -c \"git push\"', "
                    "sys.argv[2], log=sys.argv[3] == 'yes')))")
            env = dict(os.environ, PATH=folder + os.pathsep + os.environ["PATH"])

            def steps(log_too):
                return json.loads(subprocess.run(
                    [sys.executable, "-I", "-c", code, os.path.join(PLUGIN, "lib"),
                     folder, "yes" if log_too else "no"],
                    env=env, capture_output=True, text=True, check=True).stdout)
        with when("the shared parser reads two pushes, one inside a wrapper, without the log"):
            skipped = steps(False)
        with then("it names both pushes and never runs git"):
            assert [target for target, _text in skipped] == ["push", "push"]
            assert not os.path.exists(log)
        with when("it reads them with the log, as it does by default"):
            steps(True)
        with then("git runs for each push: it counts the commits of the named push, which fails here outside a repo, "
                  "so no log follows, and reads the settings of the push with no branch named"):
            with open(log) as handle:
                assert [line.split()[0] for line in handle] == ["rev-list", "config", "rev-parse"]


def test_cursors_shell_call_gets_the_same_refusal_in_cursors_own_keys(home):
    with given("the recorded Cursor input for a shell call that clears the agent marker"):
        home.install()
        data = load_fixture("cursor", "preToolUse.Shell.json")
        data.update(cwd=home.work, workspace_roots=[home.work])
        data["tool_input"] = {"command": CLEARS_THE_MARKER, "working_directory": home.work}
    with when("the adapter runs it"):
        cursor = home.send("cursor", "PreToolUse", data)
    with then("the answer is in Cursor's keys, with the same refusal text as Claude Code's"):
        assert set(cursor.output) == {"permission", "user_message", "agent_message"}
        assert cursor.output["permission"] == "deny"
        claude = home.hook("claude_code", "PreToolUse", CLEARS_THE_MARKER)
        assert claude.refused and cursor.reason == claude.reason
        assert "hookSpecificOutput" not in cursor.output


def test_a_second_run_of_the_same_tool_call_returns_the_first_answer(home, tool):
    with given("hub is installed, and a tool call that self-protection refuses"):
        home.install()
        data = home.hook_input(tool, "PreToolUse", CLEARS_THE_MARKER)
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
