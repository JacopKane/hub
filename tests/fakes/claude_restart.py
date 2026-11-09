#!/usr/bin/env python3
"""A stand-in for `claude` in the restart scenarios. It never reaches a real chat.

Everything it knows lives in $HUB_FAKE_CHATS:

    rows/<session>.json     one row of `claude agents --json` per running chat
    status/<name>           idle, busy or waiting; read on every redraw. A background
                            chat also takes "missing", which leaves status out of its row
    status/<name>.state     a background chat's state: working, blocked, done (the
                            default), failed or stopped
    status/<name>.typed     text that sits in the chat's input box at its start
    status/<name>.jobs      the chat's footer says a shell still runs
    status/<name>.drops-name  a resume of that chat ignores -n, so it comes back named
                            after the last 8 characters of its session until /rename
    events.jsonl            start, resume, stop, sent, upgrade, in the order they happened
    version                 what `claude --version` prints
    update-fails            when present, `claude update` exits 1
    update-hangs            when present, `claude update` waits until it is gone
    session_start.json      the SessionStart hook input the test copied from the fixtures

An interactive chat (`claude --session-id S -n NAME ...` or `claude --resume S
-n NAME ...`) runs in its own terminal, the way Claude Code does: it runs
its SessionStart hook as a child through sh, draws an input box, reads keys
in raw mode, and exits when the box reads exactly /exit and Return comes.
`/rename NAME` and Return renames it.
`claude --bg ...` starts a background chat with a short id (the last 8
characters of its session, so test sessions differ), which
`claude stop <id>` ends. `claude agents --json` lists the rows whose
process still runs.
"""
import json
import os
import select
import shlex
import signal
import subprocess
import sys
import time

FOLDER = os.environ["HUB_FAKE_CHATS"]
ROWS = os.path.join(FOLDER, "rows")
VALUE_FLAGS = {"--model", "--effort", "--permission-mode", "--autocompact", "-n", "--name", "--resume",
               "--session-id"}
RULE = "─" * 30
PROMPT = "❯"


def event(kind, **values):
    with open(os.path.join(FOLDER, "events.jsonl"), "a") as handle:
        handle.write(json.dumps({"event": kind, "time": time.time(), **values}) + "\n")


def runs(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def rows():
    found = []
    for name in sorted(os.listdir(ROWS)) if os.path.isdir(ROWS) else []:
        if not name.endswith(".json"):  # write_row's <session>.json.tmp would list the chat twice
            continue
        try:
            with open(os.path.join(ROWS, name)) as handle:
                row = json.load(handle)
        except (OSError, ValueError):
            continue
        if runs(row.get("pid", 0)):
            found.append(row)
    return found


def write_row(row):
    os.makedirs(ROWS, exist_ok=True)
    path = os.path.join(ROWS, row["sessionId"] + ".json")
    with open(path + ".tmp", "w") as handle:
        json.dump(row, handle)
    os.replace(path + ".tmp", path)


def drop_row(session):
    try:
        os.unlink(os.path.join(ROWS, session + ".json"))
    except FileNotFoundError:
        pass


def read(name, default=""):
    try:
        with open(os.path.join(FOLDER, "status", name)) as handle:
            return handle.read().strip()
    except OSError:
        return default


def parse(words):
    options, flags, prompt = {}, [], None
    index = 0
    while index < len(words):
        word = words[index]
        if word in VALUE_FLAGS and index + 1 < len(words):
            options[word] = words[index + 1]
            index += 2
            continue
        if word.startswith("-"):
            flags.append(word)
        else:
            prompt = word
        index += 1
    return options, flags, prompt


def session_start(session, name, source):
    with open(os.path.join(FOLDER, "session_start.json")) as handle:
        data = json.load(handle)
    data.update(session_id=session, session_title=name, source=source, cwd=os.getcwd())
    path = os.path.join(FOLDER, f"start-{session}-{os.getpid()}.json")
    with open(path, "w") as handle:
        json.dump(data, handle)
    command = (f"{shlex.quote(sys.executable)} -I {shlex.quote(os.environ['HUB_FAKE_DISPATCH'])} SessionStart "
               f"< {shlex.quote(path)}")
    subprocess.run(["sh", "-c", command], stdout=subprocess.DEVNULL, check=True)


def draw(name, text):
    status = read(name, "idle")
    lines = [f"chat {name}", ""]
    if status == "waiting":
        lines += ["Do you want to proceed?", f"{PROMPT} 1. Yes", "  2. No", "Enter to select · Esc to cancel"]
    else:
        lines += [RULE, f"{PROMPT} {text}", RULE]
        footer = "✻ Working (esc to interrupt)" if status == "busy" else "? for shortcuts"
        if os.path.exists(os.path.join(FOLDER, "status", name + ".jobs")):
            footer += " · 1 shell still running"
        lines.append(footer)
    sys.stdout.write("\x1b[2J\x1b[H" + "\r\n".join(lines))
    sys.stdout.flush()
    return status


def interactive(words):
    import termios
    import tty
    options, _flags, prompt = parse(words)
    session = options.get("--resume") or options["--session-id"]
    name = options.get("-n") or options.get("--name") or session[:8]
    if "--resume" in options and os.path.exists(os.path.join(FOLDER, "status", name + ".drops-name")):
        name = session[-8:]
    session_start(session, name, "resume" if "--resume" in options else "startup")
    event("resume" if "--resume" in options else "start", name=name, session=session, pid=os.getpid(),
          args=words, prompt=prompt)
    row = {"cwd": os.getcwd(), "kind": "interactive", "startedAt": int(time.time() * 1000), "pid": os.getpid(),
           "status": "idle", "sessionId": session, "name": name}
    text = read(name + ".typed")
    descriptor = sys.stdin.fileno()
    saved = termios.tcgetattr(descriptor)
    tty.setraw(descriptor)
    try:
        while True:
            row["status"] = draw(name, text)
            write_row(row)
            ready, _, _ = select.select([descriptor], [], [], 0.2)
            if not ready:
                continue
            for character in os.read(descriptor, 1024).decode(errors="replace"):
                if character in "\r\n":
                    if text == "/exit":
                        event("stop", name=name, session=session, pid=os.getpid())
                        drop_row(session)
                        return
                    if text.startswith("/rename "):
                        name = row["name"] = text.split(" ", 1)[1]
                        event("rename", name=name, session=session)
                    elif text:
                        event("sent", name=name, text=text)
                    text = ""
                elif character == "\x7f":
                    text = text[:-1]
                elif character >= " ":
                    text += character
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def background(words):
    options, _flags, prompt = parse(words)
    session = options.get("--resume") or options["--session-id"]
    names = os.path.join(FOLDER, "names")
    os.makedirs(names, exist_ok=True)
    if options.get("-n"):
        with open(os.path.join(names, session), "w") as handle:
            handle.write(options["-n"])
    with open(os.path.join(names, session)) as handle:
        name = handle.read()
    if os.fork():
        return
    os.setsid()
    if os.fork():
        os._exit(0)
    for number in (0, 1, 2):
        os.dup2(os.open(os.devnull, os.O_RDWR), number)
    event("resume" if "--resume" in options else "start", name=name, session=session, pid=os.getpid(),
          args=words, prompt=prompt, cwd=os.getcwd())
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    row = {"cwd": os.getcwd(), "kind": "background", "id": session[-8:], "startedAt": int(time.time() * 1000),
           "pid": os.getpid(), "sessionId": session, "name": name}
    written = None
    while True:
        # status/<name> is idle, busy or waiting, or "missing" for a row without a status; status/<name>.state is
        # working, blocked, done, failed or stopped. The row is written again only when one of them changes.
        status, state = read(name, "idle"), read(name + ".state", "done")
        if (status, state) != written:
            row.pop("status", None)
            row.update({"state": state} if status == "missing" else {"state": state, "status": status})
            write_row(row)
            written = (status, state)
        time.sleep(0.2)


def main(words):
    if words == ["agents", "--json"]:
        print(json.dumps(rows()))
    elif words == ["--version"]:
        print(f"{read('../version', '2.1.319')} (Claude Code)")
    elif words == ["update"]:
        event("upgrade")
        while os.path.exists(os.path.join(FOLDER, "update-hangs")):
            time.sleep(0.1)
        if os.path.exists(os.path.join(FOLDER, "update-fails")):
            print("update failed", file=sys.stderr)
            return 1
        with open(os.path.join(FOLDER, "version"), "w") as handle:
            handle.write("2.1.320")
        print("Updated to 2.1.320")
    elif words[:1] == ["stop"]:
        for row in rows():
            if row.get("id") == words[1]:
                event("stop", name=row["name"], session=row["sessionId"], pid=row["pid"])
                os.kill(row["pid"], signal.SIGTERM)
                drop_row(row["sessionId"])
    elif words[:1] == ["logs"]:
        print(f"recent output of {words[1]}")
    elif "--bg" in words:
        background([word for word in words if word != "--bg"])
    elif "--session-id" in words or "--resume" in words:
        interactive(words)
    else:  # hubctl's own calls go to the recorded replay beside this file
        replay = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-replay")
        os.execv(sys.executable, [sys.executable, replay, *words])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
