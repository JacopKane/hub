"""Slice 13: the chat board, Claude Code only.

Every chat shows as busy, idle, offline or needs you, with its current task.
Each chat is the hook input it would send, fed to the real dispatcher in a
temporary HOME, with a fake `claude agents --json`. The clock is the fixed
test input HUB_NOW, in seconds.
"""
import json
import os
import shutil
import statistics
import time

import pytest

from chats import SESSIONS, Chats
from conftest import PLUGIN
from steps import and_, given, then, when

T = 1_800_000_000  # the fixed clock
FULL = {"questions": [{"question": "Which retry limit?", "header": "Retries", "multiSelect": False,
                       "options": [{"label": "3", "description": "Three tries."},
                                   {"label": "5", "description": "Five tries."}]}]}
BARE = {"questions": [{"question": "Which retry limit?", "header": "Retries", "multiSelect": False, "options": []}]}

GATE = {"$schema": "../../../../schema/part.schema.json", "id": "ask_gate", "family": "questions", "kind": "hook",
        "default": "on", "runs_in": ["claude_code"], "handlers": {"PreToolUse": {"tools": ["AskUserQuestion"]}},
        "does": "Refuses a question dialog with no options."}
GATE_CODE = '''
def handle(event, hook_input, flags, answer):
    questions = hook_input.get("tool_input", {}).get("questions", [])
    if any(not question.get("options") for question in questions):
        answer.refuse("Give each question its options.")
'''


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def clock(home, seconds):
    home.env["HUB_NOW"] = str(seconds)


def board_on(home, source=PLUGIN):
    home.install(source)
    home.hubctl("on", "chat_board")


def board(home, *args):
    return json.loads(home.hubctl("board", "--json", *args).stdout)


def states(home):
    return [(card["name"], card["state"]) for card in board(home)["cards"]]


def state_of(home, name):
    return dict(states(home)).get(name)


def start_four(home, chats):
    chats_by_name = {name: chats.start(name, title=name, live=True) for name in ("LOBBY", "WEB", "DOCS", "API")}
    chats.set_listing([chats.row(name, pid=chat.pid) for name, chat in chats_by_name.items()])
    return chats_by_name


def test_four_chats_show_needs_you_busy_idle_and_offline_on_one_board(home, chats):
    with given("chat_board is on, and LOBBY, WEB, DOCS and API run with the recorded listing"):
        board_on(home)
        clock(home, T)
        running = start_four(home, chats)
    with when("WEB sends a prompt and a tool call, DOCS works and then stops, API opens a full question dialog, "
              "and LOBBY's process ends"):
        chats.send("WEB", "UserPromptSubmit", prompt="Fix the login redirect.")
        chats.tool("WEB", "Bash", {"command": "ls"})
        chats.send("DOCS", "UserPromptSubmit", prompt="Update the README.")
        chats.send("DOCS", "Stop", stop_hook_active=False)
        chats.tool("API", "AskUserQuestion", FULL)
        running["LOBBY"].end()
        clock(home, T + 180)
    with then("hubctl board --json lists API as needs you, WEB as busy, DOCS as idle and LOBBY as offline, "
              "in that order"):
        assert states(home) == [("API", "needs_you"), ("WEB", "busy"), ("DOCS", "idle"), ("LOBBY", "offline")]
    with and_("hubctl board prints four cards on one grid, and its header line reads !1 ●1 ○1 ◌1"):
        lines = home.hubctl("board").stdout.splitlines()
        assert lines[0].endswith("!1 ●1 ○1 ◌1")
        titles = [line for line in lines if line[:2] in ("! ", "● ", "○ ", "◌ ") and " · " not in line]
        assert [title.split()[1] for title in titles] == ["API", "WEB", "DOCS", "LOBBY"]
        assert len({title.index("◷") for title in titles}) == 1  # times share one column
    with and_("API's now row reads \"asks which retry limit?\", with ◷ 3m in its title row"):
        title = next(index for index, line in enumerate(lines) if line.startswith("! API"))
        assert lines[title].rstrip().endswith("◷ 3m")
        assert lines[title + 1].strip() == "asks which retry limit?"


def test_a_question_makes_the_card_need_the_user_and_the_next_event_clears_it(home, chats, tmp_path):
    source = os.path.join(str(tmp_path), "plugin-with-gate")
    with given("chat_board and a question gate are on, and WEB is busy"):
        shutil.copytree(PLUGIN, source, ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(source, "parts", "ask_gate"))
        with open(os.path.join(source, "parts", "ask_gate", "part.json"), "w") as handle:
            json.dump(GATE, handle)
        with open(os.path.join(source, "parts", "ask_gate", "handler.py"), "w") as handle:
            handle.write(GATE_CODE)
        board_on(home, source)
        clock(home, T)
        chats.start("WEB", title="WEB", live=True)
        chats.tool("WEB", "Bash", {"command": "ls"})
    with when("WEB opens a dialog that the gate refuses"):
        assert chats.tool("WEB", "AskUserQuestion", BARE).refused
    with then("WEB stays busy"):
        assert state_of(home, "WEB") == "busy"
    with when("WEB opens a full dialog"):
        chats.tool("WEB", "AskUserQuestion", FULL)
    with then("WEB needs the user, and the Board strip gets the line \"! WEB asks\""):
        data = board(home)
        assert state_of(home, "WEB") == "needs_you"
        assert [f"{line['mark']} {line['text']}" for line in data["strip"]][:1] == ["! WEB asks"]
        assert data["strip"][0]["why"] == "which retry limit?"
    with when("WEB's next tool call arrives"):
        chats.tool("WEB", "Read", {"file_path": "README.md"})
    with then("WEB is busy again, and the ! counter is hidden"):
        assert state_of(home, "WEB") == "busy"
        assert "!" not in home.hubctl("board").stdout.splitlines()[0]
    with when("a permission_prompt notification arrives for WEB, and then an idle_prompt notification"):
        chats.send("WEB", "Notification", notification_type="permission_prompt", message="Claude needs permission")
        first = state_of(home, "WEB")
        chats.send("WEB", "Notification", notification_type="idle_prompt", message="Claude is waiting")
    with then("WEB needs the user, and then WEB is idle"):
        assert (first, state_of(home, "WEB")) == ("needs_you", "idle")
    with and_("a PermissionRequest makes it need the user at once, and hub decides nothing"):
        result = chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"})
        assert state_of(home, "WEB") == "needs_you" and result.process.stdout.strip() == ""


def test_a_subagent_that_waits_for_a_yes_makes_its_chat_need_the_user_and_its_other_calls_change_nothing(home, chats):
    agent = {"agent_id": "agent-a1", "agent_type": "researcher"}
    with given("chat_board is on, WEB is idle, and a background agent of WEB works"):
        board_on(home)
        chats.start("WEB", title="WEB", live=True)
        chats.send("WEB", "Stop")
        chats.tool("WEB", "Bash", {"command": "ls"}, **agent)
    with then("WEB stays idle, because the user can still type there"):
        assert state_of(home, "WEB") == "idle"
    with when("the agent asks for the user's yes"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"}, **agent)
    with then("WEB needs the user"):
        assert state_of(home, "WEB") == "needs_you"
    with when("another agent of WEB calls a tool"):
        chats.tool("WEB", "Read", {"file_path": "README.md"}, agent_id="agent-b2", agent_type="worker")
    with then("WEB still needs the user, since that agent's yes is still open"):
        assert state_of(home, "WEB") == "needs_you"
    with when("the agent that asked goes on with its next tool call"):
        chats.tool("WEB", "Bash", {"command": "make"}, **agent)
    with then("WEB is idle again, as it was before the agent asked"):
        assert state_of(home, "WEB") == "idle"
    with when("the agent asks again, and then the user sends WEB a prompt"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "rm x"}, **agent)
        chats.send("WEB", "UserPromptSubmit", prompt="Go on.")
    with then("WEB still needs the user, because the chat's own prompt does not answer the agent's yes"):
        assert state_of(home, "WEB") == "needs_you"
    with when("the agent goes on"):
        chats.tool("WEB", "Bash", {"command": "rm x"}, **agent)
    with then("WEB is busy, as the user's prompt left it"):
        assert state_of(home, "WEB") == "busy"
    with when("two agents of WEB ask for a yes, one after the other"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"}, **agent)
        chats.send("WEB", "PermissionRequest", tool_name="Write", tool_input={"file_path": "x"},
                   agent_id="agent-b2", agent_type="worker")
    with and_("the first one goes on"):
        chats.tool("WEB", "Bash", {"command": "make"}, **agent)
    with then("WEB still needs the user for the second one's yes"):
        assert state_of(home, "WEB") == "needs_you"
        assert board(home)["cards"][0]["question"] == "for your yes to use Write"
    with when("the second one goes on too"):
        chats.tool("WEB", "Write", {"file_path": "x"}, agent_id="agent-b2", agent_type="worker")
    with then("WEB is busy again, as it was before the first one asked"):
        assert state_of(home, "WEB") == "busy"
    with when("the card file holds a broken list of waiting agents, and an agent calls a tool"):
        path = os.path.join(home.state, "board", "chats", SESSIONS["WEB"] + ".json")
        with open(path, encoding="utf-8") as handle:
            card = json.load(handle)
        card.update(state="needs_you", waiting={"agents": ["agent-a1"], "before": "gone"})
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(card, handle)
        result = chats.tool("WEB", "Bash", {"command": "ls"}, **agent)
    with then("the call runs, and WEB keeps the state its file says"):
        assert result.process.returncode == 0
        assert state_of(home, "WEB") == "needs_you"
    with when("WEB itself waits for a yes to use Edit, and then an agent asks and goes on"):
        chats.send("WEB", "UserPromptSubmit", prompt="Go on.")
        chats.send("WEB", "PermissionRequest", tool_name="Edit", tool_input={"file_path": "x"})
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"}, **agent)
        chats.tool("WEB", "Bash", {"command": "make"}, **agent)
    with then("WEB still needs the user for its own yes to use Edit"):
        assert state_of(home, "WEB") == "needs_you"
        assert board(home)["cards"][0]["question"] == "for your yes to use Edit"


def test_a_background_agents_open_yes_outlasts_the_chats_own_events_and_ends_with_that_agent(home, chats):
    agent = {"agent_id": "agent-a1", "agent_type": "researcher"}
    with given("chat_board is on, the user sent WEB a prompt, and a background agent of WEB works"):
        board_on(home)
        chats.start("WEB", title="WEB", live=True)
        chats.send("WEB", "UserPromptSubmit", prompt="Run the checks in the background.")
        chats.tool("WEB", "Bash", {"command": "ls"}, **agent)
    with when("the agent asks for the user's yes, and Claude Code then sends its permission_prompt notification "
              "with no agent id"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"}, **agent)
        chats.send("WEB", "Notification", notification_type="permission_prompt",
                   message="Claude needs your permission to use Bash")
    with then("WEB needs the user for the agent's yes"):
        assert state_of(home, "WEB") == "needs_you"
        assert board(home)["cards"][0]["question"] == "for your yes to use Bash"
    with when("WEB's own thread calls a tool and then ends its turn"):
        chats.tool("WEB", "Read", {"file_path": "README.md"})
        chats.send("WEB", "Stop", stop_hook_active=False)
    with then("WEB still needs the user, because the agent's yes is still open"):
        assert state_of(home, "WEB") == "needs_you"
    with when("the agent calls its next tool"):
        chats.tool("WEB", "Bash", {"command": "make"}, **agent)
    with then("WEB is idle, as its own thread left it, and does not stay at needs you"):
        assert state_of(home, "WEB") == "idle"
    with when("the agent asks again, WEB's own thread asks for a yes to use Edit, and that thread goes on"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"}, **agent)
        chats.send("WEB", "PermissionRequest", tool_name="Edit", tool_input={"file_path": "x"})
        assert board(home)["cards"][0]["question"] == "for your yes to use Edit"
        chats.tool("WEB", "Edit", {"file_path": "x"})
    with then("WEB needs the user for the agent's yes again"):
        assert state_of(home, "WEB") == "needs_you"
        assert board(home)["cards"][0]["question"] == "for your yes to use Bash"
    with when("the agent stops without another tool call"):
        chats.send("WEB", "SubagentStop", stop_hook_active=False, **agent)
    with then("WEB is busy, as its own thread left it"):
        assert state_of(home, "WEB") == "busy"
    with when("the agent asks once more, and WEB is resumed in a new process"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"}, **agent)
        chats.send("WEB", "SessionStart", source="resume")
    with then("WEB is idle, since the agent ended with the old process"):
        assert state_of(home, "WEB") == "idle"


def test_an_ended_chat_leaves_the_board_and_a_reused_pid_still_counts_as_offline(home, chats):
    with given("DOCS and LOBBY are idle on the board, and DOCS has a task"):
        board_on(home)
        clock(home, T)
        chats.start("DOCS", title="DOCS", live=True)
        chats.start("LOBBY", title="LOBBY", live=True)
        chats.send("DOCS", "Stop")
        chats.send("LOBBY", "Stop")
        home.hubctl("board", "task", "add", "README", "--session", SESSIONS["DOCS"])
    with when("DOCS sends SessionEnd"):
        chats.send("DOCS", "SessionEnd", reason="prompt_input_exit")
    with then("its card is gone, and its ledger is kept"):
        assert "DOCS" not in dict(states(home))
        assert os.path.exists(os.path.join(home.state, "board", "tasks", SESSIONS["DOCS"] + ".json"))
    with when("LOBBY's process ends, and a new process takes the same pid"):
        record = os.path.join(home.state, "sessions", SESSIONS["LOBBY"] + ".json")
        data = home.state_file(os.path.join("sessions", SESSIONS["LOBBY"] + ".json"))
        home.write_json(record, dict(data, pid=os.getpid()))  # a live pid that started at another time
    with then("LOBBY shows as offline, and the strip gets \"LOBBY went offline\" once"):
        assert state_of(home, "LOBBY") == "offline"
        strip = [line["text"] for line in board(home)["strip"]]
        assert strip.count("LOBBY went offline") == 1


def test_an_offline_card_leaves_the_board_when_its_session_ends_or_after_a_day(home, chats):
    with given("chat_board is on, and DOCS, LOBBY and API are idle on the board"):
        board_on(home)
        clock(home, T)
        running = {name: chats.start(name, title=name, live=True) for name in ("DOCS", "LOBBY", "API")}
        for name in running:
            chats.send(name, "Stop")
    with when("all three processes end without a SessionEnd"):
        for chat in running.values():
            chat.end()
    with then("all three show as offline"):
        assert sorted(states(home)) == [("API", "offline"), ("DOCS", "offline"), ("LOBBY", "offline")]
    with when("DOCS is resumed and quits, so its session ends"):
        chats.send("DOCS", "SessionEnd", reason="prompt_input_exit")
    with and_("LOBBY's session ends while chat_board is off, so only its hub record goes"):
        os.unlink(os.path.join(home.state, "sessions", SESSIONS["LOBBY"] + ".json"))
    with then("both cards leave the board, and API stays offline"):
        assert states(home) == [("API", "offline")]
    with when("23 hours pass"):
        clock(home, T + 23 * 3600)
    with then("API is still on the board as offline"):
        assert states(home) == [("API", "offline")]
    with when("a day has passed since API went offline"):
        clock(home, T + 24 * 3600)
    with then("API's card leaves the board, and its card file is gone"):
        assert states(home) == []
        assert not os.path.exists(os.path.join(home.state, "board", "chats", SESSIONS["API"] + ".json"))


def test_a_list_agents_result_the_chat_already_fetched_refreshes_the_board_at_no_cost(home, chats):
    with given("the cached listing is a minute old and shows DOCS as live, and DOCS has no hub record"):
        board_on(home)
        clock(home, T)
        lobby = chats.start("LOBBY", title="LOBBY", live=True)
        rows = [chats.row("LOBBY", pid=lobby.pid), chats.row("DOCS", pid=os.getpid(), status="busy")]
        home.write_json(os.path.join(home.state, "sessions", "listing.json"), {"time": time.time() - 60, "rows": rows})
        assert state_of(home, "DOCS") == "busy"
        calls_before = chats.listing_calls()
    with when("LOBBY's ListAgents result arrives in PostToolUse and shows DOCS as gone"):
        gone = [rows[0], chats.row("DOCS")]
        chats.send("LOBBY", "PostToolUse", "ListAgents", {}, tool_response={"agents": gone})
    with then("the board shows DOCS as offline"):
        assert state_of(home, "DOCS") == "offline"
    with and_("the recorded claude agents --json program was not called"):
        assert chats.listing_calls() == calls_before


def test_a_chat_on_another_machine_shows_its_state_and_no_tasks(home, chats):
    with given("the recorded listing has the chat MIX on the made-up machine studio"):
        board_on(home)
        clock(home, T)
        chats.set_listing([dict(chats.row("MIX", pid=os.getpid(), status="busy"), machine="studio")])
    with then("MIX's card names studio in its title row and shows the state the listing gives"):
        card = next(card for card in board(home)["cards"] if card["name"] == "MIX")
        assert card["machine"] == "studio" and card["state"] == "busy"
        lines = home.hubctl("board").stdout.splitlines()
        title = next(index for index, line in enumerate(lines) if line.startswith("● MIX"))
        assert "studio" in lines[title]
    with and_("its task row says that tasks show only for chats on this machine"):
        assert "tasks show only for chats on this machine" in lines[title + 2]


def test_with_chat_board_off_no_state_file_is_written_and_with_it_on_each_event_stays_in_the_budget(home, chats):
    events = [(name, kind) for name in ("LOBBY", "WEB", "DOCS", "API") for kind in ("prompt", "tool", "tool", "stop")]

    def send_all():
        spent = []
        for name, kind in events:
            started = time.monotonic()
            if kind == "prompt":
                chats.send(name, "UserPromptSubmit", prompt="Go on.")
            elif kind == "tool":
                chats.tool(name, "Bash", {"command": "ls"})
            else:
                chats.send(name, "Stop")
            spent.append(time.monotonic() - started)
        return spent

    with given("chat_board is off"):
        home.install()
        clock(home, T)
    with when("hook events of four chats arrive"):
        send_all()
    with then("~/.local/state/hubctl/board/ is empty"):
        folder = os.path.join(home.state, "board")
        assert not os.path.exists(folder) or not any(files for _, _, files in os.walk(folder))
    with when("the user turns chat_board on and the same events arrive"):
        home.hubctl("on", "chat_board")
        chats.send("WEB", "UserPromptSubmit", prompt="Start.")
        path = os.path.join(home.state, "board", "chats", SESSIONS["WEB"] + ".json")
        written = os.stat(path).st_mtime_ns
        chats.tool("WEB", "Bash", {"command": "ls"})
        chats.tool("WEB", "Read", {"file_path": "a.md"})
        unchanged = os.stat(path).st_mtime_ns == written
        spent = send_all()
    with then("a busy chat's state file was written once, and the median event ends inside 200 ms"):
        assert unchanged
        assert statistics.median(spent) < 0.2, spent


def test_the_board_text_fits_the_hub_command_and_points_only_to_hub_commands(home, chats):
    names = ("LOBBY", "WEB", "DOCS", "API", "MAIL", "SHOP", "BLOG")
    with given("chat_board is on, and seven chats are on the board"):
        board_on(home)
        for name in names:
            chats.start(name, title=name)
    with then("hub has no terminal view: no top command and no chat_board.top flag"):
        assert home.hubctl("top", check=False).returncode != 0
        assert home.hubctl("on", "chat_board.top", check=False).returncode != 0
    with when("the user types /hub board, which prints what hubctl board prints"):
        lines = home.hubctl("board").stdout.splitlines()
    with then("the body holds five cards in 15 lines, then a line with the count of the rest and the command for them"):
        first = next(index for index, line in enumerate(lines) if line.startswith("chats"))
        body = lines[first + 1:first + 16]
        assert sum(line[:1] not in ("", " ") for line in body) == 5
        assert lines[first + 16] == "2 more chats. /hub board --all shows every chat."
    with and_("the next line names only /hub commands, and no line names hubctl"):
        assert lines[-1] == "next: /hub board tasks · /hub board show <chat>"
        assert not any("hubctl" in line for line in lines)
    with when("the user asks for every chat"):
        every = home.hubctl("board", "--all").stdout
    with then("all seven cards show, and no line says more"):
        every_lines = every.splitlines()
        start = next(index for index, line in enumerate(every_lines) if line.startswith("chats"))
        titles = [line.split()[1] for line in every_lines[start + 1:start + 1 + 3 * len(names)]
                  if line[:1] not in ("", " ")]
        assert sorted(titles) == sorted(names)
        assert " more chats" not in every
    with when("the user asks for a chat that is not on the board"):
        missing = home.hubctl("board", "show", "NOPE", check=False)
    with then("the answer names /hub board, not a terminal command"):
        assert missing.returncode != 0
        assert "/hub board lists the chats" in missing.stderr and "hubctl" not in missing.stderr


def test_in_cursor_nothing_is_written_and_the_board_says_cursor_chats_are_not_visible(home, chats):
    with given("chat_board is on"):
        board_on(home)
    with when("Cursor sends its hook input for the same events"):
        chats.cursor("beforeSubmitPrompt", prompt="Go.")
        chats.cursor("stop")
    with then("nothing is written"):
        assert not os.path.exists(os.path.join(home.state, "board", "chats"))
    with and_("hubctl board lists the Claude Code chats with one line that Cursor chats are not visible"):
        text = home.hubctl("board").stdout
        assert text.count("Cursor chats are not visible") == 1
    with and_("hubctl doctor names chat_board and its reason"):
        doctor = home.hubctl("doctor", check=False).stdout
        assert "chat_board" in doctor and "Cursor" in doctor
