#!/usr/bin/env python3
"""Restart the running chats, each in its own terminal, and the calling chat last.

    restart.py plan [--only A,B] [--skip C] [--upgrade] [--caller S] [--leave-caller] [--json]
    restart.py run  [the same] [--wait MINUTES]
    restart.py ready [--jobs-may-stop] [--session S]
    restart.py hold <why> [--session S]

plan reads only. It lists every chat with what will happen to it and why,
sorted by name with the calling chat last. run always detaches, so it
outlives the chat that started it, prints where its log and report go, and
writes <state>/restart/<time>-<pid>/: restart.log, report.md, stopped.json
(the chats stopped and not yet back, with the command that resumes each),
<name>-screen.txt (a chat's screen before its stop) and, last,
result.json. ready and hold are a chat's answer to "may I restart you?".
They are kept in <state>/restart/answers/, count for 15 minutes, and are
used up when a run starts.

The safety rules: with restart.ask_first on, a chat is stopped only when it
answered ready. A chat is stopped only when it is idle with nothing to
lose: no open question or dialog, no text in its input box, no background
work it did not release, and the same process and tab the plan saw. /exit
is typed without Return first, and Return comes only when the box holds
exactly /exit; anything else is taken back. Each stopped chat comes back in
its own pane or tab, or in the background, under its own name and flags.
A chat that comes back under another name gets /rename, but only at an
idle prompt, since Return in a dialog would pick its default. Every other chat is held untouched, and the report names it with its
reason. With --upgrade, Claude Code is upgraded once, between the last stop
and the first resume. When any step failed, the calling chat is held, so it
stays up to deal with the failure.

The tmux server comes from HUB_TMUX_SERVER (default: the user's own).
Timings, in seconds: HUB_RESTART_POLL, HUB_RESTART_EXIT_WAIT,
HUB_RESTART_RESUME_WAIT, HUB_RESTART_STILL, HUB_RESTART_CALLER_WAIT,
HUB_RESTART_RENAME_WAIT.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import files, resolve, sessions  # noqa: E402
from hub.adapters import terminal  # noqa: E402

PART = "restart"
HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = "restart"
ANSWERS = os.path.join(FOLDER, "answers")


def timing(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


POLL = timing("HUB_RESTART_POLL", 2)
EXIT_WAIT = timing("HUB_RESTART_EXIT_WAIT", 60)
RESUME_WAIT = timing("HUB_RESTART_RESUME_WAIT", 90)
STILL = timing("HUB_RESTART_STILL", 30)  # typed text that stays this long is not being typed
CALLER_WAIT = timing("HUB_RESTART_CALLER_WAIT", 600)
RENAME_WAIT = timing("HUB_RESTART_RENAME_WAIT", 10)  # how long a chat that came back may take to reach its prompt
RECORD_WAIT = 10  # how long a chat that is back may take to write its hub record, which the queue reads
ANSWER_SECONDS = 15 * 60  # an answer counts this long, so one from a round that never ran can not stop a chat

TYPED = "text is typed in its input box"
DIALOG_OPEN = "a question dialog is open"
NOT_TYPABLE = "its terminal is not an iTerm tab or tmux pane, so it can not be restarted in place"
NO_RECORD = "hub has no record of its terminal, because it started before hub ran"
NOT_SHELL = "it was not started from a shell, so /exit would close its tab"
# Reasons that end a wait at once, because waiting longer can not make them go.
FINAL = ("its process changed", "its tab changed", NOT_SHELL)

VALUE_FLAGS = {"--model", "--effort", "--permission-mode", "--agent", "--add-dir", "--settings", "--mcp-config",
               "--autocompact"}
BOOL_FLAGS = {"--chrome", "--no-chrome", "--dangerously-skip-permissions",
              "--allow-dangerously-skip-permissions", "--brief"}
SHELLS = sessions.SHELLS | {"tcsh", "csh"}
RULE_START = "─"
PROMPT = "❯"
DIALOG = re.compile(r"Enter to (select|confirm)|Esc to (?!interrupt)\w+|↑/↓ to navigate")
DRAFT = re.compile(r"\bfeedback drafts?\b")
EXIT_DIALOG = "Background work is running"
JOBS = re.compile(r"\b\d+ (shells?|agents?|tasks?|monitors?|jobs?)\b|still running")
VERSION = re.compile(r"\d+(?:\.\d+)+")


def log(text):
    print(f"{time.strftime('%H:%M:%S')} {text}", flush=True)


def call(args, timeout=60, **options):
    """Run a program and return (exit code, output). A missing program or a timeout is exit 127 or 124."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              **options)
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    return done.returncode, done.stdout + done.stderr


# flags and answers

def flag_values(cwd=None):
    """{flag id: value or None} for the restart part and its children, None when a flag does not run."""
    section = resolve.current(cwd)["tools"]["claude_code"]["flags"]
    found = {}
    for flag_id in (PART, f"{PART}.ask_first", f"{PART}.upgrade", f"{PART}.terminal", f"{PART}.wait_minutes"):
        record = section.get(flag_id) or {}
        found[flag_id] = record.get("value") if record.get("runs") else None
    return found


def answer_path(session):
    session = sessions.SAFE_ID.sub("", str(session or ""))
    return files.state_path(os.path.join(ANSWERS, session + ".json")) if session else None


def answers():
    """{session: answer} of the answers given in this round. An older one belongs to a round that never ran."""
    folder = files.state_path(ANSWERS)
    found = {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        data = files.read_json(os.path.join(folder, name)) if name.endswith(".json") else None
        if isinstance(data, dict) and data.get("answer") in ("ready", "hold") and fresh(data.get("time")):
            found[name[:-5]] = data
    return found


def fresh(answered_at):
    try:
        age = time.time() - float(answered_at)
    except (TypeError, ValueError):
        return False
    return 0 <= age <= ANSWER_SECONDS


def clear_answers():
    folder = files.state_path(ANSWERS)
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        try:
            os.unlink(os.path.join(folder, name))
        except OSError:
            pass


def own_session():
    """The session of the chat this process runs in: the record whose pid is one of its parents."""
    by_pid = {data.get("pid"): data for data in sessions.records() if sessions.is_alive(data)}
    pid = os.getppid()
    for _ in range(12):
        if pid in by_pid:
            return by_pid[pid]["session"]
        info = sessions.process_info(pid)
        if not info or info["ppid"] <= 1:
            return None
        pid = info["ppid"]
    return None


# a chat's screen

def tail(screen, count=40):
    return [line.rstrip() for line in (screen or "").splitlines() if line.strip()][-count:]


def is_rule(line):
    return line.lstrip().startswith(RULE_START)


def box_start(lines):
    """The index of the rule above the input box, or None. A dialog takes the box's place while it is open."""
    for index in range(len(lines) - 1, 0, -1):
        if lines[index].startswith(PROMPT) and is_rule(lines[index - 1]):
            return index - 1
    return None


def input_text(screen):
    """The text in the chat's input box, which a stop would lose."""
    lines = tail(screen)
    start = box_start(lines)
    if start is None:
        return ""
    text = [lines[start + 1][len(PROMPT):].strip()]
    for line in lines[start + 2:]:
        if is_rule(line):
            break
        text.append(line.strip())
    return " ".join(part for part in text if part)


def screen_state(screen):
    """idle, busy, dialog, exit-dialog, draft, typed-text or jobs."""
    lines = tail(screen, 14)
    bottom = "\n".join(lines)
    start = box_start(lines)  # with the box on screen, dialog text above it is old
    live = "\n".join(lines[start:]) if start is not None else bottom
    footer = "\n".join(lines[-3:])
    if EXIT_DIALOG in live:
        return "exit-dialog"
    if DIALOG.search(live):
        return "dialog"
    if "esc to interrupt" in bottom:
        return "busy"
    if DRAFT.search(footer):
        return "draft"
    if input_text(screen):
        return "typed-text"
    if JOBS.search(footer):
        return "jobs"
    return "idle"


# the plan

def kept_flags(args):
    """The flags worth keeping from a chat's command line, such as --model opus --effort high."""
    words, keep, index = list(args or []), [], 1
    while index < len(words):
        word = words[index]
        if word in VALUE_FLAGS and index + 1 < len(words):
            keep += [word, words[index + 1]]
            index += 2
            continue
        if word in BOOL_FLAGS or ("=" in word and word.split("=", 1)[0] in VALUE_FLAGS):
            keep.append(word)
        index += 1
    return keep


def started_from_shell(pid):
    info = sessions.process_info(pid)
    parent = sessions.process_info(info["ppid"]) if info else None
    return bool(parent) and os.path.basename(parent["name"]).lstrip("-") in SHELLS


def kinds_for(choice):
    return {"tmux": ("tmux",), "iterm": ("iterm",), "plain": ()}.get(choice, ("tmux", "iterm"))


def names(text):
    return [name.strip() for name in (text or "").split(",") if name.strip()]


def selected(name, wanted):
    return any(sessions.same_name(name, other) for other in wanted)


def plan(options, values, rows=None):
    """Every chat with what a restart does to it and why. rows is the listing, read fresh when not given."""
    rows = (sessions.listing(max_age=0) if rows is None else rows) or []
    only, skip = names(options.only), names(options.skip)
    caller = options.caller or own_session()
    given = answers()
    kinds = kinds_for(values.get(f"{PART}.terminal"))
    server = os.environ.get("HUB_TMUX_SERVER") or None
    ask_first = values.get(f"{PART}.ask_first") == "on"
    plan_rows = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("sessionId"):
            continue
        session = row["sessionId"]
        kind = "background" if row.get("kind") == "background" else "interactive"
        data = sessions.record(session)
        if data and data.get("pid") != row.get("pid"):
            data = None  # a record of an earlier process of this session
        entry = {"name": row.get("name") or (data or {}).get("title") or session[:8], "session": session,
                 "pid": row.get("pid"), "kind": kind, "status": row.get("status"), "caller": session == caller,
                 "cwd": (data or {}).get("cwd") or row.get("cwd") or files.home(), "flags": [],
                 "terminal": "background", "location": None, "background_id": row.get("id") or session[:8]}
        if kind == "interactive":
            entry["flags"] = kept_flags((data or {}).get("args"))
            entry["tty"] = (data or {}).get("tty", "")
            entry["location"] = terminal.locate(data, server, kinds) if data else None
            entry["terminal"] = entry["location"]["kind"] if entry["location"] else "plain"
            entry["shell"] = started_from_shell(entry["pid"])
        answered = given.get(session)
        entry["jobs_may_stop"] = bool(answered and answered.get("jobs_may_stop"))
        if entry["caller"] and (options.leave_caller or (only and not selected(entry["name"], only))):
            entry["action"], entry["why"] = "leave", "the calling chat, not selected"
        elif selected(entry["name"], skip) or (only and not selected(entry["name"], only)):
            entry["action"], entry["why"] = "leave", "not selected"
        elif answered and answered["answer"] == "hold":
            entry["action"], entry["why"] = "leave", "it answered hold: " + (answered.get("why") or "no reason given")
        elif ask_first and not entry["caller"] and not answered and (given or options.verb == "run"):
            # A plan before any answer shows who will be asked; a run never stops a chat that did not answer.
            entry["action"], entry["why"] = "leave", "it did not answer ready"
        elif kind == "interactive" and data is None:
            entry["action"], entry["why"] = "leave", NO_RECORD
        elif kind == "interactive" and entry["location"] is None:
            entry["action"], entry["why"] = "leave", NOT_TYPABLE
        else:
            entry["action"] = "restart"
            entry["why"] = "it is asked first" if ask_first and not entry["caller"] and not answered else ""
        plan_rows.append(entry)
    plan_rows.sort(key=lambda entry: (entry["caller"], entry["name"].lower()))
    return plan_rows


def print_plan(rows, options):
    log(f"plan, {'with' if options.upgrade else 'no'} Claude Code upgrade")
    for row in rows:
        flags = f", flags {' '.join(row['flags'])}" if row["flags"] else ""
        last = ", the calling chat (last)" if row["caller"] else ""
        why = f" ({row['why']})" if row.get("why") else ""
        log(f"  {row['name']}: {row['action']}, {row['terminal']}, now {row['status']}{last}{flags}{why}")


# the run

def resume_command(row, prompt=None):
    """The shell line that brings a chat back in its own mode and under its own name."""
    if row["kind"] == "background":
        words = ["claude", "--bg", "--resume", row["session"]]
    else:
        words = ["claude", "--resume", row["session"], "-n", row["name"], *row["flags"]]
    line = " ".join(shlex.quote(word) for word in words + ([prompt] if prompt else []))
    return f"cd {shlex.quote(row['cwd'])} && {line}"


def text(name, **values):
    with open(os.path.join(HERE, "text", name), encoding="utf-8") as handle:
        return " ".join(handle.read().split()).format(**values)


def runs_claude(words):
    """Whether a command line is Claude Code: the program, or the script node or python runs, is named claude."""
    return any(os.path.basename(word) == "claude" for word in words[:2])


def other_chat_in_tab(row):
    """Another live chat on the same tty, or None: one hub recorded, or any Claude program ps shows there."""
    for data in sessions.records():
        if data.get("pid") != row["pid"] and terminal.same_tty(data.get("tty"), row.get("tty")) and \
                sessions.is_alive(data):
            return data
    for pid, words in terminal.processes_on(row.get("tty")):
        if pid != row["pid"] and runs_claude(words):
            return {"pid": pid, "title": f"pid {pid}", "session": ""}
    return None


def background_why(now, jobs_may_stop):
    """None when the listing row shows a background chat safe to stop, else the reason.

    A background row has a status (busy, waiting or idle) while its process
    lives, and a state: working, blocked, done, failed or stopped. working
    means it drives work of its own, such as a loop or a wait on CI, so it
    stops only when it said that work may stop. A row that lacks either
    field can not show the work is safe, so the chat is held.
    """
    status, state = now.get("status"), now.get("state")
    if status is None:
        return "the chat listing gives no status for it, so its work could not be checked"
    if status != "idle":
        return f"the chat listing shows it {status}"
    if state in ("done", "failed", "stopped"):
        return None
    if state == "working":
        return None if jobs_may_stop else "it is still working, and it did not say its work may stop"
    if state == "blocked":
        return "it waits for an answer from the user"
    if state is None:
        return "the chat listing gives no state for it, so its work could not be checked"
    return f"the chat listing shows the state {state}, which a restart does not know"


def runs(pid):
    """Whether pid still runs. A zombie has exited, though no parent has reaped it yet."""
    if not sessions.pid_runs(pid):
        return False
    try:
        with open(f"/proc/{pid}/stat") as handle:
            stat = handle.read()
        return stat[stat.rindex(")") + 2:].split()[0] != "Z"
    except (OSError, ValueError, IndexError):
        pass
    code, output = call(["ps", "-o", "stat=", "-p", str(pid)], timeout=5)
    return not (code == 0 and output.strip().startswith("Z"))


class Run:
    def __init__(self, options, folder):
        self.options = options
        self.folder = folder
        self.results = {}
        self.stopped = {}
        self.problems = []
        self.upgrade_line = ""
        self.server = os.environ.get("HUB_TMUX_SERVER") or None
        self.rows = []
        self.marked = set()  # the sessions this run marked as restarting, so the queue holds posts to them

    def path(self, name):
        return os.path.join(self.folder, name)

    def journal(self):
        files.write_json(self.path("stopped.json"),
                         {row["name"]: resume_command(row) for row in self.stopped.values()})

    def row_now(self, row):
        return sessions.row_for(sessions.listing(max_age=0), row["session"])

    def where_now(self, row):
        """The chat's location found again by its tty, so a closed or reused tab shows."""
        return terminal.locate({"tty": row.get("tty")}, self.server, (row["location"]["kind"],))

    def why_not_safe(self, row):
        """None when the chat may be stopped now, else the reason."""
        now = self.row_now(row)
        if not now:
            return "gone"
        if now.get("pid") != row["pid"]:
            return "its process changed since the plan"
        status = now.get("status")
        if status == "waiting":
            return DIALOG_OPEN
        if status == "busy":
            return "busy"
        if row["kind"] == "background":
            return background_why(now, row["jobs_may_stop"])
        if not row.get("shell"):
            return NOT_SHELL
        if self.where_now(row) != row["location"]:
            return "its tab changed since the plan, or it could not be found again"
        screen = terminal.screen(row["location"])
        if not (screen or "").strip():
            return "its screen could not be read"
        state = screen_state(screen)
        if state in ("dialog", "exit-dialog"):
            return "a dialog is open"
        if state == "busy":
            return "busy"
        if state == "typed-text":
            # Text that changes is being typed. Text that stays is left to the /exit check in stop_chat.
            typed, seen = input_text(screen), row.get("box_text")
            if not seen or seen[0] != typed:
                row["box_text"] = [typed, time.time()]
            elif time.time() - seen[1] >= STILL:
                return None
            return TYPED
        if state == "draft":
            return "it has an unsent feedback draft, and /exit would ask to send or discard it"
        if state == "jobs" and not row["jobs_may_stop"]:
            return "background work is running and the chat did not say it may stop"
        return None

    def wait_safe(self, row, end):
        last = None
        while True:
            why = self.why_not_safe(row)
            if why is None or why == "gone" or why.startswith(FINAL):
                return why
            if why != last:
                log(f"{row['name']}: waiting, {why}")
                last = why
            if time.time() >= end:
                return why
            time.sleep(POLL)

    def save(self, row):
        """Keep the chat's screen, or a background chat's recent output, before the stop."""
        if row["kind"] == "background":
            _code, saved = call(["claude", "logs", row["background_id"]])
        else:
            saved = terminal.screen(row["location"]) or ""
        row["screen_file"] = self.path(f"{row['name']}-screen.txt")
        files.write_text(row["screen_file"], saved)

    def box(self, row, wanted, seconds=3):
        """The input box text, read until it equals wanted or the seconds pass, since a terminal may draw late."""
        end = time.time() + seconds
        while True:
            time.sleep(min(0.5, POLL))
            found = input_text(terminal.screen(row["location"]))
            if found == wanted or time.time() >= end:
                return found

    def stop_chat(self, row):
        """None when the chat exited, else why it was held."""
        end = time.time() + self.options.wait * 60
        while True:
            why = self.wait_safe(row, end)
            if why == "gone":
                return "it exited before the restart"
            if why:
                return why
            self.save(row)
            why = self.why_not_safe(row)  # saving takes a moment, and the chat may have woken up
            if why is None:
                break
            if time.time() >= end:
                return why
        log(f"{row['name']}: stopping")
        self.stopped[row["session"]] = row
        self.journal()
        sessions.mark_restarting(row["session"], os.getpid())  # before the stop, since a post during /exit is lost
        self.marked.add(row["session"])
        if row["kind"] == "background":
            row["sent"] = True
            call(["claude", "stop", row["background_id"]])
        else:
            held = self.type_exit(row)
            if held:
                return held
        return self.wait_exit(row)

    def type_exit(self, row):
        """Type /exit without Return, and press Return only when the box holds exactly that."""
        location = row["location"]
        if not terminal.type_into(location, "/exit", enter=False):
            self.forget(row)
            return "its tab did not take the keys, so nothing was sent"
        box = self.box(row, "/exit")
        again = self.why_not_safe(row) if box == "/exit" else None
        if box == "/exit" and (not again or again == TYPED):
            row["sent"] = True
            terminal.press(location, "enter")
            return None
        self.forget(row)
        why = again or f"its input box shows {box!r}"
        if screen_state(terminal.screen(location)) in ("dialog", "exit-dialog") or not box:
            # Backspaces would go into a dialog, and an empty box shows nothing to take back.
            return f"it changed just before the stop ({why}); /exit may still be in its input box, check it by hand"
        typed = next((part for part in ("/exit", "/exi", "/ex", "/e", "/") if box.endswith(part)), "")
        for _ in typed:
            terminal.press(location, "backspace")
        left = self.box(row, box[:len(box) - len(typed)]) if typed else box
        if left and left == (row.get("box_text") or [None])[0]:
            return f"{TYPED}; /exit was typed after it to check for a grey suggestion, and taken back"
        if left:
            return f"it changed just before the stop ({why}); its input box still holds {left!r}, check it by hand"
        return f"it changed just before the stop ({why}); /exit was taken back"

    def forget(self, row):
        self.stopped.pop(row["session"], None)
        self.journal()
        self.unmark(row["session"])

    def unmark(self, session):
        sessions.clear_restarting(session)
        self.marked.discard(session)

    def wait_exit(self, row):
        end = time.time() + EXIT_WAIT
        unknown = None
        while runs(row["pid"]) and time.time() < end:
            if row["kind"] == "interactive":
                screen = terminal.screen(row["location"]) or ""
                state = screen_state(screen)
                if state == "dialog" and unknown is None:
                    unknown = " / ".join(tail(screen, 3)).strip()
                    log(f"{row['name']}: a dialog opened after /exit, nothing pressed: {unknown}")
                if state == "exit-dialog":
                    if row["jobs_may_stop"]:
                        terminal.press(row["location"], "enter")
                    else:
                        terminal.press(row["location"], "esc")
                        time.sleep(min(1, POLL))
                        if screen_state(terminal.screen(row["location"])) == "exit-dialog":
                            return ("background work showed up at exit, and the exit dialog did not close on Esc; "
                                    "it may still exit")
                        self.forget(row)
                        return "background work showed up at exit, so the exit was cancelled and the work kept running"
            time.sleep(min(1, POLL))
        if runs(row["pid"]):
            if unknown:
                return f"a dialog opened after /exit ({unknown}); nothing was pressed and it was left running"
            return f"it did not exit within {EXIT_WAIT:.0f} s of the stop, so it was left running"
        return None

    def prompt(self, row, note):
        if row["caller"]:
            return text("resume_caller.md", note=note, report=self.path("report.md"))
        return text("resume.md", time=time.strftime("%H:%M"), note=note, screen=row.get("screen_file", ""))

    def resume(self, row, note):
        """The chat's result. It never raises, so one chat's failure leaves the next chat's resume to run."""
        try:
            return self.resume_once(row, note)
        except Exception as error:  # a chat that is down must still reach the report
            log(f"{row['name']}: resume error {error!r}")
            return self.not_back(row, f"its resume failed ({type(error).__name__})")

    def resume_once(self, row, note):
        prompt = self.prompt(row, note)
        log(f"{row['name']}: resuming")
        if row["kind"] == "background":
            code, output = call(["claude", "--bg", "--resume", row["session"], prompt], cwd=row["cwd"])
            if code != 0:
                log(f"{row['name']}: the background resume exited {code}: {output.strip()[:200]}")
        else:
            time.sleep(min(2, POLL))
            other = other_chat_in_tab(row)
            if other:
                return self.not_back(row, f"nothing was typed because another chat runs in its tab ({other.get('title') or other['session'][:8]})")
            if not terminal.type_into(row["location"], resume_command(row, prompt)):
                return self.not_back(row, "nothing was typed because its tab is gone")
        end = time.time() + RESUME_WAIT
        while time.time() < end:
            now = self.row_now(row)
            if now and now.get("pid") != row["pid"]:
                # The queue posts to the address in the chat's new record, so the mark stays until that record is written.
                record_end = time.time() + RECORD_WAIT
                while (sessions.record(row["session"]) or {}).get("pid") != now["pid"] and time.time() < record_end:
                    time.sleep(POLL)
                self.forget(row)
                if row["kind"] == "interactive" and (now.get("name") or "") != row["name"]:
                    return self.rename(row)
                return "back"
            time.sleep(POLL)
        return self.not_back(row, f"it did not come back within {RESUME_WAIT:.0f} s")

    def rename(self, row):
        """Give a chat that came back under another name its own name again, typed only at an idle prompt."""
        end = time.time() + RENAME_WAIT
        while screen_state(terminal.screen(row["location"]) or "") != "idle" and time.time() < end:
            time.sleep(POLL)
        if screen_state(terminal.screen(row["location"]) or "") != "idle":
            return f"back, but not renamed: its screen is not at an idle prompt. Type /rename {row['name']} in it by hand"
        if not terminal.type_into(row["location"], f"/rename {row['name']}"):
            return f"back, but not renamed: its tab is gone. Type /rename {row['name']} in it by hand"
        log(f"{row['name']}: renamed")
        return "back, renamed"

    def not_back(self, row, why):
        self.unmark(row["session"])  # the queue may give up on what waits for it, and tell each sender
        row["resume_failed"] = True
        self.problems.append(row["name"])
        log(f"{row['name']}: not back, {why}")
        return f"NOT BACK, {why}. Resume it by hand: {resume_command(row)}"

    def upgrade(self):
        """Upgrade Claude Code only. Returns the note for the resume prompts."""
        program = os.path.realpath(shutil.which("claude") or "claude")
        cask = re.search(r"/Caskroom/([^/]+)/", program)
        formula = re.search(r"/Cellar/([^/]+)/", program)
        command = (["brew", "upgrade", "--cask", cask.group(1)] if cask else
                   ["brew", "upgrade", formula.group(1)] if formula else ["claude", "update"])
        before = self.version()
        log(f"upgrade: {' '.join(command)} (now {before or 'unreadable'})")
        code, output = call(command, timeout=900, env=dict(os.environ, HOMEBREW_NO_INSTALL_CLEANUP="1"))
        files.write_text(self.path("upgrade.log"), output)
        after = self.version()
        log(f"upgrade exit {code}, {before} -> {after}")
        if code != 0 or not after:
            self.upgrade_line = f"Claude Code upgrade FAILED (exit {code}, version now {after or 'unreadable'})"
            self.problems.append("upgrade")
            return ""
        if after == before:
            self.upgrade_line = f"Claude Code upgrade: no new version ({after})"
            return ""
        self.upgrade_line = f"Claude Code upgrade: {before} -> {after}"
        return f" on Claude Code {after}"

    @staticmethod
    def version():
        code, output = call(["claude", "--version"])
        found = VERSION.search(output) if code == 0 else None
        return found.group(0) if found else ""

    def resume_late(self, note):
        """A chat that exited after its exit wait gets one more wait, and is resumed once it is down."""
        late = [row for row in self.stopped.values() if not row["caller"] and not row.get("resume_failed")]
        end = time.time() + EXIT_WAIT
        while late and any(runs(row["pid"]) for row in late) and time.time() < end:
            time.sleep(min(1, POLL))
        for row in late:
            if runs(row["pid"]):
                self.problems.append(row["name"])  # it got /exit and still runs, so the caller stays up
            else:
                self.results[row["name"]] = "exited late, " + self.resume(row, note)

    def report(self, rows):
        lines = [f"# Restart {os.path.basename(self.folder)}", ""]
        if self.upgrade_line:
            lines.append(f"{self.upgrade_line} (log: {self.path('upgrade.log')})")
        for row in rows:
            lines.append(f"- {row['name']} ({row['kind']}): {self.results.get(row['name'], row.get('why') or 'left alone')}")
        if self.stopped:
            lines.append(f"\nThese chats were stopped and are not back yet ({self.path('stopped.json')}). "
                         "Once one has exited, resume it with its command:")
            lines += [f"- {row['name']}: {resume_command(row)}" for row in self.stopped.values()]
        files.write_text(self.path("report.md"), "\n".join(lines) + "\n")

    def go(self, rows):
        self.rows = rows
        todo = [row for row in rows if row["action"] == "restart"]
        for row in rows:
            if row["action"] != "restart":
                self.results[row["name"]] = row["why"]
        note = ""
        try:
            for row in [row for row in todo if not row["caller"]]:
                why = self.stop_chat(row)
                if why:
                    self.results[row["name"]] = f"HELD, not restarted: {why}"
                    log(f"{row['name']}: held, {why}")
                elif not self.options.upgrade:
                    self.results[row["name"]] = self.resume(row, note)
            if self.options.upgrade:
                note = self.upgrade()
        except Exception as error:  # every stopped chat must still come back
            log(f"error: {error!r}; resuming every stopped chat")
            self.problems.append(f"error {type(error).__name__}")
        finally:
            # Each chat on its own: resume() never raises, and a failed one is named NOT BACK in the report.
            for row in [row for row in self.stopped.values()
                        if not row["caller"] and not row.get("resume_failed") and not runs(row["pid"])]:
                self.results[row["name"]] = self.resume(row, note)
        self.resume_late(note)
        for row in [row for row in todo if row["caller"]]:
            self.stop_caller(row, note)
        self.report(rows)

    def stop_caller(self, row, note):
        if self.problems:
            self.results[row["name"]] = (f"HELD, not restarted: an earlier step failed ({', '.join(self.problems)}), "
                                         "so this chat stays up to deal with it")
            return
        self.results[row["name"]] = "restarted last (the calling chat)"
        self.report(self.rows)
        why = self.stop_chat(row)
        if why and row["session"] in self.stopped:
            end = time.time() + CALLER_WAIT  # it got /exit, and no one else is left to resume it
            while runs(row["pid"]) and time.time() < end:
                time.sleep(min(1, POLL))
            why = None if not runs(row["pid"]) else why
        if why:
            self.results[row["name"]] = f"HELD, not restarted: {why}"
        else:
            self.results[row["name"]] = "restarted last (the calling chat): " + self.resume(row, note)


def detach(log_file):
    """Fork twice into a new session, so the run outlives the chat that started it."""
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    descriptor = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(descriptor, 1)
    os.dup2(descriptor, 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)


def run(options, values, rows):
    folder = files.state_path(FOLDER)
    os.makedirs(folder, exist_ok=True)
    lock = os.path.join(folder, "lock")
    other = files.read_text(lock)
    if other and other.strip().isdigit() and int(other) != os.getpid() and runs(int(other)):
        print(f"Another restart is running (pid {other.strip()}), so nothing was done.", file=sys.stderr)
        return 1
    # The plan has read the answers. They are used up now, so a run that is cut off leaves none for the next one.
    clear_answers()
    run_folder = os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}")
    os.makedirs(run_folder)
    log_file = os.path.join(run_folder, "restart.log")
    print(f"The restart runs on its own now. Log: {log_file}  Report: {os.path.join(run_folder, 'report.md')}",
          flush=True)
    detach(log_file)
    files.write_text(lock, str(os.getpid()))
    if shutil.which("caffeinate"):  # keeps a Mac awake until this run ends
        subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    restart = Run(options, run_folder)
    try:
        print_plan(rows, options)
        restart.go(rows)
    except Exception as error:  # the report and result must still be written
        log(f"error: {error!r}")
        restart.report(rows)
    finally:
        for session in list(restart.marked):
            restart.unmark(session)
        clear_answers()
        try:
            os.unlink(lock)
        except OSError:
            pass
        log("done")
        # Written last, after the lock is gone, so a caller waiting for this file can start the next restart at once.
        files.write_json(restart.path("result.json"), {"results": restart.results})
    return 0


# what hubctl doctor says

def doctor_lines(values):
    """[(level, text)]: what a restart would do now, then how the newest run went."""
    rows, problem = sessions.run_listing()  # uncached and unlogged, so the doctor leaves the hooks' log alone
    if problem:
        return [("warning", f"the chat listing (claude agents --json) failed, {problem}, so a restart would "
                            f"find no chat to stop.")]
    options = argparse.Namespace(verb="plan", only=None, skip=None, caller=None, leave_caller=False)
    planned = plan(options, values, rows)
    back = [f"{row['name']} ({row['terminal']})" for row in planned if row["action"] == "restart"]
    left = [f"{row['name']}, because {row['why']}" for row in planned if row["action"] == "leave"]
    if not planned:
        found = [("note", "the chat listing works, and no chat runs now.")]
    else:
        line = "the chat listing works. A restart now could bring back " + (", ".join(back) or "no chat") + "."
        if back and values.get(f"{PART}.ask_first") == "on":
            line += " Each one is asked first."
        line += "".join(f" It leaves {why}." for why in left)
        found = [("note", line)]
    return found + last_run_lines(rows)


def last_run_lines(rows):
    folder = files.state_path(FOLDER)
    lock = (files.read_text(os.path.join(folder, "lock")) or "").strip()
    run_folders = [name for name in sorted(os.listdir(folder)) if name[:1].isdigit()] if os.path.isdir(folder) else []
    if not run_folders:
        return []
    newest = os.path.join(folder, run_folders[-1])
    if lock.isdigit() and runs(int(lock)):
        return [("note", f"a restart runs now (pid {lock}). Its report goes to {os.path.join(newest, 'report.md')}.")]
    live = {row.get("name") for row in rows if isinstance(row, dict) and runs(row.get("pid"))}
    stopped = files.read_json(os.path.join(newest, "stopped.json"))
    down = sorted(name for name in stopped if name not in live) if isinstance(stopped, dict) else []
    if down:
        return [("warning", f"the restart in {newest} stopped {', '.join(down)} and did not bring "
                            f"{'it' if len(down) == 1 else 'them'} back. The "
                            f"{'command that resumes it is' if len(down) == 1 else 'commands that resume them are'} "
                            f"in {os.path.join(newest, 'stopped.json')}.")]
    if not os.path.exists(os.path.join(newest, "result.json")):
        return [("note", f"the last restart was cut off before its end, but every chat it stopped runs again. "
                         f"Its log is {os.path.join(newest, 'restart.log')}.")]
    return [("note", f"the last restart ended. Its report is {os.path.join(newest, 'report.md')}.")]


def answer(options):
    session = options.session or own_session()
    path = answer_path(session)
    if path is None:
        print("hub can not tell which chat runs this. Add --session.", file=sys.stderr)
        return 1
    if options.verb == "ready":
        data = {"answer": "ready", "jobs_may_stop": options.jobs_may_stop, "time": time.time()}
    else:
        data = {"answer": "hold", "why": " ".join(options.why).strip(), "time": time.time()}
    files.write_json(path, data)
    print(f"Answered {options.verb}.")
    return 0


def parser():
    top = argparse.ArgumentParser(prog="restart.py", description=__doc__.splitlines()[0])
    verbs = top.add_subparsers(dest="verb", required=True)
    for verb in ("plan", "run"):
        each = verbs.add_parser(verb)
        each.add_argument("--only")
        each.add_argument("--skip")
        each.add_argument("--upgrade", action="store_true")
        each.add_argument("--caller")
        each.add_argument("--leave-caller", action="store_true")
        each.add_argument("--json", action="store_true")
        each.add_argument("--wait", type=float)
    ready = verbs.add_parser("ready")
    ready.add_argument("--jobs-may-stop", action="store_true")
    ready.add_argument("--session")
    hold = verbs.add_parser("hold")
    hold.add_argument("why", nargs="+")
    hold.add_argument("--session")
    return top


def main(argv):
    options = parser().parse_args(argv)
    if options.verb in ("ready", "hold"):
        return answer(options)
    values = flag_values(os.getcwd())
    if values[PART] != "on":
        print("The restart part is off. Turn it on with: hubctl on restart", file=sys.stderr)
        return 1
    if options.upgrade and values[f"{PART}.upgrade"] != "on":
        print("Upgrades are off for restarts. Allow them with: hubctl on restart.upgrade", file=sys.stderr)
        return 1
    if options.wait is None:
        options.wait = float(values.get(f"{PART}.wait_minutes") or 10)
    rows = plan(options, values)
    if options.verb == "plan":
        if options.json:
            print(json.dumps(rows, indent=1))
        else:
            print_plan(rows, options)
        return 0
    return run(options, values, rows)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
