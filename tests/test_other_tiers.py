"""Slice 3: the other tiers.

Every scenario runs the real hubctl, the dispatcher and the slot hooks as
processes in a temporary HOME.
"""
import itertools
import json
import os
import re
import sys

from conftest import PLUGIN, REPO, load_fixture
from steps import and_, given, then, when
from test_start_text_and_links import agent_link, cursor_agent, front_matter, read, session_start, use_cursor_beta

NEW_AGENTS = {
    "researcher": ("sonnet", "medium", "Edit, Write, NotebookEdit, Agent"),
    "worker": ("sonnet", "medium", None),
    "reviewer": ("opus", "high", "Edit, Write, NotebookEdit, Agent"),
    "planner": ("opus", "max", "Agent"),
    "critic": ("opus", "xhigh", "Edit, Write, NotebookEdit, Agent"),
}
GENERIC = ["no_public_steps", "report_short", "no_agents"]
SECTIONS = {
    "researcher": ["read_only", "sources", "read_in_full", "links"],
    "worker": ["follow_plan", "stop_when_plan_fails", "read_back"],
    "reviewer": ["read_only", "proof", "rank_by_harm"],
    "planner": ["plan_file_only", "read_first", "plan_shape"],
    "critic": ["security", "data_loss", "proof"],
}
MARKER = "<!-- hub:"


def rows(text):
    """{tier: row line} of the tier table in a start text."""
    found = {}
    for line in text.splitlines():
        if line.startswith("| ") and "---" not in line and not line.startswith("| Tier "):
            found[line.split("|")[1].strip()] = line
    return found


def tiers(text):
    return [line.split("|")[1].strip() for line in text.splitlines()
            if line.startswith("| ") and "---" not in line and not line.startswith("| Tier ")]


def section_lines(path):
    """The body of an agent file, without its front matter."""
    return read(path).split("---\n", 2)[2]


def test_the_new_agents_ship_off_and_each_one_switched_on_adds_its_row(home):
    with given("hub is installed with its defaults"):
        home.install()
        text, _ = session_start(home)
    with then("only scout and builder run, so the table has the mechanical and judgement rows"):
        assert tiers(text) == ["Mechanical", "Judgement"]
        assert "| scout | haiku | not set |" in rows(text)["Mechanical"]
        assert "| builder | opus | high |" in rows(text)["Judgement"]
        for name in NEW_AGENTS:
            assert not home.show()["flags"][name]["runs"]
            assert not os.path.lexists(agent_link(home, name))
    with when("the user switches on researcher and worker"):
        home.hubctl("on", "researcher")
        home.hubctl("on", "worker")
        text, _ = session_start(home)
    with then("a standard row names both, with the model and effort they share once"):
        assert tiers(text) == ["Mechanical", "Standard", "Judgement"]
        assert "| researcher, worker | sonnet | medium |" in rows(text)["Standard"]
        assert os.path.islink(agent_link(home, "researcher")) and os.path.islink(agent_link(home, "worker"))
    with when("the user sets worker.effort to high"):
        home.hubctl("set", "worker.effort", "high")
        text, _ = session_start(home)
    with then("the effort column shows one value per agent, in the same order"):
        assert "| researcher, worker | sonnet | medium, high |" in rows(text)["Standard"]
    with when("the user switches on reviewer, planner and critic"):
        for name in ("reviewer", "planner", "critic"):
            home.hubctl("on", name)
        text, _ = session_start(home)
    with then("every tier has its row, in order, with each agent's model and effort"):
        assert tiers(text) == ["Mechanical", "Standard", "Judgement", "Planning", "Critical"]
        assert "| builder, reviewer | opus | high |" in rows(text)["Judgement"]
        assert "| planner | opus | max |" in rows(text)["Planning"]
        assert "| critic | opus | xhigh |" in rows(text)["Critical"]
    with when("the user switches builder off and worker off"):
        home.hubctl("off", "builder")
        home.hubctl("off", "worker")
        text, _ = session_start(home)
    with then("the judgement row names reviewer alone and the standard row researcher alone"):
        assert "| reviewer | opus | high |" in rows(text)["Judgement"]
        assert "| researcher | sonnet | medium |" in rows(text)["Standard"]
    with when("the user switches off the standard row itself"):
        home.hubctl("off", "tier_rule.table.standard")
        text, _ = session_start(home)
    with then("the row is gone and the researcher agent stays"):
        assert "Standard" not in tiers(text)
        assert os.path.islink(agent_link(home, "researcher"))


def test_each_new_agent_has_its_model_tools_and_one_marked_section_per_instruction(home):
    with given("hub is installed and every new agent is switched on"):
        home.install()
        for name in NEW_AGENTS:
            home.hubctl("on", name)
    for name, (model, effort, blocked) in NEW_AGENTS.items():
        with then(f"~/.claude/agents/hub-{name}.md links to the shipped file with its model, effort and tools"):
            head = front_matter(agent_link(home, name))
            assert head["name"] == name and head["model"] == model and head["effort"] == effort
            assert head.get("disallowedTools") == blocked
            shipped = read(os.path.join(PLUGIN, "parts", name, "agent.md"))
            for section in SECTIONS[name] + GENERIC:
                assert f"{MARKER}section {name}.instructions.{section} -->" in shipped
        with when(f"the user switches off {name}.instructions.{SECTIONS[name][0]}"):
            body_before = section_lines(agent_link(home, name))
            home.hubctl("off", f"{name}.instructions.{SECTIONS[name][0]}")
        with then("the built copy drops that section only and keeps no marker"):
            body_after = section_lines(agent_link(home, name))
            assert MARKER not in body_after and len(body_after) < len(body_before)
            kept = [line for line in body_after.splitlines() if line.strip()]
            assert all(line in body_before for line in kept)


def test_the_new_agent_texts_stay_generic():
    with given("the five shipped agent files"):
        texts = {name: read(os.path.join(PLUGIN, "parts", name, "agent.md")) for name in NEW_AGENTS}
    with then("none names a private tool, a lock, live notes or a home folder path"):
        for name, text in texts.items():
            lowered = text.lower()
            for word in ("lock", "live notes", "chrome", "docker", "~/", "/users/", "claude.md",
                         "listagents", "sendmessage", "make wt"):
                assert word not in lowered, (name, word)


def test_cursor_gets_a_copy_of_each_new_agent_that_runs(home):
    with given("a profile that lists Cursor, with the Cursor beta on, and reviewer switched on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
        home.hubctl("on", "reviewer")
    with then("~/.cursor/agents/hub-reviewer.md names the mapped model and effort"):
        assert front_matter(cursor_agent(home, "reviewer"))["model"] == "claude-opus-5[effort=high]"
        assert MARKER not in read(cursor_agent(home, "reviewer"))
    with and_("an agent that is off has no Cursor copy"):
        assert not os.path.lexists(cursor_agent(home, "critic"))
    with and_("Cursor's start text has the judgement row with both agents"):
        text, _ = session_start(home, "cursor")
        assert "| builder, reviewer | opus | high |" in rows(text)["Judgement"]


def test_the_shipped_agent_parts_are_off_by_default_with_no_offer_condition():
    with given("the five part manifests"):
        manifests = {}
        for name in NEW_AGENTS:
            with open(os.path.join(PLUGIN, "parts", name, "part.json")) as handle:
                manifests[name] = json.load(handle)
    with then("each is an agent of the tiers family, off, with no offer_when"):
        for name, data in manifests.items():
            assert (data["kind"], data["family"], data["default"]) == ("agent", "tiers", "off"), name
            assert "offer_when" not in data, name


# the tier guard

CALL_IDS = itertools.count(1)


def agent_call(home, tool="claude_code", cwd=None, **tool_input):
    """Send one Agent call (Task in Cursor) through the real hook. Returns the HookResult."""
    cwd = cwd or home.work
    if tool == "claude_code":
        data = load_fixture("claude_code", "PreToolUse.Agent.json")
    else:
        data = load_fixture("cursor", "preToolUse.Task.json")
        data["workspace_roots"] = [cwd]
    data.update(cwd=cwd, tool_use_id=f"toolu_agent_{next(CALL_IDS):06d}")
    data["tool_input"] = dict({"description": "Do it", "prompt": "Do the job."}, **tool_input)
    if "subagent_type" in tool_input and tool_input["subagent_type"] is None:
        data["tool_input"].pop("subagent_type")
    return home.send(tool, "PreToolUse", data)


def guard_signals(home, name):
    return [line for line in home.signals() if line["flag"] == "tier_rule.guard" and line["signal"] == name]


def write_agent(folder, file_name, name):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, file_name), "w") as handle:
        handle.write(f"---\nname: {name}\ndescription: A helper of my own.\n---\n\nHelp.\n")


def test_the_tier_guard_only_counts_untiered_calls_until_it_is_switched_on_then_refuses_them(home):
    with given("hub is installed with the guard off, as it ships"):
        home.install()
        assert not home.show()["flags"]["tier_rule.guard"]["runs"]
    with when("the main chat starts a general-purpose agent with no model"):
        result = agent_call(home, subagent_type="general-purpose")
    with then("the call runs and the guard logs one untiered signal for the tuner"):
        assert result.ran and not result.context
        assert len(guard_signals(home, "untiered")) == 1
    with when("the user switches the guard on and the same kind of call comes"):
        home.hubctl("on", "tier_rule.guard")
        result = agent_call(home, subagent_type="general-purpose")
    with then("it is refused, and the reason names each tier that runs with its agents, model and effort"):
        assert result.refused
        assert "general-purpose" in result.reason and "(tier_rule.guard)" in result.reason
        assert "Mechanical: scout (haiku, effort not set)" in result.reason
        assert "Judgement: builder (opus, high)" in result.reason
        assert "Standard" not in result.reason
        assert len(guard_signals(home, "refused")) == 1
    with and_("a call with no agent type at all is refused the same way"):
        assert agent_call(home, subagent_type=None).refused
    with then("a hub agent that runs, a call with a model and a fork all pass"):
        assert agent_call(home, subagent_type="scout").ran
        assert agent_call(home, subagent_type="builder").ran
        assert agent_call(home, subagent_type="general-purpose", model="sonnet").ran
        assert agent_call(home, subagent_type="fork").ran
    with and_("a hub agent that is off is refused, until the user switches it on"):
        assert agent_call(home, subagent_type="reviewer").refused
        home.hubctl("on", "reviewer")
        assert agent_call(home, subagent_type="reviewer").ran
        assert "Judgement: builder (opus, high), reviewer (opus, high)" in \
            agent_call(home, subagent_type="general-purpose").reason
    with when("the user has an agent of their own, and the repo has one too"):
        write_agent(os.path.join(home.root, ".claude", "agents"), "my-helper.md", "helper")
        write_agent(os.path.join(home.work, ".claude", "agents"), "repo-helper.md", "repo-helper")
        nested = os.path.join(home.work, "src")
        os.makedirs(nested)
    with then("both pass, by name and by file name, also from a folder deeper in the repo"):
        assert agent_call(home, subagent_type="helper").ran
        assert agent_call(home, subagent_type="my-helper").ran
        assert agent_call(home, subagent_type="repo-helper", cwd=nested).ran
    with when("the user allows one more name"):
        assert agent_call(home, subagent_type="Explore").refused
        home.hubctl_from_mod("set", "tier_rule.guard.allowed", "Explore")
    with then("that name passes and others are still refused"):
        assert agent_call(home, subagent_type="Explore").ran
        assert agent_call(home, subagent_type="Plan").refused
    with and_("the refusal says only the user can allow a name, with /hub in Claude Code"):
        reason = agent_call(home, subagent_type="Plan").reason
        assert "Only the user can" in reason and "/hub in Claude Code" in reason and "terminal" not in reason
        assert "tier_rule.guard.allowed" in reason
    with and_("it tells the agent not to change that setting, and gives it no command to run"):
        assert "Do not change" in reason and "yourself" in reason
        assert "hubctl" not in reason


def test_cursor_never_gets_the_tier_guard(home):
    with given("a profile that lists Cursor, with the Cursor beta on, and the guard switched on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
        home.hubctl("on", "tier_rule.guard")
    with then("Cursor's hooks watch only the shell and file tools, never Task, and the guard does not run there"):
        folder = os.path.join(home.root, ".cursor", "plugins", "local", "hub")
        hooks = json.loads(read(os.path.join(folder, "cursor", "hooks.json")))
        assert [entry["matcher"] for entry in hooks["hooks"]["preToolUse"]] == ["Delete|Edit|Shell|Write"]
        assert not home.show("--tool", "cursor")["flags"]["tier_rule.guard"]["runs"]
        assert home.show("--tool", "cursor")["flags"]["tier_rule"]["runs"]
    with when("a Cursor Task call for an untiered agent comes through the adapter"):
        result = agent_call(home, tool="cursor", subagent_type="general-purpose")
    with then("it runs, and no guard signal is logged"):
        assert result.ran and not guard_signals(home, "untiered") and not guard_signals(home, "refused")
    with and_("the same call in Claude Code is refused"):
        assert agent_call(home, subagent_type="general-purpose").refused


def test_every_tool_other_than_bash_and_monitor_reaches_the_dispatcher_once():
    with given("Claude Code's hooks file, and the tools other than Bash that the shipped Claude Code parts name"):
        with open(os.path.join(PLUGIN, "hooks", "hooks.json")) as handle:
            groups = json.load(handle)["hooks"]["PreToolUse"]
        sys.path.insert(0, os.path.join(PLUGIN, "lib"))
        from hub import tree
        named = sorted({tool for part in tree.load(os.path.join(PLUGIN, "parts")).parts
                        if "claude_code" in part.runs_in
                        for tool in part.handlers.get("PreToolUse", {}).get("tools", []) if tool != "Bash"})
        assert {"Agent", "AskUserQuestion", "Delete", "SubagentHandback"} <= set(named)
    with then("it keeps the Bash group, adds a group for the named tools, and one group for every other tool"):
        matchers = [group["matcher"] for group in groups]
        assert matchers[:2] == ["Bash", "|".join(named)] and len(matchers) == 3
        for group in groups:
            assert [hook["command"].split()[-1] for hook in group["hooks"]] == ["PreToolUse"]
    with and_("no tool name matches two groups, and Monitor matches none"):
        other = re.compile(matchers[2])
        for name in ("WebFetch", "BashOutput", "MonitorX", "AgentX", "ReadX", "EditX", "AskUserQuestionX", "WriteX", "DeleteX"):
            assert other.search(name), name
        for name in ["Bash", "Monitor"] + named:
            assert not other.search(name), name


def test_a_handler_that_names_an_unknown_tool_to_run_in_is_a_tree_error(home):
    with given("hub is installed"):
        home.install()
    with when("a manifest's handler says it runs in a tool hub does not know"):
        home.edit_manifest("tier_rule", lambda data: data["handlers"]["PreToolUse"].update(runs_in=["vim"]))
        result = home.hubctl("apply", check=False)
    with then("apply stops and names the part and the field"):
        assert result.returncode == 1
        assert "tier_rule" in result.stderr and "runs_in" in result.stderr and "vim" in result.stderr


# the hand-back cap

def words(count):
    return " ".join(["word"] * count)


def handback(home, message, agent_id="a0000000000000001"):
    data = load_fixture("claude_code", "PreToolUse.SubagentHandback.json")
    data.update(cwd=home.work, agent_id=agent_id, tool_use_id=f"toolu_back_{next(CALL_IDS):06d}")
    data["tool_input"] = {"message": message}
    return home.send("claude_code", "PreToolUse", data)


def test_a_hand_back_over_the_cap_is_refused_twice_per_agent_then_goes_through_with_a_warning(home):
    with given("hub is installed, where the cap ships off"):
        home.install()
        assert not home.show()["flags"]["handback_cap"]["runs"]
        assert handback(home, words(250)).ran
    with when("the user switches the cap on and an agent hands back 250 words"):
        home.hubctl("on", "handback_cap")
        result = handback(home, words(250))
    with then("it is refused with the count and the cap"):
        assert result.refused
        assert "250 words, the cap is 200" in result.reason and "(handback_cap.refuse_over)" in result.reason
    with and_("a hand-back inside the ten percent margin goes through without a word"):
        quiet = handback(home, words(215))
        assert quiet.ran and not quiet.context
    with when("the same agent tries twice more with 250 words"):
        second, third = handback(home, words(250)), handback(home, words(250))
    with then("the second is refused, and the third goes through with a warning that names the cap"):
        assert second.refused
        assert third.ran and "250 words, the cap is 200" in third.context
    with and_("another agent still gets its own two refusals"):
        assert handback(home, words(250), agent_id="a0000000000000002").refused
    with and_("each refusal and the pass over the cap are logged as signals"):
        names = [line["signal"] for line in home.signals() if line["flag"].startswith("handback_cap")]
        assert names.count("refused") == 3 and names.count("over_cap") == 1
    with when("the user raises the cap to 300 words"):
        home.hubctl("set", "handback_cap.words", "300")
    with then("250 words go through"):
        assert handback(home, words(250), agent_id="a0000000000000003").ran
    with when("the user switches off refusing and an agent hands back 400 words"):
        home.hubctl("off", "handback_cap.refuse_over")
        result = handback(home, words(400), agent_id="a0000000000000004")
    with then("it goes through with the warning only"):
        assert result.ran and "400 words, the cap is 300" in result.context



# the context budget

class Transcript:
    """A Claude Code transcript the test writes, one assistant line per step."""

    def __init__(self, path, brief="Do the job."):
        self.path = path
        self.steps = 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.write({"type": "user", "message": {"role": "user", "content": brief}})

    def write(self, record):
        with open(self.path, "a") as handle:
            handle.write(json.dumps(record) + "\n")

    def step(self, context, model="claude-sonnet-4-6", copies=1):
        """One model reply at a context size in tokens. copies writes the same reply on more lines."""
        self.steps += 1
        usage = {"input_tokens": 10, "cache_read_input_tokens": context - 1010, "cache_creation_input_tokens": 1000}
        for _ in range(copies):
            self.write({"type": "assistant", "requestId": f"req_{self.steps}",
                        "message": {"id": f"msg_{self.steps}", "model": model, "usage": usage}})

    def steps_at(self, count, context):
        for _ in range(count):
            self.step(context)

    def compaction(self):
        self.write({"type": "system", "subtype": "compact_boundary"})


def main_transcript(home, session="00000000-0000-4000-8000-000000000001"):
    return os.path.join(home.root, ".claude", "projects", "-work", session + ".jsonl")


def agent_transcript(home, agent_id="a0000000000000001", brief="Do the job."):
    main = main_transcript(home)
    return Transcript(os.path.join(main[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl"), brief)


def tool_call(home, agent_id="a0000000000000001", named_path=True):
    """One Read call through the real hook, from an agent, or from the main chat when agent_id is None."""
    data = load_fixture("claude_code", "PreToolUse.SubagentHandback.json")
    data.update(cwd=home.work, transcript_path=main_transcript(home), tool_name="Read",
                tool_input={"file_path": os.path.join(home.work, "notes.md")},
                tool_use_id=f"toolu_read_{next(CALL_IDS):06d}")
    data.pop("agent_transcript_path")
    if agent_id is None:
        data.pop("agent_id")
        data.pop("agent_type")
    else:
        data["agent_id"] = agent_id
        if named_path:
            data["agent_transcript_path"] = agent_transcript_path(home, agent_id)
    return home.send("claude_code", "PreToolUse", data)


def agent_transcript_path(home, agent_id):
    return os.path.join(main_transcript(home)[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl")


def budget_signals(home, flag):
    return [line["signal"] for line in home.signals() if line["flag"] == flag]


def test_an_agent_is_reminded_once_each_time_its_context_crosses_a_point(home):
    with given("hub is installed, where the context budget ships off, and an agent is at 135k context"):
        home.install()
        assert not home.show()["flags"]["context_budget"]["runs"]
        transcript = agent_transcript(home)
        transcript.step(60000)
        transcript.step(135000)
        off = tool_call(home)
    with then("nothing is said while it is off"):
        assert off.ran and not off.context
    with when("the user switches the budget on and the agent makes its next call"):
        home.hubctl("on", "context_budget")
        result = tool_call(home)
    with then("it gets one reminder with its size and steps, asking for a hand-back at the next safe point"):
        assert result.ran
        assert "135k context after 2 steps" in result.context
        assert "next safe point" in result.context and "Never stop work half done" in result.context
        assert "(context_budget.agent_tokens)" in result.context
        assert budget_signals(home, "context_budget.agent_tokens") == ["reminded"]
    with and_("the next call with no new step says nothing again"):
        assert not tool_call(home).context
    with when("the agent grows to 170k, then to 185k"):
        transcript.step(170000)
        at_170 = tool_call(home)
        transcript.step(185000)
        at_185 = tool_call(home)
    with then("only the call past 180k reminds it, 50k after the first point"):
        assert not at_170.context
        assert "185k context after 4 steps" in at_185.context
    with when("the user moves the first point to 200k and the every gap to 10k"):
        home.hubctl("set", "context_budget.agent_tokens.at", "200000")
        home.hubctl("set", "context_budget.agent_tokens.every", "10000")
        transcript.step(195000)
        before = tool_call(home)
        transcript.step(211000)
        after = tool_call(home)
    with then("the reminder follows the new points"):
        assert not before.context and "211k context" in after.context
    with when("the user switches the agent reminder off"):
        home.hubctl("off", "context_budget.agent_tokens")
        transcript.step(260000)
    with then("the agent is not reminded"):
        assert not tool_call(home).context


def test_a_brief_can_move_the_first_reminder_up_to_a_cap(home):
    with given("the budget is on and an agent's brief sets its hand-back point to 180k"):
        home.install()
        home.hubctl("on", "context_budget")
        transcript = agent_transcript(home, brief="Read the docs.\nHand-back point: 180k\nThen report.")
        transcript.step(135000)
        at_135 = tool_call(home)
        transcript.step(182000)
        at_182 = tool_call(home)
    with then("130k passes without a word and 180k reminds it"):
        assert not at_135.context
        assert "182k context" in at_182.context
    with given("another agent whose brief asks for 300,000, a close spelling over the cap"):
        other = agent_transcript(home, agent_id="a0000000000000003",
                                 brief=[{"type": "text", "text": "Handback point: 300,000"}])
        other.step(190000)
        at_190 = tool_call(home, agent_id="a0000000000000003")
        other.step(205000)
        at_205 = tool_call(home, agent_id="a0000000000000003")
    with then("the point is held at the 200k cap"):
        assert not at_190.context and "205k context" in at_205.context
    with when("the user switches brief points off and a third agent's brief sets 180k"):
        home.hubctl("off", "context_budget.brief_point")
        third = agent_transcript(home, agent_id="a0000000000000004", brief="Hand back point: 180k")
        third.step(135000)
    with then("the brief is ignored and the agent is reminded at 130k"):
        assert "135k context" in tool_call(home, agent_id="a0000000000000004").context


def test_an_agent_is_reminded_when_its_steps_cross_a_point_even_with_a_small_context(home):
    with given("the budget is on and an agent has made 148 steps at 50k"):
        home.install()
        home.hubctl("on", "context_budget")
        transcript = agent_transcript(home)
        transcript.steps_at(148, 50000)
        assert not tool_call(home).context
    with when("its next three replies arrive before one call, one written on three lines, one by a stand-in model"):
        transcript.step(51000, copies=3)
        transcript.write({"type": "assistant", "requestId": "req_fake",
                          "message": {"id": "msg_fake", "model": "<synthetic>", "usage": {"input_tokens": 1}}})
        transcript.step(52000)
        transcript.step(53000)
        result = tool_call(home)
    with then("the jump over 150 reminds it once, counting each reply once and the stand-in not at all"):
        assert "53k context after 151 steps" in result.context
        assert "(context_budget.steps)" in result.context
        assert budget_signals(home, "context_budget.steps") == ["reminded"]
    with when("it goes on to 199 steps, then to 200"):
        transcript.steps_at(48, 50000)
        at_199 = tool_call(home)
        transcript.step(50000)
        at_200 = tool_call(home)
    with then("only the call at 200 reminds it"):
        assert not at_199.context and "after 200 steps" in at_200.context


def test_the_main_chat_is_reminded_to_hand_reading_to_agents(home):
    with given("the budget is on and the main chat is at 100k"):
        home.install()
        home.hubctl("on", "context_budget")
        main = Transcript(main_transcript(home))
        main.step(70000)
        main.step(100000)
        assert not tool_call(home, agent_id=None).context
    with when("it climbs past 120k"):
        main.step(125000)
        result = tool_call(home, agent_id=None)
    with then("it gets one reminder that names the main chat"):
        assert "The main chat is at 125k context" in result.context
        assert "Hand bigger reading to agents" in result.context and "(context_budget.main_tokens)" in result.context
        assert not tool_call(home, agent_id=None).context
    with when("it grows to 200k, then 230k"):
        main.step(200000)
        at_200 = tool_call(home, agent_id=None)
        main.step(230000)
        at_230 = tool_call(home, agent_id=None)
    with then("only 230k reminds it, 100k after the first point"):
        assert not at_200.context and "230k context" in at_230.context
    with when("the user switches the main chat reminder off"):
        home.hubctl("off", "context_budget.main_tokens")
        main.step(340000)
    with then("the main chat is not reminded"):
        assert not tool_call(home, agent_id=None).context


def test_a_compacted_agent_gets_a_note_with_its_full_transcript(home):
    with given("the budget is on and an agent at 60k"):
        home.install()
        home.hubctl("on", "context_budget")
        transcript = agent_transcript(home)
        transcript.step(60000)
        assert not tool_call(home).context
    with when("its context is compacted"):
        transcript.compaction()
        transcript.step(40000)
        result = tool_call(home)
    with then("its next call gets a note with the path of its full transcript, once"):
        assert "compacted" in result.context and transcript.path in result.context
        assert "(context_budget.compaction_note)" in result.context
        assert budget_signals(home, "context_budget.compaction_note") == ["noted"]
        assert not tool_call(home).context
    with when("the user switches the note off and the agent is compacted again"):
        home.hubctl("off", "context_budget.compaction_note")
        transcript.compaction()
        transcript.step(41000)
    with then("no note comes"):
        assert not tool_call(home).context


def test_models_the_user_lists_get_no_hand_back_reminder_but_still_get_the_note(home):
    with given("the budget is on and the user skips models whose name starts with claude-opus"):
        home.install()
        home.hubctl("on", "context_budget")
        home.hubctl("set", "context_budget.skip_models", "claude-opus")
    with when("an agent on such a model passes 130k and is compacted"):
        transcript = agent_transcript(home)
        transcript.step(135000, model="claude-opus-4-1")
        quiet = tool_call(home)
        transcript.compaction()
        transcript.step(50000, model="claude-opus-4-1")
        noted = tool_call(home)
    with then("it gets no reminder, but it gets the note"):
        assert not quiet.context
        assert "compacted" in noted.context and "context_budget.agent_tokens" not in noted.context
    with and_("an agent on another model is still reminded"):
        other = agent_transcript(home, agent_id="a0000000000000005")
        other.step(135000)
        assert "135k context" in tool_call(home, agent_id="a0000000000000005").context


def test_the_budget_finds_an_agent_transcript_by_its_usual_path_and_stays_quiet_without_one(home):
    with given("the budget is on"):
        home.install()
        home.hubctl("on", "context_budget")
    with when("an agent call has no transcript path of its own, and its file sits where Claude Code keeps it"):
        transcript = agent_transcript(home, agent_id="a0000000000000006")
        transcript.step(140000)
        result = tool_call(home, agent_id="a0000000000000006", named_path=False)
    with then("the budget reads that file"):
        assert "140k context" in result.context
    with when("an agent has no transcript at all, and the main chat has none either"):
        missing = tool_call(home, agent_id="a0000000000000007")
        main = tool_call(home, agent_id=None)
    with then("both calls run with nothing said and no error"):
        assert missing.ran and not missing.context and main.ran and not main.context
    with when("a transcript has a broken line"):
        broken = agent_transcript(home, agent_id="a0000000000000008")
        with open(broken.path, "a") as handle:
            handle.write("{broken\n")
        broken.step(150000)
    with then("the line is skipped and the rest still counts"):
        assert "150k context" in tool_call(home, agent_id="a0000000000000008").context
    with when("an agent's saved state is damaged, and it then passes 130k"):
        damaged = agent_transcript(home, agent_id="a0000000000000009")
        damaged.step(60000)
        assert not tool_call(home, agent_id="a0000000000000009").context
        home.write_json(os.path.join(home.state, "context_budget", "a0000000000000009.json"), {"offset": 3})
        damaged.step(140000)
        result = tool_call(home, agent_id="a0000000000000009")
    with then("the budget reads the transcript again and still reminds it, with no error"):
        assert result.ran and "140k context" in result.context
    for agent_id, shape, message in (("a0000000000000010", "plain text", "Hand-back point: 180k"),
                                     ("a0000000000000011", "a list", ["Hand-back point: 180k"])):
        with when(f"an agent's brief record holds its message as {shape}, not an object, and it passes 130k"):
            odd = agent_transcript(home, agent_id=agent_id)
            with open(odd.path, "w") as handle:
                handle.write(json.dumps({"type": "user", "message": message}) + "\n")
            odd.step(135000)
            result = tool_call(home, agent_id=agent_id)
        with then("the brief counts as setting no point, so the usual 130k reminder comes, with no error"):
            assert result.ran and "135k context" in result.context


def test_the_hand_back_cap_and_the_context_budget_run_in_claude_code_only(home):
    with given("a profile that lists Cursor, with the Cursor beta on and both parts switched on"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
        home.hubctl("on", "handback_cap")
        home.hubctl("on", "context_budget")
    with then("both run in Claude Code and neither runs in Cursor"):
        assert home.show()["flags"]["handback_cap"]["runs"] and home.show()["flags"]["context_budget"]["runs"]
        cursor = home.show("--tool", "cursor")["flags"]
        assert not cursor["handback_cap"]["runs"] and not cursor["context_budget"]["runs"]
    with and_("both ship off and are offered at any time"):
        for name in ("handback_cap", "context_budget"):
            with open(os.path.join(PLUGIN, "parts", name, "part.json")) as handle:
                data = json.load(handle)
            assert (data["default"], data["offer_when"], data["family"]) == ("off", "true", "tiers"), name


# the doctor's Cursor section

def cursor_section(doctor_output):
    lines = doctor_output.splitlines()
    if "Runs in Claude Code only:" not in lines:
        return None
    start = lines.index("Runs in Claude Code only:")
    return [line.strip() for line in lines[start + 1:] if line.startswith("  ")]


def reason(part, flag_id):
    with open(os.path.join(PLUGIN, "parts", part, "part.json")) as handle:
        data = json.load(handle)
    nodes = [data]
    while nodes:
        node = nodes.pop()
        if node["id"] == flag_id.split(".")[-1] and "reason_not_in" in node:
            return node["reason_not_in"]["cursor"]
        nodes.extend(node.get("children", []))
    raise AssertionError(flag_id)


def test_the_doctor_names_each_part_that_runs_in_claude_code_but_not_in_cursor_and_why(home):
    with given("hub is installed without Cursor"):
        home.install()
        home.hubctl("on", "tier_rule.guard")
    with then("the doctor has no Cursor section"):
        assert cursor_section(home.hubctl("doctor").stdout) is None
    with when("the profile lists Cursor with the Cursor beta on, and the guard, the cap, the budget and the band run in Claude Code"):
        os.makedirs(home.config, exist_ok=True)
        use_cursor_beta(home)
        home.hubctl("apply")
        home.hubctl("on", "handback_cap")
        home.hubctl("on", "context_budget")
        output = home.hubctl("doctor").stdout
    with then("the section comes after the warnings and names each one with its reason, in tree order"):
        section = cursor_section(output)
        assert section == [f"context_budget: {reason('context_budget', 'context_budget')}",
                           f"handback_cap: {reason('handback_cap', 'handback_cap')}",
                           f"tier_rule.guard: {reason('tier_rule', 'tier_rule.guard')}",
                           f"status_band: {reason('status_band', 'status_band')}"]
        lines = output.splitlines()
        warnings = [index for index, line in enumerate(lines) if line.startswith("warning:")]
        assert not warnings or max(warnings) < lines.index("Runs in Claude Code only:")
    with and_("hubctl doctor --json gives the same flags and reasons as cursor_gaps"):
        gaps = json.loads(home.hubctl("doctor", "--json").stdout)["cursor_gaps"]
        assert [f"{gap['flag']}: {gap['reason']}" for gap in gaps] == section
        assert {(gap["part"], gap["tool"]) for gap in gaps} == {
            ("context_budget", "cursor"), ("handback_cap", "cursor"), ("tier_rule", "cursor"),
            ("status_band", "cursor")}
    with when("the user switches the guard off"):
        home.hubctl_from_mod("off", "tier_rule.guard")
        section = cursor_section(home.hubctl("doctor").stdout)
    with then("the doctor no longer names it, because it does not run in Claude Code either"):
        assert [line.split(":")[0] for line in section] == ["context_budget", "handback_cap", "status_band"]
