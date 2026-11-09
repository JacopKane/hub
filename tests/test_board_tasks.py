"""Slice 13: a chat's tasks on the chat board.

A chat keeps its task line with `hubctl board task`, which finds the chat by
walking up its own parent processes to the chat's process. The ledger holds
queued, working and done tasks. Chats are real stand-in processes that run
the commands as their own children, and the clock is HUB_NOW.
"""
import json
import os

import pytest

from chats import Chats
from steps import and_, given, then, when

T = 1_800_000_000


@pytest.fixture
def chats(home):
    chats = Chats(home)
    yield chats
    chats.close()


def hubctl_args(home, *args):
    return ["env", f"HUB_NOW={home.env['HUB_NOW']}", "python3", os.path.join(home.plugin, "bin", "hubctl"), *args]


def in_chat(home, chat, *args):
    code, out, err = chat.run(hubctl_args(home, *args))
    assert code == 0, out + err
    return out


def test_a_chats_tasks_come_from_the_ledger_and_land_in_the_right_columns(home, chats):
    with given("chat_board is on, and WEB and DOCS run"):
        home.install()
        home.hubctl("on", "chat_board")
        home.env["HUB_NOW"] = str(T)
        web = chats.start("WEB", title="WEB", live=True, commands=True)
        docs = chats.start("DOCS", title="DOCS", live=True, commands=True)
        chats.send("WEB", "UserPromptSubmit", prompt="Fix it.")
        chats.send("DOCS", "UserPromptSubmit", prompt="Write it.")
    with when("WEB runs hubctl board task add \"PR\" and hubctl board task now \"fix\", "
              "and DOCS runs hubctl board task done \"README\""):
        in_chat(home, web, "board", "task", "add", "PR")
        in_chat(home, web, "board", "task", "now", "fix")
        in_chat(home, docs, "board", "task", "done", "README")
    with then("WEB's task row reads \"● fix  ○ PR\""):
        lines = home.hubctl("board").stdout.splitlines()
        title = next(index for index, line in enumerate(lines) if line.startswith("● WEB"))
        assert lines[title + 2].strip() == "● fix  ○ PR"
    with and_("hubctl board tasks shows fix under working, PR under queued and README under done"):
        columns = json.loads(home.hubctl("board", "tasks", "--json").stdout)["columns"]
        assert {column["state"]: [(task["text"], task["chat"]) for task in column["tasks"]] for column in columns} == {
            "needs_you": [], "working": [("fix", "WEB")], "queued": [("PR", "WEB")], "done": [("README", "DOCS")]}
    with and_("each command found its chat through its parent process"):
        listed = in_chat(home, web, "board", "task", "list", "--json")
        assert [task["text"] for task in json.loads(listed)["tasks"]] == ["fix", "PR"]
    with when("WEB needs the user"):
        chats.send("WEB", "PermissionRequest", tool_name="Bash", tool_input={"command": "make"})
    with then("fix moves to the needs-you column"):
        columns = json.loads(home.hubctl("board", "tasks", "--json").stdout)["columns"]
        assert [task["text"] for task in columns[0]["tasks"]] == ["fix"] and columns[1]["tasks"] == []
    with and_("back to working when WEB is busy again"):
        chats.tool("WEB", "Bash", {"command": "make"})
        columns = json.loads(home.hubctl("board", "tasks", "--json").stdout)["columns"]
        assert [task["text"] for task in columns[1]["tasks"]] == ["fix"]
    with when("the fixed clock moves 25 hours on"):
        home.env["HUB_NOW"] = str(T + 25 * 3600)
    with then("README leaves the done column and stays in hubctl board show DOCS"):
        columns = json.loads(home.hubctl("board", "tasks", "--json").stdout)["columns"]
        assert columns[3]["tasks"] == []
        assert "README" in home.hubctl("board", "show", "DOCS").stdout
    with and_("a task command outside any chat says how to name the chat"):
        process = home.hubctl("board", "task", "add", "x", check=False)
        assert process.returncode != 0 and "--session" in process.stderr
    with when("the user turns off chat_board.tasks, and a chat still tries to add a task"):
        home.hubctl("off", "chat_board.tasks")
        process = home.hubctl("board", "task", "add", "x", "--session", "00000000-0000-4000-8000-0000000000a2",
                              check=False)
    with then("nothing is written, and the chat is told the user can turn it on with /hub"):
        assert process.returncode != 0
        assert "/hub on chat_board.tasks" in process.stderr and "hubctl" not in process.stderr
        assert "x" not in home.hubctl("board", "show", "WEB").stdout.split()
