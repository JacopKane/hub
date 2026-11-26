"""The signal log the tuner reads, ~/.local/state/claude-hub/signals.jsonl.

A line holds a flag id, an event name, a signal name and the UTC time. The
call takes no text, so chat text can never reach the log.
"""
from . import files
from .resolve import now


def log(flag_id, event, signal):
    files.append_line(files.state_path("signals.jsonl"),
                      {"flag": str(flag_id), "event": str(event), "signal": str(signal), "time": now()})
