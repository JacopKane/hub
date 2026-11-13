#!/usr/bin/env python3
"""The one hook for every event.

    python3 -I dispatch.py <Claude Code event name>   reads the hook input on stdin

It reads resolved.json (resolving again first when a settings file or a
manifest changed), runs the handler of each part that runs for this event and
tool, in tree order with the gates last, and prints one joined answer in Claude Code's keys. It
imports a handler only when its part runs. The first refusal wins, then the first ask (Claude
Code asks the user before the call runs), warnings
and context are joined, a rewrite passes to the next handler, and signals are
logged. An event with no running handler exits at once with no output,
except for notices kept for the chat (chat_notices). Those go at its next
SessionStart or UserPromptSubmit, whichever parts run.

A second run of the same tool call (the same session, event and call id), as
when two copies of the plugin load, waits for the first, returns its stored
answer and logs nothing. After hubctl uninstall it does nothing at all. At
SessionStart it also does its own work, see session_start. An error goes to
errors.log in the state folder. Then a call that touches hub itself is refused, because self_protection
could not check it, and so is a public step, because the gate could not check it. In Claude Code, a Slack
call off the Slack gate's read list is refused, and a Sentry call off the Sentry gate's read list asks the
user first, even when those gates are off. Every other call runs, so a broken hub never stops ordinary work.
"""
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import traceback

LIB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import chat_notices, files, resolve, signals, uninstall  # noqa: E402

CALLS = "calls"
CALL_AGE = 24 * 3600  # stored answers older than this are removed
GATE = "public_text_gate"
SELF_PROTECTION = "self_protection"
# Used only when self_protection itself can not load: a call that names the switch step, a marker or a hub file.
ROUGH_HUB = re.compile(r"hubctl|hub_switch|AI_AGENT|CURSOR_AGENT|HUB_DOOR|\.hub/hub")
# Used only when the gate's own parser can not load: any git or gh call counts as public.
ROUGH_PUBLIC = re.compile(r"\b(?:git|gh)\b")
BROKEN = ("hub could not read its settings or parts, so it stopped this public step. "
          "Fix the error below, then send the command again.")
BROKEN_SELF = ("hub could not read its settings or parts, so it stopped this call, which touches hub itself. "
               "Ask the user to fix hub.")
# Each write gate for MCP tools, with the word in its server names and its answer when hub fails before
# the gate can decide. A call whose server name has that word gets that answer, unless the gate's read
# list shows it is a read.
WRITE_GATES = {
    "slack_write_gate": ("slack", "refusal", "hub could not read its settings or parts, so it could not check "
                                             "this Slack call and stopped it. Fix the error below, then send the "
                                             "call again."),
    "sentry_write_gate": ("sentry", "ask", "hub could not read its settings or parts, so it could not check this "
                                           "Sentry call. Run it only if the user asked for this exact action in "
                                           "this chat. The error is below."),
}


class Flags:
    """What a handler may ask about flags. Values are after the part's mode caps them."""

    def __init__(self, section, texts, tool, profile):
        self._flags = section["flags"]
        self._texts = texts
        self.tool = tool
        self.profile = profile
        self.state_dir = files.state_dir()

    def runs(self, flag_id):
        return bool(self._flags.get(flag_id, {}).get("runs"))

    def record(self, flag_id):
        """A flag's resolved record (value, set, by, scope) across every scope, or None."""
        return self._flags.get(flag_id)

    def value(self, flag_id):
        """The value of a flag that runs, or None."""
        record = self._flags.get(flag_id)
        return record["value"] if record and record["runs"] else None

    def text(self, flag_id):
        """The text file of an instruction flag that runs, or ""."""
        path = self._texts.get(flag_id)
        if not path or not self.runs(flag_id):
            return ""
        try:
            with open(path, encoding="utf-8") as handle:
                return files.fill_step(handle.read().strip())
        except OSError:
            return ""


class Answer:
    """What the handlers of one event say, before it is put in a tool's keys."""

    def __init__(self):
        self.refusal = None
        self.question = None
        self.warnings = []
        self.contexts = []
        self.updated_input = None
        self.signals = []
        self.notices = []
        self.callbacks = []

    def after_all(self, callback):
        """Run callback(answer) once every part has answered, so a part can see whether a later part refused."""
        self.callbacks.append(callback)

    def refuse(self, text):
        if self.refusal is None:
            self.refusal = text

    def ask(self, text):
        """Ask the user before the call runs. A refusal outranks it."""
        if self.question is None:
            self.question = text

    def warn(self, flag_id, finding, hint=""):
        self.warnings.append(f"{finding} ({flag_id})" + (f"\n{hint}" if hint else ""))

    def add_context(self, text):
        if text:
            self.contexts.append(text)

    def rewrite(self, tool_input):
        self.updated_input = tool_input

    def signal(self, flag_id, name):
        self.signals.append((flag_id, name))

    def notify(self, text):
        """A line for the user, not the model. Claude Code shows it as a system message, Cursor as the first line."""
        if text:
            self.notices.append(text)

    def as_dict(self):
        context = "\n\n".join(self.warnings + self.contexts)
        result = {}
        if self.refusal is not None:
            result["refusal"] = self.refusal
        elif self.question is not None:
            result["ask"] = self.question
        if context:
            result["context"] = context
        if self.updated_input is not None:
            result["updated_input"] = self.updated_input
        if self.notices:
            result["notice"] = "\n".join(self.notices)
        return result


def load_handler(part):
    path = os.path.join(files.PARTS_DIR, part, "handler.py")
    spec = importlib.util.spec_from_file_location(f"hub_part_{part}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def running_parts(section, event, tool_name):
    return [entry["part"] for entry in section["handlers"].get(event, [])
            if not entry["tools"] or tool_name in entry["tools"]]


def call_key(event, hook_input):
    call = hook_input.get("tool_use_id")
    if not call:
        return None
    raw = "\0".join([event, str(hook_input.get("session_id", "")), str(call)])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def prune_calls(folder):
    cutoff = time.time() - CALL_AGE
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            pass


def handle_all(event, hook_input, parts, flags):
    answer = Answer()
    current_input = hook_input
    for part in parts:
        load_handler(part).handle(event, current_input, flags, answer)
        if answer.refusal is not None:
            break
        if answer.updated_input is not None:
            current_input = dict(current_input, tool_input=answer.updated_input)
    for callback in answer.callbacks:
        callback(answer)
    for flag_id, name in answer.signals:
        signals.log(flag_id, event, name)
    return answer.as_dict()


def session_start(tool, session=None):
    """The dispatcher's own SessionStart work in Claude Code.

    A session that starts now watches ~/.claude/agents, so the doctor's note
    that hub made that folder goes, unless this same session start made it.
    When apply changed a skill link since the last session start, the answer
    asks Claude Code to load the skills again.
    """
    if tool != "claude_code":
        return {}
    from hub import links  # apply loads links anyway; this only keeps dispatch itself light
    note = files.read_json(files.state_path(links.AGENTS_FOLDER_MADE))
    if note is not None and (not session or note.get("session") != session):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(files.state_path(links.AGENTS_FOLDER_MADE))
    try:
        os.unlink(files.state_path(links.RELOAD_SKILLS))
    except FileNotFoundError:
        return {}
    return {"reload_skills": True}


def with_notices(result, notices):
    """The answer with the chat's kept notices first in its context."""
    if not notices:
        return result
    result = dict(result or {})
    result["context"] = "\n\n".join(part for part in (notices, result.get("context")) if part)
    return result


def run(event, hook_input, tool="claude_code"):
    """The neutral answer for one event, or None when no handler runs or hub was uninstalled."""
    if uninstall.is_out():
        return None
    notices = ""
    if tool == "claude_code" and event in chat_notices.EVENTS and not hook_input.get("agent_id"):
        notices = chat_notices.take(hook_input.get("session_id"))
    try:
        return with_notices(answer_for(event, hook_input, tool), notices)
    except Exception:
        if notices:  # the error answer has no room for them, so they wait for the next hook
            chat_notices.leave(hook_input.get("session_id"), notices)
        raise


def answer_for(event, hook_input, tool):
    session = hook_input.get("session_id")
    data = resolve.current(hook_input.get("cwd"), session)
    extra = session_start(tool, session) if event == "SessionStart" else {}
    section = data["tools"][tool]
    parts = running_parts(section, event, hook_input.get("tool_name"))
    if not parts:
        return extra or None
    flags = Flags(section, data.get("texts", {}), tool, resolve.load_profile())
    key = call_key(event, hook_input)
    if key is None:
        return dict(handle_all(event, hook_input, parts, flags), **extra)
    stored_path = files.state_path(os.path.join(CALLS, key + ".json"))
    with files.locked(os.path.join(CALLS, key + ".lock")):
        stored = files.read_json(stored_path)
        if stored is not None:
            return stored
        result = handle_all(event, hook_input, parts, flags)
        files.write_json(stored_path, result)
    if int.from_bytes(os.urandom(1), "big") < 8:  # about one run in 32
        prune_calls(os.path.dirname(stored_path))
    return result


def claude_output(event, result):
    """Put a neutral answer in Claude Code's keys. An empty answer prints nothing."""
    if not result:
        return None
    specific = {"hookEventName": event}
    output = {}
    if "refusal" in result:
        if event == "PreToolUse":
            specific.update(permissionDecision="deny", permissionDecisionReason=result["refusal"])
        else:
            output.update(decision="block", reason=result["refusal"])
    elif "ask" in result and event == "PreToolUse":
        specific.update(permissionDecision="ask", permissionDecisionReason=result["ask"])
    if "context" in result:
        specific["additionalContext"] = result["context"]
    if "updated_input" in result and event == "PreToolUse":
        specific["updatedInput"] = result["updated_input"]
    if result.get("reload_skills") and event == "SessionStart":
        specific["reloadSkills"] = True
    if len(specific) > 1:
        output["hookSpecificOutput"] = specific
    if "notice" in result:
        output["systemMessage"] = result["notice"]
    return output or None


def is_public_step(hook_input, raw):
    """Whether a call that hub failed on would make text public."""
    if not isinstance(hook_input, dict):
        return bool(ROUGH_PUBLIC.search(raw or ""))
    if hook_input.get("tool_name") != "Bash":
        return False
    command = (hook_input.get("tool_input") or {}).get("command") or ""
    try:
        return bool(load_handler(GATE).public_steps(command, hook_input.get("cwd") or os.getcwd()))
    except Exception:
        return bool(ROUGH_PUBLIC.search(command))


def write_gate_failure(hook_input):
    """(answer key, text) for a call a write gate would have checked, or None."""
    tool_name = hook_input.get("tool_name") if isinstance(hook_input, dict) else None
    if not isinstance(tool_name, str):
        return None
    server = tool_name.rsplit("__", 1)[0]
    for part, (word, key, text) in WRITE_GATES.items():
        if not (server.startswith("mcp__") and word in server.lower()):
            continue
        try:
            if load_handler(part).is_read(hook_input):
                return None
        except Exception:  # without its read list, every call to that server counts as a write
            pass
        return key, text
    return None


def self_protection_refusal(hook_input, raw):
    """When hub failed: self_protection's refusal with every check on, or a rough one when it can not load."""
    try:
        return load_handler(SELF_PROTECTION).refusal_without_settings(hook_input)
    except Exception:
        text = json.dumps(hook_input.get("tool_input")) if isinstance(hook_input, dict) else raw
        return BROKEN_SELF if ROUGH_HUB.search(text or "") else None


def failure(event, hook_input, raw, error, tool="claude_code"):
    """The neutral answer after an error: a refusal for a call that touches hub or a public step, a write
    gate's answer, else nothing."""
    if event != "PreToolUse":
        return None
    detail = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    own = self_protection_refusal(hook_input, raw)
    if own:
        return {"refusal": f"{own}\n{detail}"}
    if is_public_step(hook_input, raw):
        return {"refusal": f"{BROKEN}\n{detail}"}
    gate = write_gate_failure(hook_input) if tool == "claude_code" else None
    if gate is None:
        return None
    key, text = gate
    return {key: f"{text}\n{detail}"}


def log_error():
    try:
        files.append_text(files.state_path("errors.log"),
                          f"{resolve.now()} dispatch\n{traceback.format_exc()}\n")
    except OSError:
        pass


def main(argv):
    if len(argv) != 1:
        print("usage: dispatch.py <event>", file=sys.stderr)
        return 2
    event = argv[0]
    raw, hook_input = "", None
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw)
        output = claude_output(event, run(event, hook_input))
    except Exception as error:  # a broken hub stops only public steps and the calls a write gate would check
        log_error()
        output = claude_output(event, failure(event, hook_input, raw, error))
    if output:
        sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
