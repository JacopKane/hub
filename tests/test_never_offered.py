"""A part marked never_offered is never offered.

A part that is new in an update usually goes to offers.json and stays off.
A part marked never_offered skips that. It takes its default for every user
who did not turn it off, an apply drops an open offer for it, and setup
drops it from the tuner's answer. A user who said no to an earlier offer
keeps it off, because saying no pins the part off.
"""
import os

from steps import and_, given, then, when
from test_doors import screen

PART = "researcher"


def offered_ids(home):
    return [offer["id"] for offer in home.state_file("offers.json")["offers"]]


def strip_ids(home):
    return [line["id"] for line in screen(home)["strip"]]


def make_new_in_the_next_apply(home):
    """seen.json as an apply before the part shipped left it, so the next apply finds the part new."""
    seen = home.state_file("seen.json")
    home.write_json(os.path.join(home.state, "seen.json"), {
        "parts": [part for part in seen["parts"] if part != PART],
        "nodes": [node for node in seen["nodes"] if not node.startswith(f"{PART}.")]})


def offered_as_new(home):
    """hub installed, and the part arrived in an update and waits in an open offer."""
    home.install()
    make_new_in_the_next_apply(home)
    home.step("apply")


def ship_on_and_never_offered(part):
    part["default"] = "on"
    part["never_offered"] = True
    part.pop("offer_when", None)


def test_a_never_offered_part_drops_its_open_offer_and_runs_for_a_user_who_never_turned_it_off(home):
    with given("hub is installed, and the part came in an update and waits in an open offer"):
        offered_as_new(home)
    with then("the part is off because of the offer"):
        assert offered_ids(home) == [PART] and f"offer:{PART}" in strip_ids(home)
        record = home.show()["flags"][PART]
        assert record["by"] == "offer" and not record["runs"]
    with when("the next update ships the part on and marked never_offered, and apply runs"):
        home.edit_manifest(PART, ship_on_and_never_offered)
        home.step("apply")
    with then("the offer is gone and the part runs by default"):
        assert offered_ids(home) == [] and f"offer:{PART}" not in strip_ids(home)
        record = home.show()["flags"][PART]
        assert record["by"] == "default" and record["runs"]
    with when("the part is new again in a later apply"):
        make_new_in_the_next_apply(home)
        home.step("apply")
    with then("that apply offers nothing and the part still runs"):
        assert offered_ids(home) == [] and home.show()["flags"][PART]["runs"]


def test_a_never_offered_part_stays_off_for_a_user_who_said_no_and_setup_never_offers_it(home):
    with given("hub is installed, the part came in an update, and the user said no to its offer"):
        offered_as_new(home)
        home.step("no", PART)
        assert home.settings()["flags"][PART] == {"by": "user", "value": "off"}
    with when("the next update ships the part on and marked never_offered, and apply runs"):
        home.edit_manifest(PART, ship_on_and_never_offered)
        home.step("apply")
    with then("the part stays off and nothing is offered"):
        assert offered_ids(home) == [] and not home.show()["flags"][PART]["runs"]
    with when("a later setup answer from the tuner offers the part"):
        answer = os.path.join(home.root, "answer.json")
        home.write_json(answer, {"profile": {}, "questions": [], "instructions_off": [], "offers": [PART]})
        home.step("setup", "--answer", answer)
        pending = home.state_file(os.path.join("setup", "pending.json"))
        home.step("setup", "--reply", "{}")
    with then("setup drops that offer, so nothing is offered and the part stays off"):
        assert pending["offers"] == []
        assert offered_ids(home) == [] and f"offer:{PART}" not in strip_ids(home)
        assert not home.show()["flags"][PART]["runs"]
