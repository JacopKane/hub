"""What the chat board tells the model, and what the doctor says about it (slice 13, step 7).

keep_task_line is start text that tells each chat to keep its task line with
`hubctl board task`. read_board is a coordinator skill section that needs
chat_board. The doctor says how old the chat listing is, that Claude Code's
own task list is not copied, and why the board does not run in Cursor. The
chats have made-up names and fake processes, as in the other board scenarios.
"""
import json
import os
import re
import shlex

import pytest

from chats import Chats
from conftest import DASH
from steps import and_, given, then, when

T = 1_800_000_000
KEEP = "chat_board.instructions.keep_task_line"
READ_BOARD = "coordinator.instructions.read_board"
SLOTS = 8


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def part_file(home, part, *names):
    return os.path.join(home.plugin, "parts", part, *names)


def start_text(home):
    folder = os.path.join(home.state, "slots", "claude_code")
    return "\n".join(read(os.path.join(folder, f"{number}.md")) for number in range(1, SLOTS + 1)
                     if os.path.exists(os.path.join(folder, f"{number}.md")))


def keep_text(home):
    return read(part_file(home, "chat_board", "text", "keep_task_line.md")).strip()


def skill_text(home):
    return read(os.path.join(home.root, ".claude", "skills", "hub-coordinator", "SKILL.md"))


def section(text, flag_id):
    return text.split(f"<!-- hub:section {flag_id} -->\n")[1].split("<!-- hub:end -->")[0].strip()


def doctor(home):
    return home.hubctl("doctor", check=False).stdout


def board_lines(text):
    return [line for line in text.splitlines() if line.startswith("note: chat_board:")
            or line.startswith("warning: chat_board:")]


def write_listing(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data if isinstance(data, str) else json.dumps(data))


def test_each_chat_is_told_to_keep_its_task_line_only_while_the_board_runs(home):
    with given("hub is applied, and chat_board is off"):
        home.install()
        text = keep_text(home)
    with then("the start text has no task line instruction"):
        assert text not in start_text(home)
        assert "hubctl board task" not in start_text(home)
    with when("the user turns chat_board on"):
        home.hubctl("on", "chat_board")
    with then("the start text holds the instruction once, and it names hubctl board task"):
        assert start_text(home).count(text) == 1
        assert "hubctl board task now" in text and "hubctl board task done" in text
        assert DASH not in text
    with when(f"the user turns off {KEEP}"):
        home.hubctl("off", KEEP)
    with then("the instruction leaves the start text, and the board still runs"):
        assert text not in start_text(home)
        assert json.loads(home.hubctl("board", "--json").stdout)["cards"] == []


def test_every_command_the_task_line_instruction_names_works_in_a_chat(home, chats):
    with given("chat_board is on, and WEB runs"):
        home.install()
        home.hubctl("on", "chat_board")
        home.env["HUB_NOW"] = str(T)
        web = chats.start("WEB", title="WEB", live=True, commands=True)
        commands = re.findall(r"`(hubctl board task [^`]+)`", keep_text(home))
    with when("WEB runs each command in the instruction, with a made-up task in place of the placeholder"):
        results = []
        for command in commands:
            words = shlex.split(command.replace("<task>", "write the README"))
            results.append(web.run(["env", f"HUB_NOW={T}", "python3", os.path.join(home.plugin, "bin", "hubctl"),
                                    *words[1:]]))
    with then("the instruction names now, add and done, and each command ends well"):
        assert {shlex.split(command)[3] for command in commands} >= {"now", "add", "done"}
        assert all(code == 0 for code, _out, _err in results), results
    with and_("WEB's ledger ends with the task done"):
        tasks = json.loads(home.hubctl("board", "--json").stdout)["cards"][0]["tasks"]
        assert [(task["text"], task["state"]) for task in tasks] == [("write the README", "done")]


def test_the_coordinator_reads_the_board_only_while_the_board_runs(home):
    with given("the coordinator is on, and chat_board is off"):
        home.install()
        home.hubctl("on", "coordinator")
        shipped = read(part_file(home, "coordinator", "skill", "SKILL.md"))
        wanted = section(shipped, READ_BOARD)
    with then("the shipped skill has a read_board section that runs hubctl board --json"):
        assert "hubctl board --json" in wanted
        assert DASH not in wanted
    with and_("the coordinator's skill text leaves the read_board section out and keeps the others"):
        copy = skill_text(home)
        assert wanted not in copy
        assert section(shipped, "coordinator.instructions.forward") in copy
        assert "<!-- hub:" not in copy
    with when("the user turns chat_board on"):
        home.hubctl("on", "chat_board")
    with then("the skill text holds the read_board section"):
        assert wanted in skill_text(home)
    with when(f"the user turns off {READ_BOARD}, and chat_board stays on"):
        home.hubctl("off", READ_BOARD)
    with then("the section leaves the skill text again"):
        assert wanted not in skill_text(home)


def test_the_doctor_says_how_old_the_listing_is_on_the_board_clock(home):
    with given("chat_board is on, the board clock is fixed, and no listing is kept"):
        home.install()
        home.hubctl("on", "chat_board")
        home.env["HUB_NOW"] = str(T)
        mirror = os.path.join(home.state, "board", "listing.json")
        cache = os.path.join(home.state, "sessions", "listing.json")
    with then("the doctor says the board runs the listing when it opens"):
        assert "holds no chat listing yet" in doctor(home)
    with when("a ListAgents copy from 5 minutes ago and a cache from an hour ago are kept"):
        write_listing(mirror, {"time": T - 300, "rows": []})
        write_listing(cache, {"time": T - 3600, "rows": []})
    with then("the doctor takes the newer one and says it is 5m old"):
        assert "its chat listing is 5m old." in doctor(home)
    with when("the copy says it is from the future, as after a clock change"):
        write_listing(mirror, {"time": T + 900, "rows": []})
    with then("the doctor says it is under a minute old"):
        assert "its chat listing is under a minute old." in doctor(home)
    for broken in ("{not json", json.dumps({"time": "yesterday", "rows": []}), json.dumps({"time": T, "rows": 5}),
                   json.dumps([1, 2])):
        with when(f"both copies hold {broken!r}"):
            write_listing(mirror, broken)
            write_listing(cache, broken)
        with then("the doctor still answers, and says no listing is held"):
            assert "holds no chat listing yet" in doctor(home)


def test_the_doctor_names_the_task_list_source_and_the_cursor_reason_once(home):
    with given("chat_board is on"):
        home.install()
        home.hubctl("on", "chat_board")
        reason = json.loads(read(part_file(home, "chat_board", "part.json")))["reason_not_in"]["cursor"]
    with when("the doctor runs"):
        text = doctor(home)
    with then("it says each chat writes its own task list, and Claude Code's own list is not copied"):
        assert any("each chat writes its own task list" in line and "not copied" in line
                   for line in board_lines(text))
    with and_("no chat_board line names hubctl, because the user's doors are /hub, the pane and the band"):
        assert not any("hubctl" in line for line in board_lines(text))
    with and_("it gives the reason chat_board does not run in Cursor exactly once"):
        assert text.count(reason) == 1
    with and_("every chat_board line is a note, so the band's failed checks count does not grow"):
        assert board_lines(text) and all(line.startswith("note: ") for line in board_lines(text))
    with when("chat_board is off"):
        home.hubctl("off", "chat_board")
    with then("the doctor has no chat_board line"):
        assert board_lines(doctor(home)) == []
