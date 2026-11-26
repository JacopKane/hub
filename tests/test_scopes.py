"""Which scope may change what.

A committed .hub/hub.json comes with every clone, so anyone who can push to a
repo writes it. It may make a guard stricter, but only the user's own files,
the user scope and the local scope, may make a privacy check weaker.
"""
import json
import os

from steps import and_, given, then, when

GATE = "demo_gate"


def flags(home, cwd=None):
    return json.loads(home.hubctl("show", "--json", "--all", cwd=cwd).stdout)["flags"]


def test_a_cloned_repos_settings_can_raise_privacy_but_never_lower_it(home):
    project_file = os.path.join(home.work, ".hub", "hub.json")
    with given("the user has the em dash check on, and a cloned repo commits a file that turns the guard down"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        home.write_json(project_file, {"version": 1, "flags": {
            GATE: {"by": "user", "value": "off"},
            "core": {"by": "user", "value": "off"},
            f"{GATE}.private_paths": {"by": "user", "value": "off"},
            f"{GATE}.targets.commit": {"by": "user", "value": "off"},
            f"{GATE}.public_repos_only": {"by": "user", "value": "on"},
            f"{GATE}.dashes": {"by": "user", "value": "off"}}})
    with when("hub works out the flags in that repo"):
        found = flags(home)
    with then("the repo file lowers no privacy flag: the guard, core, the path check and its target still run"):
        assert found[GATE]["effective"] == "warn"
        assert all(found[flag]["runs"] for flag in ("core", f"{GATE}.private_paths", f"{GATE}.targets.commit"))
        assert found[f"{GATE}.public_repos_only"]["effective"] == "off"
    with and_("the repo file still turns off the dash check, which is style and not privacy"):
        assert found[f"{GATE}.dashes"]["effective"] == "off"
    with when("the repo file raises the guard to block instead"):
        home.write_json(project_file, {"version": 1, "flags": {GATE: {"by": "user", "value": "block"}}})
    with then("block applies"):
        assert flags(home)[GATE]["effective"] == "block"
    with when("the user's own local file turns off the path check in that repo"):
        home.hubctl_from_mod("off", f"{GATE}.private_paths", "--scope", "local")
    with then("the path check is off, because the local scope may lower it"):
        assert flags(home)[f"{GATE}.private_paths"]["runs"] is False
    with and_("hubctl refuses to write a lower privacy value into the project file"):
        refused = home.hubctl("off", f"{GATE}.secrets", "--scope", "project", check=False)
        assert refused.returncode == 1 and "privacy" in refused.stderr
        assert flags(home)[f"{GATE}.secrets"]["runs"]


def test_project_scope_needs_a_git_repo_and_undo_goes_back_to_the_repo_it_changed(home):
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
    with and_("the dash check runs in the subfolder at the part's warn, because the repo's file applies there"):
        assert flags(home, cwd=inside)[f"{GATE}.dashes"]["effective"] == "warn"
    with when("the user runs hubctl undo from the folder outside any repo"):
        home.hubctl("undo", cwd=outside)
    with then("undo restores the value in the repo it changed, and still makes nothing in the current folder"):
        with open(project_file) as handle:
            assert json.load(handle)["flags"] == {f"{GATE}.dashes": {"by": "user", "value": "off"}}
        assert not os.path.exists(os.path.join(outside, ".hub"))
        assert home.history()[-1]["repo"] == home.work


def test_a_local_file_that_git_tracks_counts_as_project_scope_and_doctor_warns_about_it(home):
    local_file = os.path.join(home.work, ".hub", "hub.local.json")
    with given("a cloned repo commits a .hub/hub.local.json that turns the guard and its path check off"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        home.write_json(local_file, {"version": 1, "flags": {
            GATE: {"by": "user", "value": "off"},
            f"{GATE}.private_paths": {"by": "user", "value": "off"},
            f"{GATE}.dashes": {"by": "user", "value": "off"}}})
        home.git("add", "-f", local_file)
        home.git("commit", "-q", "-m", "Add hub settings")
    with when("hub works out the flags in that repo"):
        found = flags(home)
    with then("the path check still runs, because a tracked local file can not lower a privacy flag"):
        assert found[GATE]["effective"] == "warn" and found[f"{GATE}.private_paths"]["runs"]
    with and_("the tracked file still turns off the dash check, which is style and not privacy"):
        assert found[f"{GATE}.dashes"]["effective"] == "off"
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
        assert flags(home)[f"{GATE}.private_paths"]["runs"] is False
        assert "git tracks" not in home.hubctl("doctor").stdout
