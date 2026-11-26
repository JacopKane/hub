"""Slice 7: the working rules, the path rules, the output style, the writing
skill and the link check, each a part with its own switches in both tools."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

from conftest import DASH, FIXTURES, PLUGIN, REPO, load_fixture
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
        home.step("on", "working_rules")
        home.step("on", "tier_rule")
        home.step("on", "hub_style")
        rules = rule_texts(home)
        assert len(rules) >= 20 and len({flag.split(".")[1] for flag, _ in rules}) == 7
        before, _ = session_start(home)
        assert all(text in before for _flag, text in rules if "published" not in _flag)
        gone_flag, gone_text = next((flag, text) for flag, text in rules if ".knowledge." in flag)
    with when("the user turns off one rule of working_rules.knowledge"):
        home.step("off", gone_flag)
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
        home.step("on", "path_rules")
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
        home.step("off", "path_rules.tests")
    with then("nothing comes"):
        assert file_read(home, tool, path, "s3") == ""


def test_an_output_style_the_user_picks_becomes_a_pinned_choice_with_one_line(home):
    with given("hub_style is on, so apply wrote outputStyle, and the settings file kept its key order and indent"):
        write_claude_settings(home, {"theme": "dark", "permissions": {"allow": ["Read"]}})
        home.install()
        home.step("on", "hub_style")
        text = read(claude_settings(home))
        assert json.loads(text) == {"theme": "dark", "permissions": {"allow": ["Read"]}, "remoteControlAtStartup": True,
                                    "attribution": {"sessionUrl": False}, "outputStyle": STYLE_NAME}
        assert list(json.loads(text)) == ["theme", "permissions", "remoteControlAtStartup", "attribution", "outputStyle"]
        assert '\n    "theme": "dark"' in text and '\n        "allow"' in text
        assert os.path.islink(style_link(home))
        assert f"name: {STYLE_NAME}" in read(style_link(home))
    with when("the user picks another style in /output-style, which writes the key"):
        write_claude_settings(home, {"theme": "dark", "permissions": {"allow": ["Read"]}, "remoteControlAtStartup": True,
                                     "attribution": {"sessionUrl": False}, "outputStyle": "Explanatory"})
    with and_("apply runs"):
        result = home.step("apply")
    with then("hub_style is off by user, one line says so, and outputStyle keeps the user's pick"):
        assert home.settings()["flags"]["hub_style"] == {"by": "user", "value": "off"}
        lines = [line for line in result.stdout.splitlines() if "hub_style" in line]
        assert len(lines) == 1 and "Explanatory" in lines[0] and 'Say "turn on hub_style"' in lines[0]
        assert json.loads(read(claude_settings(home)))["outputStyle"] == "Explanatory"
        assert not os.path.lexists(style_link(home))
    with when("the user turns hub_style on again"):
        home.step("on", "hub_style")
    with then("apply writes hub's style and keeps the user's pick to restore later"):
        assert json.loads(read(claude_settings(home)))["outputStyle"] == STYLE_NAME
        [entry] = [item for item in home.state_file("keys.json")["keys"] if item["key"] == "outputStyle"]
        assert entry["earlier"] == "Explanatory" and entry["had_earlier"]
    with when("the user turns hub_style off"):
        home.step("off", "hub_style")
    with then("the user's pick is back, in the same key order"):
        data = json.loads(read(claude_settings(home)))
        assert data["outputStyle"] == "Explanatory"
        assert list(data) == ["theme", "permissions", "remoteControlAtStartup", "attribution", "outputStyle"]
        assert [item["key"] for item in home.state_file("keys.json")["keys"]] == ["remoteControlAtStartup",
                                                                                 "attribution.sessionUrl"]


def test_a_settings_file_that_does_not_parse_stays_as_it_is_and_the_doctor_names_it(home):
    broken = '{"theme": "dark",\n'
    path = claude_settings(home)
    with given("the user's Claude Code settings file does not parse, and hub is installed"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(broken)
        home.install()
    with when("the user turns hub_style on"):
        result = home.step("on", "hub_style", check=False)
    with then("the switch and its apply finish, the style link is there, and the file is as the user left it"):
        assert result.returncode == 0, result.stderr
        assert home.settings()["flags"]["hub_style"]["value"] == "on"
        assert os.path.islink(style_link(home))
        assert read(path) == broken
    with and_("one line names the file and the key, and so does the doctor"):
        assert [line for line in result.stdout.splitlines() if path in line and "outputStyle" in line]
        warnings = [line for line in home.step("doctor").stdout.splitlines() if line.startswith("warning:")]
        assert [line for line in warnings if path in line and "outputStyle" in line], warnings
    with when("the user fixes the file and apply runs"):
        write_claude_settings(home, {"theme": "dark"})
        home.step("apply")
    with then("outputStyle names hub's style, and the doctor no longer names the file"):
        assert json.loads(read(path)) == {"theme": "dark", "remoteControlAtStartup": True,
                                          "attribution": {"sessionUrl": False}, "outputStyle": STYLE_NAME}
        assert path not in home.step("doctor").stdout
    with when("the file breaks again and the user turns hub_style off"):
        with open(path, "w") as handle:
            handle.write(broken)
        result = home.step("off", "hub_style", check=False)
    with then("the switch finishes, hub keeps its record to put the value back later, and the doctor names the file"):
        assert result.returncode == 0, result.stderr
        assert read(path) == broken
        assert [entry["key"] for entry in home.state_file("keys.json")["keys"]] == ["outputStyle", "remoteControlAtStartup",
                                                                                   "attribution.sessionUrl"]
        assert path in home.step("doctor").stdout


def test_link_check_sends_a_reply_back_once_for_a_bare_pr_number(home):
    with given("link_check is on"):
        home.install()
        home.step("on", "link_check")
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
        home.step("off", "link_check.prs")
    with then("a bare number passes, and a bare ticket id still warns"):
        assert reply_ends(home, "The fix is in #123.") == ""
        reason = reply_ends(home, "The fix for APP-42 is in #123.")
        assert "APP-42" in reason and "#123" not in reason
    with and_("a common upper-case word with a number is not a ticket"):
        assert reply_ends(home, "It reads UTF-8 and checks SHA-256.") == ""


def test_link_check_sends_a_reply_back_once_for_a_local_file_the_terminal_can_not_open(home):
    with given("link_check is on, with its local_paths check on by default"):
        home.install()
        home.step("on", "link_check")
    with when("a reply names files from the home folder, a scratch folder and a home subfolder"):
        reason = reply_ends(home, "I saved the plan to ~/notes/plan.md and the log to scratchpad/build/tests.log. "
                                  "The report is in Desktop/report.pdf.")
    with then("link_check sends it back once, names each path and asks for full paths"):
        for path in ("~/notes/plan.md", "scratchpad/build/tests.log", "Desktop/report.pdf"):
            assert path in reason, reason
        assert "full path" in reason and reason.endswith("(link_check)")
        assert reply_ends(home, "I saved the plan to ~/notes/plan.md.", active=True) == ""
    with when("a reply names a screenshot by its file name only, and a path in inline code"):
        reason = reply_ends(home, "See shot.png, and the config is at `~/.config/app/app.json`.")
    with then("both go back in the reply's order, because neither opens with a click"):
        assert "shot.png" in reason and "~/.config/app/app.json" in reason, reason
        assert reason.index("shot.png") < reason.index("~/.config/app/app.json"), reason
    with when("a reply names a home path after a full path with spaces, then a screenshot, on one line"):
        reason = reply_ends(home, "I moved /tmp/old notes to ~/notes/new.md and took shot.png")
    with then("the home path and the screenshot both go back, and the full path does not"):
        assert "~/notes/new.md" in reason and "shot.png" in reason and "/tmp" not in reason, reason
    with when("the reply gives every file as a full path, even one with spaces in its name"):
        absolute = reply_ends(home, "I saved the plan to /home/sam/notes/plan.md and the shot to "
                                    "/Users/sam/Desktop/Screen Shot 2026-11-20 at 10.00.png.")
    with then("nothing is said"):
        assert absolute == ""
    with and_("a full path with a home folder name inside it, after a space, is not read as a relative path"):
        assert reply_ends(home, "It synced to /Users/sam/Library/Mobile Documents/com~apple~CloudDocs/plan.pdf.") == ""
    with and_("the home folder sign alone, a web page and a table file named in prose are not local files"):
        assert reply_ends(home, "Every tool keeps its files under ~/ on each machine.") == ""
        assert reply_ends(home, "Edit index.html and export data.csv.") == ""
    with and_("a path inside a code block, a URL and a link are not local files"):
        assert reply_ends(home, "Run this:\n\n```\ncat ~/notes/plan.md > scratchpad/out.csv\n```\n") == ""
        assert reply_ends(home, "The page is https://example.com/files/shot.png and "
                                "[the chart](https://example.com/chart.png) is up.") == ""
    with and_("a very long reply full of slashes is read well inside the hook's 15 seconds"):
        started = time.monotonic()
        assert reply_ends(home, " /x" * 40000) == ""
        assert time.monotonic() - started < 10
    with when("a bare PR number and a home path are in one reply"):
        reason = reply_ends(home, "The fix is in #123, and the notes are in ~/notes/fix.md.")
    with then("one send-back names both"):
        assert "#123" in reason and "~/notes/fix.md" in reason, reason
    with when("the user adds a folder of their own to link_check.local_paths.folders"):
        home.step("set", "link_check.local_paths.folders", "Desktop", "Projects")
    with then("a path from that folder goes back, and a folder taken off the list passes"):
        assert "Projects/app/README.md" in reply_ends(home, "It is in Projects/app/README.md.")
        assert reply_ends(home, "The draft is in Documents/draft.md.") == ""
    with when("link_check.local_paths is off"):
        home.step("off", "link_check.local_paths")
    with then("local paths and file names pass, and a bare PR number still goes back"):
        assert reply_ends(home, "I saved it to ~/notes/plan.md and shot.png.") == ""
        reason = reply_ends(home, "The fix is in #123, see ~/notes/fix.md.")
        assert "#123" in reason and "~/notes/fix.md" not in reason, reason


def test_cursor_gets_the_style_as_rule_text(home):
    with given("a profile that lists Cursor, with the Cursor beta on"):
        use_cursor_beta(home)
        home.install()
    with when("hub_style is on and apply runs"):
        home.step("on", "hub_style")
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
        home.step("off", "hub_style.linked_sources")
        cursor_text, _ = session_start(home, "cursor")
    with then("Cursor's text and Claude Code's built copy both leave it out"):
        assert style_section(home, "hub_style.linked_sources") not in cursor_text
        assert style_section(home, "hub_style.status_block") in cursor_text
        built = read(style_link(home))
        assert style_section(home, "hub_style.linked_sources") not in built
        assert os.path.realpath(style_link(home)).startswith(os.path.realpath(home.state))


REMOTE_PART, REMOTE_KEY = "remote_control", "remoteControlAtStartup"
SESSION_LINK = "sessionUrl"  # under attribution


def install_before_remote_control(home):
    """hub as installed before remote_control shipped: the plugin copy has no such part yet."""
    shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__", REMOTE_PART))
    home.step("apply")


def install_printing(home):
    """home.install, but returns the lines the first apply printed."""
    shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
    return home.step("apply").stdout


def remote_lines(output):
    return [line for line in output.splitlines() if REMOTE_PART in line]


def setup_dialog(home):
    """The dialog hub setup prints for the recorded answer that asks nothing of its own."""
    return json.loads(home.step("setup", "--answer", os.path.join(FIXTURES, "agent_answers", "nothing.json")).stdout)


def remote_question(dialog):
    [question] = [question for question in dialog["questions"] if question["topic"] == REMOTE_PART]
    return question


def labels(question):
    return [option["label"] for option in question["options"]]


def turned_off_lines(output):
    return [line for line in remote_lines(output) if "is now off" in line]


def edit_claude_settings(home, **values):
    """Change keys in the user's Claude Code settings by hand, as a text editor would, keeping the rest."""
    data = json.loads(read(claude_settings(home)))
    data.update(values)
    write_claude_settings(home, data)


def test_an_update_turns_remote_control_on_for_every_new_chat_without_session_links_and_off_puts_the_file_back(home):
    path = claude_settings(home)
    with given("hub was installed before remote_control shipped, and the user's Claude Code settings hold their theme"):
        write_claude_settings(home, {"theme": "dark"})
        original = read(path)
        install_before_remote_control(home)
        assert read(path) == original
    with when("the update brings remote_control and apply runs"):
        shutil.copytree(os.path.join(PLUGIN, "parts", REMOTE_PART), os.path.join(home.plugin, "parts", REMOTE_PART))
        home.step("apply")
    with then("remote_control runs by default, nothing is offered, and the file turns Remote Control on and the "
              "session link off, in the user's key order and indent"):
        record = home.show()["flags"][REMOTE_PART]
        assert record["by"] == "default" and record["runs"]
        assert REMOTE_PART not in [offer["id"] for offer in home.state_file("offers.json")["offers"]]
        text = read(path)
        assert json.loads(text) == {"theme": "dark", REMOTE_KEY: True, "attribution": {SESSION_LINK: False}}
        assert '\n    "theme": "dark"' in text
        entries = home.state_file("keys.json")["keys"]
        assert [(entry["key"], entry["part"], entry["had_earlier"]) for entry in entries] == [
            (REMOTE_KEY, REMOTE_PART, False), (f"attribution.{SESSION_LINK}", None, False)]
    with when("the user turns remote_control off"):
        home.step("off", REMOTE_PART)
    with then("the file is exactly as the user left it, with no empty attribution left, and hub keeps no record"):
        assert read(path) == original
        assert home.state_file("keys.json")["keys"] == []
    with when("the user turns it on again, then sets the session link back on by hand, and apply runs"):
        home.step("on", REMOTE_PART)
        edit_claude_settings(home, attribution={SESSION_LINK: True})
        output = home.step("apply").stdout
    with then("remote_control still runs with no line, and the user's session link setting stays"):
        assert home.show()["flags"][REMOTE_PART]["runs"]
        assert turned_off_lines(output) == []
        assert json.loads(read(path)) == {"theme": "dark", REMOTE_KEY: True, "attribution": {SESSION_LINK: True}}
        home.step("apply")
        assert json.loads(read(path))["attribution"] == {SESSION_LINK: True}
    with when("the user later sets the Remote Control key to false by hand, and apply runs"):
        edit_claude_settings(home, **{REMOTE_KEY: False})
        result = home.step("apply")
    with then("remote_control is off by user, one line says so, and both of the user's values stay"):
        assert home.settings()["flags"][REMOTE_PART] == {"by": "user", "value": "off"}
        [line] = turned_off_lines(result.stdout)
        assert REMOTE_KEY in line and "false" in line and f'Say "turn on {REMOTE_PART}"' in line
        assert json.loads(read(path)) == {"theme": "dark", REMOTE_KEY: False, "attribution": {SESSION_LINK: True}}
        assert home.state_file("keys.json")["keys"] == []


def setup_context(home):
    """The text the first session start gives the chat when hub asks for its setup."""
    _start_text, dispatched = session_start(home)
    return dispatched.get("hookSpecificOutput", {}).get("additionalContext", "")


def run_setup(home):
    """hub setup with the recorded answer that asks nothing, and the user's empty reply."""
    setup_dialog(home)
    return home.step("setup", "--reply", "{}").stdout


def test_setup_turns_remote_control_on_for_a_new_user_who_set_it_to_false_and_off_puts_false_back(home):
    path = claude_settings(home)
    with given("a new user whose Claude Code settings already turn Remote Control and all attribution off"):
        write_claude_settings(home, {REMOTE_KEY: False, "theme": "dark", "attribution": False}, indent=2)
        original = read(path)
    with when("hub is installed"):
        output = install_printing(home)
    with then("the file is as the user left it, and hub writes no settings of its own and no line yet"):
        assert read(path) == original
        assert not os.path.exists(home.user_file)
        assert not os.path.exists(os.path.join(home.state, "keys.json"))
        assert remote_lines(output) == []
    with when("the first chat starts"):
        context = setup_context(home)
    with then("hub asks for its setup first"):
        assert "hub setup" in context and "first" in context
    with when("setup builds its dialog"):
        question = remote_question(setup_dialog(home))
    with then("the last question is hub's own about Remote Control, with On first and Off to untick it"):
        assert labels(question) == ["On", "Off"]
        assert "Remote Control" in question["text"] and "unless you pick Off" in question["text"]
    with when("the user leaves it as it is"):
        summary = home.step("setup", "--reply", "{}").stdout
    with then("remote_control runs, Remote Control is on, and the user's false is kept to put back"):
        assert REMOTE_PART not in home.settings()["flags"]
        assert home.show()["flags"][REMOTE_PART]["runs"]
        assert turned_off_lines(summary) == []
        assert json.loads(read(path)) == {REMOTE_KEY: True, "theme": "dark", "attribution": False}
        [entry] = home.state_file("keys.json")["keys"]
        assert (entry["key"], entry["earlier"], entry["had_earlier"]) == (REMOTE_KEY, False, True)
    with and_("the next chat does not ask for setup again, and apply leaves remote_control as it is"):
        assert "hub setup" not in setup_context(home)
        assert remote_lines(home.step("apply").stdout) == []
        assert json.loads(read(path))[REMOTE_KEY] is True
    with when("the user turns remote_control off"):
        home.step("off", REMOTE_PART)
    with then("the file is exactly as the user left it"):
        assert read(path) == original


def test_a_new_user_who_picks_off_in_setup_keeps_their_settings_as_they_were(home):
    path = claude_settings(home)
    with given("a new user installs hub, and its first apply turns Remote Control on"):
        write_claude_settings(home, {"theme": "dark"})
        original = read(path)
        install_printing(home)
        assert json.loads(read(path))[REMOTE_KEY] is True
    with when("the user picks Off for Remote Control in setup"):
        question = remote_question(setup_dialog(home))
        summary = home.step("setup", "--reply", json.dumps({question["id"]: "Off"})).stdout
    with then("remote_control is off by setup, the summary says so, and the file is exactly as before hub"):
        assert home.settings()["flags"][REMOTE_PART] == {"by": "setup", "value": "off"}
        assert not home.show()["flags"][REMOTE_PART]["runs"]
        assert f"{REMOTE_PART} is \"off\" now, because you picked Off" in summary
        assert read(path) == original
        assert home.state_file("keys.json")["keys"] == []


def test_a_false_set_by_hand_before_setup_waits_for_setup_and_then_setup_turns_it_on(home):
    path = claude_settings(home)
    with given("a new user installs hub, and its first apply turns Remote Control on"):
        write_claude_settings(home, {"theme": "dark"})
        install_printing(home)
        assert json.loads(read(path))[REMOTE_KEY] is True
    with when("the user sets the key to false by hand before setup, and apply runs"):
        edit_claude_settings(home, **{REMOTE_KEY: False})
        output = home.step("apply").stdout
    with then("false stays, hub writes no settings of its own, keeps its record, and setup is still asked for"):
        assert json.loads(read(path))[REMOTE_KEY] is False
        assert not os.path.exists(home.user_file)
        assert [entry["key"] for entry in home.state_file("keys.json")["keys"]] == [
            REMOTE_KEY, f"attribution.{SESSION_LINK}"]
        assert remote_lines(output) == []
        assert "hub setup" in setup_context(home)
    with when("setup runs and the user leaves Remote Control as it is"):
        summary = run_setup(home)
    with then("remote_control runs with no line, Remote Control is on again, and hub keeps the user's false"):
        assert REMOTE_PART not in home.settings()["flags"]
        assert turned_off_lines(summary) == []
        assert json.loads(read(path))[REMOTE_KEY] is True
        entry = next(entry for entry in home.state_file("keys.json")["keys"] if entry["key"] == REMOTE_KEY)
        assert (entry["earlier"], entry["had_earlier"]) == (False, True)
    with when("the user turns remote_control off"):
        home.step("off", REMOTE_PART)
    with then("the false the user set comes back, and hub's session link setting is gone"):
        assert json.loads(read(path)) == {"theme": "dark", REMOTE_KEY: False}


def test_a_hub_link_deleted_before_setup_waits_for_setup_and_then_turns_its_part_off(home):
    with given("a new user installs hub, and its first apply makes hub's agent and skill links"):
        home.install()
        entry = next(entry for entry in home.state_file("links.json")["links"] if entry["kind"] in ("agent", "skill"))
        part, link = entry["part"], entry["path"]
    with when("the user deletes one of those links before setup, and apply runs twice"):
        os.unlink(link)
        first = home.step("apply").stdout
        second = home.step("apply").stdout
    with then("hub writes no settings of its own, makes the link no more, and setup is still asked for"):
        assert not os.path.exists(home.user_file)
        assert not os.path.lexists(link)
        assert link not in first + second
        assert link in [entry["path"] for entry in home.state_file("links.json")["links"]]
        assert "hub setup" in setup_context(home)
    with when("setup runs"):
        summary = run_setup(home)
    with then("the part is off and pinned by the user, and one line says why"):
        assert home.settings()["flags"][part] == {"by": "user", "value": "off"}
        assert any(link in line and f'Say "turn on {part}"' in line for line in summary.splitlines())
        assert not os.path.lexists(link)


def test_a_silent_update_keeps_a_false_the_user_set_and_turning_on_by_hand_puts_both_keys_back(home):
    path = claude_settings(home)
    with given("a user set up before remote_control shipped, with Remote Control off and their own commit line"):
        write_claude_settings(home, {REMOTE_KEY: False, "attribution": {"commit": "Made with care"}}, indent=2)
        original = read(path)
        install_before_remote_control(home)
        run_setup(home)
    with when("the update brings remote_control and apply runs"):
        shutil.copytree(os.path.join(PLUGIN, "parts", REMOTE_PART), os.path.join(home.plugin, "parts", REMOTE_PART))
        output = home.step("apply").stdout
    with then("remote_control is off and pinned by the user with one line, and the file does not change"):
        assert home.settings()["flags"][REMOTE_PART] == {"by": "user", "value": "off"}
        [line] = turned_off_lines(output)
        assert REMOTE_KEY in line and "false" in line
        assert read(path) == original
        assert not os.path.exists(os.path.join(home.state, "keys.json"))
    with when("the user turns remote_control on by hand"):
        home.step("on", REMOTE_PART)
    with then("Remote Control is on, the session link is off next to the user's commit line, and hub keeps both "
              "earlier values"):
        assert json.loads(read(path)) == {REMOTE_KEY: True,
                                          "attribution": {"commit": "Made with care", SESSION_LINK: False}}
        entries = home.state_file("keys.json")["keys"]
        assert [(entry["earlier"], entry["had_earlier"], "made_parent" in entry) for entry in entries] == [
            (False, True, False), (None, False, False)]
    with when("the user turns remote_control off again"):
        home.step("off", REMOTE_PART)
    with then("the file is exactly as the user left it"):
        assert read(path) == original


def test_an_older_false_in_claude_json_makes_remote_control_stand_down_at_update(home):
    path, global_config = claude_settings(home), os.path.join(home.root, ".claude.json")
    with given("a user set up before remote_control shipped, with Remote Control off in ~/.claude.json from an older Claude Code"):
        write_claude_settings(home, {"theme": "dark"})
        original = read(path)
        install_before_remote_control(home)
        run_setup(home)
        with open(global_config, "w") as handle:
            handle.write(json.dumps({"numStartups": 3, REMOTE_KEY: False}) + "\n")
        older = read(global_config)
    with when("the update brings remote_control and apply runs"):
        shutil.copytree(os.path.join(PLUGIN, "parts", REMOTE_PART), os.path.join(home.plugin, "parts", REMOTE_PART))
        output = home.step("apply").stdout
    with then("remote_control is off and pinned by the user with one line, and neither file changes"):
        assert home.settings()["flags"][REMOTE_PART] == {"by": "user", "value": "off"}
        [line] = remote_lines(output)
        assert REMOTE_KEY in line and "false" in line
        assert read(path) == original and read(global_config) == older
        assert not os.path.exists(os.path.join(home.state, "keys.json"))


def test_a_user_who_opts_out_of_tracking_gets_no_remote_control_from_hub(home):
    path = claude_settings(home)
    with given("a set-up user from before remote_control shipped, with DO_NOT_TRACK=0, which leaves tracking on"):
        write_claude_settings(home, {"theme": "dark"})
        original = read(path)
        install_before_remote_control(home)
        run_setup(home)
        home.env["DO_NOT_TRACK"] = "0"
    with when("the update brings remote_control and apply runs"):
        shutil.copytree(os.path.join(PLUGIN, "parts", REMOTE_PART), os.path.join(home.plugin, "parts", REMOTE_PART))
        home.step("apply")
    with then("Remote Control is on, because 0 does not opt out"):
        assert json.loads(read(path))[REMOTE_KEY] is True
    with when("the user sets DO_NOT_TRACK=1 and apply runs"):
        home.env["DO_NOT_TRACK"] = "1"
        output = home.step("apply").stdout
    with then("remote_control is off and pinned by the user, one line names DO_NOT_TRACK, and the file is as before hub"):
        assert home.settings()["flags"][REMOTE_PART] == {"by": "user", "value": "off"}
        [line] = remote_lines(output)
        assert "DO_NOT_TRACK" in line and f'Say "turn on {REMOTE_PART}"' in line
        [change] = [item for item in home.history() if item.get("flag") == REMOTE_PART]
        assert "DO_NOT_TRACK" in change["reason"]
        assert read(path) == original
    with when("the user still turns remote_control on by hand"):
        home.step("on", REMOTE_PART)
    with then("their own choice wins, and Remote Control is on"):
        assert json.loads(read(path))[REMOTE_KEY] is True


def test_setup_keeps_remote_control_off_with_a_reason_when_the_settings_env_block_sets_do_not_track(home):
    path = claude_settings(home)
    with given("a new user whose Claude Code settings set DO_NOT_TRACK in their env block"):
        write_claude_settings(home, {"env": {"DO_NOT_TRACK": "1"}, "theme": "dark"})
        original = read(path)
    with when("hub is installed"):
        install_printing(home)
    with then("the file is as the user left it, and hub writes no settings of its own"):
        assert read(path) == original
        assert not os.path.exists(home.user_file)
    with when("setup builds its dialog"):
        question = remote_question(setup_dialog(home))
    with then("the Remote Control question shows it off, says why, and has no way to turn it on"):
        assert labels(question) == ["Keep it off", "Skip"]
        assert "DO_NOT_TRACK is set" in question["text"]
    with when("the reply still tries to turn it on"):
        refused = home.step("setup", "--reply", json.dumps({question["id"]: "On"}), check=False)
    with then("setup refuses it and writes nothing"):
        assert refused.returncode != 0 and "Keep it off" in refused.stderr
        assert not os.path.exists(home.user_file) and read(path) == original
    with when("the user skips the question"):
        summary = home.step("setup", "--reply", json.dumps({question["id"]: "Skip"})).stdout
    with then("remote_control is off and pinned by the user, one line names DO_NOT_TRACK, and the file is as before"):
        assert home.settings()["flags"][REMOTE_PART] == {"by": "user", "value": "off"}
        [line] = turned_off_lines(summary)
        assert "DO_NOT_TRACK" in line
        assert read(path) == original
        assert not os.path.exists(os.path.join(home.state, "keys.json"))


def test_uninstall_puts_back_the_values_hub_wrote(home):
    with given("the user had Remote Control off and outputStyle Default, then turned on remote_control and hub_style"):
        write_claude_settings(home, {REMOTE_KEY: False, "outputStyle": "Default"}, indent=2)
        home.install()
        home.step("on", REMOTE_PART)
        home.step("on", "hub_style")
        data = json.loads(read(claude_settings(home)))
        assert data == {REMOTE_KEY: True, "outputStyle": STYLE_NAME, "attribution": {SESSION_LINK: False}}
    with and_("the user later changed outputStyle by hand"):
        write_claude_settings(home, dict(data, outputStyle="Learning"), indent=2)
    with when('the user runs "hub uninstall"'):
        home.step("uninstall")
    with then("remoteControlAtStartup has the user's earlier value again, the session link key is gone, and keys.json is gone"):
        data = json.loads(read(claude_settings(home)))
        assert data[REMOTE_KEY] is False and "attribution" not in data
        assert not os.path.exists(os.path.join(home.state, "keys.json"))
    with and_("outputStyle keeps the value the user chose, and the style link is gone"):
        assert data["outputStyle"] == "Learning"
        assert not os.path.lexists(style_link(home))



# sha256 hashes of five lines from the long writing guide that hub's humanize
# part used to ship. The test keeps only the hashes, so it holds none of that text.
OLD_GUIDE_LINES = {
    "a5cc3c069cf62fcb8e30bb92e505150870b51c04c4538e7f3689864bdf3bafc9",
    "83ae58dde4ac62a3ef79c168c169a340d25544ed549d0ef6e7ac4048b098f077",
    "4e83e069c364fc41a0b99c73eb046b2873771422097bd29ec521e194a25d7497",
    "475063a21c3e3994feb82d6c118ff0f82d70f3a5a38b78876afdf9a6471aa768",
    "6c9c13b53bbb3168a1ece4146bb60c4f5b2beacbdf406497410696e02962b95c",
}


def files_in(folder):
    """{path relative to folder: text} of every file under folder."""
    found = {}
    for root, _folders, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            found[os.path.relpath(path, folder)] = read(path)
    return found


def line_hashes(texts):
    return {hashlib.sha256(line.strip().encode()).hexdigest() for text in texts for line in text.splitlines()}


def test_hubs_humanize_points_to_the_users_own_skill(home):
    with given("humanize is on"):
        home.install()
        home.step("on", "humanize")
        skill = read(os.path.join(home.root, ".claude", "skills", "hub-humanize", "SKILL.md"))
        words = " ".join(skill.split())
    with then("hub-humanize tells Claude to find the user's own writing skill in its skill listing and run it"):
        assert "name: hub-humanize" in skill
        assert "skill listing" in words and "Skill tool" in words
    with and_("when the user has none, it says in one line how to add one"):
        assert "~/.claude/skills/" in words
    with and_("it names none of the user's skills, has no sections to switch, and stays short"):
        assert "humanize-ai-writing" not in skill and "<!-- hub:" not in skill
        assert manifest(home, "humanize").get("children", []) == []
        assert len(skill.splitlines()) <= 30
    with and_("no file of the old writing guide is left in the part"):
        part = files_in(os.path.join(home.plugin, "parts", "humanize"))
        assert sorted(part) == ["part.json", os.path.join("skill", "SKILL.md")], sorted(part)
        assert not line_hashes(part.values()) & OLD_GUIDE_LINES


VALUE_SECTION = "<!-- hub:section builder.effort={value} -->\nSay that effort is {value}.\n<!-- hub:end -->\n"


def add_value_sections(home, *values):
    """Adds one section per effort value to the test copy of builder's agent file, and returns its path."""
    path = os.path.join(home.plugin, "parts", "builder", "agent.md")
    text = read(path) + "".join(VALUE_SECTION.format(value=value) for value in values)
    with open(path, "w") as handle:
        handle.write(text)
    return path


def test_a_section_for_one_value_of_an_option_stays_only_while_the_option_has_that_value(home):
    with given("builder is on, and its agent file has a section for effort high and one for effort max"):
        home.install()
        add_value_sections(home, "high", "max")
        home.step("on", "builder")
        link = os.path.join(home.root, ".claude", "agents", "hub-builder.md")
    with then("at the default effort the agent keeps the high section only, with no marker left"):
        agent = read(link)
        assert "Say that effort is high." in agent and "effort is max" not in agent
        assert "<!-- hub:" not in agent
    with when("the effort is max"):
        home.step("set", "builder.effort", "max")
    with then("the agent keeps the max section only"):
        agent = read(link)
        assert "Say that effort is max." in agent and "effort is high" not in agent
    with when("the effort is low"):
        home.step("set", "builder.effort", "low")
    with then("neither section is left"):
        assert "Say that effort" not in read(link)


def test_a_section_marker_with_a_value_the_option_does_not_have_is_an_error_not_a_dropped_section(home):
    with given("hub is installed with builder on, and one marker in its agent file names a value that does not exist"):
        home.install()
        home.step("on", "builder")
        path = add_value_sections(home, "hgih")
        number = next(index for index, line in enumerate(read(path).split("\n"), 1) if "builder.effort=hgih" in line)
    with when("hub applies, and the user runs the doctor"):
        applied = home.step("apply", check=False)
        doctor = home.step("doctor", check=False)
    with then("apply fails and names the file, the line and the bad value"):
        assert applied.returncode == 1
        for text in (path, f":{number}", "hgih"):
            assert text in applied.stderr, applied.stderr
    with and_("the doctor shows the same file, line and value"):
        for text in (path, f":{number}", "hgih"):
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
