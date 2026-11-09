"""Slice 7: the working rules, the path rules, the output style, the writing
skill and the link check, each a part with its own switches in both tools."""
import json
import os
import re
import subprocess
import sys

from conftest import DASH, REPO, load_fixture
from steps import and_, given, then, when
from test_start_text_and_links import SLOT_LIMIT, read, session_start, slot_files, text_of, use_cursor_beta

STYLE_NAME = "hub-style"
EVAL = os.path.join(REPO, "evals", "shared_link_changes_nothing")


# helpers

def manifest(home, part):
    with open(os.path.join(home.plugin, "parts", part, "part.json")) as handle:
        return json.load(handle)


def rule_texts(home):
    """[(flag id, text)] of every working rule, in tree order."""
    found = []
    for behavior in manifest(home, "working_rules")["children"]:
        for rule in behavior.get("children", []):
            flag = f"working_rules.{behavior['id']}.{rule['id']}"
            found.append((flag, text_of(home, "working_rules", os.path.basename(rule["text"]))))
    return found


def claude_settings(home):
    return os.path.join(home.root, ".claude", "settings.json")


def style_link(home):
    return os.path.join(home.root, ".claude", "output-styles", f"{STYLE_NAME}.md")


def write_claude_settings(home, data, indent=4):
    path = claude_settings(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(json.dumps(data, indent=indent) + "\n")


def file_read(home, tool, path, session):
    """A PostToolUse for a read of path. Returns the context the hook added."""
    if tool == "claude_code":
        data = load_fixture("claude_code", "PostToolUse.Read.json")
        data.update(cwd=home.work, session_id=session, tool_use_id=f"toolu_{session}_{os.urandom(3).hex()}")
    else:
        data = load_fixture("cursor", "postToolUse.Read.json")
        data.update(cwd=home.work, workspace_roots=[home.work], conversation_id=session,
                    tool_use_id=f"call_{session}_{os.urandom(3).hex()}")
    data["tool_input"] = {"file_path": path}
    return hook_context(home, tool, "PostToolUse", data)


def file_edit(home, tool, path, session):
    if tool == "claude_code":
        data = load_fixture("claude_code", "PostToolUse.Read.json")
        data.update(cwd=home.work, session_id=session, tool_name="Edit",
                    tool_use_id=f"toolu_{session}_{os.urandom(3).hex()}")
    else:
        data = load_fixture("cursor", "postToolUse.Read.json")
        data.update(cwd=home.work, workspace_roots=[home.work], conversation_id=session, tool_name="Edit",
                    tool_use_id=f"call_{session}_{os.urandom(3).hex()}")
    data["tool_input"] = {"file_path": path, "old_string": "True", "new_string": "1 == 1"}
    return hook_context(home, tool, "PostToolUse", data)


def hook_output(home, tool, event, data):
    lib = os.path.join(home.plugin, "lib", "hub")
    if tool == "claude_code":
        args = [sys.executable, "-I", os.path.join(lib, "dispatch.py"), event]
    else:
        names = {"PostToolUse": "postToolUse", "Stop": "stop"}
        args = [sys.executable, "-I", os.path.join(lib, "adapters", "cursor_hooks.py"), names[event]]
    process = home.run(args, cwd=data.get("cwd"), stdin=json.dumps(data))
    assert not home.errors(), home.errors()
    return json.loads(process.stdout) if process.stdout.strip() else {}


def hook_context(home, tool, event, data):
    output = hook_output(home, tool, event, data)
    if tool == "claude_code":
        return output.get("hookSpecificOutput", {}).get("additionalContext", "")
    return output.get("additional_context", "")


def reply_ends(home, message, active=False):
    """A Claude Code Stop with the reply's text. Returns the reason it was sent back, or ""."""
    data = load_fixture("claude_code", "Stop.json")
    data.update(cwd=home.work, last_assistant_message=message, stop_hook_active=active)
    output = hook_output(home, "claude_code", "Stop", data)
    return output.get("reason", "") if output.get("decision") == "block" else ""


def run_keys(home, code):
    """Run Python with hub's lib on the path, in the scenario's HOME."""
    lib = os.path.join(home.plugin, "lib")
    return home.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {lib!r}); {code}"])


def style_section(home, flag):
    """The text inside one marked section of the shipped style."""
    text = read(os.path.join(home.plugin, "parts", "hub_style", "style.md"))
    match = re.search(rf"<!-- hub:section {re.escape(flag)} -->\n(.*?)\n<!-- hub:end -->", text, re.S)
    assert match, flag
    return match.group(1).strip()


# scenarios

def test_turning_off_one_rule_removes_only_that_sentence_and_the_slots_keep_whole_rules_in_tree_order(home):
    with given("working_rules, tier_rule and hub_style are on, and the profile lists Cursor with the Cursor beta on"):
        use_cursor_beta(home)
        home.install()
        home.hubctl("on", "working_rules")
        home.hubctl("on", "tier_rule")
        home.hubctl("on", "hub_style")
        rules = rule_texts(home)
        assert len(rules) >= 20 and len({flag.split(".")[1] for flag, _ in rules}) == 7
        before, _ = session_start(home)
        assert all(text in before for _flag, text in rules if "published" not in _flag)
        gone_flag, gone_text = next((flag, text) for flag, text in rules if ".knowledge." in flag)
    with when("the user turns off one rule of working_rules.knowledge"):
        home.hubctl("off", gone_flag)
    with and_("the next SessionStart runs"):
        after, _ = session_start(home)
    with then("the slots hold every other rule, whole and in tree order, and no slot reaches 10,000 characters"):
        assert gone_text not in after
        kept = [text for flag, text in rules if flag != gone_flag and "published" not in flag]
        positions = [after.index(text) for text in kept]
        assert positions == sorted(positions)
        assert text_of(home, "tier_rule", "delegate_when.md") in after
        assert all(len(slot) < SLOT_LIMIT for slot in slot_files(home, "claude_code"))
    with and_("the same rule is gone from Cursor's slot set"):
        cursor_text, _ = session_start(home, "cursor")
        assert gone_text not in cursor_text
        knowledge = [text for flag, text in rules if ".knowledge." in flag and flag != gone_flag]
        assert all(text in cursor_text for text in knowledge)
        assert len(read(os.path.join(home.state, "slots", "cursor", "1.md"))) < SLOT_LIMIT


def test_a_path_rules_text_arrives_the_first_time_a_matching_file_is_read(home, tool):
    with given("path_rules is on, in a repo with tests/test_app.py"):
        home.install()
        home.hubctl("on", "path_rules")
        path = os.path.join(home.work, "tests", "test_app.py")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("def test_app():\n    assert True\n")
        tests_text = text_of(home, "path_rules", "tests.md")
        other = os.path.join(home.work, "README.md")
    with when("the agent reads tests/test_app.py, and then edits it"):
        first = file_read(home, tool, path, "s1")
        second = file_edit(home, tool, path, "s1")
    with then("the first PostToolUse adds the tests text, and the second adds nothing"):
        assert tests_text in first
        assert second == ""
    with and_("a file that matches no rule adds nothing"):
        assert file_read(home, tool, other, "s1") == ""
    with when("a new session reads it again"):
        again = file_read(home, tool, path, "s2")
    with then("the text comes once more"):
        assert tests_text in again
    with when("path_rules.tests is off"):
        home.hubctl("off", "path_rules.tests")
    with then("nothing comes"):
        assert file_read(home, tool, path, "s3") == ""


def test_an_output_style_the_user_picks_becomes_a_pinned_choice_with_one_line(home):
    with given("hub_style is on, so apply wrote outputStyle, and the settings file kept its key order and indent"):
        write_claude_settings(home, {"theme": "dark", "permissions": {"allow": ["Read"]}})
        home.install()
        home.hubctl("on", "hub_style")
        text = read(claude_settings(home))
        assert json.loads(text) == {"theme": "dark", "permissions": {"allow": ["Read"]}, "outputStyle": STYLE_NAME}
        assert list(json.loads(text)) == ["theme", "permissions", "outputStyle"]
        assert '\n    "theme": "dark"' in text and '\n        "allow"' in text
        assert os.path.islink(style_link(home))
        assert f"name: {STYLE_NAME}" in read(style_link(home))
    with when("the user picks another style in /output-style, which writes the key"):
        write_claude_settings(home, {"theme": "dark", "permissions": {"allow": ["Read"]}, "outputStyle": "Explanatory"})
    with and_("apply runs"):
        result = home.hubctl("apply")
    with then("hub_style is off by user, one line says so, and outputStyle keeps the user's pick"):
        assert home.settings()["flags"]["hub_style"] == {"by": "user", "value": "off"}
        lines = [line for line in result.stdout.splitlines() if "hub_style" in line]
        assert len(lines) == 1 and "Explanatory" in lines[0] and "hubctl on hub_style" in lines[0]
        assert json.loads(read(claude_settings(home)))["outputStyle"] == "Explanatory"
        assert not os.path.lexists(style_link(home))
    with when("the user turns hub_style on again"):
        home.hubctl("on", "hub_style")
    with then("apply writes hub's style and keeps the user's pick to restore later"):
        assert json.loads(read(claude_settings(home)))["outputStyle"] == STYLE_NAME
        [entry] = home.state_file("keys.json")["keys"]
        assert entry["key"] == "outputStyle" and entry["earlier"] == "Explanatory" and entry["had_earlier"]
    with when("the user turns hub_style off"):
        home.hubctl("off", "hub_style")
    with then("the user's pick is back, in the same key order"):
        data = json.loads(read(claude_settings(home)))
        assert data["outputStyle"] == "Explanatory" and list(data) == ["theme", "permissions", "outputStyle"]
        assert home.state_file("keys.json")["keys"] == []


def test_a_settings_file_that_does_not_parse_stays_as_it_is_and_the_doctor_names_it(home):
    broken = '{"theme": "dark",\n'
    path = claude_settings(home)
    with given("the user's Claude Code settings file does not parse, and hub is installed"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(broken)
        home.install()
    with when("the user turns hub_style on"):
        result = home.hubctl("on", "hub_style", check=False)
    with then("the switch and its apply finish, the style link is there, and the file is as the user left it"):
        assert result.returncode == 0, result.stderr
        assert home.settings()["flags"]["hub_style"]["value"] == "on"
        assert os.path.islink(style_link(home))
        assert read(path) == broken
    with and_("one line names the file and the key, and so does the doctor"):
        assert [line for line in result.stdout.splitlines() if path in line and "outputStyle" in line]
        warnings = [line for line in home.hubctl("doctor").stdout.splitlines() if line.startswith("warning:")]
        assert [line for line in warnings if path in line and "outputStyle" in line], warnings
    with when("the user fixes the file and apply runs"):
        write_claude_settings(home, {"theme": "dark"})
        home.hubctl("apply")
    with then("outputStyle names hub's style, and the doctor no longer names the file"):
        assert json.loads(read(path)) == {"theme": "dark", "outputStyle": STYLE_NAME}
        assert path not in home.hubctl("doctor").stdout
    with when("the file breaks again and the user turns hub_style off"):
        with open(path, "w") as handle:
            handle.write(broken)
        result = home.hubctl("off", "hub_style", check=False)
    with then("the switch finishes, hub keeps its record to put the value back later, and the doctor names the file"):
        assert result.returncode == 0, result.stderr
        assert read(path) == broken
        assert [entry["key"] for entry in home.state_file("keys.json")["keys"]] == ["outputStyle"]
        assert path in home.hubctl("doctor").stdout


def test_link_check_sends_a_reply_back_once_for_a_bare_pr_number(home):
    with given("link_check is on"):
        home.install()
        home.hubctl("on", "link_check")
    with when('a reply mentions "#123" with no link'):
        reason = reply_ends(home, "The fix is in #123, and it is ready.")
    with then("link_check sends it back once and names the number"):
        assert "#123" in reason
        assert reply_ends(home, "The fix is in #123, and it is ready.", active=True) == ""
    with when("the number carries its link"):
        linked = reply_ends(home, "The fix is in [#123](https://github.com/acme/app/pull/123).")
    with then("nothing is said"):
        assert linked == ""
    with and_("a colour and a number inside a code block are not references"):
        assert reply_ends(home, "Use colour: #123456 here.\n\n```\nissue #4567\n```") == ""
    with when("link_check.prs is off"):
        home.hubctl("off", "link_check.prs")
    with then("a bare number passes, and a bare ticket id still warns"):
        assert reply_ends(home, "The fix is in #123.") == ""
        reason = reply_ends(home, "The fix for APP-42 is in #123.")
        assert "APP-42" in reason and "#123" not in reason
    with and_("a common upper-case word with a number is not a ticket"):
        assert reply_ends(home, "It reads UTF-8 and checks SHA-256.") == ""


def test_cursor_gets_the_style_as_rule_text(home):
    with given("a profile that lists Cursor, with the Cursor beta on"):
        use_cursor_beta(home)
        home.install()
    with when("hub_style is on and apply runs"):
        home.hubctl("on", "hub_style")
        cursor_text, _ = session_start(home, "cursor")
        claude_text, _ = session_start(home)
    with then("Cursor's slot set holds the style's sections that run"):
        for flag in ("hub_style.status_block", "hub_style.linked_sources", "hub_style.delegation_section"):
            assert style_section(home, flag) in cursor_text
        assert "<!-- hub:" not in cursor_text and "keep-coding-instructions" not in cursor_text
    with and_("nothing is written for Cursor as an output style, and Claude Code gets the style as a style"):
        for root, folders, names in os.walk(os.path.join(home.root, ".cursor")):
            assert not [name for name in folders + names if "style" in name], root
        assert style_section(home, "hub_style.status_block") not in claude_text
        assert os.path.islink(style_link(home))
    with when("one section of the style is off"):
        home.hubctl("off", "hub_style.linked_sources")
        cursor_text, _ = session_start(home, "cursor")
    with then("Cursor's text and Claude Code's built copy both leave it out"):
        assert style_section(home, "hub_style.linked_sources") not in cursor_text
        assert style_section(home, "hub_style.status_block") in cursor_text
        built = read(style_link(home))
        assert style_section(home, "hub_style.linked_sources") not in built
        assert os.path.realpath(style_link(home)).startswith(os.path.realpath(home.state))


def test_uninstall_puts_back_the_values_hub_wrote(home):
    with given("keys.py wrote remoteControlAtStartup and outputStyle"):
        write_claude_settings(home, {"remoteControlAtStartup": False, "outputStyle": "Default"}, indent=2)
        home.install()
        home.hubctl("on", "hub_style")
        run_keys(home, "from hub import keys; keys.want('remoteControlAtStartup', True, 'test', [])")
        data = json.loads(read(claude_settings(home)))
        assert data == {"remoteControlAtStartup": True, "outputStyle": STYLE_NAME}
    with and_("the user later changed outputStyle by hand"):
        write_claude_settings(home, dict(data, outputStyle="Learning"), indent=2)
    with when('the user runs "hubctl uninstall"'):
        home.hubctl("uninstall")
    with then("remoteControlAtStartup has the user's earlier value again, and keys.json is gone"):
        data = json.loads(read(claude_settings(home)))
        assert data["remoteControlAtStartup"] is False
        assert not os.path.exists(os.path.join(home.state, "keys.json"))
    with and_("outputStyle keeps the value the user chose, and the style link is gone"):
        assert data["outputStyle"] == "Learning"
        assert not os.path.lexists(style_link(home))


def test_the_writing_skill_links_as_hub_humanize_with_its_sections(home):
    with given("humanize is on"):
        home.install()
        home.hubctl("on", "humanize")
        link = os.path.join(home.root, ".claude", "skills", "hub-humanize")
    with then("~/.claude/skills/hub-humanize holds the skill and the files it reads"):
        skill = read(os.path.join(link, "SKILL.md"))
        assert "name: hub-humanize" in skill
        for name in re.findall(r"`(references/[a-z-]+\.md)`", skill):
            assert os.path.isfile(os.path.join(link, name)), name
    with when("its diagrams section is off"):
        home.hubctl("off", "humanize.instructions.diagrams")
    with then("the built copy leaves that section out and keeps plain_words"):
        skill = read(os.path.join(link, "SKILL.md"))
        assert "Diagrams" not in skill and "<!-- hub:" not in skill
        assert "plain words" in skill.lower()


def skill_section(home, marker):
    text = read(os.path.join(home.plugin, "parts", "humanize", "skill", "SKILL.md"))
    match = re.search(rf"<!-- hub:section {re.escape(marker)} -->\n(.*?)\n<!-- hub:end -->", text, re.S)
    assert match, marker
    return match.group(1).strip()


DASH_TALK = re.compile(r"\bdash(?:es)?\b", re.I)


def files_in(folder):
    """{path relative to folder: text} of every file under folder."""
    found = {}
    for root, _folders, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            found[os.path.relpath(path, folder)] = read(path)
    return found


def dash_lines_for(home, value):
    """The lines about dashes in the shipped skill's sections for one em dash value, in every file."""
    found = set()
    for text in files_in(os.path.join(home.plugin, "parts", "humanize", "skill")).values():
        pattern = rf"<!-- hub:section humanize\.em_dash={value} -->\n(.*?)\n<!-- hub:end -->"
        for body in re.findall(pattern, text, re.S):
            found.update(line for line in body.splitlines() if DASH_TALK.search(line))
    return found


def linked_dash_lines(folder):
    """{(file, line)} of each line about dashes in the skill as linked, and the files that keep a marker."""
    lines, markers = set(), []
    for name, text in files_in(folder).items():
        lines.update((name, line) for line in text.splitlines() if DASH_TALK.search(line))
        if "<!-- hub:" in text:
            markers.append(name)
    return lines, markers


def assert_every_file_follows(home, folder, value):
    lines, markers = linked_dash_lines(folder)
    wanted = dash_lines_for(home, value)
    assert markers == [], markers
    assert {line for _name, line in lines} == wanted, sorted(lines)


def test_the_writing_skill_points_out_em_dashes_by_default_and_rewrites_them_when_set(home):
    with given("humanize is on with its em dash setting at the default"):
        home.install()
        home.hubctl("on", "humanize")
        folder = os.path.join(home.root, ".claude", "skills", "hub-humanize")
        link = os.path.join(folder, "SKILL.md")
        warn, rewrite = skill_section(home, "humanize.em_dash=warn"), skill_section(home, "humanize.em_dash=rewrite")
    with then("the skill says to point out each em dash and leave the choice to the writer"):
        assert home.settings()["flags"].get("humanize.em_dash") is None
        skill = read(link)
        assert warn in skill and rewrite not in skill and "<!-- hub:" not in skill
    with and_("every file the skill links to speaks of dashes only in the warn text"):
        assert dash_lines_for(home, "warn")
        assert_every_file_follows(home, folder, "warn")
    with when("the setting is rewrite"):
        home.hubctl("set", "humanize.em_dash", "rewrite")
    with then("the skill says to rewrite every em dash, and the warning text is gone"):
        skill = read(link)
        assert rewrite in skill and warn not in skill
    with and_("every file the skill links to speaks of dashes only in the rewrite text"):
        assert_every_file_follows(home, folder, "rewrite")
    with when("the setting is allow"):
        home.hubctl("set", "humanize.em_dash", "allow")
    with then("the skill says nothing about em dashes, and no shipped file holds one"):
        skill = read(link)
        assert warn not in skill and rewrite not in skill
    with and_("no file the skill links to speaks of dashes at all"):
        assert dash_lines_for(home, "allow") == set()
        assert_every_file_follows(home, folder, "allow")
        for root, _folders, names in os.walk(os.path.join(home.plugin, "parts", "humanize")):
            for name in names:
                assert DASH not in read(os.path.join(root, name)), name


def test_a_section_marker_with_a_value_the_option_does_not_have_is_an_error_not_a_dropped_section(home):
    with given("hub is installed with humanize on, and one marker in the skill names a value that does not exist"):
        home.install()
        home.hubctl("on", "humanize")
        path = os.path.join(home.plugin, "parts", "humanize", "skill", "SKILL.md")
        lines = read(path).split("\n")
        number = next(index for index, line in enumerate(lines, 1) if "hub:section humanize.em_dash=warn" in line)
        lines[number - 1] = lines[number - 1].replace("em_dash=warn", "em_dash=wran")
        with open(path, "w") as handle:
            handle.write("\n".join(lines))
    with when("hub applies, and the user runs the doctor"):
        applied = home.hubctl("apply", check=False)
        doctor = home.hubctl("doctor", check=False)
    with then("apply fails and names the file, the line and the bad value"):
        assert applied.returncode == 1
        for text in (path, f":{number}", "wran"):
            assert text in applied.stderr, applied.stderr
    with and_("the doctor shows the same file, line and value"):
        for text in (path, f":{number}", "wran"):
            assert text in doctor.stdout, doctor.stdout


def test_the_shared_link_eval_is_laid_out_the_way_the_eval_runner_reads_it():
    with given("the eval folder"):
        assert os.path.isdir(EVAL), "evals/shared_link_changes_nothing is missing"
        prompt = read(os.path.join(EVAL, "prompt.md"))
        case = read(os.path.join(EVAL, "case.yaml"))
    with then("the prompt sends only a link to a local file and fyi"):
        body = prompt.split("---\n", 2)[2].strip()
        assert re.fullmatch(r"\[[^\]]+\]\([^)]+\) fyi", body), body
        assert "--no-publish" in prompt
    with and_("the scaffold applies hub with working_rules.knowledge on, and parses"):
        script = re.search(r"scaffold_script: (\S+)", case).group(1)
        text = read(os.path.join(EVAL, script))
        assert "working_rules" in text and "apply" in text
        assert subprocess.run(["bash", "-n", os.path.join(EVAL, script)]).returncode == 0
    with and_("the graders pass only when no file was edited or written"):
        graders = {name: read(os.path.join(EVAL, "graders", name)) for name in os.listdir(os.path.join(EVAL, "graders"))}
        for tool_name in ("Edit", "Write"):
            assert any(f"tool: {tool_name}\n" in text and "min: 0\n" in text and "max: 0\n" in text
                       for text in graders.values()), tool_name
    with and_("no file uses the em dash"):
        for root, _folders, names in os.walk(EVAL):
            for name in names:
                assert DASH not in read(os.path.join(root, name)), name
