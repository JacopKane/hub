#!/usr/bin/env python3
"""One of the eight SessionStart slot hooks.

    python3 -I slot_hook.py <1..8>   reads the hook input on stdin

It applies first when the settings changed (see apply.ensure_fresh), then
prints its slot file of Claude Code's start text as additionalContext. An
empty slot prints nothing, and so does every slot after hubctl uninstall.
An error goes to errors.log, and the hook prints nothing, so a broken hub
never stops a session from starting.
"""
import json
import os
import sys

LIB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from hub import apply, files, resolve, slots, uninstall  # noqa: E402


def main(argv):
    if len(argv) != 1 or not argv[0].isdigit() or not 1 <= int(argv[0]) <= slots.COUNT:
        print(f"usage: slot_hook.py <1..{slots.COUNT}>", file=sys.stderr)
        return 2
    try:
        try:
            session = json.loads(sys.stdin.read()).get("session_id")
        except (ValueError, AttributeError):
            session = None
        if uninstall.is_out():
            return 0
        apply.ensure_fresh(session)
        text = files.read_text(slots.slot_path("claude_code", int(argv[0]))) or ""
    except Exception:  # a broken hub never stops a session
        import traceback
        try:
            files.append_text(files.state_path("errors.log"),
                              f"{resolve.now()} slot_hook {argv[0]}\n{traceback.format_exc()}\n")
        except OSError:
            pass
        return 0
    if text:
        sys.stdout.write(slots.as_json(text) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
