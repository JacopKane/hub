"""The step's `pr-night` verb: do steps that notify people wait for the morning?

    pr-night

It prints JSON: "held" (true or false), "line" (what to tell the user, or
""), "time_zone" (the zone it used) and "until" (the end of the quiet hours
as HH:MM, or null). Steps wait only while pr.instructions.night_hold runs and
pr.quiet_hours, such as 22:00-07:00, holds the time now. The time zone is
away_guard's time_zone when away_guard runs and sets one, else the system's,
so a user sets one time zone for both.
"""
import importlib.util
import json
import os
import sys

PART_DIR = os.path.dirname(os.path.abspath(__file__))
PARTS_DIR = os.path.dirname(PART_DIR)
LIB = os.path.join(os.path.dirname(PARTS_DIR), "lib")
for folder in (LIB, PART_DIR):
    if folder not in sys.path:
        sys.path.insert(0, folder)

from hub import clock, resolve  # noqa: E402
from hub.clock import local as zoned_time  # noqa: E402
from texts import text  # noqa: E402

USAGE = "usage: pr-night"


def away_module():
    """away_guard's away.py, for its quiet hours reader."""
    spec = importlib.util.spec_from_file_location("hub_away", os.path.join(PARTS_DIR, "away_guard", "away.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def running_value(flags, flag_id):
    """The flag's value when it runs, else "". A text flag runs only when it is not empty."""
    record = flags.get(flag_id) or {}
    return str(record.get("value") or "").strip() if record.get("runs") else ""


def state(flags, now):
    away = away_module()
    zone = running_value(flags, "away_guard.time_zone")
    zone = zone if clock.zone(zone) else ""
    hours = running_value(flags, "pr.quiet_hours") if (flags.get("pr.instructions.night_hold") or {}).get("runs") else ""
    options = away.Options(time_zone=zone, quiet_hours=hours)
    shown_zone = zone or zoned_time(now).tzname() or "the system time zone"
    match = away.WINDOW.match(hours)
    if not match or away.quiet_stretch(now, options) is None:
        until = f"{int(match.group(3)):02d}:{match.group(4)}" if match else None
        line = text("night_release.md", until=until) if until else ""
        return {"held": False, "line": line, "time_zone": shown_zone, "until": until}
    _start, end = away.quiet_stretch(now, options)
    until = clock.hour_minute(end, zone)
    return {"held": True, "line": text("night_hold.md", time_zone=shown_zone, until=until),
            "time_zone": shown_zone, "until": until}


def main(words):
    if words:
        print(USAGE, file=sys.stderr)
        return 2
    flags = resolve.in_memory(os.getcwd())["claude_code"]["flags"]
    print(json.dumps(state(flags, clock.epoch()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
