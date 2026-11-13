"""Slice 2: start text and links.

Every scenario runs the real hubctl, the dispatcher, the slot hooks and the
Cursor adapter as processes in a temporary HOME. Links and copies land in
that HOME's ~/.claude and ~/.cursor folders.
"""
import json
import os
import shutil
import subprocess
import sys

from conftest import PLUGIN, REPO, load_fixture
from steps import and_, given, then, when

SLOTS = 8
SLOT_LIMIT = 10_000
MARKER = "<!-- hub:"


# helpers

def agent_link(home, part):
    return os.path.join(home.root, ".claude", "agents", f"hub-{part}.md")


def cursor_agent(home, part):
    return os.path.join(home.root, ".cursor", "agents", f"hub-{part}.md")


def text_of(home, part, name):
    with open(os.path.join(home.plugin, "parts", part, "text", name)) as handle:
        return handle.read().strip()


def read(path):
    with open(path) as handle:
        return handle.read()


def front_matter(path):
    lines = read(path).split("---\n")[1].strip().splitlines()
    return dict(line.split(": ", 1) for line in lines)


def applies(home):
    path = os.path.join(home.state, "applies.jsonl")
    return len(read(path).splitlines()) if os.path.exists(path) else 0


def slot_files(home, tool):
    folder = os.path.join(home.state, "slots", tool)
    return [read(os.path.join(folder, f"{number}.md")) for number in range(1, SLOTS + 1)]


def hooks_in_order(home, session, first_script, *arguments):
    """Run the first named hook, then the dispatcher, for one Claude Code session start."""
    data = load_fixture("claude_code", "SessionStart.json")
    data.update(cwd=home.work, session_id=session)
    lib = os.path.join(home.plugin, "lib", "hub")
    for script, extra in ((first_script, arguments), ("dispatch.py", ("SessionStart",))):
        home.run([sys.executable, "-I", os.path.join(lib, script), *extra], stdin=json.dumps(data))
    assert not home.errors(), home.errors()


def session_start(home, tool="claude_code"):
    """Start every SessionStart hook of one tool at once. Returns (start text, dispatcher output)."""
    if tool == "cursor":
        data = load_fixture("cursor", "sessionStart.json")
        data["workspace_roots"] = [home.work]
        script = os.path.join(home.plugin, "lib", "hub", "adapters", "cursor_hooks.py")
        process = home.run([sys.executable, "-I", script, "sessionStart"], stdin=json.dumps(data))
        output = json.loads(process.stdout) if process.stdout.strip() else {}
        return output.get("additional_context", ""), output
    data = load_fixture("claude_code", "SessionStart.json")
    data["cwd"] = home.work
    lib = os.path.join(home.plugin, "lib", "hub")
    commands = [[sys.executable, "-I", os.path.join(lib, "dispatch.py"), "SessionStart"]]
    commands += [[sys.executable, "-I", os.path.join(lib, "slot_hook.py"), str(number)]
                 for number in range(1, SLOTS + 1)]
    processes = [subprocess.Popen(command, cwd=home.work, env=home.env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for command in commands]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(json.dumps(data), timeout=60)
        assert process.returncode == 0, stderr
        outputs.append(json.loads(stdout) if stdout.strip() else {})
    assert not home.errors(), home.errors()
    texts = [output.get("hookSpecificOutput", {}).get("additionalContext", "") for output in outputs[1:]]
    return "\n\n".join(text for text in texts if text), outputs[0]


def tier_texts(home):
    names = ["delegate_when", "higher_tier_when_unsure", "rerun_one_up", "check_claims",
             "public_needs_yes", "description_prefix"]
    return {name: text_of(home, "tier_rule", f"{name}.md") for name in names}


def add_part(home, part_id, data, files):
    folder = os.path.join(home.plugin, "parts", part_id)
    os.makedirs(folder)
    for name, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(folder, name)), exist_ok=True)
        with open(os.path.join(folder, name), "w") as handle:
            handle.write(text)
    home.write_json(os.path.join(folder, "part.json"), dict({"id": part_id}, **data))


def write_profile(home, profile):
    home.write_json(os.path.join(home.config, "profile.json"), profile)


def use_cursor_beta(home):
    """The profile lists Cursor, and the user turned on the Cursor beta (cursor_link), which is off by default."""
    write_profile(home, {"tools": {"cursor": True}})
    data = json.loads(read(home.user_file)) if os.path.exists(home.user_file) else {"version": 1, "flags": {}}
    data.setdefault("flags", {})["cursor_link"] = {"by": "user", "value": "on"}
    home.write_json(home.user_file, data)


# scenarios

def test_turning_off_one_tier_rule_sentence_removes_only_that_sentence_from_the_start_text(home):
    with given("hub is installed with scout, and the user turned builder and the tier rule on"):
        home.switch_on("builder", "tier_rule")
        home.install()
        sentences = tier_texts(home)
        before, _ = session_start(home)
        assert all(text in before for text in sentences.values())
        shown_before = home.show()["start_text"]
        count = applies(home)
    with when('the user runs "hubctl off tier_rule.check_claims"'):
        home.hubctl("off", "tier_rule.check_claims")
    with and_("a SessionStart starts the dispatcher and the eight slot hooks at once"):
        after, _ = session_start(home)
    with then("the slots hold every other tier rule sentence, whole and in tree order"):
        assert sentences["check_claims"] not in after
        kept = [text for name, text in sentences.items() if name != "check_claims"]
        positions = [after.index(text) for text in kept]
        assert positions == sorted(positions)
    with and_('apply ran once, and the start text in "hubctl show --json" is shorter by that sentence'):
        assert applies(home) == count + 1
        shown_after = home.show()["start_text"]
        assert shown_after == after
        assert sentences["check_claims"] in shown_before and sentences["check_claims"] not in shown_after
        assert len(shown_before) - len(shown_after) >= len(sentences["check_claims"])
    with when("the user edits the settings file by hand and nine hooks start at once"):
        data = home.settings()
        data["flags"]["tier_rule.rerun_one_up"] = {"by": "user", "value": "off"}
        home.write_json(home.user_file, data)
        count = applies(home)
        edited, _ = session_start(home)
    with then("the first hook applies once, and every slot hook prints the new set"):
        assert applies(home) == count + 1
        assert sentences["rerun_one_up"] not in edited
        assert edited == home.show()["start_text"]


def test_tier_rows_follow_the_agents_that_run(home):
    with given("scout runs, and the user turned builder and the tier rule on"):
        home.switch_on("builder", "tier_rule")
        home.install()
        text, _ = session_start(home)
    with then("the table has the mechanical and judgement rows only"):
        rows = [line for line in text.splitlines() if line.startswith("| ") and "---" not in line][1:]
        assert [row.split("|")[1].strip() for row in rows] == ["Mechanical", "Judgement"]
        assert "| scout | haiku |" in rows[0] and "| builder | opus | high |" in rows[1]
    with when("the user sets builder.model to sonnet"):
        home.hubctl("set", "builder.model", "sonnet")
        text, _ = session_start(home)
    with then("the judgement row names sonnet at the next session start"):
        assert "| builder | sonnet | high |" in text
    with when("the user turns builder off"):
        home.hubctl("off", "builder")
        text, _ = session_start(home)
    with then("the judgement row and ~/.claude/agents/hub-builder.md are gone"):
        assert "Judgement" not in text and "| Mechanical |" in text
        assert not os.path.lexists(agent_link(home, "builder"))
        assert os.path.islink(agent_link(home, "scout"))


def test_a_changed_agent_gets_a_built_copy_and_a_new_link_the_folder_watch_sees(home):
    with given("~/.claude/agents/hub-builder.md links to the shipped file, since the user turned builder on"):
        home.switch_on("builder")
        home.install()
        link = agent_link(home, "builder")
        shipped = os.path.join(home.plugin, "parts", "builder", "agent.md")
        assert os.readlink(link) == shipped
        inode = os.lstat(link).st_ino
        assert "builder.instructions.test_first" in read(shipped)
    with when("the user turns off builder.instructions.test_first"):
        home.hubctl("off", "builder.instructions.test_first")
    with then("the link points at the built copy, with a new inode"):
        target = os.readlink(link)
        assert target == os.path.join(home.state, "built", "agents", "builder.md")
        assert os.lstat(link).st_ino != inode
    with and_("the copy lacks that section and every marker"):
        copy = read(target)
        assert MARKER not in copy
        removed = read(shipped).split("<!-- hub:section builder.instructions.test_first -->\n")[1]
        assert removed.split("<!-- hub:end -->")[0].strip() not in copy
        assert front_matter(target)["name"] == "builder"
        kept = read(shipped).split("<!-- hub:section builder.instructions.review_own_diff -->\n")[1]
        assert kept.split("<!-- hub:end -->")[0].strip() in copy
    with when("the user sets builder.effort to medium"):
        home.hubctl("set", "builder.effort", "medium")
    with then("the copy's front matter carries the chosen value"):
        assert front_matter(link)["effort"] == "medium"
    with when("the user turns the section on again and sets the effort back"):
        home.hubctl("on", "builder.instructions.test_first")
        home.hubctl("set", "builder.effort", "high")
    with then("the link points at the shipped file"):
        assert os.readlink(link) == shipped


def test_apply_never_replaces_a_file_that_is_not_its_own_link(home):
    with given("the user's own file sits at ~/.claude/agents/hub-scout.md"):
        path = agent_link(home, "scout")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("---\nname: my-scout\ndescription: mine\n---\n\nMy own notes.\n")
    with when("the user turns builder on and apply runs"):
        home.switch_on("builder")
        home.install()
        result = home.hubctl("apply")
    with then("the file is unchanged, one line names it, and the record has no entry for it"):
        assert not os.path.islink(path) and "My own notes." in read(path)
        lines = [line for line in result.stdout.splitlines() if path in line]
        assert len(lines) == 1
        assert path not in [entry["path"] for entry in home.state_file("links.json")["links"]]
        assert os.path.islink(agent_link(home, "builder"))


def replace_with_own_link(home, path, name):
    """Put the user's own link at path, pointing at a file of the user's outside hub."""
    mine = os.path.join(home.root, "my-agents", name)
    os.makedirs(os.path.dirname(mine), exist_ok=True)
    with open(mine, "w") as handle:
        handle.write(f"---\nname: {name}\ndescription: mine\n---\n\nMine.\n")
    os.unlink(path)
    os.symlink(mine, path)
    return mine


def test_a_users_own_link_or_file_at_a_recorded_path_is_never_replaced_or_deleted(home):
    cursor_folder = os.path.join(home.root, ".cursor", "plugins", "local", "hub")
    notes = os.path.join(cursor_folder, "my-notes.md")
    with given("hub is installed with Cursor and builder on, so the record holds its links and its Cursor folder"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.switch_on("builder")
        home.install()
        recorded = {entry["path"] for entry in home.state_file("links.json")["links"]}
        assert {agent_link(home, "builder"), agent_link(home, "scout"), cursor_folder} <= recorded
    with when("the user puts a link of their own at ~/.claude/agents/hub-builder.md and builder changes"):
        builder = replace_with_own_link(home, agent_link(home, "builder"), "builder")
        home.hubctl("set", "builder.model", "sonnet")
    with then("the user's link still points at the user's file"):
        assert os.readlink(agent_link(home, "builder")) == builder
    with when("the user puts a link of their own at ~/.claude/agents/hub-scout.md and turns scout off"):
        scout = replace_with_own_link(home, agent_link(home, "scout"), "scout")
        home.hubctl("off", "scout")
    with then("the user's link is still there"):
        assert os.readlink(agent_link(home, "scout")) == scout
    with when("the user adds a file of their own to hub's Cursor folder and uninstalls hub"):
        with open(notes, "w") as handle:
            handle.write("my notes\n")
        result = home.hubctl("uninstall")
    with then("the user's links and file are unchanged, and hub's own Cursor files are gone"):
        assert os.readlink(agent_link(home, "builder")) == builder
        assert os.readlink(agent_link(home, "scout")) == scout
        assert read(notes) == "my notes\n"
        assert sorted(os.listdir(cursor_folder)) == ["my-notes.md"]
    with and_("uninstall names the folder it left"):
        assert len([line for line in result.stdout.splitlines() if cursor_folder in line]) == 1


def test_a_users_agent_of_the_same_name_holds_the_hub_link_with_an_offer_to_switch(home):
    mine = os.path.join(home.root, ".claude", "agents", "builder.md")
    with given("the user's ~/.claude/agents/builder.md has the name builder"):
        os.makedirs(os.path.dirname(mine))
        with open(mine, "w") as handle:
            handle.write("---\nname: builder\ndescription: My builder\n---\n\nMine.\n")
    with when("the user turns hub's builder on and apply runs"):
        home.switch_on("builder")
        home.install()
        result = home.hubctl("apply")
    with then("no hub-builder link is made, one line says why, and the offer is listed"):
        assert not os.path.lexists(agent_link(home, "builder"))
        assert len([line for line in result.stdout.splitlines() if mine in line]) == 1
        offers = home.hubctl("offers").stdout
        assert "builder" in offers and mine in offers
    with when("the user takes the offer"):
        home.hubctl("take", "builder")
    with then("the user's file is in the held folder and the hub link is there"):
        held = os.path.join(home.state, "held", "agents", "builder.md")
        assert not os.path.exists(mine) and read(held).endswith("Mine.\n")
        assert os.path.islink(agent_link(home, "builder"))
    with when("the user undoes it"):
        home.hubctl("undo")
    with then("the user's file is back and the hub link is gone"):
        assert read(mine).endswith("Mine.\n") and not os.path.exists(held)
        assert not os.path.lexists(agent_link(home, "builder"))


def test_a_deleted_hub_link_becomes_a_pinned_off_with_one_line(home):
    with given("hub is installed with scout on"):
        home.install()
    with when("the user deletes ~/.claude/agents/hub-scout.md and apply runs"):
        os.unlink(agent_link(home, "scout"))
        result = home.hubctl("apply")
    with then("scout is off by user, no link comes back, and one line says so"):
        assert home.settings()["flags"]["scout"] == {"by": "user", "value": "off"}
        assert not os.path.lexists(agent_link(home, "scout"))
        assert len([line for line in result.stdout.splitlines() if "scout" in line]) == 1
        home.hubctl("apply")
        assert not os.path.lexists(agent_link(home, "scout"))


def test_a_hub_link_deleted_between_sessions_is_seen_at_the_next_session_start(home):
    with given("hub is installed with scout on"):
        home.install()
    with when("the user deletes the scout link and a session starts"):
        os.unlink(agent_link(home, "scout"))
        text, _ = session_start(home)
    with then("scout is off by user and its row is gone"):
        assert home.settings()["flags"]["scout"] == {"by": "user", "value": "off"}
        assert "Mechanical" not in text


def test_text_that_needs_more_than_eight_slots_makes_the_doctor_warn(home):
    pieces = {f"piece_{number:02d}": f"Piece {number:02d}. " + "x" * 7_500 for number in range(12)}
    with given("a fixture part in the test's plugin copy with 90,000 characters of start text"):
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        children = [{"id": name, "kind": "instruction", "cost": "start text", "default": "on",
                     "text": f"text/{name}.md", "does": "A long fixture text."} for name in pieces]
        add_part(home, "bulk", {"family": "rules", "kind": "rule", "default": "on", "does": "Fixture.",
                                "children": children},
                 {f"text/{name}.md": text for name, text in pieces.items()})
        assert sum(len(text) for text in pieces.values()) >= 90_000
    with when("apply runs"):
        home.hubctl("apply")
    with then("every slot holds whole instructions under 10,000 characters"):
        slots = slot_files(home, "claude_code")
        assert all(len(slot) < SLOT_LIMIT for slot in slots)
        joined = "\n".join(slots)
        placed = [name for name, text in pieces.items() if text in joined]
        for name, text in pieces.items():
            assert joined.count(text[:12]) == (1 if name in placed else 0)
    with and_("the doctor names each instruction that did not fit"):
        doctor = home.hubctl("doctor").stdout
        left_out = [name for name in pieces if name not in placed]
        assert left_out and left_out == sorted(left_out)
        assert left_out[0] == f"piece_{12 - len(left_out):02d}"
        for name in pieces:
            assert (f"bulk.{name}" in doctor) == (name in left_out)


def test_cursor_gets_the_same_parts_with_mapped_models(home):
    with given("a profile that lists Cursor, with the Cursor beta, builder and the tier rule on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.switch_on("builder", "tier_rule")
    with when("apply runs"):
        home.install()
    with then("~/.cursor/plugins/local/hub is hub's own folder that calls the installed copy"):
        folder = os.path.join(home.root, ".cursor", "plugins", "local", "hub")
        assert os.path.isdir(folder) and not os.path.islink(folder)
        manifest = json.loads(read(os.path.join(folder, ".cursor-plugin", "plugin.json")))
        hooks = json.loads(read(os.path.join(folder, manifest["hooks"])))
        commands = [entry["command"] for entries in hooks["hooks"].values() for entry in entries]
        assert set(hooks["hooks"]) == {"preToolUse", "postToolUse", "sessionStart", "stop", "subagentStop"}
        assert all(os.path.join(home.plugin, "lib", "hub", "adapters", "cursor_hooks.py") in command
                   for command in commands)
    with and_("~/.cursor/agents/hub-builder.md names the mapped model"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-opus-5[effort=high]"
        assert front_matter(cursor_agent(home, "scout"))["name"] == "scout"
        assert MARKER not in read(cursor_agent(home, "builder"))
    with and_("Cursor's sessionStart returns its own slot set, without tier_rule.description_prefix"):
        text, _ = session_start(home, "cursor")
        sentences = tier_texts(home)
        assert sentences["check_claims"] in text
        assert sentences["description_prefix"] not in text
        claude_text, _ = session_start(home)
        assert sentences["description_prefix"] in claude_text
    with when("the user sets builder.model to sonnet"):
        home.hubctl("set", "builder.model", "sonnet")
    with then("the Cursor copy follows"):
        assert front_matter(cursor_agent(home, "builder"))["model"] == "claude-sonnet-5[effort=high]"
    with and_("every Cursor file is in the link record"):
        recorded = {entry["path"] for entry in home.state_file("links.json")["links"]}
        assert {folder, cursor_agent(home, "builder"), cursor_agent(home, "scout")} <= recorded


def test_the_repo_cursor_files_match_what_cursor_link_writes():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cursor_link_handler", os.path.join(PLUGIN, "parts", "cursor_link", "handler.py"))
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    with given("the files cursor_link renders, with the repo's relative path"):
        rendered = handler.plugin_files(PLUGIN, "./plugins/hub")
        assert {".cursor-plugin/plugin.json", "cursor/hooks.json"} <= set(rendered)
    with then("each one is in the repo root with the same content"):
        for path, text in rendered.items():
            with open(os.path.join(REPO, *path.split("/"))) as handle:
                shipped = handle.read()
            if path.endswith(".json"):
                assert json.loads(text) == json.loads(shipped), path
            else:
                assert text == shipped, path


def test_the_cursor_manifest_leaves_out_what_only_claude_codes_mod_reads():
    sys.path.insert(0, os.path.join(PLUGIN, "lib"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cursor_link_handler", os.path.join(PLUGIN, "parts", "cursor_link", "handler.py"))
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    with given("Claude Code's manifest names the mod's types file"):
        with open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")) as handle:
            claude_manifest = json.load(handle)
        assert claude_manifest["types"] == "./mod/types/index.d.ts"
    with when("cursor_link renders the Cursor manifest"):
        rendered = json.loads(handler.plugin_files(PLUGIN, "./plugins/hub")[".cursor-plugin/plugin.json"])
    with then("it has no types key, because Cursor runs no mods, and keeps the rest"):
        assert "types" not in rendered
        assert {key: value for key, value in claude_manifest.items() if key not in ("types", "description")} == {
            key: value for key, value in rendered.items() if key not in ("hooks", "skills", "description")}
    with and_("its description is Claude Code's, which already says Cursor support is beta, once"):
        assert rendered["description"] == claude_manifest["description"]
        assert rendered["description"].count("Cursor support is in beta and untested.") == 1


def test_after_an_update_every_link_points_at_the_new_copy(home):
    second = os.path.join(home.root, "plugin-copy-2", "hub")
    with given("links and built copies made from a first plugin copy, with builder on"):
        home.switch_on("builder")
        home.install()
        home.hubctl("off", "builder.instructions.test_first")
        assert os.readlink(agent_link(home, "scout")).startswith(home.plugin + os.sep)
    with when("a second copy is installed and apply runs"):
        shutil.copytree(home.plugin, second)
        path = os.path.join(second, "parts", "builder", "agent.md")
        with open(path, "a") as handle:
            handle.write("\nA line only the second copy has.\n")
        home.plugin = second
        home.hubctl("apply")
    with then("every recorded link points into the second copy, and every built copy is built again from it"):
        for entry in home.state_file("links.json")["links"]:
            if entry["type"] == "link" and not entry["target"].startswith(home.state):
                assert entry["target"].startswith(second + os.sep), entry
                assert os.readlink(entry["path"]) == entry["target"]
        assert "A line only the second copy has." in read(agent_link(home, "builder"))


def test_the_dispatcher_asks_for_a_skill_reload_after_apply_changed_a_skill_link(home):
    with given("a skill part in the test's plugin copy, switched off"):
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        add_part(home, "notes", {"family": "learning", "kind": "skill", "default": "off",
                                 "does": "A fixture skill."},
                 {"skill/SKILL.md": "---\nname: hub-notes\ndescription: Fixture.\n---\n\nTake notes.\n"})
        home.hubctl("apply")
        _, first = session_start(home)
        _, settled = session_start(home)
    with then("the first session start asks once for the hub-pr skill that pr links by default, and the next for nothing"):
        assert first["hookSpecificOutput"]["reloadSkills"] is True
        assert "reloadSkills" not in json.dumps(settled)
    with when("the user turns the skill on and a session starts"):
        home.hubctl("on", "notes")
        _, second = session_start(home)
    with then("the dispatcher returns reloadSkills once, and the skill folder is linked"):
        assert second["hookSpecificOutput"]["reloadSkills"] is True
        link = os.path.join(home.root, ".claude", "skills", "hub-notes")
        assert os.readlink(link) == os.path.join(home.plugin, "parts", "notes", "skill")
        _, third = session_start(home)
        assert "reloadSkills" not in json.dumps(third)


def test_apply_makes_the_agents_folder_and_the_doctor_says_a_new_session_is_needed(home):
    with given("a HOME with no ~/.claude/agents"):
        assert not os.path.exists(os.path.join(home.root, ".claude", "agents"))
    with when("hub is installed"):
        home.install()
    with then("the folder exists and the doctor says agents show from the next session"):
        assert os.path.isdir(os.path.join(home.root, ".claude", "agents"))
        assert "new session" in home.hubctl("doctor").stdout
    with when("a session starts"):
        session_start(home)
    with then("the doctor no longer says it"):
        assert "new session" not in home.hubctl("doctor").stdout


def test_the_note_about_the_new_agents_folder_stays_for_the_session_whose_start_made_it(home):
    with given("hub in a HOME with no ~/.claude/agents, applied by nobody yet"):
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
    with when("the first session starts and a slot hook applies before the dispatcher runs"):
        hooks_in_order(home, "00000000-0000-4000-8000-0000000000a1", "slot_hook.py", "1")
    with then("the folder exists and the doctor still says agents show from the next session"):
        assert os.path.isdir(os.path.join(home.root, ".claude", "agents"))
        assert "new session" in home.hubctl("doctor").stdout
    with when("a different session starts"):
        hooks_in_order(home, "00000000-0000-4000-8000-0000000000a2", "slot_hook.py", "1")
    with then("the doctor no longer says it"):
        assert "new session" not in home.hubctl("doctor").stdout


def utf16(text):
    return len(text.encode("utf-16-le")) // 2


def test_a_slot_stays_under_the_limit_counted_in_utf16_units(home):
    pieces = {"wide_a": "A " + "\U0001F600" * 4_000, "wide_b": "B " + "\U0001F600" * 1_500}
    with given("two fixture texts of emoji, which are 5,500 characters but 11,000 UTF-16 units"):
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        children = [{"id": name, "kind": "instruction", "cost": "start text", "default": "on",
                     "text": f"text/{name}.md", "does": "A wide fixture text."} for name in pieces]
        add_part(home, "wide", {"family": "rules", "kind": "rule", "default": "on", "does": "Fixture.",
                                "children": children},
                 {f"text/{name}.md": text for name, text in pieces.items()})
    with when("apply runs"):
        home.hubctl("apply")
    with then("every slot is under 10,000 UTF-16 units, and the two texts sit in different slots"):
        slots = slot_files(home, "claude_code")
        assert all(utf16(slot) < SLOT_LIMIT for slot in slots)
        holding = [index for index, slot in enumerate(slots) if "A " + "\U0001F600" in slot or "B " + "\U0001F600" in slot]
        assert len(holding) == 2


def test_cursors_single_start_text_stays_under_the_limit(home):
    pieces = {f"piece_{number:02d}": f"Piece {number:02d}. " + "x" * 3_900 for number in range(5)}
    with given("a profile that lists Cursor, with the Cursor beta on and a fixture part with 19,500 characters of start text"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        children = [{"id": name, "kind": "instruction", "cost": "start text", "default": "on",
                     "text": f"text/{name}.md", "does": "A long fixture text."} for name in pieces]
        add_part(home, "bulk", {"family": "rules", "kind": "rule", "default": "on", "does": "Fixture.",
                                "children": children},
                 {f"text/{name}.md": text for name, text in pieces.items()})
    with when("apply runs and a Cursor session starts"):
        home.hubctl("apply")
        text, _ = session_start(home, "cursor")
    with then("the one string Cursor gets is under 10,000 UTF-16 units"):
        assert 0 < utf16(text) < SLOT_LIMIT
    with and_("the doctor names what Cursor left out, in a sentence about one string"):
        doctor = home.hubctl("doctor").stdout
        assert "in Cursor" in doctor and "one string" in doctor


def test_show_counts_the_listing_and_the_start_text(home):
    with given("hub is installed, and the user turned builder and the tier rule on"):
        home.switch_on("builder", "tier_rule")
        home.install()
    with when("the user runs hubctl show --json"):
        cost = home.show()["cost"]
    with then("the start text total equals the start text, and each agent has a listing cost"):
        assert cost["total"]["start_text"] == len(home.show()["start_text"])
        assert cost["parts"]["tier_rule"]["start_text"] > 0
        assert cost["parts"]["builder"]["listing"] > 0 and cost["parts"]["scout"]["listing"] > 0


def test_uninstall_removes_every_recorded_link_and_keeps_the_users_files(home):
    claude = os.path.join(home.root, ".claude")
    mine = {os.path.join(claude, "agents", "my-agent.md"): "---\nname: my-agent\n---\nMine.\n",
            os.path.join(claude, "skills", "my-skill", "SKILL.md"): "---\nname: my-skill\n---\nMine.\n",
            os.path.join(home.root, ".cursor", "agents", "my-cursor.md"): "---\nname: my-cursor\n---\nMine.\n"}
    settings_file = os.path.join(claude, "settings.json")
    with given("hub links, built copies and Cursor copies beside the user's own agent, skill and Cursor agent"):
        for path, text in mine.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(text)
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
        home.hubctl("off", "builder.instructions.test_first")
        recorded = [entry["path"] for entry in home.state_file("links.json")["links"]]
        assert len(recorded) >= 5 and all(os.path.lexists(path) for path in recorded)
    with and_("one held file in ~/.local/state/hubctl/held/, and two Claude Code values hub wrote"):
        held = os.path.join(home.state, "held", "agents", "old.md")
        os.makedirs(os.path.dirname(held))
        with open(held, "w") as handle:
            handle.write("held\n")
        home.write_json(settings_file, {"outputStyle": "hub", "theme": "mine", "other": 1})
        home.write_json(os.path.join(home.state, "keys.json"), {"keys": [
            {"file": settings_file, "key": "outputStyle", "hub_value": "hub", "earlier": "Explanatory",
             "had_earlier": True},
            {"file": settings_file, "key": "theme", "hub_value": "hub-theme", "earlier": None,
             "had_earlier": False}]})
    with when('the user runs "hubctl uninstall"'):
        result = home.hubctl("uninstall")
    with then("every recorded entry and hub's own state are gone, and the user's files are unchanged"):
        assert not [path for path in recorded if os.path.lexists(path)]
        assert sorted(os.listdir(home.state)) == ["held", "uninstalled.json"]
        for path, text in mine.items():
            assert read(path) == text
        assert os.path.isdir(home.config)
    with and_("the held file is still there"):
        assert read(held) == "held\n"
    with and_("only the values that still hold hub's value are put back"):
        assert json.loads(read(settings_file)) == {"outputStyle": "Explanatory", "theme": "mine", "other": 1}
    with and_("the last lines name ~/.config/hubctl/ and the held folder with the command that deletes each, "
              "and the plugin removal command"):
        tail = result.stdout.strip().splitlines()[-3:]
        assert f"rm -rf {home.config}" in tail[0]
        assert f"rm -rf {os.path.join(home.state, 'held')}" in tail[1]
        assert "claude plugin uninstall hub@hub" in tail[2]


def hub_entries(home):
    """Every hub- entry in the HOME's Claude Code and Cursor folders."""
    found = []
    for folder in (os.path.join(home.root, ".claude", "agents"), os.path.join(home.root, ".claude", "skills"),
                   os.path.join(home.root, ".cursor", "agents"), os.path.join(home.root, ".cursor", "plugins", "local")):
        if os.path.isdir(folder):
            found += [os.path.join(folder, name) for name in os.listdir(folder) if name.startswith("hub")]
    return sorted(found)


def test_uninstall_stays_until_a_new_install(home):
    with given("hub is installed with Cursor, and the claude program is on PATH"):
        home.record_claude()
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
        assert hub_entries(home)
    with when('the user runs "hubctl uninstall"'):
        result = home.hubctl("uninstall")
    with then("it removes hub's plugin through the claude program, and says so"):
        assert home.claude_calls() == [["plugin", "uninstall", "hub@hub"]]
        assert "claude plugin uninstall hub@hub" not in result.stdout
        assert "hub@hub" in result.stdout.strip().splitlines()[-1]
    with when("a tool call, a session start in each tool and hubctl apply all run after it"):
        pre_tool = home.hook("claude_code", "PreToolUse", "git status")
        text, dispatcher = session_start(home)
        cursor_text, _ = session_start(home, "cursor")
        again = home.hubctl("apply")
    with then("nothing comes back, and no hook says anything"):
        assert hub_entries(home) == []
        assert pre_tool.ran and pre_tool.output == {}
        assert text == "" and dispatcher == {} and cursor_text == ""
        assert sorted(os.listdir(home.state)) == ["uninstalled.json"]
        assert len(again.stdout.strip().splitlines()) == 1 and "uninstalled" in again.stdout
    with when("a new copy of hub is installed"):
        second = os.path.join(home.root, "plugin-copy-2", "hub")
        shutil.copytree(PLUGIN, second, ignore=shutil.ignore_patterns("__pycache__"))
        home.plugin = second
        home.hubctl("apply")
    with then("hub's links are back, and they point into the new copy"):
        assert os.readlink(agent_link(home, "scout")).startswith(second + os.sep)
        assert not os.path.exists(os.path.join(home.state, "uninstalled.json"))


def test_a_users_agent_of_the_same_name_keeps_hub_from_writing_its_cursor_copy(home):
    cursor_mine = os.path.join(home.root, ".cursor", "agents", "builder.md")
    claude_mine = os.path.join(home.root, ".claude", "agents", "my-scout.md")
    files = {cursor_mine: "---\nname: builder\ndescription: My Cursor builder\n---\n\nMine.\n",
             claude_mine: "---\nname: scout\ndescription: My scout\n---\n\nMine too.\n"}
    with given("a profile that lists Cursor, the user's Cursor agent builder and the user's Claude Code agent scout"):
        for path, text in files.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(text)
        os.makedirs(home.config)
        use_cursor_beta(home)
    with and_("the user turned hub's builder on"):
        home.switch_on("builder")
    with when("apply runs"):
        home.install()
        result = home.hubctl("apply")
    with then("hub writes no Cursor copy of either agent, and the user's files are unchanged"):
        assert not os.path.lexists(cursor_agent(home, "builder"))
        assert not os.path.lexists(cursor_agent(home, "scout"))
        for path, text in files.items():
            assert read(path) == text
        assert os.path.islink(agent_link(home, "builder"))
    with and_("apply names each clash in one line"):
        assert len([line for line in result.stdout.splitlines() if cursor_mine in line]) == 1
    with and_("hubctl doctor names each clash with the user's file"):
        doctor = [line for line in home.hubctl("doctor").stdout.splitlines() if line.startswith("warning:")]
        assert len([line for line in doctor if cursor_mine in line and "builder" in line]) == 1
        scout_lines = [line for line in doctor if claude_mine in line and "scout" in line]
        assert scout_lines and all("Cursor" in line or "Claude Code" in line for line in scout_lines)
