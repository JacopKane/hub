#!/usr/bin/env python3
"""Stand-in for the `claude` program that records each call.

It appends its arguments as one JSON line to $HOME/claude-calls.jsonl.
`claude plugin uninstall <id>` and `claude plugin update <id>` exit 0 and
print nothing, and every other
call fails as the replay does, so a test can never reach the real program.
"""
import json
import os
import sys

with open(os.path.join(os.environ["HOME"], "claude-calls.jsonl"), "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:2] == ["plugin"] and sys.argv[2:3] in (["uninstall"], ["update"]) and len(sys.argv) == 4:
    sys.exit(0)
print(f"claude recorder: nothing recorded for '{' '.join(sys.argv[1:])}'", file=sys.stderr)
sys.exit(97)
