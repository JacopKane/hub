"""The rows the feature picker reads: `hubctl screen --json --all` on the day 9 fixture.

Only the full model carries each row's kind, flag type, allowed values and what an on
flag waits for, and each part's new mark. The band's model stays as it was.
"""
import json
import os

from conftest import PLUGIN
from steps import given, then, when
from test_doors import day9, screen

PICKER_KEYS = {"kind", "type", "values", "waits_for"}


def rows(model):
    found = {}
    for family in model["families"]:
        for part in family["parts"]:
            found[part["id"]] = part
            for child in part["children"]:
                found[child["id"]] = child
    return found


def picker_fixture():
    with open(os.path.join(PLUGIN, "mod", "tests", "picker-day9.ts"), encoding="utf-8") as handle:
        text = handle.read()
    return json.loads(text.split("/* json */", 1)[1])


def test_the_full_model_gives_each_row_its_kind_type_values_and_what_it_waits_for(home):
    with given("the day 9 fixture, where researcher is offered and coordinator is off"):
        day9(home)
    with when("hubctl screen --json --all runs"):
        found = rows(screen(home, "--all"))
    with then("a part row carries its part kind, and a switch takes off and on"):
        assert {key: found["builder"][key] for key in PICKER_KEYS} == {
            "kind": "agent", "type": "switch", "values": ["off", "on"], "waits_for": []}
        assert found["public_text_gate"]["kind"] == "hook" and found["public_text_gate"]["type"] == "mode"
        assert found["public_text_gate"]["values"] == ["off", "warn", "block"]
    with then("a child carries its own kind, and a switch under a mode takes the mode's values"):
        assert found["builder.model"]["kind"] == "option" and found["builder.model"]["type"] == "choice"
        assert found["builder.model"]["values"] == ["haiku", "sonnet", "opus"]
        assert found["builder.instructions"]["kind"] == "group"
        assert found["builder.instructions.read_first"]["kind"] == "instruction"
        assert found["coordinator.name"]["type"] == "text"
        assert found["public_text_gate.targets.commit"]["values"] == ["off", "on", "warn", "block"]
    with then("the children of an off part are listed, and each one that is on waits for that part"):
        assert found["coordinator"]["runs"] is False and found["coordinator"]["waits_for"] == []
        assert found["coordinator.start_nudge"]["waits_for"] == ["coordinator"]
        assert found["coordinator.work_guard.allowed"]["waits_for"] == ["coordinator"]
    with then("only the offered part is new"):
        assert found["researcher"]["new"] is True
        assert [row_id for row_id, row in found.items() if row.get("new")] == ["researcher"]


def test_a_part_that_is_on_waits_for_the_part_it_needs(home):
    with given("the day 9 fixture"):
        day9(home)
    with when("the user turns permission_relay on while coordinator is off"):
        home.hubctl("on", "permission_relay")
        found = rows(screen(home, "--all"))
    with then("permission_relay is on, does not run, and waits for coordinator"):
        relay = found["permission_relay"]
        assert (relay["effective"], relay["runs"], relay["waits_for"]) == ("on", False, ["coordinator"])
    with when("the user turns coordinator on too"):
        home.hubctl("on", "coordinator")
        found = rows(screen(home, "--all"))
    with then("permission_relay runs and waits for nothing"):
        assert found["permission_relay"]["runs"] is True and found["permission_relay"]["waits_for"] == []


def test_the_band_model_and_the_mod_fixtures_follow_the_real_model(home):
    with given("the day 9 fixture"):
        day9(home)
    with when("the band's model and the full model are read"):
        band, full = screen(home), rows(screen(home, "--all"))
    with then("the band's rows carry none of the picker's fields"):
        for row in rows(band).values():
            assert not PICKER_KEYS & row.keys() and "new" not in row
    with then("each row in the picker's fixture equals the real row of that id"):
        copy = picker_fixture()
        assert copy["header"]["parts_total"] == 33
        copied = rows(copy)
        assert len(copied) > 40
        for row_id, row in copied.items():
            real = dict(full[row_id])
            if "children" in row:
                real["children"] = [child for child in real["children"]
                                    if child["id"] in {kid["id"] for kid in row["children"]}]
            assert row == real, row_id
