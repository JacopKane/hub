#!/usr/bin/env python3
"""Stand-in for `claude` that serves `claude agents --json` from a file the test writes.

$HUB_FAKE_LISTING holds {"rows": [...], "delay": seconds, "exit": code}, and "raw" prints
its text in place of the rows.
Each listing call adds a line to the same path plus ".calls". `--version`
prints the same path plus ".version" when the test wrote one. Every other
call goes to the recorded replay beside this file (claude-replay), so a test
still never reaches a real sign-in or a live chat.
"""
import json
import os
import sys
import time

if sys.argv[1:] == ["agents", "--json"]:
    path = os.environ["HUB_FAKE_LISTING"]
    with open(path + ".calls", "a") as handle:
        handle.write("call\n")
    with open(path) as handle:
        listing = json.load(handle)
    time.sleep(listing.get("delay", 0))
    if listing.get("exit"):
        print("claude agents: failed", file=sys.stderr)
        sys.exit(listing["exit"])
    if "raw" in listing:  # a reply that is not a list, or not JSON at all
        sys.stdout.write(listing["raw"])
    else:
        print(json.dumps(listing["rows"]))
    sys.exit(0)
version_file = os.environ.get("HUB_FAKE_LISTING", "") + ".version"
if sys.argv[1:] == ["--version"] and os.path.exists(version_file):
    with open(version_file) as handle:
        sys.stdout.write(handle.read())
    sys.exit(0)
replay = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude-replay")
os.execv(sys.executable, [sys.executable, replay, *sys.argv[1:]])
