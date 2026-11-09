"""The model can not lower hub's protections through another program.

A tool call that starts hubctl in a tmux or screen session, over ssh, or in a
program that gives it a terminal, runs hubctl without the tool-call marker and
with a terminal, so hubctl would take it for the user. The self-protection
part reads the command that program runs, and the keys it types, as a script
of its own. It also refuses a call that disables or uninstalls the hub plugin,
and a write into hub's own plugin files. Turning off a part refuses when it
stops a protected flag under it, as turning that flag off would.

Each scenario sends the tool call through the real dispatcher, as Claude Code
or Cursor sends it, in a temporary HOME. No tmux server, screen session or ssh
connection is started: the part refuses the call before it runs.
"""
import json
import os

import pytest

from conftest import load_fixture
from steps import and_, given, then, when

PART = "self_protection"
TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1"}


def refused_by(result, check):
    return result.refused and f"({PART}.{check})" in (result.reason or "")


def file_call(home, tool_name, tool_input):
    data = load_fixture("claude_code", "PreToolUse.Bash.json")
    data.update(cwd=home.work, tool_name=tool_name, tool_input=tool_input,
                tool_use_id=f"call_relay_{tool_name}_{len(json.dumps(tool_input))}")
    return home.send("claude_code", "PreToolUse", data)


def flag(home, flag_id):
    return home.show("--all")["flags"][flag_id]


# hubctl through another program


@pytest.mark.parametrize("command", [
    "tmux new -d -s fix 'hubctl off public_text_gate'",
    "tmux new-session -d \"sh -c 'hubctl off public_text_gate'\"",
    "tmux send-keys -t fix 'hubctl off public_text_gate' Enter",
    "tmux send-keys -t fix hubctl Space off Space public_text_gate Enter",
    "tmux send-keys -t fix h u b c t l Space off Space public_text_gate Enter",
    "tmux set-buffer 'hubctl uninstall' && tmux paste-buffer -t fix",
    "ssh -tt localhost 'hubctl off public_text_gate' <<< public_text_gate",
    "ssh localhost -- hubctl uninstall",
    "timeout 5 ssh -t localhost 'cd ~ && hubctl off public_text_gate'",
    "echo 'hubctl off public_text_gate' | ssh localhost",
    "echo 'hubctl off public_text_gate' | sh",
    "printf 'hubctl uninstall\\n' | bash -s",
    "bash <<< 'hubctl off public_text_gate'",
    "ssh localhost <<'EOF'\nhubctl off public_text_gate\nEOF",
    "echo '* * * * * hubctl uninstall' | crontab -",
    "screen -dmS fix hubctl off public_text_gate",
    "screen -S fix -X stuff 'hubctl off public_text_gate\\n'",
    "script -q /dev/null hubctl off public_text_gate",
    "script -qc 'hubctl off public_text_gate' /dev/null",
    "expect -c 'spawn hubctl off public_text_gate; send \"public_text_gate\\r\"'",
    "osascript -e 'tell app \"Terminal\" to do script \"hubctl off public_text_gate\"'",
    "python3 -c 'import pty; pty.spawn([\"hubctl\", \"off\", \"public_text_gate\"])'",
    "unbuffer hubctl off public_text_gate",
    "tmux new -d \"$PROGRAM off public_text_gate\"",
    "ssh localhost \"$(printf hub)ctl off public_text_gate\"",
    "ssh localhost \"$(printf hub)ctl\" --scope local off public_text_gate",
    "tmux new -d \"`printf hub`ctl uninstall\"",
], ids=["tmux new", "tmux new with sh -c", "tmux keys as one word", "tmux keys with Space",
        "tmux keys letter by letter", "tmux buffer", "ssh with the id on stdin", "ssh words", "ssh behind timeout", "echo into ssh", "echo into sh",
        "printf into bash -s", "a here-string into bash", "a heredoc into ssh", "a crontab line", "screen words",
        "screen stuff", "script on macOS", "script on Linux", "expect",
        "osascript do script", "python pty", "unbuffer", "a hidden program with a change verb",
        "a hidden part of the name", "a hidden part of the name in its own word", "a name built in backticks"])
def test_hubctl_started_through_another_program_is_refused(home, command):
    with given("hub is installed and the user raised the gate to block in their own terminal"):
        home.install()
        home.hubctl_as_user("set", "public_text_gate", "block", typed="public_text_gate")
        before = home.settings()
    with when(f"the model runs {command!r}"):
        result = home.hook("claude_code", "PreToolUse", command)
    with then("the hubctl check refuses it, and the settings did not change"):
        assert refused_by(result, "hubctl"), result.reason
        assert home.settings() == before


@pytest.mark.parametrize("command, check", [
    ("HUBCTL off public_text_gate", "hubctl"),
    ("./HubCtl off public_text_gate", "hubctl"),
    ("/usr/local/bin/HUBCTL off public_text_gate", "hubctl"),
    ("Hubctl off --scope user public_text_gate", "hubctl"),
    ("BASH -c 'HUBCTL off public_text_gate'", "hubctl"),
    ("tmux new -d 'HubCtl off public_text_gate'", "hubctl"),
    ("PYTHON3 -c 'import subprocess; subprocess.run([\"HUBCTL\", \"off\", \"public_text_gate\"])'", "hubctl"),
    ("ENV -u AI_AGENT hubctl show", "agent_marker"),
], ids=["all capitals", "a relative path", "a full path", "a capital first letter", "inside a shell in capitals",
        "inside tmux", "interpreter code", "env in capitals"])
def test_a_command_name_in_other_letters_is_read_as_the_same_program(home, command, check):
    with given("hub is installed and the user raised the gate to block in their own terminal"):
        home.install()
        home.hubctl_as_user("set", "public_text_gate", "block", typed="public_text_gate")
        before = home.settings()
    with when(f"the model runs {command!r}, which a case-insensitive file system such as macOS runs as hubctl"):
        result = home.hook("claude_code", "PreToolUse", command)
    with then(f"the {check} check refuses it, and the settings did not change"):
        assert refused_by(result, check), result.reason
        assert home.settings() == before


@pytest.mark.parametrize("command, check", [
    ("true &!# don't\nhubctl off public_text_gate\n# it's", "hubctl"),
    ("true &!# don't\nenv -u AI_AGENT make test\n# it's", "agent_marker"),
    ("true &|# don't\necho x > ~/.config/hubctl/hub.json\n# it's", "settings_files"),
    ("echo foo \\\n# don't\nhubctl off public_text_gate\n# it's", "hubctl"),
], ids=["hubctl after &!", "the marker after &!", "a settings write after &|", "after a joined comment line"])
def test_a_line_after_a_zsh_comment_is_still_checked(home, command, check):
    with given("hub is installed and the user raised the gate to block in their own terminal"):
        home.install()
        home.hubctl_as_user("set", "public_text_gate", "block", typed="public_text_gate")
        before = home.settings()
    with when("the model runs a script whose middle line zsh runs, because &! ends a command there and the # "
              "after it starts a comment, while the apostrophes would join the lines into one quoted word"):
        result = home.hook("claude_code", "PreToolUse", command)
    with then(f"the {check} check refuses it, and the settings did not change"):
        assert refused_by(result, check), result.reason
        assert home.settings() == before


def test_cursor_refuses_hubctl_in_tmux_too(home):
    with given("hub is installed"):
        home.install()
    with when("the model in Cursor starts hubctl in a tmux session"):
        result = home.hook("cursor", "PreToolUse", "tmux new -d 'hubctl off public_text_gate'")
    with then("the hubctl check refuses it"):
        assert refused_by(result, "hubctl"), result.reason


@pytest.mark.parametrize("command", [
    "tmux new -d -s fix 'hubctl show'",
    "ssh localhost 'hubctl doctor --json'",
    "tmux send-keys -t fix 'make test' Enter",
    "tmux send-keys -t fix 'hubctl set public_text_gate block' Enter",
    "screen -ls",
    "git commit -m 'Refuse hubctl off public_text_gate from a tool call' && make test",
    "echo 'hubctl off public_text_gate' | grep off",
    "ssh localhost 'ls ~/public_text_gate_notes'",
    "tmux send-keys -t fix 'echo core is fine' Enter",
], ids=["tmux with a read", "ssh with a read", "tmux keys without hubctl", "tmux raising the gate",
        "screen list", "a commit message that names a command", "echo into grep", "a word that holds a flag id",
        "a line that holds a part id among other words"])
def test_other_programs_that_only_name_hubctl_or_raise_still_run(home, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model runs {command!r}"):
        result = home.hook("claude_code", "PreToolUse", command)
    with then("it runs"):
        assert result.ran, result.reason


# the hub plugin in Claude Code


@pytest.mark.parametrize("command", [
    "claude plugin disable hub@hub",
    "claude plugin uninstall hub@hub",
    "claude plugins remove hub --scope user",
    "claude plugin disable",
    "claude plugin disable --all",
    "claude plugin disable \"$PLUGIN\"",
    "claude plugin marketplace remove hub",
    "/usr/local/bin/claude plugin rm HUB@hub",
    "bash -c 'claude plugin disable hub@hub'",
    "tmux new -d 'claude plugin uninstall hub@hub'",
], ids=["disable", "uninstall", "remove in a scope", "disable with no name", "disable all", "a hidden name",
        "the marketplace", "by path in capitals", "inside bash -c", "inside tmux"])
def test_disabling_or_uninstalling_the_hub_plugin_is_refused(home, tool, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model runs {command!r}"):
        result = home.hook(tool, "PreToolUse", command)
    with then("the hubctl check refuses it"):
        assert refused_by(result, "hubctl"), result.reason


@pytest.mark.parametrize("command", [
    "claude plugin list",
    "claude plugin disable other@market",
    "claude plugin uninstall hubspot@market",
    "claude plugin validate plugins/hub",
    "claude plugin test plugins/hub",
], ids=["list", "another plugin", "a plugin whose name starts with hub", "validate", "test"])
def test_other_plugin_commands_still_run(home, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model runs {command!r}"):
        result = home.hook("claude_code", "PreToolUse", command)
    with then("it runs"):
        assert result.ran, result.reason


# hub's own plugin files


def test_a_write_into_hubs_own_plugin_files_is_refused_and_a_read_runs(home):
    with given("hub is installed"):
        home.install()
        manifest = os.path.join(home.plugin, "parts", PART, "part.json")
        dispatcher = os.path.join(home.plugin, "lib", "hub", "dispatch.py")
    with when("the model edits the self-protection part's manifest"):
        edit = file_call(home, "Edit", {"file_path": manifest, "old_string": '"default": "on"',
                                        "new_string": '"default": "off"'})
    with then("the settings files check refuses it"):
        assert refused_by(edit, "settings_files"), edit.reason
    with and_("a Write of the dispatcher, a sed -i of hooks.json and an rm of the plugin are refused too"):
        assert refused_by(file_call(home, "Write", {"file_path": dispatcher, "content": ""}), "settings_files")
        hooks = os.path.join(home.plugin, "hooks", "hooks.json")
        assert refused_by(home.hook("claude_code", "PreToolUse", f"sed -i '' 's/a/b/' {hooks}"), "settings_files")
        assert refused_by(home.hook("claude_code", "PreToolUse", f"rm -rf {home.plugin}"), "settings_files")
        parent = os.path.dirname(home.plugin)
        assert refused_by(home.hook("claude_code", "PreToolUse", f"rm -rf {parent}"), "settings_files")
    with and_("reading the manifest and editing a file in the work repo still run"):
        assert home.hook("claude_code", "PreToolUse", f"cat {manifest}").ran
        work_file = os.path.join(home.work, "notes.md")
        assert file_call(home, "Write", {"file_path": work_file, "content": "notes"}).ran


# a part whose protected flag would stop running


def test_turning_a_part_off_that_stops_its_running_guard_needs_the_user(home, tool):
    with given("hub is installed and the user turned the tier guard on"):
        home.install()
        home.hubctl("on", "tier_rule.guard")
        assert flag(home, "tier_rule.guard")["runs"]
        before = home.settings()
    with when("the model runs hubctl off tier_rule in a tool call"):
        result = home.hook(tool, "PreToolUse", "hubctl off tier_rule")
    with then("the hubctl check refuses it"):
        assert refused_by(result, "hubctl"), result.reason
    with and_("hubctl itself refuses it in a tool call, names the guard, and the guard still runs"):
        home.env = dict(home.env, **TOOL_CALL)
        done = home.hubctl("off", "tier_rule", check=False)
        assert done.returncode != 0 and "tier_rule.guard" in done.stderr, done.stderr
        home.env = {name: value for name, value in home.env.items() if name not in TOOL_CALL}
        assert home.settings() == before
        assert flag(home, "tier_rule.guard")["runs"]
    with and_("setup's setter leaves it too"):
        home.set_by("setup", "tier_rule", "off")
        assert home.settings() == before
    with and_("the user's own terminal can not turn it off, even typing the part's id"):
        typed = home.hubctl_as_user("off", "tier_rule", typed="tier_rule", check=False)
        assert typed.returncode != 0 and "/hub in Claude Code" in typed.stderr, typed.stderr
        assert home.settings() == before
    with and_("the user can turn it off with /hub in Claude Code"):
        home.hubctl_from_mod("off", "tier_rule")
        assert not flag(home, "tier_rule.guard")["runs"]


def test_turning_a_part_off_whose_guard_is_off_still_runs_from_a_tool_call(home):
    with given("hub is installed with the tier guard off, as it ships"):
        home.install()
        assert not flag(home, "tier_rule.guard")["runs"]
    with when("the model runs hubctl off tier_rule in a tool call"):
        result = home.hook("claude_code", "PreToolUse", "hubctl off tier_rule")
        home.env = dict(home.env, **TOOL_CALL)
        done = home.hubctl("off", "tier_rule", check=False)
    with then("the hook lets it run and hubctl turns the part off"):
        assert result.ran, result.reason
        assert done.returncode == 0, done.stderr
        assert not flag(home, "tier_rule")["runs"]


def test_turning_a_part_off_that_stops_a_pinned_flag_needs_the_user(home):
    with given("the user pinned an instruction of the tier part from their terminal"):
        home.install()
        home.hubctl("pin", "tier_rule.check_claims")
    with when("the model turns the tier part off in a tool call"):
        result = home.hook("claude_code", "PreToolUse", "hubctl off tier_rule")
    with then("the hubctl check refuses it, because the pin would stop running"):
        assert refused_by(result, "hubctl"), result.reason
    with and_("hubctl itself refuses it in a tool call, names the pin, and the pin still runs"):
        before = home.settings()
        home.env = dict(home.env, **TOOL_CALL)
        done = home.hubctl("off", "tier_rule", check=False)
        assert done.returncode != 0 and "tier_rule.check_claims" in done.stderr, done.stderr
        home.env = {name: value for name, value in home.env.items() if name not in TOOL_CALL}
        assert home.settings() == before
        assert flag(home, "tier_rule.check_claims")["runs"]
    with and_("setup's setter leaves it too"):
        home.set_by("setup", "tier_rule", "off")
        assert home.settings() == before
    with and_("the user's own hubctl turns the part off without typing anything, since the pin is theirs"):
        home.hubctl("off", "tier_rule")
        assert not flag(home, "tier_rule")["runs"]
        assert not flag(home, "tier_rule.check_claims")["runs"]
