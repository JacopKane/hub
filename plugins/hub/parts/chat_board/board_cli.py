"""The chat board as text, plain text or JSON, and each chat's task list.

The user reads it with /hub board, the coordinator reads `board --json`, and
chats write their tasks with `board task`. None of it is a terminal command.

    board [--all] [--json] [--plain]
    board tasks [--json] [--plain]
    board show <chat> [--json] [--plain]
    board task now|add|done <task> [--session <id>]
    board task list [--json] [--session <id>]

`board task` finds its chat by walking up its parent processes until it
meets the process of a chat that hub holds a record of. --session names the
chat instead.
"""
import argparse
import json
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import board, resolve  # noqa: E402

PART = "chat_board"
NO_CHAT = ("board task could not find its chat, because none of its parent processes is a chat hub knows. "
           "Name the chat with --session <id>.")
BAD_SESSION = ("This session id has no letter, digit, - or _ in it. "
               "Name the chat with --session <id>, using an id from board --json.")
TASKS_OFF = "The task list is off. The user can turn it on by saying \"turn on chat_board.tasks\"."


def runs(flag_id):
    flags = resolve.current(os.getcwd())["tools"]["claude_code"]["flags"]
    return bool(flags.get(flag_id, {}).get("runs"))


def change_tasks(session, verb, text):
    tasks = board.ledger(session)
    at = board.now()
    found = next((task for task in tasks if task.get("text") == text and task.get("state") != "done"), None)
    if verb == "add":
        if found is None:
            tasks.append({"text": text, "state": "queued", "time": at, "from": "hub"})
    elif verb == "now":
        for task in tasks:
            if task.get("state") == "working" and task is not found:
                task.update(state="queued", time=at)
        if found is None:
            tasks.append({"text": text, "state": "working", "time": at, "from": "hub"})
        else:
            found.update(state="working", time=at)
    elif verb == "done":
        if found is None:
            tasks.append({"text": text, "state": "done", "time": at, "from": "hub"})
        else:
            found.update(state="done", time=at)
    board.write_ledger(session, tasks)
    return tasks


def task_command(args):
    if not runs(f"{PART}.tasks"):
        print(TASKS_OFF, file=sys.stderr)
        return 1
    if args.session is not None and not board.safe(args.session):
        print(BAD_SESSION, file=sys.stderr)
        return 1
    session = args.session or board.own_session()
    if not session:
        print(NO_CHAT, file=sys.stderr)
        return 1
    if args.verb == "list":
        tasks = board.ordered(board.ledger(session))
        if args.json:
            print(json.dumps({"session": session, "tasks": tasks}, ensure_ascii=False))
        else:
            plain = board.plain_wanted(args.plain)
            print("\n".join(f"{board.glyph(task['state'], plain)} {task['text']}" for task in tasks) or
                  "no tasks yet")
        return 0
    text = board.clean(" ".join(args.words))
    if not text:
        print(f"board task {args.verb} needs the task, such as board task {args.verb} \"fix login\".",
              file=sys.stderr)
        return 2
    change_tasks(session, args.verb, text)
    return 0


def main(argv):
    parser = argparse.ArgumentParser(prog="/hub board")
    parser.add_argument("view", nargs="?", default="chats", choices=["chats", "tasks", "show", "task"])
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plain", action="store_true")
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    if args.view == "task":
        if not args.rest or args.rest[0] not in ("now", "add", "done", "list"):
            print("usage: board task now|add|done|list [<task>] [--session <id>]", file=sys.stderr)
            return 2
        args.verb, args.words = args.rest[0], args.rest[1:]
        return task_command(args)
    data = board.load()
    plain = board.plain_wanted(args.plain)
    if args.view == "show":
        if not args.rest:
            print("usage: /hub board show <chat>", file=sys.stderr)
            return 2
        text = board.render_show(data, args.rest[0], plain)
        if text is None:
            print(f"No chat named {args.rest[0]} is on the board. /hub board lists the chats.", file=sys.stderr)
            return 1
        sys.stdout.write(text)
        return 0
    if args.json:
        if args.view == "tasks":
            print(json.dumps({"time": data["time"], "columns": data["columns"]}, ensure_ascii=False))
        else:
            print(json.dumps(data, ensure_ascii=False))
        return 0
    sys.stdout.write(board.render_tasks(data, plain) if args.view == "tasks" else board.render(data, plain, every=args.all))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
