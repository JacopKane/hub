#!/usr/bin/env python3
"""Stand-in for the `claude` program in tests.

It prints an output recorded once from the real program, looked up by its
arguments in $HUB_REPLAY_DIR/index.json, and fails for anything not recorded,
so a test can never reach a real sign-in or a live chat.
"""
import json
import os
import sys

folder = os.environ.get("HUB_REPLAY_DIR", "")
key = " ".join(sys.argv[1:])
try:
    with open(os.path.join(folder, "index.json")) as handle:
        name = json.load(handle)[key]
    with open(os.path.join(folder, name)) as handle:
        sys.stdout.write(handle.read())
except (OSError, KeyError, ValueError):
    print(f"claude replay: nothing recorded for '{key}'", file=sys.stderr)
    sys.exit(97)
