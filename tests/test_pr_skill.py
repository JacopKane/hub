"""The pr part: the hub-pr skill, its body check and its night hold.

Every scenario installs hub in a temporary HOME and runs the real step. A PR
body is a file the check reads, and the clock is fixed with HUB_TEST_NOW.
"""
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

from conftest import DASH, PLUGIN
from steps import and_, given, then, when

SECTION = re.compile(r"^<!-- hub:section ([a-z0-9_.]+(?:=[a-z0-9_]+)?) -->$")
SAFETY_RULES = [
    "Steps other people can see, such as a push, a new PR, a body edit, a comment, a review or marking a PR ready, "
    "wait for the user's order.",
    "Nobody is @-mentioned unless the user asks for that person by name.",
    "Work and questions never go to a colleague. Bring them to the user instead.",
]
LIST_HEADER = "report_list_header.md"
KEPT_RULE = "report_kept_rule.md"


def part_path(home, *names):
    return os.path.join(home.plugin, "parts", "pr", *names)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def shipped_skill(home):
    return read(part_path(home, "skill", "SKILL.md"))


def linked_skill(home):
    return read(os.path.join(home.root, ".claude", "skills", "hub-pr", "SKILL.md"))


def instruction_ids(home):
    manifest = json.loads(read(part_path(home, "part.json")))
    group = next(child for child in manifest["children"] if child["id"] == "instructions")
    return [f"pr.instructions.{child['id']}" for child in group["children"]]


def section_text(text, marker):
    return text.split(f"<!-- hub:section {marker} -->\n")[1].split("<!-- hub:end -->")[0].strip()


def outside_every_section(text):
    """The lines of a shipped skill that no section marker wraps."""
    kept, depth = [], 0
    for line in text.split("\n"):
        if SECTION.match(line.strip()):
            depth += 1
        elif line.strip() == "<!-- hub:end -->":
            depth -= 1
        elif depth == 0:
            kept.append(line)
    return "\n".join(kept)


def quoted(home, name):
    return read(part_path(home, "text", name)).strip()


def pr_check(home, body, *extra, cwd=None, name="body.md"):
    path = os.path.join(home.root, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
    return home.hubctl("pr-check", path, *extra, cwd=cwd, check=False)


def findings(home, body, cwd=None):
    done = pr_check(home, body, "--json", cwd=cwd)
    assert done.returncode in (0, 1), done.stderr
    return json.loads(done.stdout)


def kinds(result):
    return sorted((finding["kind"], finding["line"]) for finding in result["findings"])


def good_body(extra=""):
    return ("## What it is\n\nAdds a retry to the upload, so a short network drop no longer loses a file.\n\n"
            "### To check it\n\nRun `make test` and upload a file with the network off for two seconds.\n\n"
            "### How it was tested\n\nThe upload scenario drops the network for two seconds. Not tested on a phone.\n\n"
            "## What is decided\n\nThree tries, one second apart.\n\n"
            "## What is open\n\nWhether the limit should be a setting.\n\n"
            "## Env changes\n\nNo env changes.\n" + extra)


def night(home, at, env=None):
    previous = dict(home.env)
    home.env.update({"HUB_TEST_NOW": at, **(env or {})})
    try:
        done = home.hubctl("pr-night")
    finally:
        home.env.clear()
        home.env.update(previous)
    return json.loads(done.stdout)


# The skill


def test_a_new_install_links_hub_pr_with_one_section_per_method_and_the_safety_rules_outside_them(home):
    with given("hub is installed in a fresh HOME"):
        home.install()
    with then("pr runs by default, and ~/.claude/skills/hub-pr links to a built copy of its skill"):
        assert home.show()["flags"]["pr"]["runs"]
        link = os.path.join(home.root, ".claude", "skills", "hub-pr")
        assert os.readlink(link) == os.path.join(home.state, "built", "skills", "hub-pr")
        text = shipped_skill(home)
        assert text.startswith("---\nname: hub-pr\n") and DASH not in text
    with and_("each method has exactly one section, and the open_as values one each"):
        found = [SECTION.match(line.strip()).group(1) for line in text.splitlines() if SECTION.match(line.strip())]
        assert sorted(found) == sorted(instruction_ids(home) + ["pr.open_as=draft", "pr.open_as=ready"])
    with and_("the three safety rules, the list header and the kept-rule line sit outside every section"):
        always = outside_every_section(text)
        for rule in SAFETY_RULES:
            assert rule in always, rule
        assert quoted(home, LIST_HEADER) in always and quoted(home, KEPT_RULE) in always
    with and_("the built copy fills in the step and keeps no marker"):
        copy = linked_skill(home)
        assert "{{step}}" not in copy and "<!-- hub:" not in copy
        assert "pr-check <file>" in copy and "pr-night" in copy


def test_each_method_drops_alone_when_switched_off_and_comes_back(home):
    with given("hub is installed, so the skill has every method"):
        home.install()
        text = shipped_skill(home)
        methods = instruction_ids(home)
    for method in methods:
        with when(f"the user turns off {method}"):
            home.hubctl("off", method)
            copy = linked_skill(home)
        with then("only that method's section is gone, and the safety rules stay"):
            assert section_text(text, method) not in copy
            for other in methods:
                if other != method and other != "pr.instructions.drafts":
                    assert section_text(text, other).split("\n")[0] in copy, other
            for rule in SAFETY_RULES:
                assert rule in copy
        with and_("turning it on brings the section back"):
            home.hubctl("on", method)
            assert section_text(text, method).split("\n")[0] in linked_skill(home)


def test_open_as_picks_the_draft_or_the_ready_line(home):
    with given("hub is installed with open_as at its default, draft"):
        home.install()
        draft = section_text(shipped_skill(home), "pr.open_as=draft")
        ready = section_text(shipped_skill(home), "pr.open_as=ready")
    with then("the skill tells Claude to open drafts"):
        assert draft in linked_skill(home) and ready not in linked_skill(home)
    with when("the user sets pr.open_as to ready"):
        home.hubctl("set", "pr.open_as", "ready")
    with then("the skill tells Claude to open PRs ready for review"):
        assert ready in linked_skill(home) and draft not in linked_skill(home)


BUILD_FOR_FOLDER = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
from hub import build, files, resolve
flags = resolve.current(sys.argv[2])["tools"]["claude_code"]["flags"]
class Part:
    id = "pr"
path = os.path.join(files.PARTS_DIR, "pr", "skill", "SKILL.md")
text, _changed = build.build(open(path).read(), Part, flags, path)
runs = {flag_id: record["runs"] for flag_id, record in flags.items() if flag_id.startswith("pr.instructions.")}
print(json.dumps({"text": text, "runs": runs}))
"""


def test_a_repo_settings_file_and_every_method_off_still_keep_the_safety_rules(home):
    with given("a repo commits a .hub/hub.json that turns every PR method off"):
        home.install()
        for method in instruction_ids(home):
            home.hubctl("off", method, "--scope", "project")
        home.git("add", ".hub/hub.json")
        home.git("commit", "-q", "-m", "Turn the PR methods off")
    with and_("the user turns every method off too"):
        for method in instruction_ids(home):
            home.hubctl("off", method)
    with when("the skill is built with the flags the repo's folder resolves to"):
        built = json.loads(home.run([sys.executable, "-I", "-c", BUILD_FOR_FOLDER,
                                     os.path.join(home.plugin, "lib"), home.work]).stdout)
    with then("no method runs there, and the three safety rules and the list header are still in the skill"):
        assert built["runs"] and not any(built["runs"].values())
        for rule in SAFETY_RULES:
            assert rule in built["text"], rule
        assert quoted(home, LIST_HEADER) in built["text"]
    with and_("the linked copy keeps them too"):
        copy = linked_skill(home)
        for rule in SAFETY_RULES:
            assert rule in copy, rule


def test_every_text_the_skill_or_its_scripts_use_is_a_pr_text_and_each_text_is_used(home):
    with given("hub is installed"):
        home.install()
        skill = shipped_skill(home)
        scripts = read(part_path(home, "check_body.py")) + read(part_path(home, "night.py"))
        folder = part_path(home, "text")
    with then("each text is quoted in the skill or read by a script, except the start-text rule lines"):
        check_kinds = {"check_mention.md", "check_bare_reference.md", "check_em_dash.md", "check_missing_not_tested.md"}
        for name in sorted(os.listdir(folder)):
            if name == "rules.md" or name.startswith("rule_"):
                continue
            body = read(os.path.join(folder, name)).strip()
            used = body in skill or f'"{name}"' in scripts or name in check_kinds
            assert used, name
        assert "check_{finding['kind']}.md" in scripts
    with and_("the body shape in the skill is text/body.md word for word"):
        assert "```markdown\n" + read(os.path.join(folder, "body.md")).strip() + "\n```" in skill
        assert quoted(home, "body_no_env_changes.md") in skill


# The body check


def test_a_body_that_misses_the_shape_or_tags_a_person_fails_by_kind_and_line(home):
    with given("hub is installed and a body with a mention, a bare PR number, an em dash and no open part"):
        home.install()
        body = ("## What it is\n\n"                                   # 1, 2
                "Thanks @sam for the idea in #12.\n"                  # 3: mention, bare reference
                "It retries three times" + DASH + "then stops.\n"      # 4: em dash
                "Mail sam@example.com, see [#13](https://github.com/acme/app/pull/13) "
                "and `@types/node`.\n"                                # 5: none of these count
                "```\n@sam #14\n```\n"                                # 6-8: code is not prose
                "### To check it\n\nRun the tests.\n\n"
                "### How it was tested\n\nThe retry scenario. Not tested on a phone.\n\n"
                "## What is decided\n\nThree tries.\n\n## Env changes\n\nNo env changes.\n")
    with when("the check runs on it"):
        done = pr_check(home, body)
        result = findings(home, body)
    with then("it exits 1 and names each finding by kind and line"):
        assert done.returncode == 1
        found = sorted((finding["kind"], finding["line"] or 0) for finding in result["findings"])
        assert found == [("bare_reference", 3), ("em_dash", 4), ("mention", 3), ("missing_heading", 0)]
        assert [finding["heading"] for finding in result["findings"] if finding["kind"] == "missing_heading"] \
            == ["What is open"]
    with and_("the sentences name the line, never the value it found"):
        assert "Line 3 tags a person" in done.stdout and "Line 4 has an em dash" in done.stdout
        assert '"What is open"' in done.stdout
        assert "@sam" not in done.stdout and "#12" not in done.stdout and DASH not in done.stdout
    with and_("a clean body passes with the clean line"):
        clean = pr_check(home, good_body())
        assert clean.returncode == 0 and clean.stdout.strip() == quoted(home, "check_clean.md")
    with and_("a body that does not say how it was tested fails on that part alone"):
        tested_part = good_body().split("### How it was tested")[1].split("## What is decided")[0]
        untested = findings(home, good_body().replace("### How it was tested" + tested_part, ""))
        assert [(finding["kind"], finding.get("heading")) for finding in untested["findings"]] \
            == [("missing_heading", "How it was tested")]


def test_a_long_body_gets_a_notice_and_the_file_is_not_changed(home):
    with given("hub is installed and a body of about 400 words"):
        home.install()
        body = good_body("\n" + " ".join(["word"] * 380) + "\n")
    with when("the check runs on it"):
        done = pr_check(home, body)
    with then("it exits 0 with a notice that asks before trimming, and the file is as it was"):
        assert done.returncode == 0
        assert "This is a notice, not a failure" in done.stdout and "Ask the user before you trim it" in done.stdout
        assert read(os.path.join(home.root, "body.md")) == body


def test_code_diagrams_details_and_comments_do_not_count_toward_the_length(home):
    with given("hub is installed and a short body with long code, a long diagram, a long details block and a long comment"):
        home.install()
        many = " ".join(["word"] * 300)
        body = good_body(f"\n```sh\n{many}\n```\n\n```mermaid\nflowchart LR\n{many}\n```\n\n"
                         f"<details>\n<summary>Logs</summary>\n\n{many}\n\n</details>\n\n<!-- {many} -->\n")
    with when("the check runs on it"):
        result = findings(home, body)
    with then("it gives no notice and no finding"):
        assert result["notice"] is None and result["findings"] == []


def test_a_repos_pr_template_wins_over_the_hub_shape_but_a_template_without_a_testing_part_gets_hubs(home):
    with given("hub is installed and the repo has a PR template with its own headings, in upper case"):
        home.install()
        os.makedirs(os.path.join(home.work, ".github"))
        with open(os.path.join(home.work, ".github", "PULL_REQUEST_TEMPLATE.md"), "w") as handle:
            handle.write("## Summary\n\n<!-- what and why -->\n\n## Testing\n")
    with when("a body with the template's headings is checked from a folder inside the repo"):
        inside = os.path.join(home.work, "src")
        os.makedirs(inside)
        result = findings(home, "## Summary\n\nAdds a retry.\n\n## Testing\n\nRun the tests. Not tested on Windows.\n",
                          cwd=inside)
    with then("no heading is missing, and the shape came from the template"):
        assert result["findings"] == [] and result["headings_from"] == os.path.join(".github",
                                                                                     "PULL_REQUEST_TEMPLATE.md")
    with when("a body in hub's shape is checked in that repo"):
        result = findings(home, good_body())
    with then("it misses the template's Summary, and hub's How it was tested stands in for the template's Testing"):
        assert [finding.get("heading") for finding in result["findings"]] == ["Summary"]
    with when("the repo has a folder of templates instead, and the body matches one of them"):
        os.remove(os.path.join(home.work, ".github", "PULL_REQUEST_TEMPLATE.md"))
        folder = os.path.join(home.work, ".github", "PULL_REQUEST_TEMPLATE")
        os.makedirs(folder)
        for name, heading in (("bug.md", "Steps to reproduce"), ("feature.md", "Motivation")):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write(f"## {heading}\n")
        tested = "\n\n## How it was tested\n\nThe upload scenario drops the network. Not tested on a phone.\n"
        result = findings(home, "## Motivation\n\nUsers lose uploads." + tested)
    with then("it passes"):
        assert result["findings"] == []
    with when("the body matches that template but says nothing about testing, which neither template asks for"):
        untested = findings(home, "## Motivation\n\nUsers lose uploads.\n")
    with then("it fails on hub's testing part alone, because the test gate method runs by default"):
        assert [(finding["kind"], finding.get("heading")) for finding in untested["findings"]] \
            == [("missing_heading", "How it was tested")]
        assert untested["headings_from"] == os.path.join(".github", "PULL_REQUEST_TEMPLATE", "feature.md")
    with when("the user turns the test gate method off"):
        home.hubctl("off", "pr.instructions.test_gate")
    with then("the template alone decides again, and the same body passes"):
        assert findings(home, "## Motivation\n\nUsers lose uploads.\n")["findings"] == []
    with and_("with one template again, a body in hub's shape needs the template's own Testing heading"):
        shutil.rmtree(folder)
        with open(os.path.join(home.work, ".github", "PULL_REQUEST_TEMPLATE.md"), "w") as handle:
            handle.write("## Summary\n\n## Testing\n")
        assert sorted(finding.get("heading") for finding in findings(home, good_body())["findings"]) \
            == ["Summary", "Testing"]


TEMPLATE_TESTING_HEADINGS = [
    ("## Testing:", True), ("### QA", True), ("## Verification", True), ("## How Has This Been Tested?", True),
    ("## Test plan", True), ("## How was this tested?", True), ("## Testing instructions", True),
    ("## Steps to test", True), ("## Notes on the test data", False), ("## Latest changes", False), ("## Quality", False),
]


def test_a_templates_testing_part_is_known_by_its_name_and_a_heading_that_only_mentions_tests_is_not_one(home):
    with given("hub is installed with the test gate method on, and the repo has a PR template"):
        home.install()
        os.makedirs(os.path.join(home.work, ".github"))
    for heading, names_testing in TEMPLATE_TESTING_HEADINGS:
        with when(f"the template has a Summary and {heading}, and the body fills both"):
            with open(os.path.join(home.work, ".github", "pull_request_template.md"), "w") as handle:
                handle.write(f"## Summary\n\n{heading}\n")
            result = findings(home, f"## Summary\n\nAdds a retry.\n\n{heading}\n\n"
                                    "The retry scenario ran. Not tested on a phone.\n")
        if names_testing:
            with then(f"{heading} is the template's testing part, so the body passes"):
                assert result["findings"] == []
        else:
            with then(f"{heading} is not a testing part, so the body also needs hub's How it was tested"):
                assert [(finding["kind"], finding.get("heading")) for finding in result["findings"]] \
                    == [("missing_heading", "How it was tested")]


NEAR_MISSES = ["How it's tested", "How its tested", "How this was tested", "How was it tested", "How we tested",
               "Tested", "Testing", "Tests", "Test plan"]


def test_a_tested_part_must_say_what_was_not_tested_near_misses_pass_and_the_test_gate_switch_drops_both(home):
    with given("hub is installed with the test gate method on, in a repo without a PR template"):
        home.install()
        tested_words = good_body().split("### How it was tested\n\n")[1].split("\n")[0]
        heading_line = good_body().split("\n").index("### How it was tested") + 1
        only_passing = good_body().replace(tested_words, "All tests pass.")
    with when("the tested part only says that the tests pass"):
        done = pr_check(home, only_passing)
        result = findings(home, only_passing)
    with then("it fails by kind and the heading's line, and the sentence asks what was not tested and why"):
        assert done.returncode == 1
        assert kinds(result) == [("missing_not_tested", heading_line)]
        assert done.stdout.strip() == quoted(home, "check_missing_not_tested.md").replace("{line}", str(heading_line))
        assert result["tested"] == {"heading": "How it was tested", "near_miss": False}
    with and_("a not-tested line in another part, or under a heading of the same level, does not count"):
        elsewhere = only_passing.replace("Whether the limit should be a setting.", "Not tested on a phone.")
        sibling = only_passing.replace("All tests pass.\n", "All tests pass.\n\n### Notes\n\nNot tested on a phone.\n")
        for body in (elsewhere, sibling):
            assert kinds(findings(home, body)) == [("missing_not_tested", heading_line)]
    with and_("one under a deeper heading inside the part counts, and so does each way of saying it"):
        deeper = only_passing.replace("All tests pass.\n", "All tests pass.\n\n#### Gaps\n\nThe phone app is untested.\n")
        assert findings(home, deeper)["findings"] == []
        for words in ("Un-tested on Windows.", "The phone app is left out.", "The migration was not run.",
                      "No BDD scenarios, because only texts changed.", "No smoke run, the repo has none."):
            assert findings(home, only_passing.replace("All tests pass.", words))["findings"] == [], words
    for near in NEAR_MISSES:
        with when(f"the tested part is headed {near}"):
            result = findings(home, good_body().replace("### How it was tested", f"### {near}"))
        with then(f"{near} passes as the tested part, marked as a near miss"):
            assert result["findings"] == [], near
            assert result["tested"] == {"heading": near, "near_miss": True}, near
    with and_("a near miss is still the tested part, so it needs a not-tested line too"):
        assert kinds(findings(home, only_passing.replace("### How it was tested", "### Testing"))) \
            == [("missing_not_tested", heading_line)]
    for miss in ("How to test", "Notes on testing", "QA"):
        with when(f"the part is headed {miss}, which neither hub's shape nor a template asks for"):
            result = findings(home, good_body().replace("### How it was tested", f"### {miss}"))
        with then("the body misses hub's tested heading, whatever the part says"):
            assert [(finding["kind"], finding.get("heading")) for finding in result["findings"]] \
                == [("missing_heading", "How it was tested")], miss
            assert result["tested"] == {"heading": None, "near_miss": False}
    with when("the repo's PR template uses How to test"):
        os.makedirs(os.path.join(home.work, ".github"))
        with open(os.path.join(home.work, ".github", "pull_request_template.md"), "w") as handle:
            handle.write("## Summary\n\n## How to test\n")
        own = "## Summary\n\nAdds a retry.\n\n## How to test\n\nRun the upload scenario. Not tested on a phone.\n"
    with then("its own heading passes with no near miss, and so does hub's"):
        result = findings(home, own)
        assert result["findings"] == [] and result["tested"] == {"heading": "How to test", "near_miss": False}
        assert findings(home, own.replace("## How to test", "## How it was tested"))["findings"] == []
    with and_("a body with no testing part misses the template's heading, not hub's"):
        assert [(finding["kind"], finding.get("heading"))
                for finding in findings(home, "## Summary\n\nAdds a retry.\n")["findings"]] \
            == [("missing_heading", "How to test")]
    with when("the template is gone and the user turns the test gate method off"):
        os.remove(os.path.join(home.work, ".github", "pull_request_template.md"))
        home.hubctl("off", "pr.instructions.test_gate")
    with then("a tested part without a not-tested line passes, and so does a body with no tested part"):
        tested_part = "### How it was tested\n\n" + tested_words + "\n\n"
        for body in (only_passing, good_body().replace(tested_part, "")):
            result = findings(home, body)
            assert result["findings"] == [] and result["tested"] == {"heading": None, "near_miss": False}


REFERENCE_LINES = [
    "Fixes #12.", "See PR 7 and issue 9.", "acme/app#3 is related.", "APP-42 asks for it.",
    "UTF-8 and SHA-256 are not tickets.", "colour: #123456 stays.", "[#5](https://github.com/acme/app/pull/5)",
    "https://github.com/acme/app/pull/6", "`#8` in code", "a&#9;b", "Plain words only.", "issue #10 and #11",
]


def test_the_check_finds_bare_references_exactly_where_link_check_does(home):
    with given("hub is installed, with link_check's own function loaded from the copy"):
        home.install()
        spec = importlib.util.spec_from_file_location(
            "copy_link_check", os.path.join(home.plugin, "parts", "link_check", "handler.py"))
        link_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(link_check)
        skip = ["UTF", "SHA", "ISO", "AES", "RSA", "TLS", "SSL", "HTTP", "IPV", "MP", "ES"]

        class AllOn:
            def runs(self, _flag_id):
                return True

            def value(self, flag_id):
                return skip if flag_id.endswith(".skip") else []
    with when("a body holds one sample per line and the check runs"):
        result = findings(home, good_body("\n".join(REFERENCE_LINES) + "\n"))
        first = good_body().count("\n") + 1
    with then("the lines it flags are the lines link_check flags"):
        flagged = {finding["line"] - first for finding in result["findings"] if finding["kind"] == "bare_reference"}
        expected = {index for index, line in enumerate(REFERENCE_LINES) if link_check.bare_references(line, AllOn())}
        assert flagged == expected and len(expected) >= 5


def test_the_step_refuses_wrong_words_and_a_switched_off_part(home):
    with given("hub is installed"):
        home.install()
    with then("pr-check without a file and pr-night with words exit 2 with their usage"):
        assert home.hubctl("pr-check", check=False).returncode == 2
        assert home.hubctl("pr-check", "a.md", "--yaml", check=False).returncode == 2
        assert home.hubctl("pr-night", "now", check=False).returncode == 2
    with when("the user turns pr off"):
        home.hubctl("off", "pr")
    with then("its commands do not run"):
        refused = home.hubctl("pr-night", check=False)
        assert refused.returncode != 0 and "pr is off" in refused.stderr


# The night hold


def test_night_hold_uses_away_guards_time_zone(home):
    with given("hub is installed, away_guard is on and its time zone is Asia/Tokyo, nine hours ahead of UTC"):
        home.install()
        home.hubctl("on", "away_guard")
        home.hubctl_from_mod("set", "away_guard.time_zone", "Asia/Tokyo")
    with when("the step asks at 22:30 in Tokyo"):
        state = night(home, "2026-03-02T13:30:00Z")
    with then("the steps wait until 07:00 Tokyo time, and the line says so"):
        assert state["held"] and state["until"] == "07:00" and state["time_zone"] == "Asia/Tokyo"
        assert "Asia/Tokyo" in state["line"] and "07:00" in state["line"] and "send it now" in state["line"]
    with when("the step asks at 08:30 in Tokyo"):
        state = night(home, "2026-03-02T23:30:00Z")
    with then("nothing waits, and the line says the waiting steps go now"):
        assert not state["held"] and state["line"] == quoted(home, "night_release.md").replace("{until}", "07:00")


def test_without_away_guard_the_night_hold_uses_the_system_time_zone(home):
    with given("hub is installed, away_guard is off and the system time zone is UTC"):
        home.install()
        assert not home.show()["flags"]["away_guard"]["runs"]
        home.hubctl_from_mod("set", "away_guard.time_zone", "Asia/Tokyo")
    with when("the step asks at 23:30 UTC, which is morning in Tokyo"):
        state = night(home, "2026-03-02T23:30:00Z", {"TZ": "UTC"})
    with then("the steps wait, because away_guard's zone counts only while it runs"):
        assert state["held"] and state["until"] == "07:00" and state["time_zone"] == "UTC"
    with when("the step asks at 12:00 UTC"):
        state = night(home, "2026-03-02T12:00:00Z", {"TZ": "UTC"})
    with then("nothing waits"):
        assert not state["held"]


def test_the_night_hold_never_holds_when_it_is_off_or_the_hours_are_empty(home):
    with given("hub is installed and the system time zone is UTC"):
        home.install()
    with when("the user turns off the night hold method and the step asks at 23:30 UTC"):
        home.hubctl("off", "pr.instructions.night_hold")
        state = night(home, "2026-03-02T23:30:00Z", {"TZ": "UTC"})
    with then("nothing waits"):
        assert not state["held"]
    with when("the method is on again but the quiet hours are empty"):
        home.hubctl("on", "pr.instructions.night_hold")
        home.hubctl_from_mod("set", "pr.quiet_hours", "")
        state = night(home, "2026-03-02T23:30:00Z", {"TZ": "UTC"})
    with then("nothing waits, and there is no line"):
        assert not state["held"] and state["line"] == "" and state["until"] is None


def test_a_new_install_holds_steps_from_22_to_7(home):
    with given("hub is installed with the defaults and the system time zone is UTC"):
        home.install()
    with then("the night hold holds at 22:00 and 06:59, and not at 21:59 or 07:00"):
        held = {at: night(home, f"2026-03-02T{at}:00Z", {"TZ": "UTC"})["held"]
                for at in ("21:59", "22:00", "06:59", "07:00")}
        assert held == {"21:59": False, "22:00": True, "06:59": True, "07:00": False}


# The skill's commands as Claude runs them, in a real repo. Only GitHub is a fake.

RECORDING_GH = """#!/bin/sh
printf '%s\\n' "$*" >> "$HUB_GH_LOG"
echo "gh is not reachable in this test" >&2
exit 1
"""


def skill_command(home, name):
    """The shell words the linked hub-pr skill gives for one of its commands."""
    found = re.findall(r"`(python3 -I \S+ " + name + r")[^`]*`", linked_skill(home))
    assert found, name
    return shlex.split(found[0])


def with_recording_gh(home):
    """A gh first on PATH that records each call and reaches nothing. Returns the log path."""
    folder = os.path.join(home.root, "gh-bin")
    os.makedirs(folder)
    with open(os.path.join(folder, "gh"), "w") as handle:
        handle.write(RECORDING_GH)
    os.chmod(os.path.join(folder, "gh"), 0o755)
    home.env["PATH"] = folder + os.pathsep + home.env["PATH"]
    home.env["HUB_GH_LOG"] = os.path.join(home.root, "gh-calls.log")
    return home.env["HUB_GH_LOG"]


def calls(log):
    return read(log).splitlines() if os.path.exists(log) else []


def shop_repo(home):
    """A real repo on a feature branch, with a PR template and a nested source folder."""
    repo = os.path.join(home.root, "work", "shop")
    os.makedirs(os.path.join(repo, ".github"))
    os.makedirs(os.path.join(repo, "src", "upload"))
    home.git("init", "-q", cwd=repo)
    with open(os.path.join(repo, ".github", "pull_request_template.md"), "w") as handle:
        handle.write("## Summary\n\n<!-- what and why -->\n\n## How to test\n\n## Risks\n")
    with open(os.path.join(repo, "src", "upload", "retry.py"), "w") as handle:
        handle.write("TRIES = 1\n")
    home.git("add", ".", cwd=repo)
    home.git("commit", "-q", "-m", "Start the shop", cwd=repo)
    home.git("checkout", "-q", "-b", "feat/upload-retry", cwd=repo)
    with open(os.path.join(repo, "src", "upload", "retry.py"), "w") as handle:
        handle.write("TRIES = 3\n")
    home.git("commit", "-q", "-am", "Retry the upload three times", cwd=repo)
    return repo


def repo_state(home, repo):
    return home.git("rev-parse", "HEAD", cwd=repo).stdout, home.git("status", "--porcelain", "--ignored", cwd=repo).stdout


def run_at(home, words, at, cwd, stdin=None):
    previous = dict(home.env)
    home.env.update({"HUB_TEST_NOW": at, "TZ": "UTC"})
    try:
        return home.run(words, cwd=cwd, stdin=stdin, check=False)
    finally:
        home.env.clear()
        home.env.update(previous)


def test_a_body_draft_goes_from_findings_to_clean_from_a_file_and_stdin_and_an_empty_body_fails_deep_in_a_real_repo(home):
    with given("hub is installed, a repo on a feature branch has its own PR template, and gh only records calls"):
        home.install()
        repo = shop_repo(home)
        gh_log = with_recording_gh(home)
        before = repo_state(home, repo)
        check = skill_command(home, "pr-check")
        deep = os.path.join(repo, "src", "upload")
    with and_("a first draft outside the repo uses hub's shape, tags a person, names a bare PR and has an em dash"):
        draft = os.path.join(home.root, "drafts", "body.md")
        os.makedirs(os.path.dirname(draft))
        text = ("## Summary\n\nThanks @sam, this retries the upload" + DASH + "three times.\n"
                "It follows #41.\n\n## How to test\n\nUpload a file with the network off for two seconds.\n")
        with open(draft, "w") as handle:
            handle.write(text)
    with when("Claude runs the check exactly as the built skill writes it, from two folders deep in the repo"):
        first = run_at(home, [*check, draft], "2026-03-02T12:00:00Z", deep)
        as_json = json.loads(run_at(home, [*check, draft, "--json"], "2026-03-02T12:00:00Z", deep).stdout)
    with then("it fails with each finding by kind and line, and takes the shape from the repo's template"):
        assert first.returncode == 1, first.stderr
        assert kinds(as_json) == [("bare_reference", 4), ("em_dash", 3), ("mention", 3), ("missing_heading", None),
                                  ("missing_not_tested", 6)]
        assert [finding["heading"] for finding in as_json["findings"] if finding.get("heading")] == ["Risks"]
        assert as_json["headings_from"] == os.path.join(".github", "pull_request_template.md")
    with and_("the output names lines, never the values it found"):
        assert "Line 3 tags a person" in first.stdout and '"Risks"' in first.stdout
        assert "The tested part at line 6 does not say what was left out" in first.stdout
        assert "@sam" not in first.stdout and "#41" not in first.stdout and DASH not in first.stdout
    with when("Claude fixes the draft and passes it on stdin, as the skill allows with -"):
        fixed = ("## Summary\n\nThis retries the upload three times, then stops. It follows "
                 "[#41](https://github.com/acme/shop/pull/41).\n\n## How to test\n\nUpload a file with the "
                 "network off for two seconds. Not tested on a phone.\n\n## Risks\n\nA slow network waits up to three seconds longer.\n")
        second = run_at(home, [*check, "-"], "2026-03-02T12:00:00Z", deep, stdin=fixed)
    with then("it passes with the clean line"):
        assert second.returncode == 0 and second.stdout.strip() == quoted(home, "check_clean.md")
    with and_("the draft, the repo and its branch are as they were, and GitHub was never called"):
        assert read(draft) == text and repo_state(home, repo) == before
        assert home.git("branch", "--show-current", cwd=repo).stdout.strip() == "feat/upload-retry"
        assert calls(gh_log) == []
    with when("an empty body comes in on stdin"):
        empty = run_at(home, [*check, "-"], "2026-03-02T12:00:00Z", deep, stdin="")
    with then("it fails once for each heading of the template"):
        assert empty.returncode == 1 and empty.stdout.count("heading. Add it") == 3


def test_the_night_hold_the_skill_names_follows_a_repos_hours_and_away_guards_zone(home):
    with given("hub is installed with the defaults, the system time zone is UTC and gh only records calls"):
        home.install()
        gh_log = with_recording_gh(home)
        night_hold = skill_command(home, "pr-night")
        outside = home.work
    with and_("a repo commits its own quiet hours, 20:00 to 08:00"):
        repo = shop_repo(home)
        home.hubctl("set", "pr.quiet_hours", "20:00-08:00", "--project", cwd=repo)
        home.git("add", ".hub/hub.json", cwd=repo)
        home.git("commit", "-q", "-m", "Keep PR steps quiet at night", cwd=repo)

    def state(at, cwd):
        done = run_at(home, night_hold, at, cwd)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    with when("Claude asks at 21:00 UTC inside the repo and in another repo"):
        inside, elsewhere = state("2026-03-02T21:00:00Z", repo), state("2026-03-02T21:00:00Z", outside)
    with then("the repo holds the steps until 08:00, and the other repo keeps the user's 22:00 start"):
        assert inside["held"] and inside["until"] == "08:00" and inside["time_zone"] == "UTC"
        assert "08:00" in inside["line"] and "send it now" in inside["line"]
        assert not elsewhere["held"]
    with when("Claude asks at 07:30 and at 08:00 UTC inside the repo"):
        late, morning = state("2026-03-03T07:30:00Z", repo), state("2026-03-03T08:00:00Z", repo)
    with then("07:30 still waits, and at 08:00 the line says the waiting steps go now"):
        assert late["held"]
        assert not morning["held"] and morning["line"] == quoted(home, "night_release.md").replace("{until}", "08:00")
    with when("the user turns on away_guard in Tokyo, nine hours ahead, and Claude asks at 23:30 UTC in the repo"):
        home.hubctl("on", "away_guard")
        home.hubctl_from_mod("set", "away_guard.time_zone", "Asia/Tokyo")
        tokyo = state("2026-03-02T23:30:00Z", repo)
    with then("it is 08:30 in Tokyo, so nothing waits, and the zone is Tokyo's"):
        assert not tokyo["held"] and tokyo["time_zone"] == "Asia/Tokyo"
    with and_("at 21:00 UTC, 06:00 in Tokyo, the steps wait until 08:00 Tokyo time"):
        dawn = state("2026-03-02T21:00:00Z", repo)
        assert dawn["held"] and dawn["until"] == "08:00" and "Asia/Tokyo" in dawn["line"]
    with when("the user turns the night hold method off"):
        home.hubctl("off", "pr.instructions.night_hold")
    with then("the skill drops its night section, and the command holds nothing even at 23:00 in Tokyo"):
        assert "pr-night" not in linked_skill(home)
        assert not state("2026-03-02T14:00:00Z", repo)["held"]
    with and_("GitHub was never called"):
        assert calls(gh_log) == []


def home_tree(home):
    """Every folder and file under the fake HOME, files with their size and change time.

    Python's byte cache in the plugin copy is left out, because python3 -I
    writes it for any script and it is not hub's state.
    """
    found = {}
    for folder, folders, names in os.walk(home.root):
        folders[:] = sorted(name for name in folders if name != "__pycache__")
        for name in folders:
            found[os.path.relpath(os.path.join(folder, name), home.root)] = "folder"
        for name in names:
            info = os.lstat(os.path.join(folder, name))
            found[os.path.relpath(os.path.join(folder, name), home.root)] = (info.st_size, info.st_mtime_ns)
    return found


def test_the_body_check_and_the_night_hold_read_settings_on_a_fresh_home_and_write_nothing(home):
    with given("hub's files are in a fresh HOME but hub was never applied, so there is no state, no settings and no link"):
        shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
        check = [sys.executable, "-I", part_path(home, "check_body.py")]
        night_hold = [sys.executable, "-I", part_path(home, "night.py")]
        hub_made = [home.state, home.config, os.path.join(home.root, ".claude")]
        assert not any(os.path.exists(path) for path in hub_made)
    with and_("a repo commits quiet hours of its own, 20:00 to 08:00, written by hand in .hub/hub.json"):
        repo = shop_repo(home)
        os.makedirs(os.path.join(repo, ".hub"))
        with open(os.path.join(repo, ".hub", "hub.json"), "w") as handle:
            json.dump({"version": 1, "flags": {"pr.quiet_hours": {"by": "user", "value": "20:00-08:00"}}}, handle)
        home.git("add", ".hub/hub.json", cwd=repo)
        home.git("commit", "-q", "-m", "Keep PR steps quiet at night", cwd=repo)
    with and_("a clean draft body sits outside every repo, and a second body names a bare ticket and UTF-8"):
        draft = os.path.join(home.root, "drafts", "body.md")
        os.makedirs(os.path.dirname(draft))
        with open(draft, "w") as handle:
            handle.write(good_body())
        ticket_sentence = "It closes APP-42, and the export is UTF-8 now."
        with_ticket = good_body("\n" + ticket_sentence + "\n")
        ticket_line = with_ticket.split("\n").index(ticket_sentence) + 1
        before = home_tree(home)
    with when("Claude checks the clean draft from a repo without a template, and the second body on stdin"):
        clean = run_at(home, [*check, draft], "2026-03-02T12:00:00Z", home.work)
        ticket = run_at(home, [*check, "-", "--json"], "2026-03-02T12:00:00Z", home.work, stdin=with_ticket)
    with then("the clean draft passes in hub's own shape, and only the ticket is flagged, by the shipped lists"):
        assert clean.returncode == 0, clean.stderr
        assert clean.stdout.strip() == quoted(home, "check_clean.md")
        assert ticket.returncode == 1, ticket.stderr
        assert kinds(json.loads(ticket.stdout)) == [("bare_reference", ticket_line)]

    def state(at, cwd):
        done = run_at(home, night_hold, at, cwd)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    with when("Claude asks the night hold at 21:00 UTC deep inside the repo, in another repo and outside any repo"):
        inside = state("2026-03-02T21:00:00Z", os.path.join(repo, "src", "upload"))
        elsewhere = state("2026-03-02T21:00:00Z", home.work)
        no_repo = state("2026-03-02T21:00:00Z", home.root)
    with then("the repo's own hours hold the steps until 08:00, and the shipped 22:00 start lets them go elsewhere"):
        assert inside["held"] and inside["until"] == "08:00" and inside["time_zone"] == "UTC"
        assert not elsewhere["held"] and elsewhere["until"] == "07:00"
        assert not no_repo["held"] and no_repo["until"] == "07:00"
    with when("Claude asks again at 23:30 UTC outside any repo"):
        late = state("2026-03-02T23:30:00Z", home.root)
    with then("the shipped quiet hours hold the steps until 07:00 in the system time zone"):
        assert late["held"] and late["until"] == "07:00" and late["time_zone"] == "UTC"
        assert late["line"] == quoted(home, "night_hold.md").replace("{time_zone}", "UTC").replace("{until}", "07:00")
    with and_("nothing under HOME or its XDG folders changed: no state, no resolved flags, no settings and no link"):
        assert home_tree(home) == before
        assert not any(os.path.exists(path) for path in hub_made)
    with when("the user's own settings sit under the settings folder's old name, with quiet hours from 21:00"):
        old_name = os.path.join(".config", "hubctl")
        new_name = os.path.relpath(home.config, home.root)
        os.makedirs(os.path.join(home.root, old_name))
        user_settings = json.dumps({"version": 1, "flags": {"pr.quiet_hours": {"by": "user", "value": "21:00-07:00"}}})
        with open(os.path.join(home.root, old_name, "hub.json"), "w") as handle:
            handle.write(user_settings)
        before_move = home_tree(home)
    with and_("Claude asks the night hold at 21:00 UTC outside any repo and checks the clean draft"):
        evening = state("2026-03-02T21:00:00Z", home.root)
        checked = run_at(home, [*check, draft], "2026-03-02T21:00:00Z", home.work)
    with then("the user's hours hold the steps until 07:00, and the draft still passes"):
        assert evening["held"] and evening["until"] == "07:00"
        assert checked.returncode == 0, checked.stderr
    with and_("the settings folder now has its new name with the file as it was, and nothing else was written"):

        def renamed(path):
            return new_name + path[len(old_name):] if path == old_name or path.startswith(old_name + os.sep) else path

        assert home_tree(home) == {renamed(path): value for path, value in before_move.items()}
        assert read(os.path.join(home.config, "hub.json")) == user_settings
        assert not os.path.exists(home.state) and not os.path.exists(os.path.join(home.root, ".claude"))
