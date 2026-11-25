"""A settings file from before the PR test gate was removed still loads.

The scenario runs the real hubctl in a temporary HOME.
"""
import json

from steps import and_, given, then, when

RETIRED = ["pr_test_gate", "pr_test_gate.report", "pr_test_gate.body"]


def test_a_settings_file_that_still_turns_the_pr_test_gate_on_loads_and_the_doctor_finds_nothing(home):
    with given("hub is installed, and the doctor's answer on a settings file with no gate in it"):
        home.install()
        clean = json.loads(home.hubctl("doctor", "--json").stdout)
    with when("the settings file still turns the PR test gate and its two checks on, as an older hub wrote it"):
        home.switch_on(*RETIRED)
        applied = home.hubctl("apply", check=False)
        shown = home.hubctl("show", "--json", "--all", check=False)
        doctor = home.hubctl("doctor", check=False)
        found = json.loads(home.hubctl("doctor", "--json").stdout)
    with then("apply, show and the doctor all answer, and none of them names the gate"):
        assert (applied.returncode, shown.returncode, doctor.returncode) == (0, 0, 0), applied.stderr + shown.stderr
        assert not set(RETIRED) & set(json.loads(shown.stdout)["flags"])
        assert "pr_test_gate" not in doctor.stdout + doctor.stderr
    with and_("the doctor finds the same as on the file with no gate in it, and hub logged no error"):
        assert {key: found[key] for key in ("runs", "problems", "part_lines")} == {
            key: clean[key] for key in ("runs", "problems", "part_lines")}
        assert home.errors() == ""
    with when("the user then switches another part on"):
        home.hubctl("on", "pr_gif")
    with then("the change goes through, so the old values do not stop later writes"):
        assert home.show()["flags"]["pr_gif"]["runs"]
