"""The away state that away_guard reads, and the step's `away` verb.

    away set "<reason>" [--hours N]   the user left; it ends after N hours, 12 by default
    away clear                        the user is back
    away status

The user is away while a set away state lasts, during the quiet hours until
they type, or after idle_minutes with no typed prompt and no dialog answer.
Idle time counts from the later of the last activity and the end of the last
away state, so a state that just ended does not turn straight into silence.
The files live in hub's state folder: away.json, last_active.json and
away_urgent.jsonl. A broken file means not away, so a broken file never holds
a question.
"""
import json
import os
import re
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import clock, files  # noqa: E402
from hub.clock import local as zoned_time  # noqa: E402

PART = "away_guard"
STATE = "away.json"
ACTIVE = "last_active.json"
URGENT_LOG = "away_urgent.jsonl"
DEFAULT_HOURS = 12
IDLE_MINUTES = 60
URGENT = re.compile(r"^urgent:\s*(\S.*)", re.S)
WINDOW = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")
USAGE = 'usage: away set "<reason>" [--hours N] | away clear | away status'


class Options:
    """The part's options: idle_minutes, time_zone ("" is the system's) and quiet_hours ("" is off)."""

    def __init__(self, idle_minutes=IDLE_MINUTES, time_zone="", quiet_hours=""):
        try:
            self.idle_minutes = float(idle_minutes)
        except (TypeError, ValueError):
            self.idle_minutes = IDLE_MINUTES
        self.time_zone = str(time_zone or "").strip()
        self.quiet_hours = str(quiet_hours or "").strip()

    @classmethod
    def from_values(cls, value):
        """value(flag_id) gives an option's value, or None."""
        return cls(*(value(f"{PART}.{name}") for name in ("idle_minutes", "time_zone", "quiet_hours")))

    def zone_name(self):
        return self.time_zone if clock.zone(self.time_zone) else "the system time zone"


def show(seconds_at, options):
    return clock.hour_minute(seconds_at, options.time_zone)


def _number(record, key):
    try:
        return float(record[key])
    except (KeyError, TypeError, ValueError):
        return None


def stored():
    """The away record as written, ended or not, or {}."""
    try:
        record = files.read_json(files.state_path(STATE), {})
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def flag(now=None):
    """The set away state while it lasts, or None."""
    now = clock.epoch() if now is None else now
    record = stored()
    since, until = _number(record, "since"), _number(record, "until")
    if since is None or until is None or until <= now:
        return None
    return {"kind": "flag", "since": since, "until": until, "reason": str(record.get("reason") or "")}


def last_active():
    """{"at": the last prompt or answer, "prompt": the last typed prompt}, or {}."""
    try:
        record = files.read_json(files.state_path(ACTIVE), {})
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    return {key: _number(record, key) for key in ("at", "prompt") if _number(record, key) is not None}


def mark_active(prompt):
    """The user typed a prompt (prompt=True) or answered a dialog."""
    now = clock.epoch()
    record = last_active()
    record["at"] = now
    if prompt:
        record["prompt"] = now
    files.write_json(files.state_path(ACTIVE), record)


def set_flag(reason, hours=DEFAULT_HOURS, since=None):
    now = clock.epoch()
    files.write_json(files.state_path(STATE), {"since": now if since is None else since,
                                               "until": now + hours * 3600, "reason": reason})
    return now


def clear():
    """End the away state now. The end time stays, so idle time counts from it."""
    record = stored()
    now = clock.epoch()
    until = _number(record, "until")
    if until is not None and until > now:
        files.write_json(files.state_path(STATE), dict(record, until=now))


def quiet_stretch(now, options):
    """(start, end) in seconds when now is inside the quiet hours, else None."""
    match = WINDOW.match(options.quiet_hours)
    if not match:
        return None
    start_hour, start_minute, end_hour, end_minute = (int(part) for part in match.groups())
    if max(start_hour, end_hour) > 23 or max(start_minute, end_minute) > 59:
        return None
    start_of_window, end_of_window = start_hour * 60 + start_minute, end_hour * 60 + end_minute
    if start_of_window == end_of_window:
        return None
    moment = zoned_time(now, options.time_zone)
    minute = moment.hour * 60 + moment.minute
    since_midnight = minute * 60 + moment.second
    if start_of_window < end_of_window:
        if not start_of_window <= minute < end_of_window:
            return None
        start = now - since_midnight + start_of_window * 60
    elif minute >= start_of_window:
        start = now - since_midnight + start_of_window * 60
    elif minute < end_of_window:
        start = now - since_midnight + start_of_window * 60 - 24 * 3600
    else:
        return None
    return start, start + ((end_of_window - start_of_window) % (24 * 60)) * 60


def current(options):
    """The away state or None: a set state first, then the quiet hours, then silence over the idle limit."""
    now = clock.epoch()
    found = flag(now)
    if found:
        return found
    active = last_active()
    stretch = quiet_stretch(now, options)
    if stretch and active.get("at", 0) < stretch[0]:
        return {"kind": "quiet", "since": stretch[0], "until": stretch[1],
                "reason": f"in the quiet hours {options.quiet_hours} in {options.zone_name()}"}
    at = active.get("at")
    if at is None:
        return None
    base = max(at, _number(stored(), "until") or 0)
    if now - base > options.idle_minutes * 60:
        return {"kind": "idle", "since": base, "until": None, "reason": f"silent since {show(base, options)}"}
    return None


def urgent_reason(source):
    """The why of an "urgent: <why>" mark, or None."""
    match = URGENT.match(source) if isinstance(source, str) else None
    return match.group(1).strip() if match else None


def log_urgent(reason, session_id, cwd):
    files.append_line(files.state_path(URGENT_LOG),
                      {"time": clock.stamp(), "session_id": session_id, "cwd": cwd, "reason": reason})


def urgent_count(since):
    count = 0
    for line in files.read_lines(files.state_path(URGENT_LOG)):
        at = clock.seconds(line.get("time")) if isinstance(line, dict) else None
        if at is not None and at >= since:
            count += 1
    return count


def describe(state, options):
    if state["kind"] == "flag":
        return (f"away since {show(state['since'], options)} until {show(state['until'], options)}, "
                f"{state['reason']}")
    return f"away, {state['reason']}"


def options_here():
    from hub import resolve

    flags = resolve.current(os.getcwd())["tools"]["claude_code"]["flags"]
    return Options.from_values(lambda flag_id: (flags.get(flag_id) or {}).get("value"))


def main(words):
    command = words[0] if words else "status"
    if command == "set":
        reason = words[1].strip() if len(words) > 1 else ""
        hours = DEFAULT_HOURS
        if len(words) == 4 and words[2] == "--hours":
            try:
                hours = float(words[3])
            except ValueError:
                hours = 0
        elif len(words) != 2:
            reason = ""
        if not reason or hours <= 0:
            print(USAGE, file=sys.stderr)
            return 2
        now = set_flag(reason, hours)
        options = options_here()
        print(f"away since {show(now, options)} until {show(now + hours * 3600, options)}, {reason}")
        return 0
    if command == "clear" and len(words) == 1:
        clear()
        print("not away")
        return 0
    if command == "status" and len(words) <= 1:
        options = options_here()
        state = current(options)
        print(describe(state, options) if state else "not away")
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
