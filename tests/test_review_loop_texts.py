"""The texts of the review loop and how the part ships.

The review loop keeps a build and review loop on a written acceptance list,
checks the verdict lines at the end of each review, and stops the loop after
a number of failed reviews in a row on one area until the user decides. These
scenarios pin every word the user and the model will read, the values each
message fills in, and that the part ships on, with its handlers and its
command. tests/test_review_loop.py walks the stop itself. The four agent
sections are text files only for now. A later PR moves each one into its
agent file, so until then no agent file changes for anyone.
"""
import json
import os
import re

from conftest import DASH, PLUGIN, REPO
from steps import and_, given, then, when
from test_other_tiers import agent_call
from test_start_text_and_links import agent_link, read, session_start, text_of

PART = os.path.join(PLUGIN, "parts", "review_loop")
TEXT = os.path.join(PART, "text")
START_TEXTS = {"write_list_first": "write_list_first.md", "threat_model": "threat_model.md",
               "stop_means_ask": "stop_means_ask.md"}
TEMPLATE = "acceptance_template.md"
# Each message and the values the logic fills into it.
MESSAGES = {
    "no_list.md": {"agent"},
    "list_unreadable.md": {"path"},
    "state_unreadable.md": {"path"},
    "list_incomplete.md": {"path", "missing"},
    "list_changed.md": {"area"},
    "no_verdict_shape.md": {"area", "lines"},
    "stopped.md": {"area", "rounds", "summary"},
    "stopped_no_verdicts.md": {"area", "starts"},
    "round_summary.md": {"round", "items"},
    "same_function_twice.md": {"function"},
    "held_no_list.md": {"area"},
    "cluster.md": {"area", "count", "function"},
    "verdict_missing.md": {"area", "lines", "tries"},
    "verdict_refused.md": {"problem", "tries"},
    "verdict_unknown_item.md": {"item"},
    "verdict_missing_item.md": {"item"},
    "verdict_no_fail.md": set(),
    "verdict_go_with_fail.md": {"item"},
    "verdict_reopened_without_test.md": {"item"},
    "verdict_fail_without_place.md": {"item"},
    "verdict_unreadable.md": {"agent", "area", "tries"},
    "ended_without_verdict.md": {"area", "lines"},
    "ended_twice.md": {"agent", "area"},
    "serious_outside_finding.md": {"area", "severity", "finding"},
    "outside_findings.md": {"area", "count"},
    "lifted_ship.md": {"area"},
    "lifted_redesign.md": {"area"},
    "lifted_one_more_round.md": {"area"},
    "lifted_park.md": {"area"},
    "decide_box.md": {"area", "choice"},
}
# The verdict problems, which fill {problem} in verdict_refused.md, so each reads as the end of its sentence.
PROBLEMS = {name for name in MESSAGES if name.startswith("verdict_") and name not in
            ("verdict_missing.md", "verdict_refused.md", "verdict_unreadable.md")}
FRAGMENTS = PROBLEMS | {"round_summary.md", "same_function_twice.md"}
CHOICES = ("ship it as it is", "redesign with a new list", "one more round", "park it")
# The four agent sections and the agent each one is for.
SECTIONS = {"critic": "judge_the_list", "reviewer": "judge_the_list", "builder": "fix_the_class",
            "planner": "write_acceptance"}
SECTION_TEXTS = os.path.join(TEXT, "agent_sections")
VALUE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
HOME_PATH = re.compile(r"~/|/Users/|/home/|/private/|/tmp/")


def one_line(text):
    return text.endswith("\n") and "\n" not in text[:-1] and text.strip() != ""


def plain(text):
    """No em dash, no home or temp path, no @-tag and no name of hub's control step."""
    return DASH not in text and not HOME_PATH.search(text) and "@" not in text and "hubctl" not in text


def manifest():
    with open(os.path.join(PART, "part.json"), encoding="utf-8") as handle:
        return json.load(handle)


def nodes(data, prefix=""):
    """{flag id: node} for a part.json and every child."""
    flag_id = f"{prefix}.{data['id']}" if prefix else data["id"]
    found = {flag_id: data}
    for child in data.get("children", []):
        found.update(nodes(child, flag_id))
    return found


def section(name):
    """The text of one agent's review loop section, from its file in the part."""
    return read(os.path.join(SECTION_TEXTS, f"{name}.{SECTIONS[name]}.md")).strip()


def test_every_review_loop_message_is_one_plain_line_that_fills_only_its_named_values():
    with given("the review loop's text folder"):
        shipped = sorted(name for name in os.listdir(TEXT) if name.endswith(".md"))
    with then("it holds exactly the start texts, the template and the planned messages"):
        assert shipped == sorted([*START_TEXTS.values(), TEMPLATE, *MESSAGES])
    for name, values in MESSAGES.items():
        with when(f"{name} is read"):
            message = read(os.path.join(TEXT, name))
        with then(f"{name} is one line that fills exactly {sorted(values)}"):
            assert one_line(message), name
            assert set(VALUE.findall(message)) == values, name
        with and_(f"{name} has no em dash, no home path, no @-tag and no name of hub's control step"):
            assert plain(message), name
        with and_(f"{name} {'reads as the end of a sentence' if name in FRAGMENTS else 'is whole sentences'}"):
            assert message.rstrip().endswith((".", "?", "{lines}")), name
            if name in PROBLEMS:
                assert message[0].islower() or message.startswith("{"), name
    with then("a refusal says hub stopped the call and what to do next"):
        for name in ("no_list.md", "list_unreadable.md", "state_unreadable.md", "list_incomplete.md", "list_changed.md",
                     "no_verdict_shape.md", "stopped.md", "stopped_no_verdicts.md"):
            assert read(os.path.join(TEXT, name)).startswith("hub stopped this"), name
    with and_("both stops give the user the same four choices and say only the user's own answer lifts it"):
        for name in ("stopped.md", "stopped_no_verdicts.md"):
            stop = read(os.path.join(TEXT, name))
            assert all(choice in stop for choice in CHOICES), name
            assert "Only the user's own typed answer lifts the stop" in stop and "coordinator chat" in stop, name
    with and_("a missing list names both ways to give one, a path or none with a reason"):
        no_list = read(os.path.join(TEXT, "no_list.md"))
        assert "`Acceptance: /absolute/path.md`" in no_list and "`Acceptance: none, <reason>`" in no_list
    with and_("one more round says the next failed review, or one with no verdict, stops the loop again"):
        again = read(os.path.join(TEXT, "lifted_one_more_round.md"))
        assert "next failed review" in again and "no verdict" in again and "stops the loop again" in again
    with and_("ship and park close the area, and redesign starts the count again at zero"):
        for name in ("lifted_ship.md", "lifted_park.md"):
            assert "is closed, and a new round needs a new list" in read(os.path.join(TEXT, name)), name
        assert "starts again at zero" in read(os.path.join(TEXT, "lifted_redesign.md"))
    with when("the message for a High or Critical finding outside the list is read"):
        serious = read(os.path.join(TEXT, "serious_outside_finding.md"))
    with then("it asks the user at once, in the coordinator chat, to add it to the list or ship anyway"):
        assert "Ask the user now" in serious and "coordinator chat" in serious
        assert "add it to the list" in serious and "ship anyway" in serious
    with and_("it says nothing waits for the answer"):
        assert "Keep working while the question waits" in serious and "does not stop the loop" in serious
    with and_("the other findings outside the list never fail a review"):
        assert "do not fail the review" in read(os.path.join(TEXT, "outside_findings.md"))
    with and_("a NO-GO with every item passing is refused, because an outside finding never fails a review"):
        assert "never fails it" in read(os.path.join(TEXT, "verdict_no_fail.md"))


def test_the_start_texts_template_and_agent_sections_describe_one_list_and_one_verdict_shape():
    with given("the three start texts"):
        starts = {flag: read(os.path.join(TEXT, name)) for flag, name in START_TEXTS.items()}
    with then("each is one plain line"):
        for flag, text in starts.items():
            assert one_line(text) and plain(text), flag
    with and_("the first names both brief lines, the second the threats in and out of scope, the third the choices"):
        assert "`Acceptance: /absolute/path.md`" in starts["write_list_first"]
        assert "`Acceptance: none, <reason>`" in starts["write_list_first"]
        assert "in scope" in starts["threat_model"] and "out of scope" in starts["threat_model"]
        assert all(choice in starts["stop_means_ask"] for choice in CHOICES)
        assert "Only the user's own answer lifts the stop" in starts["stop_means_ask"]
    with when("the acceptance template is read"):
        template = read(os.path.join(TEXT, TEMPLATE))
        lines = template.splitlines()
    with then("it has a title, an area line, a threat model with both scopes, and items that each name a check"):
        assert lines[0].startswith("# Acceptance: ") and any(line.startswith("area: ") for line in lines)
        for heading in ("## Threat model", "In scope:", "Out of scope:", "## Items"):
            assert heading in lines, heading
        items = [line for line in lines if re.match(r"- A\d+\. ", line)]
        assert len(items) >= 2 and all(" Check: " in line for line in items)
        assert plain(template)
    with given("the folder of agent sections"):
        shipped = sorted(os.listdir(SECTION_TEXTS))
    with then("it holds one file for each of the four sections"):
        assert shipped == sorted(f"{name}.{flag}.md" for name, flag in SECTIONS.items())
    for name in SECTIONS:
        with when(f"the {name} section is read"):
            text = section(name)
        with then(f"the {name} section is one plain rule line that names the acceptance list"):
            assert text.startswith("- ") and "\n" not in text and plain(text), name
            assert "acceptance list" in text, name
            assert "lock" not in text.lower(), name
    with and_("the critic and reviewer sections are the same rule, with the verdict lines the logic will read"):
        assert section("critic") == section("reviewer")
        judge = section("critic")
        for words in ("`Verdict: GO`", "`Verdict: NO-GO`", "`A1: PASS`", "FAIL <file>:<line> <function>",
                      "FAIL not checked: <reason>`", "Outside the list", "Critical, High, Medium or Low",
                      "never fails the review", "failing test"):
            assert words in judge, words
    with and_("the builder fixes the function a cluster names instead of adding a case"):
        assert "instead of adding one more special case" in section("builder")


HANDLERS = {"PreToolUse": {"tools": ["Agent", "SubagentHandback"], "runs_in": ["claude_code"]},
            "PostToolUse": {"tools": ["Agent"], "runs_in": ["claude_code"]},
            "SubagentStop": {"tools": [], "runs_in": ["claude_code"]}}


def test_the_review_loop_ships_on_with_its_hooks_in_claude_code_and_only_the_user_turns_it_off(home):
    with given("the shipped part.json"):
        data = manifest()
        flags = nodes(data)
    with then("it is a guarded rule in the tiers family, on by default, with no stage"):
        assert {key: data.get(key) for key in ("id", "family", "kind", "stage", "default", "privacy")} == {
            "id": "review_loop", "family": "tiers", "kind": "rule", "stage": None, "default": "on",
            "privacy": "guard"}
    with and_("its hooks run only in Claude Code, and its one command shows the loop"):
        assert data["handlers"] == HANDLERS
        assert os.path.isfile(os.path.join(PART, "handler.py"))
        assert list(data["commands"]) == ["review-loop"] and data["commands"]["review-loop"]["script"] == "show.py"
        assert os.path.isfile(os.path.join(PART, "show.py"))
    with and_("its options and checks have the planned defaults and ranges, and the same area warning is not in it"):
        assert flags["review_loop.reviewers"]["default"] == ["critic", "reviewer"]
        assert flags["review_loop.builders"]["default"] == ["builder", "worker"]
        for flag_id, default, bounds in (("review_loop.verdict_check.tries", 2, [1, 5]),
                                         ("review_loop.stop_after.rounds", 2, [1, 10])):
            assert (flags[flag_id]["default"], flags[flag_id]["range"]) == (default, bounds), flag_id
        for flag_id in ("require_list", "verdict_check", "stop_after", "serious_outside"):
            node = flags[f"review_loop.{flag_id}"]
            assert node["default"] == "on" and node["runs_in"] == ["claude_code"], flag_id
            assert "cursor" in node["reason_not_in"], flag_id
        assert not any(flag_id.startswith("review_loop.same_area") for flag_id in flags)
    with and_("each start text belongs to its instruction flag"):
        for flag, name in START_TEXTS.items():
            assert flags[f"review_loop.{flag}"]["text"] == f"text/{name}", flag
    with given("hub is installed fresh, and the user turned critic, reviewer, builder and planner on"):
        home.switch_on("critic", "reviewer", "builder", "planner")
        home.install()
    with then("the review loop is on by its default"):
        record = home.show("--all")["flags"]["review_loop"]
        assert (record["value"], record["runs"], record["by"]) == ("on", True, "default")
    with and_("the three start texts show"):
        text, _ = session_start(home)
        for name in START_TEXTS.values():
            assert text_of(home, "review_loop", name) in text, name
    with and_("each of the four agent files links to its shipped file, which names no review loop section"):
        for name in SECTIONS:
            shipped = os.path.join(home.plugin, "parts", name, "agent.md")
            assert os.readlink(agent_link(home, name)) == shipped, name
            assert "review_loop" not in read(shipped) and section(name) not in read(shipped), name
    with and_("the doctor no longer names it as a beta part"):
        doctor = home.hubctl("doctor").stdout
        assert "review_loop" not in "".join(line for line in doctor.splitlines(True) if "Beta, untested" in line)
        beta = json.loads(home.hubctl("doctor", "--json").stdout)["beta"]
        assert "review_loop" not in [entry["part"] for entry in beta]
    with and_("a critic call with no acceptance list is stopped and told the line to add"):
        refused = agent_call(home, subagent_type="critic", prompt="Review the export button.")
        assert not refused.ran and "names no acceptance list" in refused.reason
    with and_("a model cannot turn it off, because the part is guarded"):
        refused = home.hubctl("off", "review_loop", check=False)
        assert refused.returncode == 1 and "review_loop is protected" in refused.stderr
        assert home.show("--all")["flags"]["review_loop"]["runs"]
    with when("the user turns it off from their own screen"):
        home.hubctl_from_mod("off", "review_loop")
        text, _ = session_start(home)
    with then("the start texts are gone, the agent files still link to their shipped files, and the call runs"):
        assert text_of(home, "review_loop", "stop_means_ask.md") not in text
        for name in SECTIONS:
            assert os.readlink(agent_link(home, name)) == os.path.join(home.plugin, "parts", name, "agent.md"), name
        result = agent_call(home, subagent_type="critic", prompt="Review the export button.")
        assert result.ran and not result.context


def test_an_update_turns_the_review_loop_on_unless_the_user_turned_it_off(home):
    with given("hub was installed before the review loop shipped"):
        home.install()
        seen = home.state_file("seen.json")
        seen["parts"] = [part for part in seen["parts"] if part != "review_loop"]
        home.write_json(os.path.join(home.state, "seen.json"), seen)
    with when("the update applies"):
        home.hubctl("apply")
    with then("the review loop is not offered and is on, so a critic call with no list is stopped"):
        assert "review_loop" not in [offer["id"] for offer in home.state_file("offers.json")["offers"]]
        assert home.show("--all")["flags"]["review_loop"]["runs"]
        assert not agent_call(home, subagent_type="critic", prompt="Review the export button.").ran
    with when("the user turns it off on their own screen"):
        home.hubctl_from_mod("off", "review_loop")
    with then("the same call runs"):
        assert agent_call(home, subagent_type="critic", prompt="Review the export button.").ran
    with when("a later update applies, and again hub has not yet seen the review loop as shipped"):
        seen = home.state_file("seen.json")
        seen["parts"] = [part for part in seen["parts"] if part != "review_loop"]
        home.write_json(os.path.join(home.state, "seen.json"), seen)
        home.hubctl("apply")
    with then("the review loop stays off, because the user turned it off"):
        assert home.settings()["flags"]["review_loop"] == {"by": "user", "value": "off"}
        assert not home.show("--all")["flags"]["review_loop"]["runs"]
        assert agent_call(home, subagent_type="critic", prompt="Review the export button.").ran


def test_the_readme_lists_the_review_loop_as_on_with_texts_only_in_cursor():
    with given("the README's table of parts"):
        rows = {line.split("|")[1].strip(): line for line in read(os.path.join(REPO, "README.md")).split("\n")
                if line.startswith("| ") and line.count("|") == 6}
    with then("the review loop has its own row, on, in Claude Code and in Cursor as texts only"):
        cells = [cell.strip() for cell in rows["review_loop"].split("|")[1:-1]]
        assert not cells[1].startswith("Planned")
        assert "two failed reviews" in cells[1]
        assert cells[2:5] == ["on", "Claude Code, Cursor (texts only)", "\"turn off the review loop\""]
        assert plain(rows["review_loop"])
