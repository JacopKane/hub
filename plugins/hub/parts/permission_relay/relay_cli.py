"""hubctl relay: the prompts that wait in other chats, for the coordinator's mod only.

The verbs are internal. The mod calls them, and the user sees what waits in
the /hub pane and the band, never in a terminal command.

    hubctl relay next --session S --json [--skip ID ...]
                                           what the coordinator's mod shows next
    hubctl relay pick <id> <label> --session S
                                           the button the user picked
    hubctl relay again --session S         show the prompts put off with Later again

next answers {"hub": false} to every chat except the live coordinator that
holds the relay's lock (relay/owner.json), which it takes when the chat that
held it no longer runs. For the coordinator it answers the count of prompts
that wait in other live chats and one dialog: its id, its text and its
buttons. It passes over each --skip id, the prompts the mod put off in
memory after a dismissed dialog or a typed answer, but still counts them.
pick takes only the exact labels of that dialog's buttons, and none of them
answers a prompt: "Open the tab" brings the chat's own tab or pane to
the front, "Later" puts the dialog off, and "Keep it blocked" removes a
block. Every verb refuses inside a tool call, so a model never runs it.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(os.path.dirname(HERE)), "lib")
for folder in (LIB, HERE):
    if folder not in sys.path:
        sys.path.insert(0, folder)

from hub import dispatch, files, resolve, sessions  # noqa: E402
from hub.adapters import terminal  # noqa: E402

import relay_store  # noqa: E402

PART = relay_store.PART
OPEN, LATER, KEEP = "Open the tab", "Later", "Keep it blocked"
LATER_MARK, OPENED_MARK = "later", "opened"


def flags():
    data = resolve.current(os.getcwd())
    return dispatch.Flags(data["tools"]["claude_code"], data.get("texts", {}), "claude_code", None)


def text(name):
    with open(os.path.join(HERE, "text", name), encoding="utf-8") as handle:
        return handle.read().strip()


def chat_name(record):
    data = sessions.record(record.get("session")) or {}
    return relay_store.clean(data.get("title") or "", 80) or "A chat " + relay_store.clean(record["session"], 8)


def buttons(record, flag_values):
    found = [OPEN] if flag_values.runs(f"{PART}.jump") else []
    if record["kind"] == relay_store.BLOCK:
        found.append(KEEP)
    return found + [LATER]


def dialog_text(record, flag_values):
    chat = chat_name(record)
    template = "block.md" if record["kind"] == relay_store.BLOCK else "prompt.md"
    shown = text(template).format(chat=chat, tool=record["tool"])
    limit = flag_values.value(f"{PART}.list.preview_chars")
    limit = limit if isinstance(limit, int) else 300
    preview = relay_store.clean(record.get("preview") or "", min(limit, relay_store.PREVIEW_LIMIT))
    if preview:
        shown += " " + text("preview.md").format(preview=preview)
    return shown + " " + text("question.md")


def coordinator(session, flag_values):
    """Whether session is the live coordinator that holds the relay. Call it under store()."""
    data = sessions.record(session)
    name = flag_values.value("coordinator.name") or "HUB"
    if not data or not sessions.same_name(data.get("title"), name):
        return False
    return relay_store.take_owner(session)


def others(session, secret):
    return [record for record in relay_store.all_records(secret, check_chats=True) if record["session"] != session]


def next_dialog(args, flag_values):
    with relay_store.store():
        if not coordinator(args.session, flag_values):
            return {"hub": False}
        records = others(args.session, relay_store.key())
        marks = relay_store.marks()
        relay_store.save_marks(marks, records)
        shown = None
        if flag_values.runs(f"{PART}.dialogs"):
            skipped = set(args.skip)
            shown = next((record for record in records if record["id"] not in marks and record["id"] not in skipped),
                         None)
        dialog = None if shown is None else {"id": shown["id"], "text": dialog_text(shown, flag_values),
                                             "buttons": buttons(shown, flag_values)}
        return {"hub": True, "waiting": len(records), "dialog": dialog}


def refuse(why):
    print(why, file=sys.stderr)
    return 1


def pick(args, flag_values):
    with relay_store.store():
        if not coordinator(args.session, flag_values):
            return refuse("Only the coordinator that holds the relay can pick.")
        if not relay_store.ID.match(args.id):
            return refuse("That is not a prompt the relay keeps.")
        record = next((record for record in others(args.session, relay_store.key()) if record["id"] == args.id),
                      None)
        if record is None:
            return refuse("That is not a prompt the relay keeps.")
        if args.label not in buttons(record, flag_values):
            return refuse("That is not one of the dialog's buttons, so nothing changed.")
        marks = relay_store.marks()
        if args.label == KEEP:
            relay_store.remove(record["id"] + ".json")
            return 0
        if args.label == LATER:
            marks[record["id"]] = LATER_MARK
            relay_store.save_marks(marks, [record, *[{"id": name} for name in marks]])
            return 0
        if args.label != OPEN:
            return refuse("That is not one of the dialog's buttons, so nothing changed.")
        chat = sessions.record(record["session"])
    # Open the tab: outside the lock, since it waits on the terminal.
    location = terminal.locate(chat, os.environ.get("HUB_TMUX_SERVER"))
    if not terminal.focus(location):
        return refuse("hub could not find that chat's tab or pane, so it stays in the list.")
    with relay_store.store():
        marks = relay_store.marks()
        marks[record["id"]] = OPENED_MARK
        relay_store.save_marks(marks, [{"id": name} for name in marks])
    return 0


def again(args, flag_values):
    with relay_store.store():
        if not coordinator(args.session, flag_values):
            return refuse("Only the coordinator that holds the relay can ask again.")
        records = others(args.session, relay_store.key())
        marks = {name: value for name, value in relay_store.marks().items() if value != LATER_MARK}
        relay_store.save_marks(marks, records)
    print(len(records))
    return 0


def parser():
    top = argparse.ArgumentParser(prog="hubctl relay", description=__doc__.split("\n\n")[0])
    verbs = top.add_subparsers(dest="verb", required=True)
    shown = verbs.add_parser("next", help="what the coordinator's mod shows next")
    shown.add_argument("--session", required=True)
    shown.add_argument("--json", action="store_true")
    shown.add_argument("--skip", action="append", default=[], help="an id to pass over this time")
    picked = verbs.add_parser("pick", help="the button the user picked")
    picked.add_argument("id")
    picked.add_argument("label")
    picked.add_argument("--session", required=True)
    asked = verbs.add_parser("again", help="show the prompts put off with Later again")
    asked.add_argument("--session", required=True)
    return top


def main(argv):
    if files.in_tool_call():
        return refuse("hubctl relay does not run inside a tool call. Only the coordinator's mod runs it.")
    args = parser().parse_args(argv)
    flag_values = flags()
    if args.verb == "next":
        answer = next_dialog(args, flag_values)
        print(json.dumps(answer, ensure_ascii=False) if args.json else
              (answer.get("dialog") or {}).get("text", "") if answer.get("hub") else "")
        return 0
    return {"pick": pick, "again": again}[args.verb](args, flag_values)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
