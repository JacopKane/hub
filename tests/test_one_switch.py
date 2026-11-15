"""Slice 1: one switch, end to end.

Every scenario runs the real engine, hubctl and the dispatcher as processes
in a temporary HOME. Scenarios that feed hook input run once per tool.
"""
import itertools
import json
import os
import resource
import statistics
import subprocess
import sys
import time

from conftest import DASH, FIXTURES, GIT_OPTION_VALUES, PLUGIN, REPO, Home, commit, load_fixture
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


def test_a_push_or_commit_after_each_git_option_that_takes_a_value_is_read(home, tool):
    with given("a repo with a local bare remote and a new commit whose message has a dash, the em dash check on, "
               "and self-protection off, so only the gate reads each line"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        home.hubctl_from_mod("off", "self_protection")
        bare = os.path.join(home.root, "remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    sessions = (f"00000000-0000-4000-8000-{number:012d}" for number in itertools.count(1))
    with when("Claude pushes main after each of git's options that take a value, with the value as the next word, "
              "each in its own session"):
        pushes = {option: home.hook(tool, "PreToolUse", f"git {option} {value} push origin main",
                                    session=next(sessions))
                  for option, value in GIT_OPTION_VALUES.items()}
    with then("git reads the next word as the option's value, so the gate reads the push and names the dash"):
        for option, result in pushes.items():
            assert result.refused and result.findings == ["dashes"], (option, result.reason)
    with when("Claude commits after each option and its value, with a message that names a private path"):
        commits = {option: home.hook(tool, "PreToolUse", f"git {option} {value} commit -m 'see /home/sam/notes'",
                                     session=next(sessions))
                   for option, value in GIT_OPTION_VALUES.items()}
    with then("the gate reads the commit message and names the path"):
        for option, result in commits.items():
            assert result.refused and result.findings == ["private_paths"], (option, result.reason)


def test_a_push_whose_remote_branch_or_folder_the_shell_fills_in_is_refused_as_unreadable_without_a_crash(home, tool):
    with given("a repo with a local bare remote and a new commit"):
        home.install()
        bare = os.path.join(home.root, "remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("commit", "-q", "--allow-empty", "-m", "Late change")
    pushes = ["git push origin $BRANCH", "git push $REMOTE main", "git -C $DIR push", "git -C ~$DIR push",
              "git push origin ${BRANCH}:main", "git push origin $(git branch --show-current)",
              "git -C $(pwd) push", "git -C `cd .; pwd` push", "git --git-dir=$(pwd)/.git push",
              'git -c "$SETTING" push', "git -c $SETTING push origin main"]
    with when("Claude sends pushes whose remote, branch, folder or git option comes from a variable or a substitution"):
        results = {push: home.hook(tool, "PreToolUse", push) for push in pushes}
    with then("the gate reads each one, finds it can not tell the commits, and refuses it as unreadable"):
        for push, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)


def test_a_push_is_checked_at_every_commit_it_names_split_as_git_splits_it_and_a_search_is_unreadable(home, tool):
    with given("a repo with a local bare remote, a clean new commit on main, a branch draft whose new commit "
               "message has a colon and a dash, and the em dash check on"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        bare = os.path.join(home.root, "remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("checkout", "-q", "-b", "draft")
        home.git("commit", "-q", "--allow-empty", "-m", f"Draft: late {DASH} change")
        home.git("checkout", "-q", "main")
        home.git("commit", "-q", "--allow-empty", "-m", "Plain change")
    named = ["git push origin main draft", "git push origin 'draft^{/Draft: late}:refs/heads/x'",
             "git push origin main '+draft^{/Draft: late}:x'"]
    searches = ["git push origin ':/Draft:refs/heads/x'", "git push origin '+:/Draft: late:x'",
                "git push origin main ':/Draft:x'"]
    with when("Claude pushes draft's commit as a second refspec, or by a revision whose text holds a colon, "
              "each in its own session"):
        results = {push: home.hook(tool, "PreToolUse", push, session=f"named-{index}")
                   for index, push in enumerate(named)}
    with then("the gate reads the commit each refspec sends, up to its last colon, and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    with when("Claude pushes a commit that a search of commit messages picks, in any refspec, each in its own session"):
        results = {push: home.hook(tool, "PreToolUse", push, session=f"search-{index}")
                   for index, push in enumerate(searches)}
    with then("the gate does not look the search up and refuses each one as unreadable"):
        for push, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)
    with when("Claude pushes main alone, and deletes a remote branch"):
        plain = home.hook(tool, "PreToolUse", "git push origin main")
        delete = home.hook(tool, "PreToolUse", "git push origin :old")
    with then("both run, since main's new commit is clean"):
        assert plain.ran, plain.reason
        assert delete.ran, delete.reason


def git_log_waits(home, seconds):
    """A PATH whose git waits that long before git log, and runs every other git call at once."""
    folder = os.path.join(home.root, "slow-log-bin")
    os.makedirs(folder, exist_ok=True)
    real = subprocess.run(["sh", "-c", "command -v git"], env=home.env, capture_output=True, text=True).stdout.strip()
    with open(os.path.join(folder, "git"), "w") as handle:
        handle.write(f'#!/bin/sh\nif [ "$1" = log ]; then sleep {seconds}; fi\nexec "{real}" "$@"\n')
    os.chmod(os.path.join(folder, "git"), 0o755)
    return folder + os.pathsep + home.env["PATH"]


def test_a_push_is_read_by_gits_own_rules_for_its_words_and_one_whose_commits_git_can_not_list_is_unreadable(
        home, tool):
    with given("a repo with a local bare remote, tag v1 on main's pushed commit, a new commit on main whose "
               "message has a dash, a branch docs at that commit that is also a folder, and the em dash check on"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        bare = os.path.join(home.root, "remote.git")
        home.git("init", "-q", "--bare", bare)
        home.git("remote", "add", "origin", bare)
        home.git("push", "-q", "-u", "origin", "main")
        home.git("tag", "v1")
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
        home.git("branch", "docs")
        os.makedirs(os.path.join(home.work, "docs"))
    worded = ["git push origin main --recurse-submodules check", "git push origin main -o ci.skip",
              "git push origin main --push-option ci.skip", "git push origin main -oci.skip",
              "git push origin main --repo origin", "git push origin main tag v1", "git push origin tag v1 main",
              "git push origin main '^refs/heads/none'", "git push origin main 2>&1",
              "git push origin main > push.log", "git push origin docs", "git push origin -- main"]
    with when("Claude pushes main with an option value, a tag word, a ^ entry or a redirect after it, or pushes "
              "docs, each in its own session"):
        results = {push: home.hook(tool, "PreToolUse", push, session=f"worded-{index}")
                   for index, push in enumerate(worded)}
    with then("the gate reads the words as git push does, lists main's new commit and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    unclear = ["git push origin main nosuch", "git push origin 'ma*:main'",
               "git push origin main --re origin", "git push origin -- main --output=listed.txt"]
    with when("Claude pushes a branch git does not have, a pattern, a shortened option git can not tell apart, "
              "or a word after -- that git log would read as its own option"):
        results = {push: home.hook(tool, "PreToolUse", push, session=f"unclear-{index}")
                   for index, push in enumerate(unclear)}
    with then("the gate can not list the commits, refuses each one as unreadable, and runs no option of git log"):
        for push, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)
        assert not os.path.exists(os.path.join(home.work, "listed.txt"))
    with when("git log takes longer than the gate waits for it"):
        kept = home.env["PATH"]
        home.env["PATH"] = git_log_waits(home, 12)
        try:
            slow = home.hook(tool, "PreToolUse", "git push origin main", session="slow")
        finally:
            home.env["PATH"] = kept
    with then("the gate stops waiting and refuses the push as unreadable"):
        assert slow.refused and slow.findings == ["unreadable"], slow.reason
    with when("Claude pushes tag v1 alone, or deletes the tag on the remote"):
        tag = home.hook(tool, "PreToolUse", "git push origin tag v1", session="tag")
        delete = home.hook(tool, "PreToolUse", "git push --delete origin tag v1", session="delete")
    with then("both run, since v1's commit is clean"):
        assert tag.ran, tag.reason
        assert delete.ran, delete.reason
    with when("tag late is at main's new commit, whose message has a dash, and Claude deletes late or docs on the "
              "remote"):
        home.git("tag", "late", "main")
        deletes = hooks(home, tool, ["git push --delete origin tag late", "git push --delete origin docs",
                                     "git push -d origin docs late"], "deletes")
    with then("each runs, since a delete sends no commits"):
        for push, result in deletes.items():
            assert result.ran, (push, result.reason)
    with when("main gets a newer commit with a file, the repo loses that file's content, so git log stops before "
              "the commit with the dash, and Claude pushes main"):
        with open(os.path.join(home.work, "notes.txt"), "w") as handle:
            handle.write("Plain notes\n")
        home.git("add", "notes.txt")
        home.git("commit", "-q", "-m", "Add notes")
        blob = home.git("rev-parse", "HEAD:notes.txt").stdout.strip()
        os.remove(os.path.join(home.work, ".git", "objects", blob[:2], blob[2:]))
        broken = home.hook(tool, "PreToolUse", "git push origin main", session="broken")
    with then("git counts the commits but fails to list them, and the gate refuses the push as unreadable"):
        assert broken.refused and broken.findings == ["unreadable"], broken.reason


def pushed_repo(home):
    """A repo whose main is pushed to a local bare remote, then gets a clean new commit, with the em dash check
    on and the PR test gate off, since it answers first for some of the same pushes."""
    home.install()
    home.hubctl("on", f"{GATE}.dashes")
    home.hubctl_from_mod("off", "pr_test_gate")
    bare = os.path.join(home.root, "remote.git")
    home.git("init", "-q", "--bare", bare)
    home.git("remote", "add", "origin", bare)
    home.git("push", "-q", "-u", "origin", "main")
    home.git("commit", "-q", "--allow-empty", "-m", "Plain change")


def hooks(home, tool, pushes, name):
    return {push: home.hook(tool, "PreToolUse", push, session=f"{name}-{index}") for index, push in enumerate(pushes)}


def test_a_new_line_ends_a_push_as_the_shell_ends_it_unless_quotes_a_backslash_or_a_pipe_carry_it_on(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    two_lines = ["git push origin main\ngit branch --delete old", "git push origin main # send it\ngit branch -d old",
                 "git push origin main\ngit push --dry-run origin main"]
    with when("Claude pushes main, and the next line of the same call is another command with a delete or dry run "
              "option, each in its own session"):
        results = hooks(home, tool, two_lines, "two-lines")
    with then("the gate reads the push up to the end of its line, lists main's new commit and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    one_push = ["git push origin main -o 'note=one\ntwo --delete'", "git push origin main -o $'it\\'s\ntwo --delete'",
                "git push origin \\\nmain", "git push origin \\\n  main # send it"]
    with when("Claude pushes main with a new line inside quotes, even after an escaped quote in $'...', or with a "
              "backslash that carries the push on to the next line"):
        results = hooks(home, tool, one_push, "one-push")
    with then("the gate reads one push of main and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    piped = ["printf 'git push origin main\\n' |\nbash", "printf 'git push origin main\\n' | # run it\nbash",
             "printf 'git push origin main\\n' |&\nbash"]
    with when("Claude pipes a push into a shell whose name stands on the next line, after | or |& and maybe a "
              "comment"):
        results = hooks(home, tool, piped, "piped")
    with then("the shell still reads its script from the pipe, which the gate can not see, so the call is "
              "unreadable"):
        for push, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)


def test_a_comment_starts_right_after_an_operator_as_the_shell_starts_it(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    touching = ["echo hi;# it's\ngit push origin main\necho ok;# that's",
                "true &# it's\ngit push origin main\necho ok;# that's",
                "true &&# it's\ngit push origin main\necho ok;# that's",
                "(# it's\ngit push origin main\n)# that's",
                "echo hi |# it's\ngit push origin main\necho ok;# that's",
                "cat <# it's\ngit push origin main\necho ok ># that's"]
    with when("Claude pushes main on the line after a comment that touches ;, &, &&, (, |, < or > and holds a quote "
              "mark, each in its own session"):
        results = hooks(home, tool, touching, "touching")
    with then("the gate drops each comment at its line end, as the shell does, reads the push and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    with when("Claude pipes a push into a shell whose name stands on the next line, after a pipe and a comment that "
              "touches it"):
        piped = home.hook(tool, "PreToolUse", "printf 'git push origin main\\n' |#c\nbash", session="piped")
    with then("the shell still reads its script from the pipe, so the call is unreadable"):
        assert piped.refused and piped.findings == ["unreadable"], piped.reason
    with when("Claude commits with a message where a # stands inside a word, after a letter or a quote"):
        inside = home.hook(tool, "PreToolUse", "git commit -m a#'Fix'#\"two\"#3 # it's", session="inside")
    with then("the # is word text, as the shell reads it, and the commit runs"):
        assert not inside.refused, inside.reason
    with when("Claude commits with a message where a # follows an escaped ; and comes before a dash"):
        escaped = home.hook(tool, "PreToolUse", f"git commit -m Fix\\;#'Late {DASH} change'", session="escaped")
    with then("the escaped ; is word text, so the # is too, and the gate names the dash"):
        assert escaped.refused and escaped.findings == ["dashes"], escaped.reason


def test_a_quote_mark_in_a_comment_does_not_change_how_the_gate_reads_later_lines(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    filled_in = ["echo hi # don't\ngit commit -m \"$MESSAGE\"", "echo hi # say \"x\ngit commit -m \"it's $MESSAGE\"",
                 "git commit -m \"x\"#\"$MESSAGE\""]
    with when("Claude commits with a message from a variable on the line after a comment that holds a quote mark, "
              "or right after a # that stands inside a word, each in its own session"):
        results = hooks(home, tool, filled_in, "filled-in")
    with then("the shell drops only the comment and fills in the variable, so each commit is unreadable"):
        for line, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (line, result.reason)
    with when("Claude pushes main from a substitution on the line after a comment that holds a quote mark"):
        substituted = home.hook(tool, "PreToolUse", "# it's late\necho \"$(git push origin main)\"",
                                session="substituted")
    with then("the gate reads the push inside the substitution and names the dash"):
        assert substituted.refused and substituted.findings == ["dashes"], substituted.reason


def test_a_heredoc_starts_only_where_the_shell_starts_one_and_ends_only_at_its_own_end_line(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    no_heredoc = ["echo '<<EOF'\ngit push origin main\nEOF", "echo \"<<EOF\"\ngit push origin main\nEOF",
                  "# <<EOF\ngit push origin main\nEOF", "echo hi # <<EOF\ngit push origin main\nEOF",
                  "cat <<<EOF\ngit push origin main\nEOF", "echo $((1<<EOF))\ngit push origin main\nEOF",
                  "echo ${x:-<<EOF}\ngit push origin main\nEOF"]
    with when("Claude pushes main on the line after a << in quotes, in a comment, in a <<< here-string, in "
              "arithmetic or in a ${...} default, each in its own session"):
        results = hooks(home, tool, no_heredoc, "no-heredoc")
    with then("none of them starts a heredoc in the shell, so the gate reads the push and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    after_body = ["cat <<EOF\n EOF\n'\nEOF\ngit push origin main", "cat <<A <<B\na\nA\nb'\nB\ngit push origin main",
                  "cat <<-EOF\n\t'\n\tEOF\ngit push origin main", "bash <<'EOF'\ngit push origin main\nEOF"]
    with when("Claude pushes main after a heredoc body that holds a quote mark and a line that only looks like its "
              "end, after two heredocs on one line, after a <<- body, or inside a body a shell runs"):
        results = hooks(home, tool, after_body, "after-body")
    with then("the gate ends each body only at the line that holds its own end word, reads the push and names "
              "the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    with when("Claude writes a push inside a heredoc body whose end line has a space before the end word"):
        in_body = home.hook(tool, "PreToolUse", "cat <<EOF\nx\n EOF\ngit push origin main\nEOF", session="in-body")
    with then("the shell reads that push as body text, and so does the gate, so the call runs"):
        assert not in_body.refused, in_body.reason
    with when("Claude shifts a number in a $((...)) sum on the line and in a heredoc body, and pushes main from a "
              "substitution inside another sum"):
        shift = home.hook(tool, "PreToolUse", "echo $((1<<3)) && cat <<EOF\n$((2<<1))\nEOF", session="shift")
        in_sum = home.hook(tool, "PreToolUse", "echo $(( $(git push origin main) + 1 ))", session="in-sum")
    with then("the shift is no heredoc, so the call runs, and the gate reads the push in the sum and names the dash"):
        assert not shift.refused, shift.reason
        assert in_sum.refused and in_sum.findings == ["dashes"], in_sum.reason
    odd = ["cat <<$END\ngit push origin main\n$END", "cat <<EOF\ngit push origin main",
           "((cat <<EOF\ngit push origin main\nEOF\n))", "cat <<EOF \"a\nb\"\ngit push origin main\nEOF"]
    with when("Claude writes a << whose end word holds a $, whose end line is missing, that stands in a (( )) part "
              "over several lines, or whose line runs on inside quotes"):
        results = hooks(home, tool, odd, "odd")
    with then("the gate can not tell where the heredoc starts or ends, so each call is unreadable"):
        for push, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)


def test_a_backslash_at_a_line_end_joins_the_two_lines_into_one_as_the_shell_joins_them(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    joined = ["gi\\\nt push origin main", "git pu\\\nsh origin main", f"git commit -\\\nm 'Late {DASH} change'",
              "cat <\\\n<EOF\n'\nEOF\ngit push origin main", "cat <<EOF\na\\\nEOF\n'\nEOF\ngit push origin main",
              "cat <<EOF \\\n| cat\n'\nEOF\ngit push origin main", "git push \"orig\\\nin\" main"]
    with when("Claude splits a program, an action, an option, a double-quoted remote or a << over two lines with a "
              "backslash, or ends a line of a heredoc body, or the line that starts it, with a backslash"):
        results = hooks(home, tool, joined, "joined")
    with then("the gate joins the lines without a space, as the shell does, reads the step and names the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    kept = ["cat <<'EOF'\na\\\nEOF\ngit push origin main", "# a note \\\ngit push origin main",
            "echo 'a\\\n' ; git push origin main"]
    with when("Claude ends a line with a backslash in a heredoc whose end word is quoted, in a comment or in single "
              "quotes, and pushes main after it"):
        results = hooks(home, tool, kept, "kept")
    with then("the shell keeps that backslash and the line end as they are, so the gate reads the push and names "
              "the dash"):
        for push, result in results.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
    with when("Claude pushes to a remote whose name comes from a variable split over two lines"):
        split_variable = home.hook(tool, "PreToolUse", "git push $\\\nREMOTE main", session="split-variable")
    with then("the shell fills in the variable, so the push is unreadable"):
        assert split_variable.refused and split_variable.findings == ["unreadable"], split_variable.reason


def test_a_dollar_quoted_escape_that_bash_and_zsh_read_apart_is_unreadable(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    apart = ["$'\\g\\i\\t' push origin main", "git push origin $'ma\\\nin'", "git commit -m $'Fix \\q it'",
             "git commit -m $'Fix\\0 it'", "git commit -m $'Fix \\cA it'", "git commit -m $'Fix \\xZ it'",
             "git commit -m $'Fix \\400 it'", "git commit -m $'Fix \\8 it'", "git commit -m $'Fix \\U110000 it'",
             "git commit -m $'Fix \\u00e9 it'"]
    with when("Claude writes a $'...' string with an escape that bash and zsh fill in differently, such as an "
              "unknown letter, a backslash at a line end, a NUL, \\c, \\x with no digit, an octal past 377, \\8, "
              "or a \\u or \\U code point, which macOS bash 3.2 keeps as written"):
        results = hooks(home, tool, apart, "apart")
    with then("the gate can not tell which text the shell makes, so each call is unreadable"):
        for line, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (line, result.reason)
    with when("Claude commits with a $'...' message whose escapes both shells fill in the same way"):
        same = home.hook(tool, "PreToolUse", "git commit -m $'It\\'s \\\"fixed\\\"\\t\\x41\\101\\e\\?\\\\'",
                         session="same")
    with then("the commit runs"):
        assert not same.refused, same.reason
    with when("Claude pushes main with a program name written as a $\"...\" string"):
        locale_quoted = home.hook(tool, "PreToolUse", '$"git" push origin main', session="locale-quoted")
    with then("the gate drops the $ as bash does, reads the push and names the dash"):
        assert locale_quoted.refused and locale_quoted.findings == ["dashes"], locale_quoted.reason


def test_a_dollar_quoted_string_is_read_with_the_escapes_the_shell_fills_in(home, tool):
    with given("a repo whose main has a new commit whose message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("commit", "-q", "--allow-empty", "-m", f"Late {DASH} change")
    with when("Claude pushes main between two echo commands, where the first echo's $'...' string holds an "
              "escaped quote and the second echo prints a quote"):
        hidden_push = home.hook(tool, "PreToolUse", "echo $'\\'' ; git push origin main ; echo \\'",
                                session="hidden-push")
    with then("the gate reads the push between them, as the shell runs it, and names the dash"):
        assert hidden_push.refused and hidden_push.findings == ["dashes"], hidden_push.reason
    messages = ["git commit -m $'Late \\xe2\\x80\\x94 change'", "git commit -m $'Late \\342\\200\\224 change'"]
    with when("Claude commits with a message whose dash is written as hex or octal escapes of its bytes in $'...'"):
        results = hooks(home, tool, messages, "escaped-dash")
    with then("the gate reads the dash the shell writes and names it"):
        for message, result in results.items():
            assert result.refused and result.findings == ["dashes"], (message, result.reason)
    with when("Claude commits with a plain $'...' message that has a new line and an escaped quote"):
        plain = home.hook(tool, "PreToolUse", "git commit -m $'It\\'s fixed\\n\\nNo dash here'", session="plain")
    with then("the commit runs"):
        assert not plain.refused, plain.reason


def commit_notes(home, text, message):
    with open(os.path.join(home.work, "notes.txt"), "w") as notes:
        notes.write(text)
    home.git("add", "notes.txt")
    home.git("commit", "-q", "-m", message)


def merge_side_into_main(home, resolved):
    """Merge branch side into main, where both changed the second line of notes.txt, and settle the conflict by
    writing resolved as that line."""
    home.run(["git", "merge", "-q", "side", "-m", "Merge side"], check=False)
    with open(os.path.join(home.work, "notes.txt"), "w") as notes:
        notes.write(f"one\n{resolved}\n")
    home.git("add", "notes.txt")
    home.git("commit", "-q", "--no-edit")


def test_a_merge_whose_conflict_is_settled_with_new_text_is_read_like_any_other_commit(home, tool):
    with given("a pushed main with notes.txt, then a branch side and main that each change its second line, and "
               "the PR test gate off"):
        pushed_repo(home)
        commit_notes(home, "one\ntwo\n", "Add notes")
        home.git("push", "-q", "origin", "main")
        home.git("checkout", "-q", "-b", "side")
        commit_notes(home, "one\nside two\n", "Side change")
        home.git("checkout", "-q", "main")
        commit_notes(home, "one\nmain two\n", "Main change")
        before_merge = home.git("rev-parse", "HEAD").stdout.strip()
    with and_("side merged into main, with the conflict settled by a line that has a dash"):
        merge_side_into_main(home, f"Settled {DASH} line")
    with when("Claude pushes main"):
        dashed = home.hook(tool, "PreToolUse", "git push origin main", session="merge-dash")
    with then("the gate reads the text the merge commit itself adds and names the dash"):
        assert dashed.refused and dashed.findings == ["dashes"], dashed.reason
    with when("main goes back to before the merge, merges side again with a plain line, and Claude pushes it"):
        home.git("reset", "-q", "--hard", before_merge)
        merge_side_into_main(home, "Settled line")
        plain = home.hook(tool, "PreToolUse", "git push origin main", session="merge-plain")
    with then("the push runs"):
        assert not plain.refused, plain.reason


def test_a_push_that_may_send_more_than_the_branches_it_names_is_unreadable_and_tags_are_read(home, tool):
    with given("a repo whose main has a clean new commit, a branch draft and a tag v2 at a new commit whose "
               "message has a dash, and the PR test gate off"):
        pushed_repo(home)
        home.git("checkout", "-q", "-b", "draft")
        home.git("commit", "-q", "--allow-empty", "-m", f"Draft {DASH} change")
        home.git("tag", "v2")
        home.git("checkout", "-q", "main")
    every = ["git push --all", "git push --all origin", "git push --branches origin", "git push --mirror origin",
             "git push --mirr origin", "git push origin :", "git push origin +:", "git push origin main :"]
    with when("Claude pushes every branch, mirrors the repo, or pushes the branches that match on the remote"):
        results = hooks(home, tool, every, "every")
    with then("the gate can not tell which commits go out and refuses each one as unreadable"):
        for push, result in results.items():
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)
    settings = [("push.default", "matching"), ("remote.origin.push", "refs/heads/draft:refs/heads/draft"),
                ("remote.origin.mirror", "true")]
    with when("the repo's settings send more than the current branch, and Claude pushes with no branch named"):
        results = {}
        for index, (key, value) in enumerate(settings):
            home.git("config", key, value)
            results[key] = [home.hook(tool, "PreToolUse", push, session=f"setting-{index}-{position}")
                            for position, push in enumerate(["git push", "git push origin"])]
            named = home.hook(tool, "PreToolUse", "git push origin main", session=f"named-{index}")
            home.git("config", "--unset", key)
            assert named.ran, (key, named.reason)
        results["-c"] = [home.hook(tool, "PreToolUse", "git -c push.default=matching push", session="one")]
    with then("each push with no branch named is refused as unreadable, and a push that names main runs"):
        for key, found in results.items():
            for result in found:
                assert result.refused and result.findings == ["unreadable"], (key, result.reason)
    with when("an old-style remotes file names remote old, and Claude pushes to it with no branch named"):
        os.makedirs(os.path.join(home.work, ".git", "remotes"))
        with open(os.path.join(home.work, ".git", "remotes", "old"), "w") as handle:
            handle.write(f"URL: {os.path.join(home.root, 'remote.git')}\nPush: refs/heads/draft:refs/heads/draft\n")
        from_file = home.hook(tool, "PreToolUse", "git push old", session="file")
        os.remove(os.path.join(home.work, ".git", "remotes", "old"))
    with then("it is refused as unreadable"):
        assert from_file.refused and from_file.findings == ["unreadable"], from_file.reason
    with when("Claude pushes every tag, and pushes with settings that send only the current branch, then with no "
              "push setting left"):
        tags = hooks(home, tool, ["git push --tags", "git push origin --tags"], "tags")
        home.git("config", "remote.origin.mirror", "false")
        plain = hooks(home, tool, ["git push", "git -c user.name=Ann push", "git push origin"], "plain")
        home.git("config", "--unset", "remote.origin.mirror")
        unset = hooks(home, tool, ["git push", "git push origin"], "unset")
    with then("the tag pushes are refused for v2's dash, and the others run, since main's new commit is clean, also "
              "once the repo has no push setting at all"):
        for push, result in tags.items():
            assert result.refused and result.findings == ["dashes"], (push, result.reason)
        for push, result in [*plain.items(), *unset.items()]:
            assert result.ran, (push, result.reason)


def test_a_push_that_git_runs_on_another_repo_is_unreadable(home, tool):
    with given("a repo whose main has a clean new commit, a second repo whose new commit has a dash, and the PR "
               "test gate off"):
        pushed_repo(home)
        other = os.path.join(home.root, "other")
        home.git("init", "-q", "-b", "main", other)
        home.git("commit", "-q", "--allow-empty", "-m", f"Other {DASH} change", cwd=other)
    elsewhere = [f"git --git-dir={other}/.git push origin main", f"git --git-dir {other}/.git push",
                 f"git --work-tree={other} push", f"git --work-tree {other} push origin main",
                 "git --namespace=other push origin main", "git --namespace other push",
                 f"git --bare push {other}/.git main",
                 f"GIT_DIR={other}/.git git push origin main", f"GIT_WORK_TREE={other} git push",
                 f"GIT_COMMON_DIR={other}/.git git push", "GIT_NAMESPACE=other git push origin main",
                 f"GIT_OBJECT_DIRECTORY={other}/.git/objects git push",
                 f"GIT_CONFIG_GLOBAL={other}/.gitconfig git push origin main",
                 f"export GIT_DIR={other}/.git; git push origin main",
                 f"env GIT_DIR={other}/.git git push origin main",
                 f"GIT_DIR={other}/.git bash -c 'git push origin main'"]
    with when("Claude pushes with a git option or a variable that points git at another repo or namespace"):
        results = hooks(home, tool, elsewhere, "elsewhere")
    with then("the gate can not read that repo's commits from this folder and refuses each one as unreadable"):
        for push, result in results.items():
            assert result.refused and "unreadable" in result.findings, (push, result.reason)
    with when("Claude pushes main plainly"):
        plain = home.hook(tool, "PreToolUse", "git push origin main", session="plain")
    with then("it runs, since main's new commit is clean"):
        assert plain.ran, plain.reason


def empty_commits(home, count):
    """count empty commits on top of main, written at once by git fast-import, since one git commit each is slow."""
    commits = [f"commit refs/heads/main\ncommitter Ann <ann@example.com> {1_700_000_000 + number} +0000\n"
               f"data {len(f'Change {number}')}\nChange {number}\n" + ("from refs/heads/main^0\n" if number == 0 else "")
               for number in range(count)]
    subprocess.run(["git", "fast-import", "--quiet"], cwd=home.work, env=home.env, input="".join(commits), text=True,
                   check=True)


def test_a_push_too_long_to_read_in_full_is_refused_and_asked_to_go_in_smaller_parts(home, tool):
    with given("a repo whose main has 1,000 clean new commits, and the PR test gate off"):
        pushed_repo(home)
        empty_commits(home, 999)
    with when("Claude pushes main"):
        most = home.hook(tool, "PreToolUse", "git push origin main", session="most")
    with then("it runs, since the gate read every commit"):
        assert most.ran, most.reason
    with when("main gets one more commit, and Claude pushes it"):
        home.git("commit", "-q", "--allow-empty", "-m", "One more")
        many = home.hook(tool, "PreToolUse", "git push origin main", session="many")
    with then("it is refused, since the gate reads at most 1,000 commits, and asked to push fewer commits at a time, "
              "with a large commit left to the user's own switch"):
        assert many.refused and many.findings == ["unreadable"], many.reason
        assert "Push fewer commits at a time" in many.reason, many.reason
        assert 'only the user can let it through, by saying "turn off public_text_gate.unreadable"' in many.reason
        assert "do not change the setting yourself" in many.reason, many.reason
        assert gate_text(home, "unreadable_hint.md") not in many.reason
    with when("main's first 1,000 commits are pushed, and the next commit adds a file longer than the gate reads"):
        home.git("push", "-q", "origin", "main~1:main")
        with open(os.path.join(home.work, "long.txt"), "w") as handle:
            handle.write("plain line\n" * 20_000)
        home.git("add", "long.txt")
        home.git("commit", "-q", "-m", "Add a long file")
        long_change = home.hook(tool, "PreToolUse", "git push origin main", session="long")
    with then("it is refused the same way"):
        assert long_change.refused and long_change.findings == ["unreadable"], long_change.reason
        assert "Push fewer commits at a time" in long_change.reason, long_change.reason
    with when("the next commit adds a file twenty times longer than the gate reads, and Claude pushes main"):
        with open(os.path.join(home.work, "huge.txt"), "w") as handle:
            handle.write("plain line\n" * 400_000)
        home.git("add", "huge.txt")
        home.git("commit", "-q", "-m", "Add a huge file")
        started = time.monotonic()
        huge_change = home.hook(tool, "PreToolUse", "git push origin main", session="huge")
        took = time.monotonic() - started
    with then("the gate stops git once it has read past its limit and refuses the same way, at once, not as too "
              "slow"):
        assert huge_change.refused and huge_change.findings == ["unreadable"], huge_change.reason
        assert "Push fewer commits at a time" in huge_change.reason, huge_change.reason
        assert "git did not list the commits of this push in time" not in huge_change.reason, huge_change.reason
        assert took < 6, took
    remote = os.path.join(home.root, "remote.git")
    with when("Claude pushes main by the remote's folder and by its file URL, which have no remote-tracking "
              "branches, and to origin after its remote-tracking branches are gone"):
        by_place = hooks(home, tool, [f"git push {remote} main~1:main", f"git push file://{remote} main~1:main"],
                         "place")
        home.git("update-ref", "-d", "refs/remotes/origin/main")
        unfetched = home.hook(tool, "PreToolUse", "git push origin main~1:main", session="unfetched")
    with then("each is refused, since every commit of main counts as new, and asked to add the remote by name and "
              "fetch it first"):
        for push, result in [*by_place.items(), ("unfetched", unfetched)]:
            assert result.refused and result.findings == ["unreadable"], (push, result.reason)
            assert "Add the remote by name and fetch it first" in result.reason, (push, result.reason)
            assert "Push fewer commits at a time" not in result.reason, (push, result.reason)
            assert gate_text(home, "unreadable_hint.md") not in result.reason, (push, result.reason)


def lock_held(path, seconds):
    """Another process that holds the lock file for that many seconds, as a second call of the same chat does while
    its own hook runs. It returns once the lock is held."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    holder = subprocess.Popen([sys.executable, "-I", "-c",
                               "import fcntl, sys, time; handle = open(sys.argv[1], 'a'); "
                               "fcntl.flock(handle, fcntl.LOCK_EX); print('held', flush=True); "
                               "time.sleep(float(sys.argv[2]))", path, str(seconds)],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def test_a_push_gets_only_the_time_the_hook_has_left_after_earlier_parts_and_earlier_pushes(home):
    slow_note = "git did not list the commits of this push in time"
    with given("a repo whose main has a clean new commit, the PR test gate off, the context budget on, a chat "
               "transcript, and a git whose log takes 5 seconds"):
        pushed_repo(home)
        home.hubctl("on", "context_budget")
        transcript = os.path.join(home.root, "chat.jsonl")
        open(transcript, "w").close()
        kept = home.env["PATH"]
        home.env["PATH"] = git_log_waits(home, 5)
    try:
        with when("Claude pushes main, then pushes it again in a script a shell reads from a heredoc"):
            started = time.monotonic()
            twice = home.hook("claude_code", "PreToolUse",
                              "git push origin main\nbash <<'EOF'\ngit push origin main\nEOF", session="twice")
            took = time.monotonic() - started
        with then("the second push gets only what the first left of the gate's 8 seconds, so it is refused as too "
                  "slow, inside the hook's time"):
            assert twice.refused and twice.findings == ["unreadable"], twice.reason
            assert slow_note in twice.reason, twice.reason
            assert took < 9.5, took
        with when("another call of the same chat holds the context budget's lock for 5 seconds, and Claude pushes "
                  "main once"):
            holder = lock_held(os.path.join(home.state, "context_budget", "budget.json.lock"), 5)
            data = home.hook_input("claude_code", "PreToolUse", "git push origin main", session="budget")
            data["transcript_path"] = transcript
            started = time.monotonic()
            waited = home.send("claude_code", "PreToolUse", data)
            took = time.monotonic() - started
            holder.wait()
        with then("the gate gets only the 3 seconds the hook has left after the context budget waited, so git log "
                  "is too slow, and the answer still comes inside the hook's time"):
            assert waited.refused and waited.findings == ["unreadable"], waited.reason
            assert slow_note in waited.reason, waited.reason
            assert took < 9.5, took
    finally:
        home.env["PATH"] = kept


def test_a_commit_whose_message_file_sits_in_a_folder_the_shell_fills_in_is_refused_as_unreadable_without_a_crash(home, tool):
    with given("a repo with a message file in it"):
        home.install()
        with open(os.path.join(home.root, "body.txt"), "w") as handle:
            handle.write("Plain change\n")
    commits = ["git -C $DIR commit -F body.txt", "git -C $(pwd) commit -F body.txt"]
    with when("Claude commits with that message file, inside a folder that comes from a variable or a substitution"):
        results = {commit: home.hook(tool, "PreToolUse", commit) for commit in commits}
    with then("the gate can not read the message before the command runs and refuses each one as unreadable"):
        for commit, result in results.items():
            assert result.refused and result.findings == ["unreadable"], commit


def test_a_caller_that_needs_only_the_steps_can_skip_reading_the_pushed_commits():
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        with given("a git first on PATH that logs each call before it runs the real git"):
            log = os.path.join(folder, "calls.txt")
            real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
            with open(os.path.join(folder, "git"), "w") as handle:
                handle.write(f"#!/bin/sh\necho \"$*\" >> '{log}'\nexec '{real}' \"$@\"\n")
            os.chmod(os.path.join(folder, "git"), 0o755)
            code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import importlib.util as u; "
                    "spec = u.spec_from_file_location('gate', sys.argv[2]); gate = u.module_from_spec(spec); "
                    "spec.loader.exec_module(gate); "
                    "print(json.dumps(gate.public_steps('git push origin main && bash -c \"git push\"', "
                    "sys.argv[3], log=sys.argv[4] == 'yes')))")
            env = dict(os.environ, PATH=folder + os.pathsep + os.environ["PATH"])

            def steps(log_too):
                return json.loads(subprocess.run(
                    [sys.executable, "-I", "-c", code, os.path.join(PLUGIN, "lib"),
                     os.path.join(PLUGIN, "parts", GATE, "handler.py"), folder, "yes" if log_too else "no"],
                    env=env, capture_output=True, text=True, check=True).stdout)
        with when("the gate's parser reads two pushes, one inside a wrapper, without the log"):
            skipped = steps(False)
        with then("it names both pushes and never runs git"):
            assert [target for target, _text in skipped] == ["push", "push"]
            assert not os.path.exists(log)
        with when("it reads them with the log, as the gate itself does"):
            steps(True)
        with then("git runs for each push: it counts the commits of the named push, which fails here outside a repo, "
                  "so no log follows, and reads the settings of the push with no branch named"):
            with open(log) as handle:
                assert [line.split()[0] for line in handle] == ["rev-list", "config", "rev-parse"]


def test_pr_and_issue_texts_are_checked_by_their_own_targets(home, tool):
    with given("hub is installed with the em dash check on and the PR test gate off, and a PR body file holds a dash"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        home.hubctl_from_mod("off", "pr_test_gate")
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
