"""The task list: each agent and background job a chat starts, from the hook payloads Claude Code sends.

Every payload here was captured from Claude Code 2.1.319 (fixtures under
hook_input/claude_code/tasks), then sent through the real dispatcher. The view
is the real `tasks` command reading the files the hooks wrote.
"""
import json
import os
import subprocess
import sys

import pytest

from conftest import load_fixture
from steps import and_, given, then, when
from test_dispatch_order import EveryToolGroup

PART = "task_list"
GLYPHS = "▶✓✗■?"
EMPTY = "No tasks yet. Agents, background jobs, questions, waits and timers show up here when they start."
NOON = 1794312000  # Tue 10 Nov 2026 12:00 UTC, so "today" never crosses midnight in a test


def transcript_path(home, session):
    return os.path.join(home.root, ".claude", "projects", "-home-sam-work-demo", f"{session}.jsonl")


def agent_transcript(home, session, agent_id):
    return os.path.join(home.root, ".claude", "projects", "-home-sam-work-demo", session, "subagents",
                        f"agent-{agent_id}.jsonl")


def payload(home, name, **changes):
    data = load_fixture("claude_code", "tasks", name)
    path = transcript_path(home, data["session_id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        open(path, "w").close()
    data.update(cwd=home.work, transcript_path=path)
    data.update(changes)
    return data


def notice(home, task_id, status, tool_use_id="toolu_lab0007"):
    """The captured agent notice, with its task id and status set. The words around them stay as captured."""
    data = payload(home, "UserPromptSubmit.notice.agent_completed.json")
    data["prompt"] = (data["prompt"].replace("a2a7db315f2641a17", task_id)
                      .replace("<status>completed</status>", f"<status>{status}</status>")
                      .replace("toolu_lab0007", tool_use_id))
    return data


def send(home, data):
    event = data["hook_event_name"]
    return home.send("claude_code", event, data)


def store(home):
    return os.path.join(home.state, PART)


def task_files(home):
    folder = os.path.join(store(home), "tasks")
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


def view(home, *args):
    return home.hubctl("tasks", *args).stdout


def listed(home):
    return json.loads(view(home, "--json"))["tasks"]


def by_key(home):
    return {task["source"]["key"]: task for task in listed(home)}


@pytest.fixture
def tasks_on(home):
    home.install()
    home.hubctl("on", PART)
    home.env["HUB_NOW"] = str(NOON)
    return home


@pytest.mark.parametrize("status, outcome, opened", [
    ("completed", "ok", "PostToolUse.Agent.background.json"),
    ("failed", "failed", "PostToolUse.Agent.background.json"),
    ("killed", "stopped", "PostToolUse.Agent.background.long.json"),
])
def test_a_background_agent_opens_a_running_row_and_its_notice_closes_it(tasks_on, status, outcome, opened):
    home = tasks_on
    with given("a chat starts a background agent"):
        launch = payload(home, opened)
        agent_id = launch["tool_response"]["agentId"]
        send(home, launch)
    with then("the list shows it as a running agent, with its description and model"):
        task = by_key(home)[agent_id]
        assert (task["kind"], task["state"], task["outcome"]) == ("agent", "running", None)
        assert task["title"] == launch["tool_input"]["description"]
        assert task["model"] == "claude-haiku-5-5" and task["source"]["call"] == launch["tool_use_id"]
    with when(f"its notice arrives with the status {status}"):
        send(home, notice(home, agent_id, status, launch["tool_use_id"]))
    with then(f"the agent is done with the outcome {outcome}"):
        task = by_key(home)[agent_id]
        assert (task["state"], task["outcome"]) == ("done", outcome)


def test_the_captured_notices_close_with_their_own_words(tasks_on):
    home = tasks_on
    with given("a background agent, a killed agent and a background shell that fails, as captured"):
        for name in ("PostToolUse.Agent.background.json", "PostToolUse.Agent.background.long.json"):
            send(home, payload(home, name))
        send(home, payload(home, "PostToolUse.Bash.background.json", tool_input={
            "command": "sleep 1; exit 3", "description": "Fail in the background", "run_in_background": True},
            tool_response={"backgroundTaskId": "b8tlezr7l"}, tool_use_id="toolu_lab0013"))
    with when("the three captured notices arrive"):
        for name in ("UserPromptSubmit.notice.agent_completed.json", "UserPromptSubmit.notice.agent_killed.json",
                     "UserPromptSubmit.notice.shell_failed.json"):
            send(home, payload(home, name))
    with then("they close as ok, stopped and failed"):
        found = by_key(home)
        assert [found[key]["outcome"] for key in ("a2a7db315f2641a17", "afaf1c40eb2498bbf", "b8tlezr7l")] == [
            "ok", "stopped", "failed"]


def test_a_notice_the_prompt_hook_missed_is_closed_by_the_transcript_read(tasks_on):
    home = tasks_on
    with given("a running background agent"):
        launch = payload(home, "PostToolUse.Agent.background.json")
        send(home, launch)
    with when("its notice reaches the transcript, but no prompt hook ran for it"):
        line = {"type": "attachment", "attachment": {"type": "queued_command",
                                                     "prompt": notice(home, "a2a7db315f2641a17", "completed")["prompt"]}}
        with open(launch["transcript_path"], "a") as handle:
            handle.write(json.dumps(line) + "\n")
    with then("the list reads the transcript and shows the agent done ok"):
        task = by_key(home)["a2a7db315f2641a17"]
        assert (task["state"], task["outcome"]) == ("done", "ok")


def test_a_subagent_start_with_no_agent_call_opens_nothing(tasks_on):
    home = tasks_on
    with when("Claude Code sends a SubagentStart for an agent no Agent call started"):
        send(home, payload(home, "SubagentStart.json"))
    with then("no task is written"):
        assert task_files(home) == [] and listed(home) == []


def test_background_shells_and_monitors_open_job_rows_and_a_foreground_shell_writes_nothing(tasks_on):
    home = tasks_on
    with when("a foreground shell runs"):
        send(home, payload(home, "PostToolUse.Bash.foreground.json"))
    with then("the task list writes no file at all"):
        assert not os.path.exists(store(home))
    with when("a background shell and a Monitor start"):
        send(home, payload(home, "PostToolUse.Bash.background.json"))
        send(home, payload(home, "PostToolUse.Monitor.json"))
    with then("each opens a running job with its description"):
        found = by_key(home)
        assert {key: (task["kind"], task["state"], task["title"]) for key, task in found.items()} == {
            "bda1d6dpj": ("job", "running", "Sleep in the background"),
            "bmxlmw3bp": ("job", "running", "Watch two ticks")}
    with when("the Monitor sends an event notice, which has no status"):
        send(home, payload(home, "UserPromptSubmit.notice.monitor_event.json"))
    with then("the monitor keeps running"):
        assert by_key(home)["bmxlmw3bp"]["state"] == "running"


def test_task_stop_closes_a_shell_job_as_stopped(tasks_on):
    home = tasks_on
    with given("a long background shell, as captured"):
        send(home, payload(home, "PostToolUse.Bash.long.json"))
        assert by_key(home)["b1x3rooyv"]["state"] == "running"
    with when("the chat stops it with TaskStop, which sends no notice for a shell"):
        send(home, payload(home, "PostToolUse.TaskStop.shell.json"))
    with then("the job is done and stopped"):
        task = by_key(home)["b1x3rooyv"]
        assert (task["state"], task["outcome"]) == ("done", "stopped")


def test_a_foreground_agent_is_done_at_once_and_a_failed_one_is_done_failed(tasks_on):
    home = tasks_on
    with when("a foreground agent returns, and another fails with an API error"):
        send(home, payload(home, "PostToolUse.Agent.foreground.json"))
        send(home, payload(home, "PostToolUseFailure.Agent.json"))
    with then("the first is a done ok agent and the second a done failed agent"):
        tasks = {task["title"]: task for task in listed(home)}
        assert (tasks["Foreground helper"]["state"], tasks["Foreground helper"]["outcome"]) == ("done", "ok")
        assert (tasks["Failing foreground"]["state"], tasks["Failing foreground"]["outcome"]) == ("done", "failed")
        assert "API error" in tasks["Failing foreground"]["detail"]


def test_a_task_the_stop_list_no_longer_holds_closes_as_gone_until_its_notice_comes(tasks_on):
    home = tasks_on
    with given("a running background agent and background shell"):
        send(home, payload(home, "PostToolUse.Agent.background.json"))
        send(home, payload(home, "PostToolUse.Bash.background.json"))
    with when("a Stop arrives with no background_tasks field, as older versions send"):
        stop = payload(home, "Stop.background_tasks.json")
        old = dict(stop)
        del old["background_tasks"]
        send(home, old)
    with then("nothing closes"):
        assert {task["state"] for task in listed(home)} == {"running"}
    with when("a Stop lists only the shell"):
        stop["background_tasks"] = [task for task in stop["background_tasks"] if task["id"] == "bda1d6dpj"]
        send(home, stop)
    with then("the agent closes as gone and the shell keeps running"):
        found = by_key(home)
        assert (found["a2a7db315f2641a17"]["state"], found["a2a7db315f2641a17"]["outcome"]) == ("done", "gone")
        assert found["bda1d6dpj"]["state"] == "running"
    with when("the agent's notice arrives late"):
        send(home, payload(home, "UserPromptSubmit.notice.agent_completed.json"))
    with then("the notice sets the outcome"):
        assert by_key(home)["a2a7db315f2641a17"]["outcome"] == "ok"


def test_the_main_chats_stop_leaves_tasks_a_subagent_started(tasks_on):
    home = tasks_on
    with given("a subagent starts a background shell"):
        launch = payload(home, "PostToolUse.Bash.background.json", agent_id="a9191d1faf6f0d014",
                         agent_type="general-purpose")
        send(home, launch)
    with when("the main chat stops with an empty background list"):
        send(home, payload(home, "Stop.background_tasks.json", background_tasks=[]))
    with then("the subagent's shell still runs"):
        assert by_key(home)["bda1d6dpj"]["state"] == "running"


def test_session_end_closes_only_that_chats_running_tasks_as_gone(tasks_on):
    home = tasks_on
    with given("two chats, each with a running background shell"):
        send(home, payload(home, "PostToolUse.Bash.background.json"))
        send(home, payload(home, "PostToolUse.Bash.long.json"))
    with when("the first chat ends"):
        end = payload(home, "Stop.background_tasks.json", hook_event_name="SessionEnd", reason="other")
        for field in ("background_tasks", "session_crons", "stop_hook_active", "last_assistant_message"):
            end.pop(field)
        send(home, end)
    with then("its shell is gone and the other chat's shell still runs"):
        found = by_key(home)
        assert (found["bda1d6dpj"]["state"], found["bda1d6dpj"]["outcome"]) == ("done", "gone")
        assert found["b1x3rooyv"]["state"] == "running"


def usage_line(request, message, model, usage, tool=None):
    content = [{"type": "tool_use", "id": f"toolu_{message}", "name": tool[0], "input": tool[1]}] if tool else [
        {"type": "text", "text": "thinking"}]
    return json.dumps({"type": "assistant", "requestId": request, "timestamp": "2026-11-10T11:00:00.000Z",
                       "message": {"id": message, "model": model, "role": "assistant", "usage": usage,
                                   "content": content}}) + "\n"


OPUS_USAGE = {"input_tokens": 2000, "output_tokens": 40000, "cache_read_input_tokens": 900000,
              "cache_creation_input_tokens": 60000,
              "cache_creation": {"ephemeral_5m_input_tokens": 20000, "ephemeral_1h_input_tokens": 40000}}
SONNET_USAGE = {"input_tokens": 1000, "output_tokens": 20000, "cache_read_input_tokens": 500000,
                "cache_creation_input_tokens": 10000}
HAIKU_USAGE = {"input_tokens": 0, "output_tokens": 10000}
# $ per million tokens: opus 5.5 input 4, output 20, cache read 0.20; sonnet 5.5 2, 10, 0.20; haiku 5.5 output 0.50.
# A cache write costs input x1.25 for 5 minutes and x2 for an hour, and a write with no split counts as an hour.
OPUS_COST = (2000 * 4 + 40000 * 20 + 900000 * 0.20 + 20000 * 4 * 1.25 + 40000 * 4 * 2) / 1e6  # 1.408
SONNET_COST = (1000 * 2 + 20000 * 10 + 500000 * 0.20 + 10000 * 2 * 2) / 1e6  # 0.342
HAIKU_COST = 10000 * 0.50 / 1e6  # 0.005


def test_an_agents_cost_is_the_price_table_sum_of_its_own_transcript_counting_each_reply_once(tasks_on):
    home = tasks_on
    with given("a running background agent whose transcript holds an Opus reply twice and a Sonnet reply"):
        launch = payload(home, "PostToolUse.Agent.background.json")
        send(home, launch)
        path = agent_transcript(home, launch["session_id"], "a2a7db315f2641a17")
        os.makedirs(os.path.dirname(path))
        bash = ("Bash", {"command": "pytest -q", "description": "Run the tests"})
        with open(path, "w") as handle:
            handle.write(usage_line("req_1", "msg_1", "claude-opus-5-5", OPUS_USAGE))
            handle.write(usage_line("req_1", "msg_1", "claude-opus-5-5", OPUS_USAGE, tool=bash))
            handle.write(usage_line("req_2", "msg_2", "claude-sonnet-5-5", SONNET_USAGE))
    with then("its cost is the sum within 1 percent, and its now line is its last tool call"):
        task = by_key(home)["a2a7db315f2641a17"]
        assert abs(task["cost"]["usd"] - (OPUS_COST + SONNET_COST)) <= 0.01 * (OPUS_COST + SONNET_COST)
        assert task["now"] == "Run the tests"
    with when("one more reply lands, and the list reads again from where it stopped"):
        with open(path, "a") as handle:
            handle.write(usage_line("req_3", "msg_3", "claude-haiku-5-5", HAIKU_USAGE))
        task = by_key(home)["a2a7db315f2641a17"]
    with then("only the new reply is added"):
        total = OPUS_COST + SONNET_COST + HAIKU_COST
        assert abs(task["cost"]["usd"] - total) <= 0.01 * total
        assert task["cost"]["read_to"] == os.path.getsize(path)


def test_twenty_hook_runs_at_once_keep_twenty_tasks(tasks_on):
    home = tasks_on
    with given("twenty background agent results"):
        script = os.path.join(home.plugin, "lib", "hub", "dispatch.py")
        inputs = []
        for number in range(20):
            data = payload(home, "PostToolUse.Agent.background.json", tool_use_id=f"toolu_many{number:04d}")
            data["tool_response"] = dict(data["tool_response"], agentId=f"a{number:016x}")
            inputs.append(json.dumps(data))
    with when("all twenty hooks run at the same time"):
        processes = [subprocess.Popen([sys.executable, "-I", script, "PostToolUse"], cwd=home.work, env=home.env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in inputs]
        for process, text in zip(processes, inputs):
            process.stdin.write(text)
            process.stdin.close()
        codes = [process.wait(timeout=60) for process in processes]
    with then("every hook ended well and the list holds twenty running agents"):
        assert codes == [0] * 20 and home.errors() == ""
        tasks = listed(home)
        assert len(tasks) == 20 and {task["state"] for task in tasks} == {"running"}


def open_three(home):
    launch = payload(home, "PostToolUse.Agent.background.json", session_title="DOCS",
                     tool_input={"description": "Plan the hub task manager with every slice and its tests",
                                 "prompt": "SUB-BG", "subagent_type": "planner", "run_in_background": True})
    send(home, launch)
    path = agent_transcript(home, launch["session_id"], "a2a7db315f2641a17")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        handle.write(usage_line("req_1", "msg_1", "claude-opus-5-5", OPUS_USAGE))
    send(home, payload(home, "PostToolUse.Bash.background.json", session_title="WEB"))
    send(home, payload(home, "PostToolUse.Agent.foreground.json", session_title="DOCS"))


def cells(text):
    return len(text)  # the rows use no wide characters


def test_a_huge_width_gives_a_bounded_list(tasks_on):
    home = tasks_on
    with given("a running agent and a shell job"):
        open_three(home)
    with when("the list is drawn with a width of one hundred million columns"):
        text = view(home, "--width", "100000000")
    with then("no line is longer than the 300 column limit"):
        assert text.strip()
        assert all(cells(line) <= 300 for line in text.splitlines())


def test_the_rows_fit_at_60_and_100_columns(tasks_on):
    home = tasks_on
    with given("a long agent title, a shell job and a done agent"):
        open_three(home)
    for width in (60, 100):
        with when(f"the list is drawn {width} columns wide"):
            text = view(home, "--width", str(width))
        with then("every line fits, and each running row keeps its owner and the start of its title"):
            assert all(cells(line) <= width for line in text.splitlines()), text
            assert "DOCS" in text and "Plan the hub" in text and "WEB" in text
            assert "RUNNING  2" in text and "DONE TODAY  1" in text
    with and_("at 100 columns the agent row also shows its model, and at 60 the cost column goes first"):
        wide, narrow = view(home, "--width", "100"), view(home, "--width", "60")
        assert "haiku" in wide
        assert "~$" not in narrow


def test_plain_text_has_no_glyphs_and_show_done_brief_and_empty_state(tasks_on):
    home = tasks_on
    with then("an empty list says what will show there"):
        assert view(home).strip() == EMPTY
        assert view(home, "--brief").strip() == "No tasks yet."
    with given("a long agent title, a shell job and a done agent"):
        open_three(home)
    with then("the plain view has the same rows with no glyph and no rule"):
        plain = view(home, "--plain")
        assert "Plan the hub" in plain and not any(glyph in plain for glyph in GLYPHS + "─")
        assert any(glyph in view(home) for glyph in GLYPHS)
    with and_("show opens one record by its name"):
        record = view(home, "show", "Sleep in the background")
        assert record.splitlines()[0].startswith("Sleep in the background")
        assert "Owner     WEB" in record and "Source    background shell bda1d6dpj" in record
    with and_("done lists today's done tasks with their outcome"):
        done = view(home, "done")
        assert "Foreground helper" in done and "ok" in done and "Sleep in the background" not in done
    with and_("the brief gives the running tasks and the counts in two lines"):
        brief = view(home, "--brief").splitlines()
        assert len(brief) == 2
        assert brief[0].startswith("Running 2: ") and "WEB" in brief[0]
        assert brief[1].startswith("Running 2 · done today 1")
    with and_("show names a missing task plainly"):
        missing = home.hubctl("tasks", "show", "nothing like it", check=False)
        assert missing.returncode == 1 and "No task is called" in missing.stdout + missing.stderr


def test_done_tasks_go_after_seven_days(tasks_on):
    home = tasks_on
    with given("a foreground agent that was done at noon"):
        send(home, payload(home, "PostToolUse.Agent.foreground.json"))
        assert len(listed(home)) == 1
    with when("the list is read eight days later"):
        home.env["HUB_NOW"] = str(NOON + 8 * 86400)
        tasks = listed(home)
    with then("the task and its file are gone"):
        assert tasks == [] and task_files(home) == []


def test_the_owner_is_the_chat_title_from_the_hook_input(tasks_on):
    home = tasks_on
    with when("a chat titled API starts a background shell"):
        send(home, payload(home, "PostToolUse.Bash.background.json", session_title="API"))
    with then("the job's owner is API, with its session id"):
        owner = by_key(home)["bda1d6dpj"]["owner"]
        assert owner["name"] == "API" and owner["session"] == "00000000-0000-4000-8000-0000000000b1"


def test_the_command_refuses_while_the_part_is_off(home):
    with given("hub as it ships, with the task list off"):
        home.install()
    with then("tasks says how to switch it on"):
        result = home.hubctl("tasks", check=False)
        assert result.returncode != 0 and "turn on task_list" in result.stdout + result.stderr


def test_the_tasks_command_reads_only_hubs_own_task_list(tasks_on, tmp_path):
    home = tasks_on
    with given("a running shell in hub's task list, and another folder that holds a task list of its own"):
        send(home, payload(home, "PostToolUse.Bash.background.json"))
        other = tmp_path / "other"
        (other / "tasks").mkdir(parents=True)
        (other / "sessions").mkdir()
        (other / "sessions" / "00000000-0000-4000-8000-0000000000b1.json").write_text("{}")
        before = sorted(str(path.relative_to(other)) for path in other.rglob("*"))
    with when("tasks is run with --store naming that folder"):
        listed_rows = json.loads(view(home, "--json", "--store", str(other)))["tasks"]
    with then("it lists hub's own task list"):
        assert [task["source"]["key"] for task in listed_rows] == ["bda1d6dpj"]
    with and_("the other folder is left as it was"):
        assert sorted(str(path.relative_to(other)) for path in other.rglob("*")) == before


def test_a_shell_result_starts_python_only_while_the_task_list_or_a_part_for_that_tool_runs(home):
    with given("hub as it ships, and the PostToolUse group for named tools"):
        home.install()
        group = EveryToolGroup(home, "PostToolUse", 0)
        shell = payload(home, "PostToolUse.Bash.background.json")
        read = load_fixture("claude_code", "PostToolUse.Read.json")
        read.update(cwd=home.work, tool_input={"file_path": os.path.join(home.work, "notes.md")})
    with when("a background shell and a file read end with the task list off"):
        group.send(shell)
        group.send(read)
    with then("Python never starts"):
        assert group.python_starts() == 0 and group.fast_path()
    with when("the task list is on, and the same two calls end again"):
        home.hubctl("on", PART)
        group.send(dict(shell, tool_use_id="toolu_again1"))
        group.send(dict(read, tool_use_id="toolu_again2"))
    with then("only the shell result starts the dispatcher, and it opens the job"):
        assert group.python_starts() == 1
        assert by_key(home)["bda1d6dpj"]["state"] == "running"
    with when("path_rules, which reads file reads, is on as well"):
        home.hubctl("on", "path_rules")
        group.send(dict(read, tool_use_id="toolu_again3"))
    with then("the file read starts it too"):
        assert group.python_starts() == 2
