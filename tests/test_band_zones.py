"""Slice 13: the band's chats zone and its failed checks counter.

The band draws `hubctl screen --json`, which the mod runs as a child of its
chat. So each chat here runs that command as its own child, and the screen's
"board" key is what its band would show. The mod picks the full or folded
chats zone by the width it draws at; tests in plugins/hub/mod/tests check that.
"""
import json
import os
import unicodedata

import pytest

from chats import Chats
from steps import and_, given, then, when

T = 1_800_000_000
FULL = {"questions": [{"question": "Which retry limit?", "header": "Retries", "multiSelect": False,
                       "options": [{"label": "3", "description": "Three tries."},
                                   {"label": "5", "description": "Five tries."}]}]}


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def hubctl_args(home, *args):
    return ["env", f"HUB_NOW={home.env['HUB_NOW']}", "python3", os.path.join(home.plugin, "lib", "hub_switch.py"), *args]


def screen_in(home, chat):
    """The screen model as the band of `chat` reads it."""
    code, out, err = chat.run(hubctl_args(home, "screen", "--json"))
    assert code == 0, out + err
    return json.loads(out)


def in_chat(home, chat, *args):
    code, out, err = chat.run(hubctl_args(home, *args))
    assert code == 0, out + err
    return out


def board_on(home):
    home.install()
    home.hubctl("on", "chat_board")
    home.env["HUB_NOW"] = str(T)


def start(chats, *names):
    return {name: chats.start(name, title=name, live=True, commands=True) for name in names}


def four_chats(home, chats):
    """API needs the user, DOCS is busy with 2 of 3 tasks done, LOBBY is idle, and WEB looks on."""
    running = start(chats, "LOBBY", "WEB", "DOCS", "API")
    chats.set_listing([chats.row(name, pid=chat.pid) for name, chat in running.items()])
    chats.send("DOCS", "UserPromptSubmit", prompt="Write the docs.")
    for verb, task in (("done", "intro"), ("done", "install"), ("now", "usage")):
        in_chat(home, running["DOCS"], "board", "task", verb, task)
    chats.send("LOBBY", "Stop", stop_hook_active=False)
    chats.tool("API", "AskUserQuestion", FULL)
    return running


def two_problems(home):
    """Two settings files that turn every hook off, which the doctor counts as two failed checks."""
    folder = os.path.join(home.root, ".claude")
    os.makedirs(folder, exist_ok=True)
    for name in ("settings.json", "settings.local.json"):
        with open(os.path.join(folder, name), "w") as handle:
            json.dump({"disableAllHooks": True}, handle)
    return [os.path.join(folder, name) for name in ("settings.json", "settings.local.json")]


def failed_checks(home):
    found = json.loads(home.hubctl("doctor", "--json").stdout)
    return len(found["problems"]) + sum(1 for line in found["part_lines"] if line["level"] == "warning")


def test_the_band_names_the_chat_that_needs_the_user_and_counts_the_rest(home, chats):
    with given("status_band.chats is on, API needs the user, DOCS is busy with 2 of 3 tasks done, "
               "and LOBBY is idle"):
        board_on(home)
        running = four_chats(home, chats)
    with when("WEB's band reads the screen"):
        screen = screen_in(home, running["WEB"])
        zone = screen["board"]["chats"]
    with then("the full chats zone reads \"! API  ● DOCS 2/3  ○ LOBBY\", and WEB is not in its own band"):
        assert screen["band"]["chats"] is True
        assert zone["full"] == "! API  ● DOCS 2/3  ○ LOBBY"
        assert zone["full_cells"] == 26
        assert "WEB" not in zone["full"] + zone["folded"]
    with and_("the folded zone reads \"! API ●1 ○1\", so API keeps its name"):
        assert zone["folded"] == "! API ●1 ○1"
        assert zone["folded_cells"] == 11
    with and_("the notice is API's question, and [open] shows API's card"):
        assert zone["question"]["line"] == "! API asks  which retry limit?"
        assert zone["question"]["chat"] == "API"
        card = in_chat(home, running["WEB"], "board", "show", zone["question"]["session"])
        assert card.startswith("! API  needs you")
    with when("DOCS views the same board"):
        zone = screen_in(home, running["DOCS"])["board"]["chats"]
    with then("DOCS is left out of its own band, and WEB, which has sent nothing yet, shows as idle"):
        assert zone["full"] == "! API  ○ LOBBY  ○ WEB"


def test_the_band_counts_the_failed_checks_of_the_doctors_last_run(home, chats):
    with given("status_band.problems is on, and the doctor has run once"):
        board_on(home)
        web = start(chats, "WEB")["WEB"]
        before = failed_checks(home)
    with when("two settings files turn every hook off, and the doctor runs again"):
        paths = two_problems(home)
        after = failed_checks(home)
    with then("the band's counter is two higher, and is the doctor's own count"):
        assert after == before + 2
        assert screen_in(home, web)["board"]["failed"] == after
    with when("the keys are gone and the doctor runs again"):
        for path in paths:
            os.unlink(path)
        failed_checks(home)
    with then("the counter is back where it started"):
        assert screen_in(home, web)["board"]["failed"] == before
    with when("the user turns off status_band.problems"):
        two_problems(home)
        failed_checks(home)
        home.hubctl("off", "status_band.problems")
    with then("the band shows no counter"):
        screen = screen_in(home, web)
        assert screen["band"]["problems"] is False
        assert screen["board"]["failed"] == 0


def test_turning_off_the_chats_zone_removes_only_that_zone(home, chats):
    with given("chat_board and the band are on, and four chats run"):
        board_on(home)
        running = four_chats(home, chats)
    with when("the user turns off status_band.chats"):
        home.hubctl("off", "status_band.chats")
        screen = screen_in(home, running["WEB"])
    with then("the band has no chats zone, its other zones stay, and hubctl board still lists every chat"):
        assert screen["band"]["chats"] is False
        assert screen["board"]["chats"] is None
        assert screen["band"]["runs"] is True and screen["band"]["problems"] is True
        names = [card["name"] for card in json.loads(home.hubctl("board", "--json").stdout)["cards"]]
        assert sorted(names) == ["API", "DOCS", "LOBBY", "WEB"]


def test_with_chat_board_off_the_band_has_no_chats_zone(home, chats):
    with given("the band is on, and chat_board is off"):
        home.install()
        home.env["HUB_NOW"] = str(T)
        web = start(chats, "WEB")["WEB"]
    with then("status_band.chats does not run, since it needs chat_board"):
        screen = screen_in(home, web)
        assert screen["band"]["chats"] is False
        assert screen["board"]["chats"] is None


def test_a_chat_alone_has_no_chats_zone(home, chats):
    with given("chat_board is on, and only WEB runs"):
        board_on(home)
        web = start(chats, "WEB")["WEB"]
        chats.send("WEB", "UserPromptSubmit", prompt="Fix it.")
    with then("WEB's band has no chats zone, because it never shows itself"):
        assert screen_in(home, web)["board"]["chats"] is None


# Odd input. The band is one line in every chat, so nothing a chat writes may break it.

def test_a_question_with_hidden_characters_and_line_breaks_stays_one_clean_line(home, chats):
    with given("chat_board is on, and API asks a question full of escapes, tabs and line breaks"):
        board_on(home)
        running = start(chats, "WEB", "API")
        odd = {"questions": [dict(FULL["questions"][0],
                                  question="Which\x1b[31m retry\tlimit?\n\n[open] ‮yes\r\nnow")]}
        chats.tool("API", "AskUserQuestion", odd)
    with when("WEB's band reads the screen"):
        question = screen_in(home, running["WEB"])["board"]["chats"]["question"]
    with then("the line has no control or format character and no line break"):
        assert question["line"].startswith("! API asks  which")
        assert not any(unicodedata.category(char) in ("Cc", "Cf") for char in question["line"])
        assert "\x1b" not in question["line"] and "\n" not in question["line"]


def test_wide_names_count_two_cells_each(home, chats):
    with given("chat_board is on, and a chat named in wide letters is idle"):
        board_on(home)
        web = start(chats, "WEB")["WEB"]
        chats.start("WIDE", title="文档", live=True)
        chats.send("WIDE", "Stop", stop_hook_active=False)
    with then("its zone counts each wide letter as two cells"):
        zone = screen_in(home, web)["board"]["chats"]
        assert zone["full"] == "○ 文档"
        assert zone["full_cells"] == 6


def test_more_than_99_other_chats_fold_to_99_plus(home, chats):
    with given("chat_board is on, and the listing holds 120 idle chats besides WEB"):
        board_on(home)
        web = start(chats, "WEB")["WEB"]
        rows = [chats.row(f"C{number}", pid=os.getpid(), session=f"s-{number}") for number in range(120)]
        chats.set_listing(rows + [chats.row("WEB", pid=web.pid)])
    with then("the folded zone reads ○99+"):
        zone = screen_in(home, web)["board"]["chats"]
        assert zone["folded"] == "○99+"


def test_the_band_outside_any_chat_shows_every_chat(home, chats):
    with given("chat_board is on, and four chats run"):
        board_on(home)
        four_chats(home, chats)
    with when("hubctl screen --json runs in a terminal that is no chat"):
        zone = json.loads(home.hubctl("screen", "--json").stdout)["board"]["chats"]
    with then("no chat is left out"):
        assert zone["full"] == "! API  ● DOCS 2/3  ○ LOBBY  ○ WEB"


def test_a_chat_that_needs_the_user_does_not_ask_in_its_own_band(home, chats):
    with given("chat_board is on, WEB asks a question, and DOCS is idle"):
        board_on(home)
        running = start(chats, "WEB", "DOCS")
        chats.send("DOCS", "Stop", stop_hook_active=False)
        chats.tool("WEB", "AskUserQuestion", FULL)
    with then("WEB's own band has no question, since the dialog is already in front of the user"):
        zone = screen_in(home, running["WEB"])["board"]["chats"]
        assert zone["question"] is None
        assert zone["full"] == "○ DOCS"


def test_only_the_coordinator_chats_band_is_told_to_reread_the_board_while_idle(home, chats):
    with given("chat_board is on, and HUB and WEB run, but the coordinator part is off"):
        board_on(home)
        running = start(chats, "HUB", "WEB")
    with then("no band is told to reread the board on its own"):
        assert [screen_in(home, running[name])["board"]["coordinator"] for name in ("HUB", "WEB")] == [False, False]
    with when("the user turns the coordinator part on"):
        home.hubctl("on", "coordinator")
    with then("HUB's band is told to, since HUB has the coordinator's name, and WEB's band is not"):
        assert [screen_in(home, running[name])["board"]["coordinator"] for name in ("HUB", "WEB")] == [True, False]
    with and_("a terminal that is no chat is not told to"):
        assert json.loads(home.hubctl("screen", "--json").stdout)["board"]["coordinator"] is False
    with when("the user turns status_band.chats off"):
        home.hubctl("off", "status_band.chats")
    with then("HUB's band is not told to either, since it shows no chats"):
        assert screen_in(home, running["HUB"])["board"]["coordinator"] is False
    with when("the user turns it on again, and turns chat_board off"):
        home.hubctl("on", "status_band.chats")
        home.hubctl("off", "chat_board")
    with then("HUB's band is not told to, since the board is off"):
        assert screen_in(home, running["HUB"])["board"]["coordinator"] is False


@pytest.mark.parametrize("content", ['{"failed": -3}','{"failed": true}', '{"failed": "2"}', '[2]', "not json",
                                     '{"failed": 1e400}'])
def test_a_broken_doctor_result_shows_no_counter(home, chats, content):
    with given("the doctor's last result file is broken"):
        board_on(home)
        web = start(chats, "WEB")["WEB"]
        os.makedirs(home.state, exist_ok=True)
        with open(os.path.join(home.state, "doctor-last.json"), "w") as handle:
            handle.write(content)
    with then("the band shows no counter, and the screen still reads"):
        assert screen_in(home, web)["board"]["failed"] == 0
