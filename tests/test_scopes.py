"""Which scope may change what.

A committed .hub/hub.json comes with every clone, so anyone who can push to a
repo writes it. It may make the gate stricter, but only the user's own files,
the user scope and the local scope, may make a privacy check weaker. Word
lists from every scope add up.
"""
import json
import os

from conftest import DASH, commit
from steps import and_, given, then, when

GATE = "public_text_gate"


def test_a_cloned_repos_settings_can_raise_privacy_but_never_lower_it(home, tool):
    project_file = os.path.join(home.work, ".hub", "hub.json")
    with given("the user has a private word and the em dash check on, and a cloned repo commits a file that turns "
               "the gate down"):
        home.install()
        home.hubctl("set", "core.private_words", "Quillbrook")
        home.hubctl("on", f"{GATE}.dashes")
        home.write_json(project_file, {"version": 1, "private_words": ["Marrowind"], "flags": {
            GATE: {"by": "user", "value": "off"},
            "core": {"by": "user", "value": "off"},
            f"{GATE}.private_paths": {"by": "user", "value": "off"},
            f"{GATE}.targets.commit": {"by": "user", "value": "off"},
            f"{GATE}.public_repos_only": {"by": "user", "value": "on"},
            f"{GATE}.dashes": {"by": "user", "value": "off"}}})
    with when("Claude commits a home path, the user's word and the repo's word in that repo"):
        results = {name: home.hook(tool, "PreToolUse", commit(text)) for name, text in [
            ("path", "See /home/sam/plan.md"), ("user word", "Ship quillbrook"), ("repo word", "Ship marrowind")]}
    with then("each is refused, because the repo file can not lower a privacy flag and the lists add up"):
        assert results["path"].findings == ["private_paths"]
        assert results["user word"].findings == ["private_words"]
        assert results["repo word"].findings == ["private_words"]
    with and_("the repo file still turns off the dash check, which is style and not privacy"):
        assert home.hook(tool, "PreToolUse", commit(f"Tidy {DASH} done")).ran
    with when("the repo file raises the gate to block instead"):
        home.write_json(project_file, {"version": 1, "flags": {GATE: {"by": "user", "value": "block"}}})
        dashes = [home.hook(tool, "PreToolUse", commit(f"Once {DASH} more")) for _ in range(2)]
    with then("block applies, so the dash is refused every time"):
        assert [result.refused for result in dashes] == [True, True]
    with when("the user's own local file turns off the path check in that repo"):
        home.hubctl_from_mod("off", f"{GATE}.private_paths", "--scope", "local")
    with then("a home path goes through, because the local scope may lower it"):
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/plan.md")).ran
    with and_("hubctl refuses to write a lower privacy value into the project file"):
        refused = home.hubctl("off", f"{GATE}.secrets", "--scope", "project", check=False)
        assert refused.returncode == 1 and "privacy" in refused.stderr
        assert home.show("--all")["flags"][f"{GATE}.secrets"]["runs"]


def test_project_scope_needs_a_git_repo_and_undo_goes_back_to_the_repo_it_changed(home, tool):
    outside = os.path.join(home.root, "loose-folder")
    inside = os.path.join(home.work, "docs", "drafts")
    project_file = os.path.join(home.work, ".hub", "hub.json")
    with given("hub is installed with the em dash check off, as it ships, with a folder that is not in a git repo "
               "and a subfolder deep in the demo repo"):
        home.install()
        os.makedirs(outside)
        os.makedirs(inside)
    with when("the user sets a project value in the folder outside any repo"):
        refused = home.hubctl("on", f"{GATE}.dashes", "--scope", "project", cwd=outside, check=False)
    with then("hubctl refuses, says it needs a git repo, and makes no .hub folder there"):
        assert refused.returncode == 1 and "git repo" in refused.stderr
        assert not os.path.exists(os.path.join(outside, ".hub"))
    with when("the user sets the same value from the subfolder of the demo repo"):
        home.hubctl("on", f"{GATE}.dashes", "--scope", "project", cwd=inside)
    with then("the file lands at the repo's top, and the history line records that repo"):
        assert not os.path.exists(os.path.join(inside, ".hub"))
        with open(project_file) as handle:
            assert json.load(handle)["flags"] == {f"{GATE}.dashes": {"by": "user", "value": "on"}}
        assert home.history()[-1]["repo"] == home.work
    with and_("a dash is stopped in the subfolder, because the repo's file applies there"):
        assert home.hook(tool, "PreToolUse", commit(f"Tidy {DASH} done"), cwd=inside).refused
    with when("the user runs hubctl undo from the folder outside any repo"):
        home.hubctl("undo", cwd=outside)
    with then("undo restores the value in the repo it changed, and still makes nothing in the current folder"):
        with open(project_file) as handle:
            assert json.load(handle)["flags"] == {f"{GATE}.dashes": {"by": "user", "value": "off"}}
        assert not os.path.exists(os.path.join(outside, ".hub"))
        assert home.history()[-1]["repo"] == home.work


def test_a_local_file_that_git_tracks_counts_as_project_scope_and_doctor_warns_about_it(home, tool):
    local_file = os.path.join(home.work, ".hub", "hub.local.json")
    with given("a cloned repo commits a .hub/hub.local.json that turns the gate and its path check off"):
        home.install()
        home.write_json(local_file, {"version": 1, "flags": {
            GATE: {"by": "user", "value": "off"},
            f"{GATE}.private_paths": {"by": "user", "value": "off"},
            f"{GATE}.dashes": {"by": "user", "value": "off"}}})
        home.git("add", "-f", local_file)
        home.git("commit", "-q", "-m", "Add hub settings")
    with when("Claude commits a home path in that repo"):
        result = home.hook(tool, "PreToolUse", commit("See /home/sam/plan.md"))
    with then("it is refused, because a tracked local file can not lower a privacy flag"):
        assert result.findings == ["private_paths"]
    with and_("the tracked file still turns off the dash check, which is style and not privacy"):
        assert home.hook(tool, "PreToolUse", commit(f"Tidy {DASH} done")).ran
    with and_("hubctl refuses to write a lower privacy value into the tracked local file"):
        refused = home.hubctl("off", f"{GATE}.secrets", "--scope", "local", check=False)
        assert refused.returncode == 1 and "privacy" in refused.stderr and "git tracks" in refused.stderr
    with when("the user runs hubctl doctor in that repo"):
        doctor = home.hubctl("doctor")
    with then("it warns that git tracks the local file, names it and says how to stop tracking it"):
        assert local_file in doctor.stdout and "git tracks" in doctor.stdout
        assert "git rm --cached" in doctor.stdout
    with when("the user stops tracking the file, so it is the user's own again"):
        home.git("rm", "-q", "--cached", local_file)
        home.git("commit", "-q", "-m", "Stop tracking local settings")
    with then("the local file may lower the path check, and doctor has no warning"):
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/plan.md")).ran
        assert "git tracks" not in home.hubctl("doctor").stdout
