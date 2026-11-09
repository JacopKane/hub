"""One Chrome lock for every chat of this user, and the `hubctl browser-lock` command.

    hubctl browser-lock take "<job>"            take it now, or exit 1 and say who holds it
    hubctl browser-lock wait "<job>" [seconds]  keep trying (default 540, at most 570), exit 1 on time out
    hubctl browser-lock release                 give it back, only from the chat that holds it
    hubctl browser-lock status                  exit 0 when free, 1 when held
    hubctl browser-lock clear                   for the user only: remove a stale lock, whoever holds it

The owner is the chat's session id, from CLAUDE_CODE_SESSION_ID, or --session
<id>. A subagent's shell gets its main chat's id there. The lock is a folder in
hub's state folder, locks/chrome, with owner.json inside. Making the folder is
the atomic step, so two chats can never both take it. HUB_LOCK_POLL sets the
seconds between tries while waiting.
"""
import os
import re
import shutil
import sys
import time

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import clock, files  # noqa: E402

FOLDER = os.path.join("locks", "chrome")
OWNER = "owner.json"
FRESH = 5  # seconds a lock may have no owner file while a chat is still writing it
WAIT_DEFAULT, WAIT_MAX = 540, 570  # under the 10-minute limit of a shell call
SESSION = re.compile(r"[A-Za-z0-9_-]+")


def folder():
    return files.state_path(FOLDER)


def owner():
    """{"session", "job", "start"}, {} while the owner file is missing, or None when the lock is free."""
    if not os.path.isdir(folder()):
        return None
    try:
        record = files.read_json(os.path.join(folder(), OWNER), {})
    except (OSError, ValueError):
        record = {}
    if not isinstance(record, dict) or not record.get("session"):
        return {}
    return {key: str(record.get(key) or "") for key in ("session", "job", "start")}


def age():
    """Seconds since the lock was taken, or None."""
    try:
        return time.time() - os.stat(folder()).st_mtime
    except OSError:
        return None


def describe(record, me=""):
    if record is None:
        return "free"
    if not record:
        seconds = age()
        return (f"held with no owner file for {int(seconds or 0)} s. If that is more than a few seconds, the "
                "lock is stale, and the user clears it in a terminal with hubctl browser-lock clear")
    who = "this chat" if record["session"] == me else f"another chat ({record['session']})"
    return f'held by {who}, job "{record["job"]}", since {record["start"]}'


def try_take(me, job):
    os.makedirs(os.path.dirname(folder()), mode=0o700, exist_ok=True)
    try:
        os.mkdir(folder())
    except FileExistsError:
        return False
    files.write_json(os.path.join(folder(), OWNER),
                     {"session": me, "job": re.sub(r"\s+", " ", job).strip() or "unnamed", "start": clock.stamp()})
    return True


def remove():
    shutil.rmtree(folder(), ignore_errors=True)


def session(words):
    if "--session" in words:
        index = words.index("--session")
        found = words[index + 1] if index + 1 < len(words) else ""
        del words[index:index + 2]
    else:
        found = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not SESSION.fullmatch(found or ""):
        print("browser-lock: no session id. Pass --session <id>, or run it from a chat.", file=sys.stderr)
        sys.exit(2)
    return found


def poll():
    try:
        return max(0.05, float(os.environ.get("HUB_LOCK_POLL") or 5))
    except ValueError:
        return 5


def took(job):
    print(f'took the Chrome lock for "{job}". Release it with hubctl browser-lock release')


def main(words):
    words = list(words)
    command = words.pop(0) if words else ""
    if command == "status":
        record = owner()
        print(describe(record, os.environ.get("CLAUDE_CODE_SESSION_ID", "")))
        return 0 if record is None else 1
    if command == "clear":
        record = owner()
        if record is None:
            print("already free")
            return 0
        print(f"clearing: {describe(record)}")
        remove()
        return 0
    if command not in ("take", "wait", "release"):
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    me = session(words)
    if command == "take":
        job = " ".join(words)
        if try_take(me, job):
            took(job)
            return 0
        print(f"not taken: {describe(owner(), me)}")
        return 1
    if command == "wait":
        limit = WAIT_DEFAULT
        if words and words[-1].isdigit():
            limit = min(int(words.pop()), WAIT_MAX)
        job = " ".join(words)
        end = time.monotonic() + limit
        while True:
            if try_take(me, job):
                took(job)
                return 0
            if time.monotonic() >= end:
                print(f"still {describe(owner(), me)}. Run the same wait again in the foreground, and do not hand "
                      "back while you wait.")
                return 1
            time.sleep(poll())
    record = owner()
    if record is None:
        print("already free")
        return 0
    if record.get("session") != me:
        print(f"not released: {describe(record, me)}")
        return 1
    remove()
    print("released")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
