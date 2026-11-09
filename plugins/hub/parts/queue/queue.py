#!/usr/bin/env python3
"""Holds ordinary messages between idle main chats for a short time, so a receiver wakes once, not once per message.

As a hook (handler.py calls it):
  PreToolUse on SendMessage  holds the message, or lets the native send run.
  UserPromptSubmit           the chat just woke for another reason, so what is held for it goes now and costs no wake.
As a command (python3 -I queue.py ...):
  flush         one pass over the queue, which the flush job runs every few seconds. It also writes the heartbeat,
                unless --no-heartbeat. All posts of one pass go on one connection, one line each, so a receiver
                takes them in one turn.
  report        the numbers that show whether the queue saves wakes.
  off, on       the switch. While off nothing new is held, and what is held goes at the next flush.
  stop          off first, then a forced flush that hands back what it can not post. The flush job runs it when
                it is removed, so turning the part off or uninstalling hub never strands a message.

What is held: a message from a main chat to one other idle main chat, with no urgent mark on its first line, and,
while the version guard runs, to a chat on a tested Claude Code version. Everything else takes the native path
untouched. Messages to and from agents never wait. With keep_awake, each hold keeps the machine from idle sleep
until the limit has passed.

A chat is a row of `claude agents --json` with a pid, joined with hub's session record of that chat: the record's
pid must be the row's, still alive with its recorded start time, and its inbox socket must exist. Status comes from
the row, the version from the record.

A held message belongs to the receiver's session, not its name or address. It follows that session across a
resume, and a new session of the same name or on the same address, as after /clear, never gets it. Past the limit
the sender is told.

Failure rule: any error in the hook means no answer, which is the native send. A bug may cost a wake, never a
message. A stale heartbeat means no flush is running, so nothing new is held.
Hold times, flush times and the heartbeat come from hub's clock, so tests can fix them with HUB_TEST_NOW.
A sender that is down when its message is given up, as during a restart, gets the notice at its next session start
or turn, through hub's chat notices.
A posted message is the sender's: its address, name and true permission class. It is never marked as typed by a
person, and the receiver's inbound rules apply to it as to any other message. Messages merge only when all three
are the same.

A post has no acknowledgement. With the version guard on, the queue trusts it only on the tested versions. When a
receiver changed version while a message was held, the message is posted once, saved, and handed back to the
sender to send again. A copy is acceptable where a loss is not. On a tested version a receiver that drops a post
under overload still drops it silently.

Each held item carries the settings it was held under (quiet, limit, every, versions), because the flush runs
outside the hooks and does not read hub's flags. Run it with python3 -I, so this file's folder is not on the
import path and its name never hides the standard queue module.
"""
import calendar
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid

PART_DIR = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(os.path.dirname(os.path.dirname(PART_DIR)), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import chat_notices, clock, files, sessions  # noqa: E402

FOLDER = "queue"
QUIET_SECONDS = 120  # send when no new message came for this long
LIMIT_SECONDS = 300  # no message waits longer than this
EVERY_SECONDS = 30  # how often the flush job runs
URGENT_MARK = "[urgent]"  # the sender starts the first line with this to send at once
TESTED_VERSIONS = ["2.1.316", "2.1.318", "2.1.319"]
MAX_HELD_MESSAGE_BYTES = 64 * 1024  # a larger message goes natively
MAX_POST_BYTES = 256 * 1024  # a merged post stays under this, the rest goes in the next post
HOP_PASS_LENGTH = 6  # a reply this deep in a chain goes natively, so the native loop guard counts it
LOCK_WAIT_SECONDS = 2
STOP_FLUSHES = 3  # a stop runs this many forced flushes. The last one gives up on what it can not post
STOP_LOCK_WAIT_SECONDS = 30  # how long each of them waits for a flush that holds the lock
CONNECT_TIMEOUT_SECONDS = 2
POST_SETTLE_SECONDS = 0.3  # the native client also stays connected briefly after writing on macOS
TRANSCRIPT_TAIL_BYTES = 512 * 1024
VERSION_TIMEOUT_SECONDS = 3
PROTOCOL_VERSION = 1
TAG = "cross-session-message"
CLASS_OF_MODE = {  # from the cross-session docs. plan has no entry, because its class depends on the session kind
    "bypassPermissions": "bypass",
    "default": "prompting",
    "acceptEdits": "prompting",
    "auto": "prompting",
    "dontAsk": "prompting",
}
MANAGED_SETTINGS = ["/Library/Application Support/ClaudeCode/managed-settings.json",
                    "/etc/claude-code/managed-settings.json"]
ADDRESS = re.compile(r"^uds:[A-Za-z0-9%:_/.\\-]{1,300}$")
CLOSING_TAG = re.compile(r"<(?!\\)(?=\s*/\s*" + TAG + ")", re.I)
VERSION = re.compile(r"\b(\d+\.\d+\.\d+)\b")
USAGE = ("usage: queue.py [flush [--receiver SESSION] [--force] [--no-heartbeat] | report [--days N] [--root DIR]"
         " | off | on | stop]")


class Pass(Exception):
    """The message takes the native path. The text is the reason, for the event log."""


# settings

def settings_from(flags=None):
    """The settings a hold uses, from hub's flags, or the defaults."""
    def value(flag_id, default):
        found = flags.value(flag_id) if flags is not None else None
        return default if found is None else found
    guard = flags is None or flags.runs("queue.version_guard")
    return {
        "quiet": value("queue.quiet_seconds", QUIET_SECONDS),
        "limit": value("queue.hold_seconds", LIMIT_SECONDS),
        "every": value("queue.flush_job.every_seconds", EVERY_SECONDS),
        "urgent": value("queue.urgent_prefix", URGENT_MARK) or URGENT_MARK,
        "versions": list(value("queue.version_guard.versions", TESTED_VERSIONS)) if guard else None,
        "keep_awake": flags is None or flags.runs("queue.keep_awake"),
    }


def margin(settings):
    """Flush this early, so the next tick can not pass the limit."""
    return settings["every"] + 5


def heartbeat_max_age(settings):
    """A heartbeat older than this means no flush is running."""
    return 3 * settings["every"]


def item_settings(item):
    return {"quiet": item.get("quiet", QUIET_SECONDS), "limit": item.get("limit", LIMIT_SECONDS),
            "every": item.get("every", EVERY_SECONDS), "versions": item.get("versions", TESTED_VERSIONS)}


# files

def queue_file(*parts):
    return files.state_path(os.path.join(FOLDER, *parts))


def held_file(receiver_session):
    return queue_file("held", os.path.basename(str(receiver_session)) + ".jsonl")


def text(name):
    with open(os.path.join(PART_DIR, "text", name), encoding="utf-8") as handle:
        return handle.read().strip()


def log_event(event, **fields):
    try:
        os.makedirs(queue_file(), mode=0o700, exist_ok=True)
        with open(queue_file("events.jsonl"), "a") as log:
            log.write(json.dumps(dict(fields, at=round(time.time(), 1), event=event)) + "\n")
    except OSError:
        pass


class QueueLock:
    def __init__(self, wait=LOCK_WAIT_SECONDS):
        self.wait = wait

    def __enter__(self):
        os.makedirs(queue_file("held"), mode=0o700, exist_ok=True)
        self.handle = open(queue_file("lock"), "w")
        deadline = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() > deadline:
                    self.handle.close()
                    raise
                time.sleep(0.05)

    def __exit__(self, *_):
        self.handle.close()


def append_durably(path, line):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (line + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_held(path, items):
    if not items:
        if os.path.exists(path):
            os.unlink(path)
        return
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write("".join(json.dumps(item) + "\n" for item in items))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def remember(settings):
    """What the flush needs outside the hooks: how often it runs, and where claude is when the job's PATH lacks it."""
    program = shutil.which("claude")
    data = {"every": settings["every"], "claude_dir": os.path.dirname(program) if program else None}
    if files.read_json(queue_file("config.json")) != data:
        files.write_json(queue_file("config.json"), data)


def remembered():
    data = files.read_json(queue_file("config.json"), {})
    return data if isinstance(data, dict) else {}


# chats

def live_chats():
    """Every live interactive chat on this machine, or None when the listing failed."""
    rows = sessions.listing()
    if rows is None:
        return None
    chats = []
    for row in rows:
        if not isinstance(row, dict) or row.get("kind", "interactive") != "interactive" or not row.get("pid"):
            continue
        data = sessions.record(row.get("sessionId"))
        if not data or data.get("pid") != row.get("pid") or not sessions.is_alive(data):
            continue
        address = data.get("address")
        if not isinstance(address, str) or not os.path.exists(address):
            continue
        chats.append({"session": row.get("sessionId"), "name": row.get("name") or data.get("title"),
                      "socket": address, "status": row.get("status"), "version": data.get("version")})
    return chats


def resolve_receiver(to, chats):
    if to.startswith("uds:"):
        found = [chat for chat in chats or [] if chat["socket"] == to[4:]]
    else:
        found = [chat for chat in chats or [] if sessions.same_name(chat.get("name"), to)]
    return found[0] if len(found) == 1 else None


def claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(files.home(), ".claude")


def denied_by_settings(cwd):
    paths = MANAGED_SETTINGS + [
        os.path.join(claude_dir(), "settings.json"),
        os.path.join(cwd or "", ".claude", "settings.json"),
        os.path.join(cwd or "", ".claude", "settings.local.json"),
    ]
    for path in paths:
        try:
            with open(path) as handle:
                rules = (json.load(handle).get("permissions") or {}).get("deny") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(str(rule).split("(")[0] == "SendMessage" for rule in rules):
            return True
    return False


def agent_named(to, transcript_path):
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return False
    for path in glob.glob(os.path.join(glob.escape(transcript_path[:-6]), "subagents", "*.meta.json")):
        try:
            with open(path) as handle:
                if sessions.same_name(json.load(handle).get("name"), to):
                    return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def incoming_hop_chain(transcript_path):
    """The chain the native send would extend: from the last user entry, if that was a message from another chat."""
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - TRANSCRIPT_TAIL_BYTES))
            lines = handle.read().split(b"\n")
    except (OSError, TypeError):
        raise Pass("transcript-unreadable") from None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("toolUseResult") \
                or entry.get("isCompactSummary") or entry.get("isSidechain"):
            continue
        origin = entry.get("origin") or {}
        if entry.get("isMeta") and not origin:
            continue  # a reminder added after the incoming message, which must not hide its chain
        return (origin.get("hopChain") or []) if origin.get("kind") == "peer" else None
    raise Pass("transcript-unreadable")


def heartbeat_fresh(settings):
    try:
        return clock.epoch() - os.path.getmtime(queue_file("heartbeat")) <= heartbeat_max_age(settings)
    except OSError:
        return False


def plan_hold(hook_input, chats, settings):
    """Returns the item to hold and the receiver, or raises Pass."""
    tool_input = hook_input.get("tool_input") or {}
    to, message = tool_input.get("to"), tool_input.get("message")
    if hook_input.get("agent_id"):
        raise Pass("from-agent")
    if os.path.exists(queue_file("off")):
        raise Pass("off")
    if tool_input.get("notify_when_idle"):
        raise Pass("notify-when-idle")
    if not isinstance(to, str) or not isinstance(message, str) or not message.strip():
        raise Pass("not-a-text-message")
    if len(message.encode()) > MAX_HELD_MESSAGE_BYTES:
        raise Pass("too-large")
    if message.lstrip().lower().startswith(settings["urgent"].lower()):
        raise Pass("urgent")
    if not heartbeat_fresh(settings):
        raise Pass("no-heartbeat")
    if chats is None:
        raise Pass("no-listing")
    receiver = resolve_receiver(to, chats)
    if receiver is None:
        raise Pass("not-one-main-chat")
    if not receiver.get("session"):
        raise Pass("receiver-has-no-session")
    own_socket = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or ""
    if receiver["socket"] == own_socket or receiver["session"] == hook_input.get("session_id"):
        raise Pass("self")
    if settings["versions"] is not None and receiver.get("version") not in settings["versions"]:
        raise Pass("untested-version")
    if receiver.get("status") != "idle":
        raise Pass("busy")
    sender_address = "uds:" + own_socket
    if not ADDRESS.match(sender_address):
        raise Pass("no-sender-address")
    sender_class = CLASS_OF_MODE.get(hook_input.get("permission_mode"))
    if sender_class is None:
        raise Pass("class-unknown")
    transcript_path = hook_input.get("transcript_path") or ""
    if agent_named(to, transcript_path):
        raise Pass("agent-of-that-name")
    if denied_by_settings(hook_input.get("cwd")):
        raise Pass("denied-by-settings")
    chain = incoming_hop_chain(transcript_path)
    if chain is not None and len(chain) >= HOP_PASS_LENGTH:
        raise Pass("long-hop-chain")
    names = [chat.get("name") for chat in chats if chat["socket"] == own_socket]
    if not names or not names[0]:
        raise Pass("sender-not-a-main-chat")
    item = {
        "held_at": clock.epoch(),
        "receiver_socket": receiver["socket"],
        "receiver_session": receiver["session"],
        "receiver_name": receiver.get("name"),
        "sender_address": sender_address,
        "sender_session": hook_input.get("session_id"),
        "sender_name": names[0],
        "sender_class": sender_class,
        "hop_chain": chain,
        "body": message,
        "quiet": settings["quiet"],
        "limit": settings["limit"],
        "every": settings["every"],
        "versions": settings["versions"],
    }
    return item, receiver


def keep_awake(settings):
    """A sleeping machine would stall the flush. A missing tool only risks a late message, so it never stops the hold."""
    program = shutil.which("caffeinate")
    if not settings["keep_awake"] or program is None:
        return
    try:
        subprocess.Popen([program, "-i", "-t", str(settings["limit"] + settings["every"])],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as error:
        log_event("keep-awake-failed", error=type(error).__name__)


def on_send(hook_input, settings):
    """The refusal text when the message is held, or None for the native send."""
    tool_input = hook_input.get("tool_input") or {}
    chats = live_chats()
    try:
        item, receiver = plan_hold(hook_input, chats, settings)
        with QueueLock():
            # Again under the lock: a stop that turned the queue off while this send waited has run its last flush.
            if os.path.exists(queue_file("off")):
                raise Pass("off")
            append_durably(held_file(receiver["session"]), json.dumps(item))
    except Pass as native:
        log_event("passed", reason=str(native))
        # Keep the order: what is already held for this receiver goes before this message.
        to = tool_input.get("to")
        receiver = resolve_receiver(to, chats) if isinstance(to, str) else None
        session = receiver.get("session") if receiver is not None else None
        if session and os.path.exists(held_file(session)):
            flush(receiver_session=session, force=True)
        return None
    remember(settings)
    keep_awake(settings)
    log_event("held", receiver=item["receiver_name"], sender=item["sender_name"])
    return text("queued.md").format(name=item["receiver_name"], quiet=settings["quiet"], limit=settings["limit"],
                                    urgent=settings["urgent"])


def flush_in_background(*arguments):
    subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "flush", *arguments],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def on_prompt(hook_input):
    session = hook_input.get("session_id")
    if session and os.path.exists(held_file(session)):
        flush_in_background("--receiver", session, "--force")


def on_any_hook(settings):
    """No flush is running, so whoever runs next moves what is held."""
    if not heartbeat_fresh(settings) and glob.glob(queue_file("held", "*.jsonl")):
        flush_in_background("--no-heartbeat")


# posting

def queue_token(address):
    """Counts this hop for the native chain length check. Hop tokens use a secret per process, so it can not be the
    sender's own."""
    return hashlib.sha256(address.encode()).hexdigest()[:24]


def envelope(address, name, sender_class, body, chain):
    attributes = ' from="%s"' % address
    if chain is not None:
        attributes += ' hop-chain="%s"' % ",".join((chain + [queue_token(address)])[-32:])
    clean_name = re.sub(r'["<>\r\n]', "", name or "")
    if clean_name:
        attributes += ' from-name="%s"' % clean_name
    attributes += ' from-mode="%s"' % sender_class
    return "<%s%s>\n%s\n</%s>" % (TAG, attributes, CLOSING_TAG.sub("<\\\\", body), TAG)


def post(receiver_socket, messages):
    """Writes each (address, text) as its own line on one connection, so an idle receiver takes them in one turn.
    Each line keeps its own sender and class, because the receiver reads and checks every line by itself."""
    lines = [json.dumps({
        "msgV": PROTOCOL_VERSION, "msg_id": str(uuid.uuid4()), "type": "user",
        "message": {"role": "user", "content": content}, "priority": "next", "from": address,
    }) + "\n" for address, content in messages]
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(CONNECT_TIMEOUT_SECONDS)
    try:
        client.connect(receiver_socket)
        client.sendall("".join(lines).encode())
        time.sleep(POST_SETTLE_SECONDS)
    finally:
        client.close()


def merged_body(items, now):
    """Every message says when it was sent and how long it waited, so the receiver can read it in its true order."""
    def stamp(item):
        sent = time.strftime("%H:%M:%S", time.localtime(item["held_at"]))
        return "sent %s, waited %d s" % (sent, max(0, now - item["held_at"]))
    if len(items) == 1:
        return "[%s]\n%s" % (stamp(items[0]), items[0]["body"])
    parts = ["%d queued messages from this chat, oldest first." % len(items)]
    for number, item in enumerate(items, 1):
        parts.append("[%d of %d, %s]\n%s" % (number, len(items), stamp(item), item["body"]))
    return "\n\n".join(parts)


def posts_for(items):
    """One post per sender, because a post carries one sender's name and class. Oldest first. A sender's post ends
    when its name or class changes, so no message takes another's class, or when it reaches the size limit."""
    senders = {}
    for item in items:
        senders.setdefault(item["sender_address"], []).append(item)
    for sender_items in senders.values():
        batch, size = [], 0
        for item in sender_items:
            length = len(item["body"].encode())
            changed = batch and (item["sender_class"], item["sender_name"]) != \
                (batch[0]["sender_class"], batch[0]["sender_name"])
            if changed or (batch and size + length > MAX_POST_BYTES):
                yield batch
                batch, size = [], 0
            batch.append(item)
            size += length
        yield batch


def sender_address_now(item, chats):
    """A resume puts the sender on a new address, and an upgrade comes with a resume."""
    found = [chat for chat in chats or [] if item.get("sender_session") and chat["session"] == item["sender_session"]]
    return "uds:" + found[0]["socket"] if len(found) == 1 else item["sender_address"]


def tell_sender(item, content, chats, post_it=True):
    """Posts the notice to the sender's live session. When the sender is down, as during a restart, or the post
    fails, the notice waits for its next session start or turn, so it is never lost. A bare address is never used,
    because after /clear or a restart it may belong to another chat. With post_it false, the notice only waits. A
    stop uses this, because no later flush would notice a failed post."""
    session = item.get("sender_session")
    found = [chat for chat in chats or [] if session and chat["session"] == session]
    if post_it and len(found) == 1:
        with sessions.posting_to(session) as allowed:  # a sender that is restarting gets the notice when it is back
            if allowed:
                address = "uds:" + found[0]["socket"]
                try:
                    post(found[0]["socket"], [(address, envelope(address, item["sender_name"], item["sender_class"],
                                                                 content, None))])
                    return
                except OSError as error:
                    log_event("notice-post-failed", sender=item["sender_name"], error=type(error).__name__)
    if not chat_notices.leave(session, content):
        log_event("notice-lost", sender=item["sender_name"], why="no sender session")


def give_up(items, why, chats, notice="not_delivered.md", final=False):
    """The queue can not vouch for these messages. The text is kept, and the sender is told. Returns how many."""
    saved = queue_file("undelivered.jsonl")
    template = text(notice)
    for item in items:
        append_durably(saved, json.dumps(dict(item, gave_up_at=time.time(), why=why)))
        log_event("undelivered", receiver=item["receiver_name"], sender=item["sender_name"], why=why)
        tell_sender(item, template.format(name=item["receiver_name"], why=why, saved=saved, body=item["body"]),
                    chats, post_it=not final)
    return len(items)


def current_receiver(items, chats):
    """The session only. A resume keeps it on a new address. /clear keeps the address with a new session, and a
    restart can reuse the name or the address, so neither of those can say this is the chat the message was for."""
    session = items[0]["receiver_session"]
    found = [chat for chat in chats or [] if session and chat["session"] == session]
    return found[0] if len(found) == 1 else None


def flush_file(path, chats, force, now, final=False):
    """Posts or keeps what one held file holds. Returns how many messages it gave up."""
    with open(path) as handle:
        lines = handle.read().splitlines()
    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except ValueError:
            append_durably(queue_file("undelivered.jsonl"), json.dumps({"unreadable_line": line, "gave_up_at": now}))
            log_event("undelivered", why="unreadable-line")
    if not items:
        write_held(path, [])
        return 0
    with sessions.posting_to(items[0]["receiver_session"]) as allowed:
        if allowed:
            return post_or_keep(path, items, chats, force, now, final)
        # A post now could land during the chat's /exit. The restart clears the mark when the chat is back or the run
        # ends, so the messages wait until then, however old they are. Only a queue that stops gives up on them.
        if final:
            given_up = give_up(items, "that chat was restarting when the queue stopped", chats, final=True)
            write_held(path, [])
            return given_up
        log_event("kept", reason="restarting", receiver=items[0]["receiver_name"], messages=len(items))
        return 0


def post_or_keep(path, items, chats, force, now, final):
    settings = item_settings(max(items, key=lambda item: item["held_at"]))
    oldest_age = now - min(item["held_at"] for item in items)
    newest_age = now - max(item["held_at"] for item in items)
    past_limit = final or oldest_age + margin(settings) >= settings["limit"]
    receiver = current_receiver(items, chats)
    if receiver is None:
        if past_limit:
            why = "hub could not list the chats" if chats is None else "that chat is gone"
            given_up = give_up(items, why, chats, final=final)
            write_held(path, [])
            return given_up
        return 0
    untested = settings["versions"] is not None and receiver.get("version") not in settings["versions"]
    if untested:
        reason = "untested-version"
    elif force or os.path.exists(queue_file("off")):
        reason = "forced"
    elif receiver.get("status") != "idle":
        reason = "busy"
    elif past_limit:
        reason = "limit"
    elif newest_age >= settings["quiet"]:
        reason = "quiet"
    else:
        return 0
    batches = list(posts_for(items))
    messages = []
    for batch in batches:
        first = batch[0]
        chains = [item["hop_chain"] for item in batch if item.get("hop_chain") is not None]
        chain = max(chains, key=len) if chains else None
        address = sender_address_now(first, chats)  # a resume while the message waited moved the sender
        messages.append((address, envelope(address, first["sender_name"], first["sender_class"],
                                           merged_body(batch, now), chain)))
    if untested:
        # The receiver changed version while these waited, and the flush can not make a native send. So it tries the
        # post, then saves the text and hands it back. Only then does it empty the file, so an error here keeps the
        # messages held.
        try:
            post(receiver["socket"], messages)
        except OSError as error:
            log_event("post-failed", receiver=items[0]["receiver_name"], error=type(error).__name__)
        given_up = give_up(items, "that chat now runs Claude Code %s, which this queue has not tested"
                           % receiver.get("version"), chats, "may_not_have_arrived.md", final)
        write_held(path, [])
        return given_up
    try:
        post(receiver["socket"], messages)
    except OSError as error:
        log_event("post-failed", receiver=items[0]["receiver_name"], error=type(error).__name__)
        if past_limit:
            given_up = give_up(items, "the post failed: " + type(error).__name__, chats, final=final)
            write_held(path, [])
            return given_up
        write_held(path, items)
        return 0
    for batch in batches:
        first = batch[0]
        for item in batch:
            log_event("delivered", wait_seconds=round(now - item["held_at"], 1), receiver=first["receiver_name"],
                      sender=first["sender_name"], reason=reason, limit=settings["limit"])
        log_event("posted", messages=len(batch), reason=reason, receiver=first["receiver_name"],
                  sender=first["sender_name"])
    # Post before this rewrite. A crash between the two sends a copy with the same sent time, and a copy is
    # acceptable where a loss is not.
    write_held(path, [])
    return 0


def beat(now):
    """Writes the heartbeat, and logs a long gap so the report can tell a sleeping machine from a late message."""
    path = queue_file("heartbeat")
    every = remembered().get("every") or EVERY_SECONDS
    try:
        gap = now - os.path.getmtime(path)
        if gap > 3 * every:
            log_event("gap", start=round(now - gap, 1), end=round(now, 1))
    except OSError:
        pass
    with open(path, "a"):
        written = clock.epoch()
        os.utime(path, (written, written))


def find_claude():
    """The flush job may run with a short PATH, so it adds the folder where a hook last found claude."""
    if shutil.which("claude") is None and remembered().get("claude_dir"):
        os.environ["PATH"] = remembered()["claude_dir"] + os.pathsep + os.environ.get("PATH", "")


def held_paths():
    return sorted(glob.glob(queue_file("held", "*.jsonl")))


def flush(receiver_session=None, force=False, heartbeat=False, final=False, lock_wait=LOCK_WAIT_SECONDS):
    """Returns how many messages it gave up."""
    given_up = 0
    with QueueLock(lock_wait):
        now = clock.epoch()
        find_claude()
        chats = live_chats()
        paths = [held_file(receiver_session)] if receiver_session else held_paths()
        failed = False
        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                given_up += flush_file(path, chats, force, now, final)
            except Exception as error:  # one bad file must not stop the others
                failed = True
                log_event("flush-failed", error=type(error).__name__)
        if heartbeat and not failed:
            beat(now)
    return given_up


def keep_unlocked(paths):
    """Last resort when a stop never gets the lock. Every held message moves to the undelivered file, and its sender
    is told at its next start or turn. A flush that is still running may post one of them as well. A copy is better
    than a loss. Returns how many."""
    given_up = 0
    for path in paths:
        try:
            with open(path) as handle:
                items = [json.loads(line) for line in handle if line.strip()]
        except (OSError, ValueError) as error:
            log_event("stop-could-not-read", error=type(error).__name__)
            continue
        given_up += give_up(items, "the queue stopped while another flush held its lock", None, final=True)
        os.unlink(path)
    return given_up


def stop():
    """Turns the queue off, then delivers what waits. Anything it can not deliver is saved, and one line says so.
    Exits 1 if a message is still held."""
    command(["off"])  # first, so nothing new is held while the last flush runs
    given_up = 0
    for attempt in range(STOP_FLUSHES):
        try:
            given_up += flush(force=True, final=attempt == STOP_FLUSHES - 1, lock_wait=STOP_LOCK_WAIT_SECONDS)
        except BlockingIOError:
            log_event("stop-waited", attempt=attempt + 1)
            continue
        if not held_paths():
            break
    if held_paths():
        given_up += keep_unlocked(held_paths())
    if given_up:
        count = "1 queued message" if given_up == 1 else f"{given_up} queued messages"
        print(text("stopped_undelivered.md").format(count=count, saved=queue_file("undelivered.jsonl")))
    return 1 if held_paths() else 0


# the report

def over_limit(waits, gaps, limit):
    """Returns how many messages waited past the limit, and how many of those a sleep or flush gap explains: they
    would have been on time without it. A sleeping machine delays a message all the same, so it counts as late."""
    late, across_gap = 0, 0
    for at, wait, own_limit in waits:
        own_limit = own_limit or limit
        if wait <= own_limit:
            continue
        late += 1
        asleep = sum(max(0, min(end, at) - max(start, at - wait)) for start, end in gaps)
        if wait - asleep <= own_limit:
            across_gap += 1
    return late, across_gap


def transcript_counts(root, start):
    """Message wakes and messages sent by main chats, read the same way before and after the queue exists.
    A wake is a run of user entries whose origin is another chat, with no assistant entry between them, because the
    chat takes the whole run in one turn. A message that arrives during a turn is an attachment and wakes nothing.
    A sent message is a SendMessage whose result names another chat or says it was queued, so messages to agents do
    not count."""
    queued = re.escape(text("queued.md").split("{")[0].strip())
    sent_result = re.compile(r"another Claude session|uds:|" + queued)
    wakes, sent = 0, 0
    pattern = os.path.join(glob.escape(root), "*.jsonl")
    nested = os.path.join(glob.escape(root), "*", "*.jsonl")
    for path in glob.glob(pattern) + glob.glob(nested):
        if os.path.getmtime(path) < start:
            continue
        send_ids = set()
        in_wake = False
        with open(path, errors="replace") as handle:
            for line in handle:
                if '"origin"' not in line and '"assistant"' not in line and "SendMessage" not in line \
                        and "tool_result" not in line:
                    continue
                try:
                    entry = json.loads(line)
                    stamp = calendar.timegm(time.strptime(entry["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
                except (ValueError, KeyError, TypeError):
                    continue
                if stamp < start or entry.get("isSidechain"):
                    continue
                content = (entry.get("message") or {}).get("content")
                blocks = content if isinstance(content, list) else []
                if entry.get("type") == "assistant":
                    in_wake = False
                    send_ids.update(block.get("id") for block in blocks
                                    if isinstance(block, dict) and block.get("name") == "SendMessage")
                elif entry.get("type") == "user":
                    if (entry.get("origin") or {}).get("kind") == "peer":
                        wakes += 0 if in_wake else 1
                        in_wake = True
                    for block in blocks:
                        if isinstance(block, dict) and block.get("type") == "tool_result" \
                                and block.get("tool_use_id") in send_ids:
                            if sent_result.search(json.dumps(block.get("content"))):
                                sent += 1
    return wakes, sent


def report(days, root):
    start = time.time() - days * 86400
    events = []
    try:
        with open(queue_file("events.jsonl")) as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("at", 0) >= start:
                    events.append(event)
    except OSError:
        pass
    passed = {}
    for event in events:
        if event.get("event") == "passed":
            passed[event.get("reason")] = passed.get(event.get("reason"), 0) + 1
    waits = [(event["at"], event["wait_seconds"], event.get("limit")) for event in events
             if event.get("event") == "delivered"]
    gaps = [(event["start"], event["end"]) for event in events if event.get("event") == "gap"]
    posts = [event for event in events if event.get("event") == "posted"]

    def count(name):
        return sum(1 for event in events if event.get("event") == name)

    print("last %d days" % days)
    print("held %d, delivered %d in %d posts, passed at once %d %s" % (
        count("held"), len(waits), len(posts), sum(passed.values()), json.dumps(passed, sort_keys=True)))
    late, across_gap = over_limit(waits, gaps, LIMIT_SECONDS)
    print("longest wait %.0f s, past the limit %d, %d of them across a sleep or flush gap" % (
        max([wait for _, wait, _ in waits] or [0]), late, across_gap))
    print("undelivered %d, machine asleep or flush down %d times" % (count("undelivered"), len(gaps)))
    if root:
        wakes, sent = transcript_counts(root, start)
        print("message wakes %d, messages sent %d, wakes per message %.2f" % (wakes, sent,
                                                                              wakes / sent if sent else 0))


# versions

def claude_version():
    """The version `claude --version` prints, or None."""
    program = shutil.which("claude")
    if program is None:
        return None
    try:
        process = subprocess.run([program, "--version"], capture_output=True, text=True,
                                 timeout=VERSION_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = VERSION.search(process.stdout) if process.returncode == 0 else None
    return found.group(1) if found else None


# the commands

def option(arguments, name, default=None):
    if name not in arguments:
        return default
    index = arguments.index(name) + 1
    if index >= len(arguments):
        sys.exit(USAGE)
    return arguments[index]


def command(arguments):
    name = arguments[0] if arguments else ""
    if name == "flush":
        session = option(arguments, "--receiver")
        flush(receiver_session=session, force="--force" in arguments,
              heartbeat=session is None and "--no-heartbeat" not in arguments)
    elif name == "report":
        report(int(option(arguments, "--days", "7")), option(arguments, "--root"))
    elif name == "off":
        os.makedirs(queue_file(), mode=0o700, exist_ok=True)
        open(queue_file("off"), "a").close()
    elif name == "on":
        if os.path.exists(queue_file("off")):
            os.unlink(queue_file("off"))
    elif name == "stop":
        sys.exit(stop())
    else:
        sys.exit(USAGE)


if __name__ == "__main__":
    command(sys.argv[1:])
