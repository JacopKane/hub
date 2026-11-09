"""Setup on the first run, the situation profile, and the tuner after it.

The setup agent's answers are recorded files (tests/fixtures/agent_answers),
because the cheap model costs money. `claude auth status` and `--version`
come from the replay. Everything else is the real engine in a temporary HOME.
"""
import importlib.util
import json
import os
import re
import shlex
import shutil
import sys
import time

from conftest import FIXTURES, PLUGIN, load_fixture
from steps import and_, given, then, when

ANSWERS = os.path.join(FIXTURES, "agent_answers")
STARTING_SET = {"core", "tuner", "scout", "public_text_gate", "self_protection", "status_band"}
# Parts a new user turns on by hand. Setup never turns them on.
OPT_IN = {"builder", "tier_rule"}


def work_laptop(home):
    """Cursor's folder, iTerm, a repo with a remote and CI, and two agents of the user's own."""
    os.makedirs(os.path.join(home.root, ".cursor"))
    home.env["TERM_PROGRAM"] = "iTerm.app"
    bare = os.path.join(home.root, "remote.git")
    home.git("init", "-q", "--bare", bare)
    home.git("remote", "add", "origin", bare)
    os.makedirs(os.path.join(home.work, ".github", "workflows"))
    with open(os.path.join(home.work, ".github", "workflows", "ci.yml"), "w") as handle:
        handle.write("on: push\n")
    agents = os.path.join(home.root, ".claude", "agents")
    os.makedirs(agents)
    for name in ("my-helper", "my-tester"):
        with open(os.path.join(agents, name + ".md"), "w") as handle:
            handle.write(f"---\nname: {name}\ndescription: mine\n---\nHelp.\n")


def session_start(home, tool):
    name = "SessionStart.json" if tool == "claude_code" else "sessionStart.json"
    data = load_fixture(tool, name)
    data["cwd"] = home.work
    if tool == "cursor":
        data["workspace_roots"] = [home.work]
    return home.send(tool, "SessionStart", data)


def context_of(result):
    """The start text a session start gives the chat, in either tool."""
    if result.tool == "claude_code":
        return result.context
    return result.output.get("additional_context", "")


def running_parts(home):
    flags = home.show("--all")["flags"]
    return {flag for flag, record in flags.items() if "." not in flag and record["runs"]}


def profile(home):
    with open(os.path.join(home.config, "profile.json")) as handle:
        return json.load(handle)


def setup(home, *args):
    return home.hubctl("setup", *args)


def replies(dialog, picks):
    """The user's picks as setup --reply takes them, keyed by the ids hub gave the agent's questions."""
    return json.dumps({question["id"]: picks[question["topic"]] for question in dialog["questions"]
                       if question["topic"] in picks})


def test_first_run_on_a_work_laptop_with_cursor_scans_asks_at_most_three_questions_and_turns_on_the_starting_set(home):
    with given("a fresh HOME with Cursor's folder, iTerm, a repo with a remote and CI, two agents of the user's "
               "own, a git user name and a recorded claude auth status"):
        work_laptop(home)
        home.install()
        assert not os.path.exists(home.user_file)
    with when("the first SessionStart runs"):
        started = time.monotonic()
        first = session_start(home, "claude_code")
        took = time.monotonic() - started
    with then("within a second its context holds the scan and the instruction to run hub setup first"):
        assert took < 1.0, took
        context = context_of(first)
        assert "hub setup" in context and "first" in context
        for fact in ("cursor", "iterm", "claude.ai", "github-actions", "my-helper", "my-tester"):
            assert fact in context.lower(), fact
        assert "sam@example.com" not in context and "00000000-0000-4000-8000-0000000000aa" not in context
    with when("the recorded setup answer goes to hubctl setup --answer"):
        dialog = json.loads(setup(home, "--answer", os.path.join(ANSWERS, "work_laptop.json")).stdout)
    with then("the dialog holds two questions, each with a skip and an id that hub gave it"):
        assert [question["topic"] for question in dialog["questions"]] == ["gate_mode", "use"]
        for question in dialog["questions"]:
            assert question["options"][-1]["label"] == "Skip"
            assert question["id"] != question["topic"]
    with when("the user answers warn and work"):
        summary = setup(home, "--reply", replies(dialog, {"gate_mode": "warn", "use": "work"})).stdout
    with then("profile.json holds this machine with use work"):
        found = profile(home)
        assert found["this_machine"] == "laptop"
        assert found["machines"]["laptop"]["use"] == "work"
        assert found["machines"]["laptop"]["tools"]["cursor"]
        assert "sam@example.com" not in json.dumps(found)
    and_("the starting set runs, and nothing else is on, not even the Cursor beta the profile could suggest")
    assert running_parts(home) == STARTING_SET
    and_("the summary says what is on and why, what works now, what starts next session and where the rest is")
    for part in STARTING_SET - {"core"}:
        assert part in summary, part
    for heading in ("Works now", "Starts next session", "hubctl show"):
        assert heading in summary, heading
    and_("the settings file exists with its version, so the next session does not run setup again")
    assert home.settings()["version"] == 1
    assert "hub setup" not in context_of(session_start(home, "claude_code"))


def builder_link(home):
    return os.path.join(home.root, ".claude", "agents", "hub-builder.md")


def tier_sentence(home):
    with open(os.path.join(home.plugin, "parts", "tier_rule", "text", "delegate_when.md")) as handle:
        return handle.read().strip()


def test_a_new_user_has_builder_and_the_tier_rule_off_until_they_turn_them_on(home):
    with given("a new user on a work laptop who skips every setup question"):
        work_laptop(home)
        home.install()
        session_start(home, "claude_code")
        dialog = json.loads(setup(home, "--answer", os.path.join(ANSWERS, "work_laptop.json")).stdout)
        summary = setup(home, "--reply", replies(dialog, {"gate_mode": "Skip", "use": "Skip"})).stdout
    with then("builder and the tier rule are off by default, and the summary does not list them"):
        flags = home.show("--all")["flags"]
        for part in OPT_IN:
            assert not flags[part]["runs"] and flags[part]["by"] == "default", (part, flags[part])
            assert f"- {part}:" not in summary, part
    and_("there is no builder agent file, and the start text has no tier table and no tier rule sentence")
    assert not os.path.lexists(builder_link(home))
    start_text = home.show()["start_text"]
    assert "| Judgement |" not in start_text and "| Mechanical |" not in start_text
    assert tier_sentence(home) not in start_text
    with when("the user turns builder and the tier rule on"):
        home.hubctl("on", "builder")
        home.hubctl("on", "tier_rule")
    with then("both run, set by the user, and builder's agent file links to the shipped one"):
        flags = home.show("--all")["flags"]
        for part in OPT_IN:
            assert flags[part]["runs"] and flags[part]["by"] == "user", (part, flags[part])
        assert os.readlink(builder_link(home)) == os.path.join(home.plugin, "parts", "builder", "agent.md")
    and_("the start text now holds the tier table with builder's row and the tier rule sentences")
    start_text = home.show()["start_text"]
    assert "| builder | opus | high |" in start_text and "| scout | haiku |" in start_text
    assert tier_sentence(home) in start_text


def test_a_skipped_question_changes_nothing(home):
    with given("the same first run"):
        work_laptop(home)
        home.install()
        session_start(home, "claude_code")
        dialog = json.loads(setup(home, "--answer", os.path.join(ANSWERS, "work_laptop.json")).stdout)
    with when("the user skips both questions"):
        setup(home, "--reply", replies(dialog, {"gate_mode": "Skip", "use": "Skip"}))
    with then("the profile has no use for this machine and no flag changed"):
        assert "use" not in profile(home)["machines"]["laptop"]
        history = home.history() if os.path.exists(os.path.join(home.state, "history.jsonl")) else []
        assert not [line for line in history if line.get("by") == "setup"]
        assert home.settings()["flags"] == {}


def test_compare_rules_turns_off_a_hub_instruction_the_user_already_has(home):
    with given("the user turned the tier rule on, and their CLAUDE.md holds a rule of it that hub also ships"):
        os.makedirs(os.path.join(home.root, ".claude"))
        with open(os.path.join(home.root, ".claude", "CLAUDE.md"), "w") as handle:
            handle.write("Check a cheaper agent's claims before you build on them.\n")
        home.switch_on("tier_rule")
        home.install()
    with when("the recorded setup answer names that instruction and an id hub does not have"):
        setup(home, "--answer", os.path.join(ANSWERS, "compare_rules.json"))
        summary = setup(home, "--reply", "{}").stdout
    with then("the instruction is off by setup and listed in the summary, and the unknown id is dropped"):
        entry = home.settings()["flags"]["tier_rule.check_claims"]
        assert entry == {"value": "off", "by": "setup"}
        assert "tier_rule.check_claims" in summary
        assert "nope.instruction" not in json.dumps(home.settings())


def test_in_cursor_setup_asks_in_one_message(home):
    with given("Cursor's recorded sessionStart input on a first run, with the Cursor beta on for this project"):
        work_laptop(home)
        home.install()
        use_cursor_beta_in_project(home)
    with when("the first run happens"):
        context = context_of(session_start(home, "cursor"))
    with then("the context says to ask the questions in one plain message"):
        assert "hub setup" in context
        assert "one plain message" in context


def test_a_profile_fact_the_user_types_is_pinned_and_a_scan_or_a_tool_call_leaves_it(home):
    with given("a set-up machine"):
        work_laptop(home)
        home.install()
        dialog = json.loads(setup(home, "--answer", os.path.join(ANSWERS, "work_laptop.json")).stdout)
        setup(home, "--reply", replies(dialog, {"use": "work"}))
    with when("the user sets this machine's use to home in their own terminal"):
        said = home.hubctl("profile", "set", "machines.laptop.use", "home").stdout
    with then("the fact is home and pinned, and the line says so"):
        assert profile(home)["machines"]["laptop"]["use"] == "home"
        assert "machines.laptop.use" in profile(home)["pins"]
        assert "pinned" in said
    with when("a scan runs, and a command the model runs tries to set it back"):
        home.hubctl("profile", "scan")
        home.env["AI_AGENT"] = "claude-code_2-1-319_agent"
        refused = home.hubctl("profile", "set", "machines.laptop.use", "work", check=False)
    with then("the fact stays home, and the model's command is refused with the reason"):
        assert profile(home)["machines"]["laptop"]["use"] == "home"
        assert refused.returncode == 1 and "Only the user can" in refused.stderr


# The tuner

DASH = chr(0x2014)
SIGNAL_KEYS = {"flag", "event", "signal", "time"}


def on_day(home, day, hour=10):
    home.env["HUB_TEST_NOW"] = f"2026-11-{day:02d}T{hour:02d}:00:00Z"


def set_up(home):
    """A machine where setup ran and changed nothing."""
    home.install()
    setup(home, "--answer", os.path.join(ANSWERS, "nothing.json"))
    setup(home, "--reply", "{}")


def dash_check_on_by_setup(home):
    """The dash check, off for a new user, turned on by setup. That is no pin, so the tuner may offer it."""
    home.set_by("setup", "public_text_gate.dashes", "on")


def use_cursor_beta_in_project(home):
    """The Cursor beta, off by default, turned on in the project file, so this machine's first run is not over."""
    home.hubctl("on", "cursor_link", "--project")


def seed_signals(home, flag, warned=10, went_ahead=9):
    """Made-up lines in the signal log, as the gate writes them."""
    with open(os.path.join(home.state, "signals.jsonl"), "a") as handle:
        for name, count in (("warned", warned), ("went_ahead", went_ahead)):
            for _ in range(count):
                handle.write(json.dumps({"flag": flag, "event": "PreToolUse", "signal": name,
                                         "time": home.env["HUB_TEST_NOW"]}) + "\n")


def pending(home):
    path = os.path.join(home.state, "tuner", "pending.json")
    return json.load(open(path))["candidates"] if os.path.exists(path) else []


def decide(home, answer):
    return home.hubctl("tuner", "decide", "--answer", os.path.join(ANSWERS, answer))


def test_on_day_9_the_tuner_turns_off_the_dash_warning_with_one_line_and_an_undo_and_never_touches_a_pinned_flag(home):
    messages = [f"Fix the parser {DASH} step {number}" for number in range(10)]
    with given("a set-up machine on a fixed clock, where setup turned the dash check on, which is no pin, and the "
               "user pinned private_paths on"):
        on_day(home, 1)
        set_up(home)
        dash_check_on_by_setup(home)
        home.hubctl("pin", "public_text_gate.private_paths")
    with when("over nine days ten commit messages with a dash are warned, and nine are sent again unchanged"):
        for number, message in enumerate(messages):
            on_day(home, 1 + number * 9 // 10)
            command = f'git commit -q --allow-empty -m "{message}"'
            assert home.hook("claude_code", "PreToolUse", command, session=f"day-{number}").refused
            if number < 9:
                assert home.hook("claude_code", "PreToolUse", command, session=f"day-{number}").ran
    and_("the log also holds as many went-past lines for private_paths, which the user pinned")
    seed_signals(home, "public_text_gate.private_paths")
    with then("the log holds ten warned and nine went_ahead lines for the dash check"):
        dashes = [line["signal"] for line in home.signals() if line["flag"] == "public_text_gate.dashes"]
        assert dashes.count("warned") == 10 and dashes.count("went_ahead") == 9
    with when("the next day's first session starts"):
        on_day(home, 10)
        context = context_of(session_start(home, "claude_code"))
    with then("the chat gets the one candidate and the command that decides it"):
        assert pending(home) == [{"flag": "public_text_gate.dashes", "value": "off",
                                  "reason": "you went past it 9 times"}]
        assert "tuner decide" in context and "public_text_gate.dashes" in context
        assert "private_paths" not in context
    with when("the recorded model answer picks the candidate, the pinned flag and scout"):
        decide(home, "tuner_picks_more.json")
    with then("only the dash check is off, by the tuner, back at its shipped default, and the reason is in the "
               "history"):
        flags = home.settings()["flags"]
        assert "public_text_gate.dashes" not in flags
        assert not home.show("--all")["flags"]["public_text_gate.dashes"]["runs"]
        assert flags["public_text_gate.private_paths"] == {"value": "on", "by": "user"}
        assert "scout" not in flags and home.show()["flags"]["scout"]["runs"]
        line = [line for line in home.history() if line.get("by") == "tuner"]
        assert len(line) == 1 and line[0]["reason"] == "you went past it 9 times"
        assert (line[0]["flag"], line[0]["old"], line[0]["new"]) == ("public_text_gate.dashes", "on", "off")
    and_("the band holds one line for it, with the reason and an undo")
    strip = json.loads(home.hubctl("screen", "--json").stdout)["strip"]
    tuned = [entry for entry in strip if entry["id"] == line[0]["id"]]
    assert len(tuned) == 1 and tuned[0]["reason"] == "you went past it 9 times"
    assert tuned[0]["buttons"] == [{"label": "Undo", "args": ["undo", line[0]["id"]]}]
    with when("the user takes the undo"):
        home.hubctl("undo", line[0]["id"])
    with then("the dash check is back on, pinned"):
        assert home.settings()["flags"]["public_text_gate.dashes"] == {"value": "on", "by": "user"}
    and_("every line of the signal log holds only the allowed keys, and no word of a commit message")
    raw = open(os.path.join(home.state, "signals.jsonl")).read()
    for signal in home.signals():
        assert set(signal) == SIGNAL_KEYS
    for word in ("parser", "step", "Fix"):
        assert word not in raw


def test_the_tuner_runs_at_most_once_a_day_and_only_with_new_signals(home):
    with given("a set-up machine on a fixed clock, where setup turned the dash check on, with signals that pick it"):
        on_day(home, 1, hour=9)
        set_up(home)
        dash_check_on_by_setup(home)
        seed_signals(home, "public_text_gate.dashes")
    with when("two sessions start on one day, with a new signal between them"):
        first = context_of(session_start(home, "claude_code"))
        seed_signals(home, "public_text_gate.dashes", warned=1, went_ahead=1)
        on_day(home, 1, hour=20)
        second = context_of(session_start(home, "claude_code"))
    with then("the tuner ran once"):
        assert "tuner decide" in first
        assert "tuner decide" not in second
    with when("a session starts the next day"):
        on_day(home, 2, hour=10)
        next_day = context_of(session_start(home, "claude_code"))
    with then("it runs again, for the signal that came after the first run"):
        assert "tuner decide" in next_day
    with when("a session starts the day after, with no new signal since the last run"):
        on_day(home, 3, hour=11)
        quiet = context_of(session_start(home, "claude_code"))
    with then("it does not run"):
        assert "tuner decide" not in quiet
    with when("a new signal comes, and a session starts"):
        seed_signals(home, "public_text_gate.dashes", warned=1, went_ahead=0)
        on_day(home, 3, hour=12)
    with then("it runs"):
        assert "tuner decide" in context_of(session_start(home, "claude_code"))


def test_the_tuner_never_picks_a_flag_a_project_file_sets(home):
    with given("the project file sets the dash check on, and the signals would pick it"):
        on_day(home, 1)
        set_up(home)
        home.hubctl("on", "public_text_gate.dashes", "--project")
        project = os.path.join(home.work, ".hub", "hub.json")
        before = open(project).read()
        seed_signals(home, "public_text_gate.dashes")
    with when("the tuner runs at a session start"):
        context = context_of(session_start(home, "claude_code"))
    with then("dashes was not a candidate, and the project file is unchanged"):
        assert pending(home) == [] and "tuner decide" not in context
        assert open(project).read() == before
        assert not os.path.exists(os.path.join(home.work, ".hub", "hub.local.json"))


def test_the_tuners_line_shows_once_as_a_system_message_or_as_cursors_first_line(home):
    with given("a tuner change to the dash check, which setup turned on, on a machine where the status band is "
               "off, and the user turned on the Cursor beta and its chat line"):
        on_day(home, 1)
        set_up(home)
        dash_check_on_by_setup(home)
        home.hubctl("off", "status_band")
        home.hubctl("on", "cursor_link")
        home.hubctl("on", "cursor_link.tuner_chat_line")
        seed_signals(home, "public_text_gate.dashes")
        session_start(home, "claude_code")
        decide(home, "tuner_picks_more.json")
        change = [line for line in home.history() if line.get("by") == "tuner"][0]
    with when("the next session starts in Claude Code and in Cursor"):
        on_day(home, 1, hour=11)
        claude = session_start(home, "claude_code")
        cursor = context_of(session_start(home, "cursor"))
    with then("Claude Code shows one line with the undo, and Cursor's context starts with it"):
        line = claude.output["systemMessage"]
        assert "public_text_gate.dashes" in line and f"hubctl undo {change['id']}" in line
        assert "\n" not in line
        assert cursor.startswith(line)
    with and_("Claude Code's start text holds no second copy of the line"):
        assert line not in context_of(claude)
    with when("one more session starts in each tool"):
        again = session_start(home, "claude_code")
        cursor_again = context_of(session_start(home, "cursor"))
    with then("the line does not show again"):
        assert "systemMessage" not in again.output
        assert not cursor_again.startswith(line)
    with when("the user turns the tuner off"):
        assert home.show("--all", "--tool", "cursor")["flags"]["cursor_link.tuner_chat_line"]["runs"] is True
        home.hubctl("off", "tuner")
    with then("Cursor's chat line stops too, because it needs the tuner"):
        assert home.show("--all", "--tool", "cursor")["flags"]["cursor_link.tuner_chat_line"]["runs"] is False


def test_where_no_engine_line_shows_the_tuners_change_the_start_text_holds_it_once(home):
    with given("a tuner change to the dash check, which setup turned on, on a machine where the status band is on, "
               "and the Cursor beta is on"):
        on_day(home, 1)
        set_up(home)
        dash_check_on_by_setup(home)
        home.hubctl("on", "cursor_link")
    with and_("Cursor's chat line is off, since it is in beta and off by default"):
        chat_line = home.show("--all", "--tool", "cursor")["flags"]["cursor_link.tuner_chat_line"]
        assert chat_line["runs"] is False
        seed_signals(home, "public_text_gate.dashes")
        session_start(home, "claude_code")
        decide(home, "tuner_picks_more.json")
        change = [line for line in home.history() if line.get("by") == "tuner"][0]
    with when("the next session starts in Claude Code and in Cursor"):
        on_day(home, 1, hour=11)
        claude = session_start(home, "claude_code")
        cursor = context_of(session_start(home, "cursor"))
    with then("Claude Code shows no system message, and both start texts hold the line with its undo"):
        assert "systemMessage" not in claude.output
        for context in (context_of(claude), cursor):
            assert "public_text_gate.dashes" in context and f"hubctl undo {change['id']}" in context
            assert "first reply" in context
    with when("one more session starts in each tool"):
        again = context_of(session_start(home, "claude_code"))
        cursor_again = context_of(session_start(home, "cursor"))
    with then("neither start text holds the line again"):
        assert f"hubctl undo {change['id']}" not in again
        assert f"hubctl undo {change['id']}" not in cursor_again


def band_read(home):
    """What the mod reads for the band: the screen model, marked as the band's read."""
    return json.loads(home.hubctl("screen", "--json", "--band").stdout)


def band_line(model):
    """The one strip line the band draws from a screen model, as register.tsx picks it, or None."""
    flags = model["band"]
    if not flags["runs"]:
        return None
    lines = [line for line in model["strip"] if line["buttons"]]
    notices = [line for line in lines if line["kind"] in ("change", "update")] if flags["notices"] else []
    offers = [line for line in lines if line["kind"] == "offer"] if flags["offers"] else []
    found = notices or offers
    return found[0] if found else None


def tuner_change(home):
    """A set-up machine on day 1 where the tuner turned off the dash check setup turned on. Returns the history line."""
    on_day(home, 1)
    set_up(home)
    dash_check_on_by_setup(home)
    seed_signals(home, "public_text_gate.dashes")
    session_start(home, "claude_code")
    decide(home, "tuner_picks_more.json")
    return [line for line in home.history() if line.get("by") == "tuner"][0]


def test_when_the_band_showed_the_tuners_change_the_next_start_text_does_not_repeat_it(home):
    with given("a tuner change, and the band on in Claude Code with its mod running"):
        change = tuner_change(home)
    with when("the mod reads the screen for the band before the next session starts"):
        shown = band_line(band_read(home))
    with then("the band shows the change with its undo"):
        assert shown is not None and shown["id"] == change["id"]
    with when("the next session starts in Claude Code"):
        on_day(home, 1, hour=11)
        claude = session_start(home, "claude_code")
    with then("the start text does not hold the line a second time"):
        assert f"hubctl undo {change['id']}" not in context_of(claude)
        assert "systemMessage" not in claude.output


def test_when_the_start_text_told_the_tuners_change_the_band_does_not_show_it_again(home):
    with given("a tuner change, and the band on in Claude Code"):
        change = tuner_change(home)
    with when("the next session starts before the mod reads the screen"):
        on_day(home, 1, hour=11)
        context = context_of(session_start(home, "claude_code"))
    with then("the start text holds the line with its undo"):
        assert f"hubctl undo {change['id']}" in context
    with when("the mod then reads the screen for the band"):
        model = band_read(home)
    with then("the band does not show that line again"):
        shown = band_line(model)
        assert shown is None or shown["id"] != change["id"]
    with and_("hubctl show still lists the change with its undo"):
        strip = json.loads(home.hubctl("screen", "--json").stdout)["strip"]
        listed = [line for line in strip if line["id"] == change["id"]]
        assert listed and listed[0]["buttons"] == [{"label": "Undo", "args": ["undo", change["id"]]}]


def test_a_screen_read_without_the_band_mark_hides_no_notice(home):
    with given("a tuner change, and the band on in Claude Code"):
        change = tuner_change(home)
    with when("something other than the band reads the screen, and the next session starts"):
        home.hubctl("screen", "--json")
        on_day(home, 1, hour=11)
        context = context_of(session_start(home, "claude_code"))
    with then("the start text still holds the line"):
        assert f"hubctl undo {change['id']}" in context


def agent_transcript(home, number, tools):
    """A made-up subagent transcript whose assistant turns call these tools."""
    path = os.path.join(home.root, "transcripts", f"agent-{number}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        for name in tools:
            handle.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"toolu_{number}", "name": name, "input": {}}]}}) + "\n")
    return path


def subagent_stop(home, number, agent_type, tools):
    data = load_fixture("claude_code", "SubagentStop.json")
    data.update(cwd=home.work, agent_id=f"a{number:016d}", agent_type=agent_type,
                agent_transcript_path=agent_transcript(home, number, tools))
    return home.send("claude_code", "SubagentStop", data)


def offers(home):
    path = os.path.join(home.state, "offers.json")
    return [offer["id"] for offer in json.load(open(path))["offers"]] if os.path.exists(path) else []


def test_four_builder_runs_with_no_edits_offer_researcher_and_never_switch_it_on(home):
    with given("a set-up machine where the user turned builder on and researcher is off"):
        on_day(home, 1)
        home.switch_on("builder")
        set_up(home)
        assert not home.show()["flags"]["researcher"]["runs"]
    with when("three builder runs make no edit, one makes an edit, and a scout run makes none"):
        for number in range(3):
            subagent_stop(home, number, "builder", ["Read", "Grep", "Bash"])
        subagent_stop(home, 3, "builder", ["Read", "Edit"])
        subagent_stop(home, 4, "scout", ["Read"])
    with then("nothing is offered yet"):
        assert "researcher" not in offers(home)
    with when("a fourth builder run makes no edit"):
        subagent_stop(home, 5, "builder", ["Read"])
    with then("researcher is offered in offers.json and the strip, with the reason, and it stays off"):
        assert "researcher" in offers(home)
        strip = json.loads(home.hubctl("screen", "--json").stdout)["strip"]
        offered = [entry for entry in strip if entry["id"] == "offer:researcher"]
        assert len(offered) == 1 and offered[0]["reason"] == "4 builder runs made no edits"
        assert not home.show()["flags"]["researcher"]["runs"]
    and_("the log holds the runs, with no word of the transcripts")
    assert [line["signal"] for line in home.signals() if line["flag"] == "builder"] == ["no_edit"] * 4
    with when("the user says no, and four more builder runs make no edit"):
        home.hubctl("no", "researcher")
        for number in range(6, 10):
            subagent_stop(home, number, "builder", ["Read"])
    with then("researcher is not offered again, and it stays off"):
        assert "researcher" not in offers(home)
        assert not home.show()["flags"]["researcher"]["runs"]


# The side call

def tuner_ask(home):
    return json.loads(home.hubctl("tuner", "ask").stdout)


def screen_tuner(home):
    return json.loads(home.hubctl("screen", "--json").stdout)["tuner"]


def test_where_the_mod_runs_the_candidates_go_to_its_side_call_and_the_chat_stays_clean(home):
    with given("a set-up machine on a fixed clock where setup turned the dash check on and the mod asked the tuner "
               "once, with nothing waiting"):
        on_day(home, 1)
        set_up(home)
        dash_check_on_by_setup(home)
        assert tuner_ask(home)["candidates"] == []
        seed_signals(home, "public_text_gate.dashes")
    with when("the next day's first session starts"):
        on_day(home, 2)
        context = context_of(session_start(home, "claude_code"))
    with then("the chat gets no tuner list, and the screen tells the mod that one candidate waits"):
        assert "tuner decide" not in context
        waiting = screen_tuner(home)
        assert waiting["pending"] == 1 and waiting["side_call"] and waiting["since"]
    with when("the mod asks for the request"):
        request = tuner_ask(home)
    with then("it names tuner.tune.model, the tuner's instructions and the candidate"):
        assert request["model"] == "haiku"
        assert '{"pick"' in request["system"] and "one line with an undo" in request["system"]
        assert "- public_text_gate.dashes off: you went past it 9 times" in request["prompt"]
    with when("the model's answer goes to hubctl tuner decide on stdin"):
        decided = home.run([sys.executable, os.path.join(home.plugin, "bin", "hubctl"), "tuner", "decide",
                            "--answer", "-"], stdin='{"pick": ["public_text_gate.dashes"]}')
    with then("the dash check is off by the tuner, back at its shipped default, and nothing waits"):
        assert "public_text_gate.dashes" in decided.stdout
        assert "public_text_gate.dashes" not in home.settings()["flags"]
        assert not home.show("--all")["flags"]["public_text_gate.dashes"]["runs"]
        last = home.history()[-1]
        assert (last["flag"], last["by"], last["old"], last["new"]) == ("public_text_gate.dashes", "tuner", "on", "off")
        assert screen_tuner(home)["pending"] == 0
    with when("the mod has not asked for two weeks, and new signals come"):
        on_day(home, 17)
        home.set_by("setup", "public_text_gate.dashes", "on")
        seed_signals(home, "public_text_gate.dashes")
        late = context_of(session_start(home, "claude_code"))
    with then("the chat gets the list again, since the mod may not run"):
        assert "tuner decide" in late
    with when("the mod asks again, then the user switches the side call off, and new signals come next day"):
        tuner_ask(home)
        home.hubctl("off", "tuner.tune.side_call")
        on_day(home, 18)
        seed_signals(home, "public_text_gate.dashes", warned=10, went_ahead=10)
        off = context_of(session_start(home, "claude_code"))
    with then("the chat gets the list, and the screen says the side call is off"):
        assert "tuner decide" in off
        assert not screen_tuner(home)["side_call"]


def test_when_the_side_call_gives_up_the_next_chat_gets_the_list_once_and_the_mod_does_not_ask_again(home):
    with given("a set-up machine where setup turned the dash check on, the mod asked lately, and one candidate "
               "waits for it"):
        on_day(home, 1)
        set_up(home)
        dash_check_on_by_setup(home)
        tuner_ask(home)
        seed_signals(home, "public_text_gate.dashes")
        on_day(home, 2)
        assert "tuner decide" not in context_of(session_start(home, "claude_code"))
        since = screen_tuner(home)["since"]
    with when("the mod gives the candidates back for an older list"):
        home.hubctl("tuner", "give-back", "--since", "2026-11-01T10:00:00Z")
    with then("nothing changes, so a late answer never hands over a newer list"):
        assert screen_tuner(home)["pending"] == 1
    with when("the mod gives back the list it tried, because its model call failed"):
        home.hubctl("tuner", "give-back", "--since", since)
    with then("the screen tells the mod nothing waits for it"):
        assert screen_tuner(home)["pending"] == 0
    with when("the next session starts, and then one more"):
        on_day(home, 2, hour=11)
        first = context_of(session_start(home, "claude_code"))
        second = context_of(session_start(home, "claude_code"))
    with then("the first chat gets the list, the second does not, and the candidate still waits for a decision"):
        assert "tuner decide" in first and "- public_text_gate.dashes off: you went past it 9 times" in first
        assert "tuner decide" not in second
        assert [candidate["flag"] for candidate in pending(home)] == ["public_text_gate.dashes"]
        assert screen_tuner(home)["pending"] == 0


# The terminal link

def bin_folder(home):
    return os.path.join(home.root, ".local", "bin")


def test_the_terminal_gets_hubctl_through_one_link_that_follows_updates_and_goes_on_uninstall(home):
    with given("a first run in a fresh HOME where ~/.local/bin is on PATH"):
        home.env["PATH"] = bin_folder(home) + os.pathsep + home.env["PATH"]
        home.install()
        setup(home, "--answer", os.path.join(ANSWERS, "nothing.json"))
        summary = setup(home, "--reply", "{}").stdout
    with then("the summary offers the link, and there is none yet"):
        assert "setup link-bin" in summary
        assert not os.path.lexists(os.path.join(bin_folder(home), "hubctl"))
    with when("the user takes the link offer"):
        done = home.hubctl("setup", "link-bin")
    with then("~/.local/bin/hubctl points at the installed plugin's bin/hubctl, and links.json holds it"):
        link = os.path.join(bin_folder(home), "hubctl")
        assert os.readlink(link) == os.path.join(home.plugin, "bin", "hubctl")
        assert [entry["kind"] for entry in home.state_file("links.json")["links"] if entry["path"] == link] == ["bin"]
        assert "PATH" not in done.stdout
    and_("the terminal runs hubctl by its name")
    assert json.loads(home.run(["hubctl", "show", "--json"]).stdout)["flags"]["tuner"]["runs"]
    with when("a second plugin copy is installed and apply runs"):
        second = os.path.join(home.root, "plugin-copy-2", "hub")
        shutil.copytree(home.plugin, second)
        home.plugin = second
        home.hubctl("apply")
    with then("the link points into the second copy"):
        assert os.readlink(link) == os.path.join(second, "bin", "hubctl")
    with when("hubctl uninstall runs"):
        home.hubctl("uninstall")
    with then("the link is gone"):
        assert not os.path.lexists(link)


def test_the_terminal_link_never_covers_the_users_own_hubctl_and_names_the_shell_file_for_path(home):
    with given("a set-up machine whose ~/.local/bin holds the user's own hubctl and is not on PATH, in zsh"):
        home.env["SHELL"] = "/bin/zsh"
        home.install()
        setup(home, "--answer", os.path.join(ANSWERS, "nothing.json"))
        setup(home, "--reply", "{}")
        os.makedirs(bin_folder(home))
        own = os.path.join(bin_folder(home), "hubctl")
        with open(own, "w") as handle:
            handle.write("#!/bin/sh\necho mine\n")
    with when("the user takes the link offer"):
        done = home.hubctl("setup", "link-bin")
    with then("the user's file stays as it is, and one line says so"):
        assert not os.path.islink(own) and open(own).read() == "#!/bin/sh\necho mine\n"
        assert f"hub left {own} as it is" in done.stdout
    and_("one line names the shell file that puts ~/.local/bin on PATH")
    assert "~/.zshrc" in done.stdout and "PATH" in done.stdout
    with when("hubctl uninstall runs"):
        home.hubctl("uninstall")
    with then("the user's file is still there"):
        assert open(own).read() == "#!/bin/sh\necho mine\n"


def test_the_doctor_names_the_shell_file_while_the_link_folder_is_not_on_path_and_a_deleted_link_stays_gone(home):
    with given("a set-up machine in bash whose ~/.local/bin is not on PATH, with hub's terminal link"):
        home.env["SHELL"] = "/usr/local/bin/bash"
        home.install()
        setup(home, "--answer", os.path.join(ANSWERS, "nothing.json"))
        setup(home, "--reply", "{}")
        done = home.hubctl("setup", "link-bin")
        link = os.path.join(bin_folder(home), "hubctl")
    with then("setup and the doctor each name ~/.bashrc for PATH"):
        assert "~/.bashrc" in done.stdout
        found = json.loads(home.hubctl("doctor", "--json").stdout)["problems"]
        assert [problem["kind"] for problem in found if "~/.bashrc" in problem["text"]] == ["bin_not_on_path"]
    with when("~/.local/bin is on PATH"):
        home.env["PATH"] = bin_folder(home) + os.pathsep + home.env["PATH"]
    with then("the doctor says nothing about it"):
        found = json.loads(home.hubctl("doctor", "--json").stdout)["problems"]
        assert "bin_not_on_path" not in [problem["kind"] for problem in found]
    with when("the user deletes the link and apply runs"):
        os.unlink(link)
        applied = home.hubctl("apply").stdout
    with then("the link stays gone, and the terminal link is off, pinned by the user"):
        assert not os.path.lexists(link)
        assert "core.terminal_link" in applied
        record = home.show("--all")["flags"]["core.terminal_link"]
        assert not record["runs"] and record["by"] == "user"


# The setup skill
SETUP_SKILL = os.path.join(PLUGIN, "skills", "hub-setup", "SKILL.md")
CURSOR_SETUP_SKILL = "cursor/skills/hub-setup/SKILL.md"
SKILL_HUBCTL = "python3 -I ${CLAUDE_PLUGIN_ROOT}/bin/hubctl"
# Verbs the setup skill may run without a prompt. None of them switches a flag the user names.
SETUP_VERBS = ("setup", "show --all", "profile")


def skill_parts(text):
    """(front matter as a dict, body) of a SKILL.md."""
    assert text.startswith("---\n"), "a SKILL.md starts with front matter"
    head, _, body = text[4:].partition("\n---\n")
    return dict(line.split(": ", 1) for line in head.splitlines()), body


def skill_commands(body, hubctl=SKILL_HUBCTL):
    """{first words: the rest} of each hubctl command the skill names in a code span."""
    found = {}
    for words in re.findall("`" + re.escape(hubctl) + r" ([^`]+)`", body):
        verb = next(verb for verb in SETUP_VERBS if words.startswith(verb))
        found[words.split(" ")[1] if verb == "setup" else verb] = words
    return found


def test_the_hub_setup_skill_runs_setup_with_one_dialog_in_claude_code_and_one_message_in_cursor(home):
    with given("the plugin's hub-setup skill"):
        with open(SETUP_SKILL) as handle:
            head, body = skill_parts(handle.read())
        commands = skill_commands(body)
    with then("it is named hub-setup, and a chat may load it on its own, as the first-run text asks"):
        assert head["name"] == "hub-setup"
        assert "disable-model-invocation" not in head
        assert "set" in head["description"] and "hub" in head["description"]
    with and_("it may run only hub's setup, show --all and profile verbs without a prompt"):
        rules = [rule.strip() for rule in head["allowed-tools"].split(", ")]
        assert sorted(rules) == sorted(f"Bash({SKILL_HUBCTL} {verb}{' *' if verb == 'setup' else ''})"
                                       for verb in SETUP_VERBS)
    with and_("it starts the setup agent and names the commands for the scan, the answer and the reply"):
        assert "Start the tuner agent" in body
        assert set(commands) == {"show --all", "profile", "--answer", "--reply"}
    with and_("it asks in one AskUserQuestion dialog in Claude Code and in one plain message in Cursor"):
        assert "AskUserQuestion" in body and "one plain message" in body
    with given("a work laptop on its first run, with the Cursor beta on for this project"):
        work_laptop(home)
        home.install()
        use_cursor_beta_in_project(home)
        hubctl = [sys.executable, "-I", os.path.join(home.plugin, "bin", "hubctl")]
    with then("the first-run start text sends both tools to the skill"):
        assert "hub-setup" in context_of(session_start(home, "claude_code"))
        assert "hub-setup" in context_of(session_start(home, "cursor"))
    with when("the chat runs the skill's commands to look, with nothing set up yet"):
        home.run(hubctl + shlex.split(commands["show --all"]))
        home.run(hubctl + shlex.split(commands["profile"]))
    with then("looking writes no settings file, so the first run is still on"):
        assert not os.path.exists(home.user_file)
    with when("the chat passes the recorded answer to the skill's answer command"):
        words = shlex.split(commands["--answer"].replace("<file>", os.path.join(ANSWERS, "work_laptop.json")))
        dialog = json.loads(home.run(hubctl + words).stdout)
    with then("it gets the questions to ask, each with a skip"):
        assert [question["topic"] for question in dialog["questions"]] == ["gate_mode", "use"]
    with when("the chat passes the user's answers to the skill's reply command"):
        reply = replies(dialog, {"gate_mode": "warn", "use": "work"})
        words = shlex.split(commands["--reply"].replace("<json>", reply))
        summary = home.run(hubctl + words).stdout
    with then("setup is done and the summary says what is on and what starts next session"):
        assert home.settings()["version"] == 1
        assert "Works now" in summary and "Starts next session" in summary
    with when("cursor_link renders the Cursor plugin for a copy in a folder with a space"):
        spec = importlib.util.spec_from_file_location(
            "cursor_link_handler", os.path.join(PLUGIN, "parts", "cursor_link", "handler.py"))
        handler = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.join(PLUGIN, "lib"))
        spec.loader.exec_module(handler)
        cursor_head, cursor_body = skill_parts(handler.plugin_files(PLUGIN, "/opt/my tools/hub")[CURSOR_SETUP_SKILL])
    with then("Cursor gets hub-setup with only the keys it reads, and the copy's hubctl in every command"):
        assert cursor_head == {"name": "hub-setup", "description": head["description"]}
        assert "${CLAUDE_" not in cursor_body
        assert skill_commands(cursor_body, "python3 -I '/opt/my tools/hub/bin/hubctl'") == commands


def test_every_text_names_an_agent_by_the_name_in_its_agent_file():
    """An agent file's name is its plain id, such as scout. The link ~/.claude/agents/hub-<id>.md is only a file name."""
    with given("every shipped agent file, with the name in its front matter"):
        parts = os.path.join(PLUGIN, "parts")
        names = {}
        for part in sorted(os.listdir(parts)):
            path = os.path.join(parts, part, "agent.md")
            if os.path.isfile(path):
                with open(path) as handle:
                    names[part] = re.search(r"^name: (\S+)$", handle.read(), re.M).group(1)
    with then("each agent is named by its part id, as scout and builder are"):
        assert names and all(name == part for part, name in names.items()), names
    with when("the texts and skills that tell a chat to start an agent are read"):
        found = []
        for root, _folders, files in os.walk(PLUGIN):
            if "__pycache__" in root:
                continue
            for file in files:
                if file.endswith(".md") and file != "agent.md":
                    with open(os.path.join(root, file)) as handle:
                        found += [(file, line) for line in handle.read().splitlines() if re.search(r"hub-\w+ agent", line)]
    with then("none calls an agent hub-<id>, which is not its name"):
        assert not found, found
