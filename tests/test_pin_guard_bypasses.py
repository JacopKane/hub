"""The ways the model could lower a protected flag, each one closed.

A protected flag is a privacy check, the mode of a gate part, a guard, or a
value the user pinned. Only the user door lowers one: the mod, for a change the
user typed or clicked in /hub, sends hubctl a fresh single-use ticket. hubctl
in a terminal can raise a protected flag but never lower it. Every scenario runs as a command the model starts (AI_AGENT
ends in "_agent") in a temporary HOME, tries one way around that rule, and
then checks that the protected flag still runs as before.
"""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

from steps import and_, given, then, when

TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1"}
CURSOR_TOOL_CALL = {"CURSOR_AGENT": "1"}
GATE = "public_text_gate"
SECRETS = "public_text_gate.secrets"
DASHES = "public_text_gate.dashes"


def as_model(home, caller=TOOL_CALL):
    home.env = dict(home.env, **caller)


def flag(home, flag_id, *args):
    return home.show("--all", *args)["flags"][flag_id]


def still_runs(home, flag_id, value):
    record = flag(home, flag_id)
    assert record["runs"] and record["value"] == value, record


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def write_answer(home, answer):
    path = os.path.join(home.root, "answer.json")
    home.write_json(path, answer)
    return path


# Protected flags and the user doors


@pytest.mark.parametrize("caller", [TOOL_CALL, CURSOR_TOOL_CALL], ids=["claude_code", "cursor"])
def test_a_command_the_model_runs_can_not_lower_a_gate_or_a_privacy_check(home, caller):
    with given("hub is installed and nobody changed the gate"):
        home.install()
    with when("the model runs hubctl off for the gate and a privacy check, and set for the gate's mode"):
        as_model(home, caller)
        tries = [home.hubctl("off", GATE, check=False), home.hubctl("off", SECRETS, check=False),
                 home.hubctl("set", GATE, "off", check=False)]
    with then("each is refused, and the refusal says to ask the user"):
        for done in tries:
            assert done.returncode == 1, done.stdout
            assert "ask the user" in done.stderr.lower(), done.stderr
    and_("the gate and the check still run as before")
    still_runs(home, GATE, "warn")
    still_runs(home, SECRETS, "on")


def test_hubctl_with_the_tool_call_mark_taken_away_and_no_terminal_can_not_lower_a_gate(home):
    with given("hub is installed"):
        home.install()
    with when("hubctl runs with neither mark and no terminal, as env -u AI_AGENT gives a tool call"):
        done = home.hubctl("off", GATE, check=False)
    with then("it is refused, points to /hub in Claude Code, and offers no terminal door"):
        assert done.returncode == 1
        assert "/hub in Claude Code" in done.stderr and "own terminal" not in done.stderr, done.stderr
    and_("the gate still runs")
    still_runs(home, GATE, "warn")


def test_the_user_lowers_a_gate_with_hub_in_claude_code_and_never_in_their_own_terminal(home):
    with given("hub is installed"):
        home.install()
    with when("the user runs hubctl off for the gate in a terminal, typing something else or the flag's name"):
        tries = [home.hubctl_as_user("off", GATE, typed=text, check=False) for text in ("yes", GATE)]
    with then("nothing changes, and each refusal names the gate and points to /hub in Claude Code"):
        for done in tries:
            assert done.returncode == 1
            assert GATE in done.stderr and "/hub in Claude Code" in done.stderr, done.stderr
        still_runs(home, GATE, "warn")
    with when("the user switches it off with /hub, so the mod sends hubctl a fresh ticket"):
        home.hubctl_from_mod("off", GATE)
    with then("the gate is off, pinned by the user"):
        assert home.settings()["flags"][GATE] == {"value": "off", "by": "user"}
        assert not flag(home, GATE)["runs"]


def test_the_mod_door_lowers_a_gate_only_outside_a_tool_call(home):
    with given("hub is installed"):
        home.install()
    with when("a command the model runs claims to be the mod and hands hubctl a fresh ticket"):
        as_model(home)
        home.env["HUB_DOOR"] = "mod"
        claimed = home.run([*home_hubctl(home), "off", GATE], stdin=home.mod_ticket() + "\n", check=False)
    with then("it is refused, and the gate still runs"):
        assert claimed.returncode == 1
        still_runs(home, GATE, "warn")
    with when("the mod runs it for a change the user typed"):
        home.hubctl_from_mod("off", GATE)
    with then("the gate is off, by the user through the mod"):
        assert home.settings()["flags"][GATE] == {"value": "off", "by": "user"}
        assert home.history()[-1]["door"] == "mod"


def test_a_command_the_model_runs_can_not_undo_the_users_raise_of_a_gate(home):
    with given("the user raised the gate to block"):
        home.install()
        home.hubctl("set", GATE, "block")
    with when("the model runs hubctl undo"):
        as_model(home)
        done = home.hubctl("undo", check=False)
    with then("it is refused, and the gate still blocks"):
        assert done.returncode == 1
        still_runs(home, GATE, "block")


def test_a_tool_call_can_not_add_an_agent_to_the_tier_guards_allowed_list_and_the_user_can(home):
    with given("hub is installed, and the tier guard's allowed list holds its default"):
        home.install()
        before = flag(home, "tier_rule.guard.allowed")["value"]
    with when("the model adds an agent to the allowed list, which loosens the guard"):
        as_model(home)
        done = home.hubctl("set", "tier_rule.guard.allowed", *before, "my-helper", check=False)
    with then("it is refused, and the list is unchanged"):
        assert done.returncode == 1 and "ask the user" in done.stderr.lower(), done.stderr
        assert flag(home, "tier_rule.guard.allowed")["value"] == before
    with when("the user adds it with /hub in Claude Code"):
        home.hubctl_from_mod("set", "tier_rule.guard.allowed", *before, "my-helper")
    with then("the list holds the agent, set by the user"):
        assert flag(home, "tier_rule.guard.allowed")["value"] == [*before, "my-helper"]
        assert flag(home, "tier_rule.guard.allowed")["by"] == "user"


# Effective values and pins across scopes


def test_a_local_file_from_a_tool_call_can_not_lower_a_gate_the_user_raised(home):
    with given("the user raised the gate to block in their own settings"):
        home.install()
        home.hubctl("set", GATE, "block")
    with when("the model sets the gate to warn in this repo's local file, which is the shipped default"):
        as_model(home)
        done = home.hubctl("set", GATE, "warn", "--scope", "local", check=False)
    with then("it is refused, no local file is written, and the gate still blocks in the repo"):
        assert done.returncode == 1
        assert not os.path.exists(os.path.join(home.work, ".hub", "hub.local.json"))
        still_runs(home, GATE, "block")


def test_hubctl_with_no_door_can_not_lower_in_the_local_file_a_gate_that_setup_raised(home):
    with given("setup raised the gate to block, which is no pin"):
        home.install()
        home.set_by("setup", GATE, "block")
    with when("hubctl with neither mark and no terminal sets the gate to warn in this repo's local file"):
        done = home.hubctl("set", GATE, "warn", "--scope", "local", check=False)
    with then("it is refused, because the gate would run weaker in the repo, and it still blocks there"):
        assert done.returncode == 1 and "/hub in Claude Code" in done.stderr, done.stderr
        assert not os.path.exists(os.path.join(home.work, ".hub", "hub.local.json"))
        still_runs(home, GATE, "block")


def test_a_pin_in_one_scope_holds_against_a_tool_call_in_another(home):
    with given("the user switched builder off, which pins it"):
        home.install()
        home.hubctl("off", "builder")
    with when("the model switches builder on in this repo's local file"):
        as_model(home)
        done = home.hubctl("on", "builder", "--scope", "local", check=False)
    with then("it is refused as the user's pin, and builder stays off in the repo"):
        assert done.returncode == 1 and "only the user" in done.stderr.lower()
        assert not flag(home, "builder")["runs"]


# Who set a value


def test_a_change_from_a_tool_call_is_set_by_the_model_and_is_never_a_pin(home):
    with given("hub is installed, with setup's answer that raises the gate to block"):
        home.install()
        answer = write_answer(home, {"profile": {"this_machine": "laptop"}, "questions": [
            {"id": "gate_mode", "text": "How strict should the gate be?",
             "options": [{"label": "Block", "does": "Stops the call.", "flags": {GATE: "block"}}]}]})
    with when("the model sets the gate to warn, its default, and switches builder off"):
        as_model(home)
        home.hubctl("set", GATE, "warn")
        home.hubctl("off", "builder")
    with then("the gate's value is not kept, and builder's entry is by the model"):
        flags = home.settings()["flags"]
        assert GATE not in flags
        assert flags["builder"] == {"value": "off", "by": "model"}
        assert home.history()[-1]["by"] == "model"
    and_("a --by option is no longer taken")
    assert home.hubctl("set", "builder", "on", "--by", "user", check=False).returncode == 2
    with when("the user then picks Block in setup"):
        dialog = json.loads(home.hubctl("setup", "--answer", answer).stdout)
        home.hubctl("setup", "--reply", json.dumps({dialog["questions"][0]["id"]: "Block"}))
    with then("the gate blocks, since the model's write was no pin"):
        still_runs(home, GATE, "block")


# Setup


def test_setup_can_not_lower_a_gate_or_a_privacy_check(home):
    with given("hub is installed, with an answer whose option also switches the gate and secrets off, "
               "and a second question whose only option switches the gate off"):
        home.install()
        answer = write_answer(home, {"profile": {"this_machine": "laptop"}, "questions": [
            {"id": "use", "text": "What do you use this machine for?",
             "options": [{"label": "Work", "does": "Marks it a work machine.", "machine": {"use": "work"},
                          "flags": {GATE: "off", SECRETS: "off"}}]},
            {"id": "quiet", "text": "Should hub stay quiet?",
             "options": [{"label": "Yes", "does": "Switches the gate off.", "flags": {GATE: "off"}}]}]})
    with when("the model runs setup"):
        as_model(home)
        dialog = json.loads(home.hubctl("setup", "--answer", answer).stdout)
    with then("the dialog leaves out the question that would only lower the gate"):
        assert [question["topic"] for question in dialog["questions"]] == ["use"]
    with when("the reply picks the first option"):
        home.hubctl("setup", "--reply", json.dumps({dialog["questions"][0]["id"]: "Work"}))
    with then("the gate and secrets still run, and the machine fact landed"):
        still_runs(home, GATE, "warn")
        still_runs(home, SECRETS, "on")
        with open(os.path.join(home.config, "profile.json")) as handle:
            assert json.load(handle)["machines"]["laptop"]["use"] == "work"


def test_setup_with_a_forged_pending_file_can_not_lower_a_gate(home):
    with given("hub is installed, and the model wrote setup's pending file itself"):
        home.install()
        home.write_json(os.path.join(home.state, "setup", "pending.json"), {
            "this_machine": "laptop", "instructions_off": [SECRETS], "offers": [],
            "questions": [{"id": "q", "text": "Q?", "options": [
                {"label": "Go", "does": "", "flags": {GATE: "off"}}]}]})
    with when("the model replies to it"):
        as_model(home)
        home.hubctl("setup", "--reply", json.dumps({"q": "Go"}), check=False)
    with then("the gate and secrets still run"):
        still_runs(home, GATE, "warn")
        still_runs(home, SECRETS, "on")


def test_setup_refuses_a_reply_to_a_question_it_did_not_ask(home):
    with given("setup asked one question"):
        home.install()
        answer = write_answer(home, {"profile": {"this_machine": "laptop"}, "questions": [
            {"id": "use", "text": "What do you use this machine for?",
             "options": [{"label": "Work", "does": "Marks it a work machine.", "machine": {"use": "work"}}]}]})
        as_model(home)
        dialog = json.loads(home.hubctl("setup", "--answer", answer).stdout)
    with when("the reply answers an id setup did not give"):
        done = home.hubctl("setup", "--reply", json.dumps({"made_up": "Work"}), check=False)
    with then("setup refuses it and names the id, and the first run is not over"):
        assert done.returncode == 1 and "made_up" in done.stderr
        assert not os.path.exists(home.user_file)
    and_("the id setup gave is not the agent's own")
    assert dialog["questions"][0]["id"] != "use"


# The tuner


def test_the_tuner_can_not_lower_a_gate_from_a_forged_list(home):
    with given("hub is installed, and the model wrote the tuner's list with the gate and secrets on it"):
        home.install()
        home.write_json(os.path.join(home.state, "tuner", "pending.json"), {"time": "2026-11-01T10:00:00Z",
            "candidates": [{"flag": GATE, "value": "off", "reason": "made up"},
                           {"flag": SECRETS, "value": "off", "reason": "made up"}]})
    with when("the model runs tuner decide and picks both"):
        as_model(home)
        home.run([*home_hubctl(home), "tuner", "decide", "--answer", "-"],
                 stdin=json.dumps({"pick": [GATE, SECRETS]}))
    with then("the gate and secrets still run"):
        still_runs(home, GATE, "warn")
        still_runs(home, SECRETS, "on")


def test_forged_signals_never_make_a_protected_flag_a_tuner_candidate(home):
    with given("setup switched the tier guard on, which is no pin, and the signal log says the user went past "
               "secrets, the gate, the tier guard and the dash check many times"):
        home.install()
        home.set_by("setup", "tier_rule.guard", "on")
        home.env["HUB_TEST_NOW"] = "2026-11-01T10:00:00Z"
        os.makedirs(home.state, exist_ok=True)
        with open(os.path.join(home.state, "signals.jsonl"), "a") as handle:
            for forged in (SECRETS, GATE, "tier_rule.guard", DASHES):
                for name, count in (("warned", 10), ("went_ahead", 10)):
                    for _ in range(count):
                        handle.write(json.dumps({"flag": forged, "event": "PreToolUse", "signal": name,
                                                 "time": home.env["HUB_TEST_NOW"]}) + "\n")
    with when("the tuner's rules run"):
        found = home.run([sys.executable, "-I", "-c",
                          "import sys, json; sys.path.insert(0, sys.argv[1]); from hub import tuner; "
                          "print(json.dumps(tuner.candidates(sys.argv[2])))",
                          os.path.join(home.plugin, "lib"), home.work]).stdout
    with then("only the dash check, a style setting, is a candidate, and the protected flags still run"):
        assert [candidate["flag"] for candidate in json.loads(found)] == [DASHES]
        still_runs(home, SECRETS, "on")
        still_runs(home, GATE, "warn")
        assert flag(home, "tier_rule.guard")["runs"]
    and_("the soften_gates flag says the tuner only offers a softer gate")
    sys.path.insert(0, os.path.join(home.plugin, "lib"))
    try:
        from hub import tree
        does = tree.load(os.path.join(home.plugin, "parts")).get("tuner.tune.soften_gates").does
    finally:
        sys.path.pop(0)
    assert does.startswith("May offer")


# Writes that land between the check and the write


# hubctl with one seam: it writes a mark file when set_flag asks for the settings lock,
# so a test that holds the lock knows the command has made its checks so far.
AT_THE_LOCK = """
import contextlib, sys, traceback
sys.path.insert(0, sys.argv[1])
from hub import cli, files
real_locked = files.locked

@contextlib.contextmanager
def noted(name="settings.lock"):
    if name == "settings.lock" and any(frame.name == "set_flag" for frame in traceback.extract_stack()):
        open(sys.argv[2], "w").close()
    with real_locked(name):
        yield

files.locked = noted
sys.exit(cli.main(sys.argv[3:]))
"""


def write_while_hubctl_waits_at_the_lock(home, args, change_user_file, from_the_mod=False, path=None):
    """Run hubctl while the test holds the settings lock, change a settings file once hubctl waits for it,
    then let hubctl go on. Returns the finished hubctl.

    change_user_file changes the file's data in place, or returns text to write as it is. The file is the
    user's unless path names another. With from_the_mod, hubctl runs as the mod runs it for the user,
    with a fresh ticket waiting on stdin.
    """
    path = path or home.user_file
    mark = os.path.join(home.root, "at-the-lock")
    os.makedirs(home.state, exist_ok=True)
    env, stdin = home.env, None
    if from_the_mod:
        env = {name: value for name, value in home.env.items() if name not in (*TOOL_CALL, *CURSOR_TOOL_CALL, "HUB_DOOR")}
        stdin = subprocess.PIPE
    with open(os.path.join(home.state, "settings.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            process = subprocess.Popen([sys.executable, "-I", "-c", AT_THE_LOCK, os.path.join(home.plugin, "lib"),
                                        mark, *args], cwd=home.work, env=env, stdin=stdin, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            if from_the_mod:
                process.stdin.write(home.mod_ticket() + "\n")
                process.stdin.close()
            deadline = time.monotonic() + 30
            while not os.path.exists(mark):
                assert process.poll() is None, process.communicate()
                assert time.monotonic() < deadline, "hubctl never asked for the settings lock"
                time.sleep(0.02)
            data = read_json(path) if os.path.exists(path) else {"version": 1, "flags": {}}
            text = change_user_file(data)
            if isinstance(text, str):
                write_text(path, text)
            else:
                home.write_json(path, data)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    stdout, stderr = process.communicate(timeout=60)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def test_a_raise_that_lands_while_the_model_sets_the_gate_to_its_default_still_holds(home):
    with given("hub is installed, and the gate runs at its default, warn"):
        home.install()
    with when("the model sets the gate to warn, and setup raises it to block before the model's write"):
        as_model(home)

        def setup_raises_the_gate(data):
            data.setdefault("flags", {})[GATE] = {"value": "block", "by": "setup"}

        done = write_while_hubctl_waits_at_the_lock(home, ["set", GATE, "warn"], setup_raises_the_gate)
    with then("the model's change is refused and says to ask the user, and the gate still blocks"):
        assert done.returncode == 1 and "ask the user" in done.stderr.lower(), done.stderr
        assert home.settings()["flags"][GATE] == {"value": "block", "by": "setup"}
        still_runs(home, GATE, "block")


def test_a_pin_that_lands_while_the_model_writes_another_scope_still_holds(home):
    with given("hub is installed, and builder runs"):
        home.install()
        assert flag(home, "builder")["runs"]
    with when("the model switches builder off in this repo's local file, and the user pins it on before the write"):
        as_model(home)

        def user_pins_builder(data):
            data.setdefault("flags", {})["builder"] = {"value": "on", "by": "user"}

        done = write_while_hubctl_waits_at_the_lock(home, ["off", "builder", "--scope", "local"], user_pins_builder)
    with then("the model's change is refused as the user's pin, and builder still runs in the repo"):
        assert done.returncode == 1 and "only the user" in done.stderr.lower(), done.stderr
        assert flag(home, "builder")["runs"]


def test_a_raise_that_lands_while_the_users_change_waits_asks_the_user_to_run_it_again(home):
    with given("hub is installed, and the gate runs at its default, warn, so setting warn reads no ticket"):
        home.install()
    with when("the user sets the gate to warn with /hub, and setup raises it to block before the write"):
        def setup_raises_the_gate(data):
            data.setdefault("flags", {})[GATE] = {"value": "block", "by": "setup"}

        done = write_while_hubctl_waits_at_the_lock(home, ["set", GATE, "warn"], setup_raises_the_gate,
                                                    from_the_mod=True)
    with then("nothing changes, the user is told to run it again, and the gate still blocks"):
        assert done.returncode == 1 and "changed while hubctl ran" in done.stderr, done.stderr
        assert home.settings()["flags"][GATE] == {"value": "block", "by": "setup"}
        still_runs(home, GATE, "block")
    with when("the user runs it again with /hub"):
        home.hubctl_from_mod("set", GATE, "warn")
    with then("the gate is back at warn, pinned by the user"):
        assert home.settings()["flags"][GATE] == {"value": "warn", "by": "user"}
        still_runs(home, GATE, "warn")


# Odd input: look-alike ids, case, spaces, quotes, a scope given twice, a half-written file


# Each one looks like a protected flag's id to a person, and none of them is one.
LOOK_ALIKE_IDS = [
    "\u0440ublic_text_gate",  # a Cyrillic r
    "public_text_gat\u0435",  # a Cyrillic e
    "\uff50ublic_text_gate",  # a full-width p
    "public_text\u200b_gate",  # a zero-width space
    "public_text_gate\u00a0",  # a no-break space
    "Public_Text_Gate",
    "PUBLIC_TEXT_GATE",
    " public_text_gate",
    "public_text_gate ",
    "public_text_gate\t",
    "public_text_gate\n",
    '"public_text_gate"',
    "'public_text_gate'",
    "public_text_gate\\",
    "public_text_gate.Secrets",
    "public_text_gate..secrets",
]

# Each one looks like "off" or "block", and none of them is a value the gate takes.
ODD_GATE_VALUES = ["OFF", "Off", " off", "off ", "off\n", '"off"', "'off'", "off\\", "\u043eff", "Block", "warn,off"]


def nothing_written(home, before):
    assert user_file_text(home) == before
    assert not os.path.exists(os.path.join(home.work, ".hub"))


def history_lines(home):
    return len(home.history()) if os.path.exists(os.path.join(home.state, "history.jsonl")) else 0


def user_file_text(home):
    return open(home.user_file, encoding="utf-8").read() if os.path.exists(home.user_file) else None


def test_a_flag_id_that_only_looks_like_a_protected_one_changes_nothing_through_any_door(home):
    with given("hub is installed, and the user has no settings file yet"):
        home.install()
        before, lines = user_file_text(home), history_lines(home)
    for door in ("the model", "the mod, for a change the user typed"):
        with when(f"{door} switches off each look-alike id of the gate and its secrets check"):
            if door == "the model":
                as_model(home)
                run = home.hubctl
            else:
                run = home.hubctl_from_mod
            tries = [(flag_id, run("off", flag_id, check=False)) for flag_id in LOOK_ALIKE_IDS]
        with then("each is refused as an unknown flag, and no file and no history line is written"):
            for flag_id, done in tries:
                assert done.returncode == 1 and "no flag named" in done.stderr, (flag_id, done.stderr)
            nothing_written(home, before)
            assert history_lines(home) == lines
    with and_("the gate and its secrets check still run as before"):
        still_runs(home, GATE, "warn")
        still_runs(home, SECRETS, "on")


def test_a_gate_value_with_odd_case_spaces_or_quotes_is_refused_even_for_the_user(home):
    with given("hub is installed, and the user raised the gate to block"):
        home.install()
        home.hubctl("set", GATE, "block")
        before = user_file_text(home)
    with when("the mod sets the gate to each odd value for a change the user typed"):
        tries = [(value, home.hubctl_from_mod("set", GATE, value, check=False)) for value in ODD_GATE_VALUES]
        tries.append(("off block", home.hubctl_from_mod("set", GATE, "off", "block", check=False)))
    with then("each is refused with the values the gate takes, and the file is as it was"):
        for value, done in tries:
            assert done.returncode == 1, (value, done.stderr)
            assert "takes one of" in done.stderr or "takes one value" in done.stderr, (value, done.stderr)
        nothing_written(home, before)
    with and_("the gate still blocks"):
        still_runs(home, GATE, "block")


def look_alike_tickets(ticket):
    """Lines that look like the ticket to a person or a careless check, and none of them is it."""
    other = "0" if ticket[0] != "0" else "1"
    return [
        ticket.upper(),
        other + ticket[1:],  # one character changed
        ticket[:-1],  # one character short
        ticket + "0",  # one character too many
        ticket[:32],
        ticket[:10] + "\u0430" + ticket[11:],  # a Cyrillic a in place of one character
        f'"{ticket}"',
        hashlib.sha256(ticket.encode()).hexdigest(),  # the ticket file's own name, which anyone can list
        f"{ticket} {ticket}",
        "",
        "yes",
        GATE,
    ]


def test_the_mod_door_takes_only_the_tickets_exact_text(home):
    with given("hub is installed, and the mod made a fresh ticket"):
        home.install()
        ticket = home.mod_ticket()
        ticket_file = os.path.join(home.state, "doors", hashlib.sha256(ticket.encode()).hexdigest())
    with when("hubctl off for the gate gets a look-alike of that ticket"):
        tries = [(text, home.hubctl_from_mod("off", GATE, ticket=text, check=False))
                 for text in look_alike_tickets(ticket)]
    with then("each one changes nothing, writes no settings file, and leaves the real ticket unused"):
        for text, done in tries:
            assert done.returncode == 1 and "/hub in Claude Code" in done.stderr, (text, done.stderr)
        still_runs(home, GATE, "warn")
        assert user_file_text(home) is None
        assert os.path.exists(ticket_file)
    with when("it gets the exact ticket with spaces around it"):
        home.hubctl_from_mod("off", GATE, ticket=f"  {ticket}  ")
    with then("the gate is off, pinned by the user, and the ticket is used up"):
        assert home.settings()["flags"][GATE] == {"value": "off", "by": "user"}
        assert not os.path.exists(ticket_file)


def test_a_scope_given_twice_uses_the_last_one_and_never_lowers_a_protected_flag(home):
    with given("hub is installed"):
        home.install()
        local, project = (os.path.join(home.work, ".hub", name) for name in ("hub.local.json", "hub.json"))
    with when("the model switches the gate off, naming the user scope and a repo scope in both orders"):
        as_model(home)
        tries = [(scopes, home.hubctl("off", GATE, *scopes, check=False))
                 for scopes in (["--scope", "local", "--scope", "user"], ["--scope", "user", "--local"],
                                ["--project", "--scope", "user"], ["--local", "--project"])]
    with then("each is refused for the last scope named, and no settings file is written"):
        for scopes, done in tries:
            refusal = "project file can not lower it" if scopes[-1] == "--project" else "ask the user"
            assert done.returncode == 1 and refusal in done.stderr.lower(), (scopes, done.stderr)
        nothing_written(home, None)
    with when("the mod lowers the secrets check with --scope local and then --project, for the user"):
        shared = home.hubctl_from_mod("off", SECRETS, "--scope", "local", "--project", check=False)
    with then("the last scope, project, is the one checked, so it is refused and no file is written"):
        assert shared.returncode == 1 and "project file can not lower it" in shared.stderr, shared.stderr
        assert not os.path.exists(local) and not os.path.exists(project)
    with when("the mod switches builder off with --project and then --scope local"):
        home.hubctl_from_mod("off", "builder", "--project", "--scope", "local")
    with then("only the local file holds the change"):
        assert read_json(local)["flags"]["builder"] == {"value": "off", "by": "user"}
        assert not os.path.exists(project)
    with and_("the gate and its secrets check still run as before"):
        still_runs(home, GATE, "warn")
        still_runs(home, SECRETS, "on")


def test_a_half_written_settings_file_is_refused_and_never_overwritten(home):
    with given("the user raised the gate to block, and their settings file was cut off half way"):
        home.install()
        home.hubctl("set", GATE, "block")
        whole = user_file_text(home)
        half = whole[:len(whole) // 2]
        write_text(home.user_file, half)
    with when("the mod lowers the gate for the user, and the model switches builder off"):
        by_the_user = home.hubctl_from_mod("off", GATE, check=False)
        as_model(home)
        by_the_model = home.hubctl("off", "builder", check=False)
    with then("both are refused as a file that is not valid JSON, and the file is as it was"):
        for done in (by_the_user, by_the_model):
            assert done.returncode == 1 and "not valid JSON" in done.stderr, done.stderr
        assert user_file_text(home) == half
    with when("the file is whole again"):
        write_text(home.user_file, whole)
    with then("the gate still blocks"):
        still_runs(home, GATE, "block")
    with when("the model switches builder off, and a half-written local file lands before the write"):
        def half_a_local_file(_data):
            return '{"version": 1, "flags": {"public_text_gate": {"value": "of'

        done = write_while_hubctl_waits_at_the_lock(home, ["off", "builder"], half_a_local_file,
                                                    path=os.path.join(home.work, ".hub", "hub.local.json"))
    with then("it is refused as a file that is not valid JSON, and the user's file is as it was"):
        assert done.returncode == 1 and "not valid JSON" in done.stderr, done.stderr
        assert user_file_text(home) == whole


def home_hubctl(home):
    return [sys.executable, os.path.join(home.plugin, "bin", "hubctl")]
