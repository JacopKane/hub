"""Only the mod's /hub door lowers a protected flag.

hub has no terminal tool. hubctl in a terminal, whether the user's own, a
tmux pane or an ssh session, can raise a protected flag and change a flag
that is not protected, but it never lowers a protected one. The mod proves
each change it makes for the user with a single-use ticket on stdin. No
environment variable or argument claims a door.
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys

from steps import and_, given, then, when

GATE = "demo_gate"
TOOL_CALL = {"AI_AGENT": "claude-code_2-1-319_agent", "CLAUDECODE": "1"}


def gate_value(home):
    return home.show("--all")["flags"][GATE]["value"]


def raised_gate(home):
    home.install()
    home.hubctl_as_user("set", GATE, "block")
    assert gate_value(home) == "block"


def test_no_terminal_lowers_a_protected_flag_and_only_a_fresh_mod_ticket_does(home):
    with given("hub is installed and the user raised the gate to block in their own terminal"):
        raised_gate(home)
    with when("the user runs hubctl off for the gate in their own terminal and types its id"):
        typed = home.hubctl_as_user("off", GATE, typed=GATE, check=False)
    with then("it is refused, the refusal points to /hub, and the gate still blocks"):
        assert typed.returncode == 1 and "/hub" in typed.stderr, typed.stderr
        assert gate_value(home) == "block"
    with when("hubctl runs with HUB_DOOR=mod, no marker and no terminal"):
        env = {name: value for name, value in home.env.items() if name not in ("AI_AGENT", "CURSOR_AGENT")}
        claimed = subprocess.run([sys.executable, os.path.join(home.plugin, "lib", "hub_switch.py"), "off", GATE], cwd=home.work,
                                 env={**env, "HUB_DOOR": "mod"}, input="", capture_output=True, text=True,
                                 timeout=60)
    with then("the claim is ignored and nothing changes"):
        assert claimed.returncode == 1 and gate_value(home) == "block", claimed.stderr
    with when("the mod's call carries a ticket that matches no ticket file, or one older than a minute"):
        wrong = home.hubctl_from_mod("off", GATE, ticket="0" * 64, check=False)
        old = home.hubctl_from_mod("off", GATE, ticket=home.mod_ticket(age_seconds=61), check=False)
    with then("both are refused and the gate still blocks"):
        assert wrong.returncode == 1 and old.returncode == 1, (wrong.stderr, old.stderr)
        assert gate_value(home) == "block"
    with when("a tool call hands hubctl a fresh ticket"):
        stolen = home.mod_ticket()
        in_tool_call = subprocess.run([sys.executable, os.path.join(home.plugin, "lib", "hub_switch.py"), "off", GATE], cwd=home.work,
                                      env={**home.env, **TOOL_CALL}, input=stolen + "\n", capture_output=True,
                                      text=True, timeout=60)
    with then("it is refused, since a tool call never comes through a door"):
        assert in_tool_call.returncode == 1 and gate_value(home) == "block", in_tool_call.stderr
    with when("the mod hands hubctl a fresh ticket for the user's /hub off"):
        ticket = home.mod_ticket()
        home.hubctl_from_mod("off", GATE, ticket=ticket)
    with then("the gate is off, pinned by the user through the mod, and the ticket file is gone"):
        assert home.settings()["flags"][GATE] == {"value": "off", "by": "user"}
        assert home.history()[-1]["door"] == "mod"
        digest = hashlib.sha256(ticket.encode()).hexdigest()
        assert not os.path.exists(os.path.join(home.state, "doors", digest))
    with when("the user raises it again in a terminal, and the same ticket is sent a second time"):
        home.hubctl_as_user("set", GATE, "block")
        again = home.hubctl_from_mod("off", GATE, ticket=ticket, check=False)
    with then("the raise worked from the terminal, and the used ticket changes nothing"):
        assert again.returncode == 1 and gate_value(home) == "block", again.stderr
    with and_("a flag that is not protected still changes from the terminal"):
        home.hubctl_as_user("off", f"{GATE}.dashes")
        assert home.settings()["flags"][f"{GATE}.dashes"]["value"] == "off"


def test_the_users_hub_lowering_works_when_the_doors_folder_is_missing(home):
    with given("hub is installed, the user raised the gate to block, and hub's state folder has no doors folder"):
        raised_gate(home)
        shutil.rmtree(os.path.join(home.state, "doors"), ignore_errors=True)
        assert not os.path.exists(os.path.join(home.state, "doors"))
    with when("the mod reads the screen model before a change, as it does for every /hub change"):
        model = json.loads(home.hubctl_from_mod("screen", "--json", "--band", ticket="").stdout)
    with then("the doors folder it names exists, with the same rights as the state folder"):
        folder = model["doors"]
        assert os.path.isdir(folder) and not os.path.islink(folder), folder
        assert os.stat(folder).st_mode & 0o7777 == os.stat(home.state).st_mode & 0o7777
    with when("the mod writes its ticket file with a write that makes no folders, and sends the user's /hub off"):
        ticket = secrets.token_hex(32)
        with open(os.path.join(folder, hashlib.sha256(ticket.encode()).hexdigest()), "x") as handle:
            handle.write("{}")
        home.hubctl_from_mod("off", GATE, ticket=ticket)
    with then("the gate is off, pinned by the user through the mod"):
        assert home.settings()["flags"][GATE] == {"value": "off", "by": "user"}
