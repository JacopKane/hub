"""The model can not lower hub's protections from a tool call.

Each scenario sends the tool call that a model trying to unblock itself
would make, through the real dispatcher, as Claude Code or Cursor sends it.
The self_protection part refuses it before the call runs. The refusal says
only the user changes hub's protections, from the hub screen or their own
terminal. Calls that only read hub's files, and changes that do not lower a
protection, still run.
"""
import json
import os

import pytest

from conftest import CONFIG_NAME, REPO, load_fixture
from steps import and_, given, then, when

PART = "self_protection"


def refused_by(result, check):
    return result.refused and f"({PART}.{check})" in result.reason


def asks_the_user(result):
    text = result.reason.lower()
    return "user" in text and "terminal" not in text and "in their own words" in text


def file_call(home, tool_name, tool_input):
    """A Claude Code Edit or Write call, shaped like the recorded Bash call."""
    data = load_fixture("claude_code", "PreToolUse.Bash.json")
    data.update(cwd=home.work, tool_name=tool_name, tool_input=tool_input,
                tool_use_id=f"call_file_{tool_name}_{len(tool_input.get('file_path', ''))}")
    return home.send("claude_code", "PreToolUse", data)


# hubctl from a tool call


@pytest.mark.parametrize("command", [
    "hubctl off public_text_gate",
    "hubctl off public_text_gate.private_paths --scope local",
    "hubctl on public_text_gate.public_repos_only",
    "hubctl off self_protection",
    "hubctl uninstall",
    "python3 -I plugins/hub/bin/hubctl off public_text_gate.secrets",
    "cd /tmp && nohup hubctl off core --json 2>&1",
], ids=["gate mode", "privacy check in the local scope", "a loosening option",
        "this part", "uninstall", "by path through python", "behind cd and nohup"])
def test_a_tool_call_that_runs_hubctl_to_lower_a_protection_is_refused(home, tool, command):
    with given("hub is installed with the gate at block"):
        home.install()
        home.hubctl("set", "public_text_gate", "block")
        before = home.settings()
    with when(f"the model runs {command!r} in a tool call"):
        result = home.hook(tool, "PreToolUse", command)
    with then("the call is refused by the hubctl check and asks the model to ask the user"):
        assert refused_by(result, "lowering"), result.reason
        assert asks_the_user(result), result.reason
    with and_("the settings did not change"):
        assert home.settings() == before


def test_a_tool_call_can_not_claim_to_be_the_user(home, tool):
    with given("hub is installed with the gate at block"):
        home.install()
        home.hubctl("set", "public_text_gate", "block")
        before = home.settings()
    with when("the model runs hubctl with a --by user option in a tool call, and the hook lets it through or not"):
        result = home.hook(tool, "PreToolUse", "hubctl set public_text_gate warn --by user")
        home.env = dict(home.env, AI_AGENT="claude-code_2-1-319_agent", CLAUDECODE="1")
        done = home.hubctl("set", "public_text_gate", "warn", "--by", "user", check=False)
    with then("either the hook refuses it, or hubctl rejects the option, and the settings did not change"):
        assert result.refused or done.returncode != 0, done.stdout
        assert home.settings() == before


def test_a_pinned_flag_can_not_be_changed_from_a_tool_call_even_in_another_scope(home, tool):
    with given("the user turned public_text_gate.dashes on and pinned it from their own terminal"):
        home.install()
        home.hubctl("on", "public_text_gate.dashes")
        home.hubctl("pin", "public_text_gate.dashes")
    with when("the model turns it off in the local scope from a tool call"):
        result = home.hook(tool, "PreToolUse", "hubctl off public_text_gate.dashes --scope local")
    with then("the call is refused, because a pin is the user's"):
        assert refused_by(result, "lowering"), result.reason


@pytest.mark.parametrize("command", [
    "bash -c 'hubctl off public_text_gate'",
    "echo done $(hubctl uninstall)",
    "sh <<'EOF'\ncd ~\nhubctl off public_text_gate.secrets\nEOF",
    "eval hubctl uninstall",
    "echo off public_text_gate | xargs hubctl",
    "hubctl off \"$FLAG\"",
], ids=["bash -c", "substitution", "heredoc script", "eval", "xargs", "hidden flag"])
def test_hubctl_inside_a_wrapper_or_a_substitution_is_refused_too(home, tool, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model hides the call as {command!r}"):
        result = home.hook(tool, "PreToolUse", command)
    with then("the call is refused"):
        assert refused_by(result, "lowering"), result.reason


@pytest.mark.parametrize("command", [
    "hubctl off public_text_gate.dashes",
    "hubctl show",
    "hubctl doctor --json",
    "hubctl set public_text_gate block",
    "hubctl on status_band",
    "which hubctl && cat plugins/hub/bin/hubctl",
], ids=["dashes is a style flag", "show", "doctor", "raising the gate", "an unprotected part", "reading the program"])
def test_hubctl_calls_that_lower_no_protection_still_run(home, tool, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model runs {command!r}"):
        result = home.hook(tool, "PreToolUse", command)
    with then("it runs"):
        assert result.ran, result.reason


# the tool call marker


@pytest.mark.parametrize("command", [
    "env -u AI_AGENT hubctl show",
    "env --unset=AI_AGENT python3 tool.py",
    "env -i PATH=/usr/bin python3 tool.py",
    "AI_AGENT= python3 tool.py",
    "AI_AGENT=terminal hubctl show",
    "unset AI_AGENT; python3 tool.py",
    "export CURSOR_AGENT=0 && python3 tool.py",
    "python3 -c \"import os; os.environ.pop('AI_AGENT')\"",
], ids=["env -u", "env --unset", "env -i", "empty prefix", "other value", "unset", "export", "python"])
def test_a_tool_call_that_unsets_or_overrides_the_agent_marker_is_refused(home, tool, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model runs {command!r}"):
        result = home.hook(tool, "PreToolUse", command)
    with then("the call is refused by the marker check"):
        assert refused_by(result, "agent_marker"), result.reason
        assert asks_the_user(result), result.reason


def test_reading_the_agent_marker_still_runs(home, tool):
    with given("hub is installed"):
        home.install()
    with when("the model prints the marker"):
        result = home.hook(tool, "PreToolUse", 'echo "$AI_AGENT" && printenv CURSOR_AGENT')
    with then("it runs"):
        assert result.ran, result.reason


# hub's settings and state files


@pytest.mark.parametrize("command", [
    "echo '{}' > ~/.config/hubctl/hub.json",
    "cat >> \"$XDG_STATE_HOME/hubctl/signals.jsonl\" <<'EOF'\n{\"flag\": \"public_text_gate\"}\nEOF",
    "printf x | tee ~/.local/state/hubctl/tuner/pending.json",
    "sed -i '' 's/block/off/' ~/.config/hubctl/hub.json",
    "cp /tmp/forged.json $HOME/.config/hubctl/hub.json",
    "mv /tmp/forged.json ${XDG_CONFIG_HOME}/hubctl/hub.json",
    "rm -rf ~/.local/state/hubctl",
    f"cd ~/.config/{CONFIG_NAME} && echo '{{}}' >hub.json",
    "python3 -c \"open('/x/.config/hubctl/hub.json', 'w').write('{}')\"",
    "perl -pi -e 's/block/off/' .hub/hub.json",
    "echo '{}' > .hub/hub.local.json",
    "dd if=/tmp/forged.json of=$HOME/.config/hubctl/hub.json",
], ids=["redirect", "heredoc append with a variable", "tee", "sed -i", "cp", "mv", "rm", "relative after cd",
        "python one-liner", "perl -i on the project file", "the local project file", "dd"])
def test_a_tool_call_that_writes_hubs_settings_or_state_is_refused(home, tool, command):
    with given("hub is installed"):
        home.install()
        before = home.settings() if os.path.exists(home.user_file) else None
    with when(f"the model runs {command!r}"):
        result = home.hook(tool, "PreToolUse", command)
    with then("the call is refused by the settings files check"):
        assert refused_by(result, "settings_files"), result.reason
        assert asks_the_user(result), result.reason
    with and_("nothing changed"):
        assert (home.settings() if os.path.exists(home.user_file) else None) == before


@pytest.mark.parametrize("command", [
    "cat ~/.config/hubctl/hub.json",
    "ls -la ~/.local/state/hubctl",
    "tail -n 5 ~/.local/state/hubctl/errors.log | grep dispatch",
    "jq .flags ~/.config/hubctl/hub.json > /tmp/flags.json",
    "cp ~/.config/hubctl/hub.json /tmp/hub-copy.json",
    "git diff -- .hub/hub.json",
], ids=["cat", "ls", "tail and grep", "jq to another file", "a copy out", "git diff"])
def test_reading_hubs_files_still_runs(home, tool, command):
    with given("hub is installed"):
        home.install()
    with when(f"the model runs {command!r}"):
        result = home.hook(tool, "PreToolUse", command)
    with then("it runs"):
        assert result.ran, result.reason


@pytest.mark.parametrize("tool_name, extra", [
    ("Edit", {"old_string": "block", "new_string": "off"}),
    ("Write", {"content": "{}"}),
])
def test_edit_and_write_on_hubs_settings_are_refused(home, tool_name, extra):
    with given("hub is installed"):
        home.install()
    with when(f"the model calls {tool_name} on hub.json"):
        result = file_call(home, tool_name, dict(extra, file_path=home.user_file))
    with then("the call is refused by the settings files check"):
        assert refused_by(result, "settings_files"), result.reason
    with when(f"the model calls {tool_name} on a file of its own repo"):
        ordinary = file_call(home, tool_name, dict(extra, file_path=os.path.join(home.work, "notes.md")))
    with then("that call runs"):
        assert ordinary.ran, ordinary.reason


# order, switches and failure


def test_self_protection_answers_before_the_public_text_gate(home):
    with given("hub is installed with the gate on"):
        home.install()
    with when("one command both commits a private path and uninstalls hub"):
        result = home.hook("claude_code", "PreToolUse",
                           "git commit -q --allow-empty -m 'see /home/sam/plan.md' && hubctl uninstall")
    with then("the refusal is self_protection's, not the gate's"):
        assert refused_by(result, "lowering") and result.findings == [], result.reason


def test_the_user_can_turn_one_check_off_with_hub_in_claude_code(home):
    with given("hub is installed"):
        home.install()
    with when("the user tries to turn the settings files check off in their own terminal, typing its id"):
        typed = home.hubctl_as_user("off", f"{PART}.settings_files", check=False)
    with then("it is refused and says only the user can ask in their own words, and a write of hub.json is still refused"):
        assert typed.returncode != 0 and "in their own words" in typed.stderr, typed.stderr
        assert refused_by(home.hook("claude_code", "PreToolUse", "echo '{}' > ~/.config/hubctl/hub.json"),
                          "settings_files")
    with when("the user turns it off through the mod, and the model writes hub.json"):
        home.hubctl_from_mod("off", f"{PART}.settings_files")
        result = home.hook("claude_code", "PreToolUse", "echo '{}' > ~/.config/hubctl/hub.json")
    with then("that call runs, and the other checks still refuse"):
        assert result.ran, result.reason
        assert refused_by(home.hook("claude_code", "PreToolUse", "hubctl uninstall"), "lowering")


def test_a_broken_hub_still_refuses_a_call_that_touches_hub(home, tool):
    with given("hub's settings file is broken, so the dispatcher can not resolve"):
        home.install()
        os.makedirs(home.config, exist_ok=True)
        with open(home.user_file, "w") as handle:
            handle.write("{ not json")
    with when("the model runs hubctl to turn the gate off"):
        result = home.hook(tool, "PreToolUse", "hubctl off public_text_gate", errors_expected=True)
    with then("the call is refused and asks the model to ask the user"):
        assert result.refused and asks_the_user(result), result.reason
    with and_("an ordinary call still runs"):
        assert home.hook(tool, "PreToolUse", "ls -la", errors_expected=True).ran


def test_signals_count_each_refusal(home):
    with given("hub is installed"):
        home.install()
    with when("one hubctl call is refused"):
        home.hook("claude_code", "PreToolUse", "hubctl uninstall")
    with then("one blocked signal is logged for the hubctl check"):
        found = [(line["flag"], line["signal"]) for line in home.signals() if line["flag"].startswith(PART)]
        assert found == [(f"{PART}.lowering", "blocked")], json.dumps(home.signals())


def test_the_security_doc_states_what_the_local_checks_can_not_do():
    with given("the repo's SECURITY.md"):
        with open(os.path.join(REPO, "SECURITY.md"), encoding="utf-8") as handle:
            text = handle.read()
    with then("its design limits section says the checks stop accidents, not a model set on getting around them"):
        assert "## Design limits" in text
        limits = text.split("## Design limits", 1)[1].split("\n## ", 1)[0]
        assert "by accident" in limits and "unblock" in limits
        assert "on purpose" in limits and "not a sandbox" in limits
    with and_("it says every change hub makes shows with an undo"):
        assert "undo" in limits
