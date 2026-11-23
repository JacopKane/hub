"""The pr_gif part: the hub-pr-gif skill, which says how to record, check, upload and embed a GIF in a PR.

Every scenario installs hub in a temporary HOME and runs the real apply, switch and session start.
"""
import os
import shutil

from conftest import DASH, PLUGIN
from steps import and_, given, then, when
from test_start_text_and_links import read, session_start

PART = "pr_gif"
SKILL_NAME = "hub-pr-gif"
# One phrase for each step of the skill's outline, in order.
OUTLINE = [
    "needs a GIF or an image",
    "Record the product in use",
    "a test run only when",
    "invented demo data",
    "one frame per second, plus the first and the last",
    "record again",
    "outside the repo",
    "Never `git add`",
    "GitHub attachment",
    "waits for the user's order",
    "night hold",
    "Never press Comment",
    "one-line caption",
    "Never write a link to a file that was not uploaded",
    "A screenshot is fine when motion adds nothing",
    '"No GIF fits, because"',
    "five or more words",
]
# Words that tie a skill to one machine or one person's setup.
NOT_GENERIC = ["~/.claude", "/Users/", "/home/", "/opt/homebrew", "chrome-lock", "scratchpad", "hubctl", "<!-- hub:"]


def skill_link(home):
    return os.path.join(home.root, ".claude", "skills", f"hub-{PART}")


def front_matter(text):
    head = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines() if ": " in line)


def listed_skills(home):
    """{name: description} of each skill folder under ~/.claude/skills, read from its front matter."""
    folder = os.path.join(home.root, ".claude", "skills")
    heads = [front_matter(read(os.path.join(folder, entry, "SKILL.md"))) for entry in sorted(os.listdir(folder))
             if os.path.isfile(os.path.join(folder, entry, "SKILL.md"))]
    return {head["name"]: head.get("description", "") for head in heads}


def out_of_order(words, phrases):
    """The phrases not found after the one before them, each searched from where the one before it starts."""
    position, missing = 0, []
    for phrase in phrases:
        found = words.find(phrase, position)
        if found == -1:
            missing.append(phrase)
        else:
            position = found
    return missing


def offered(home):
    return [offer["id"] for offer in home.state_file("offers.json")["offers"]]


def test_a_new_install_lists_hub_pr_gif_and_its_text_walks_from_recording_to_the_caption(home):
    with given("hub is installed in a fresh HOME"):
        home.install()
    with then("pr_gif runs by default in both tools, and nothing offers it"):
        flags = home.show("--all")["flags"]
        assert flags[PART]["runs"] and flags[PART]["value"] == "on" and flags[PART]["by"] == "default"
        assert PART not in offered(home)
    with when("a session starts"):
        _, output = session_start(home)
    with then("the dispatcher asks Claude Code to read the skills again"):
        assert output["hookSpecificOutput"]["reloadSkills"] is True
    with and_("the skill listing holds hub-pr-gif, with a description that says when to use it"):
        description = listed_skills(home)[SKILL_NAME]
        assert "GIF" in description and "pull request" in description and "Use it" in description
    with and_("its text loads through the link and walks each step of the outline in order"):
        text = read(os.path.join(skill_link(home), "SKILL.md"))
        words = " ".join(text.split())
        assert out_of_order(words, OUTLINE) == []
    with and_("it stays generic, with no machine paths, no setup of one person, no em dash and no sections"):
        assert [word for word in NOT_GENERIC if word in text] == []
        assert DASH not in text and len(text.splitlines()) <= 90
    with when("the user turns pr_gif off"):
        home.hubctl("off", PART)
    with then("the link is gone and the listing no longer has the skill"):
        assert not os.path.lexists(skill_link(home))
        assert SKILL_NAME not in listed_skills(home)
    with when("the user turns it on again"):
        home.hubctl("on", PART)
    with then("the same text is back"):
        assert read(os.path.join(skill_link(home), "SKILL.md")) == text


def test_an_update_that_brings_pr_gif_keeps_it_off_and_offers_it_and_the_offer_links_the_skill(home):
    with given("hub from before pr_gif shipped is installed, with pr and pr_test_gate on by default"):
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__", PART))
        home.hubctl("apply")
        before = home.show("--all")["flags"]
        assert PART not in before and before["pr"]["runs"] and before["pr_test_gate"]["runs"]
        session_start(home)
    with when("the update adds the pr_gif part and apply runs"):
        shutil.copytree(os.path.join(PLUGIN, "parts", PART), os.path.join(home.plugin, "parts", PART),
                        ignore=shutil.ignore_patterns("__pycache__"))
        home.hubctl("apply")
    with then("pr_gif stays off, is offered once, and the PR parts still run"):
        flags = home.show("--all")["flags"]
        assert (flags[PART]["value"], flags[PART]["runs"]) == ("off", False)
        assert offered(home) == [PART]
        assert flags["pr"]["runs"] and flags["pr_test_gate"]["runs"]
    with and_("no skill is linked for it, and a second apply offers nothing twice"):
        assert not os.path.lexists(skill_link(home)) and SKILL_NAME not in listed_skills(home)
        home.hubctl("apply")
        assert offered(home) == [PART]
    with when("the user takes the offer and a session starts"):
        home.hubctl("on", PART)
        _, output = session_start(home)
    with then("pr_gif runs, pinned on by the user, and the session reads the skills again"):
        assert home.show()["flags"][PART]["runs"]
        assert home.settings()["flags"][PART] == {"by": "user", "value": "on"}
        assert output["hookSpecificOutput"]["reloadSkills"] is True
    with and_("the listing holds hub-pr-gif, and its text is the shipped one"):
        assert SKILL_NAME in listed_skills(home)
        shipped = read(os.path.join(home.plugin, "parts", PART, "skill", "SKILL.md"))
        assert read(os.path.join(skill_link(home), "SKILL.md")) == shipped
