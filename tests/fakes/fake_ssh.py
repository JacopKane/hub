#!/usr/bin/env python3
"""A stand-in for ssh in the PR test gate scenarios. It never connects.

`ssh -G -- <host>` prints the settings ssh would use for the host, as the real
one does after it reads its config. The JSON file in $FAKE_SSH_CONFIG says
which Host aliases name another host:

- hosts: {alias: the HostName ssh connects to}; any other host names itself
- fail: hosts whose config ssh can not read, which exit 255

Each call's words go to $FAKE_SSH_LOG, one JSON list a line. Every other call
exits 255, as when ssh can not connect.
"""
import json
import os
import sys

words = sys.argv[1:]
config = {}
if os.environ.get("FAKE_SSH_CONFIG") and os.path.exists(os.environ["FAKE_SSH_CONFIG"]):
    with open(os.environ["FAKE_SSH_CONFIG"]) as handle:
        config = json.load(handle)
if os.environ.get("FAKE_SSH_LOG"):
    with open(os.environ["FAKE_SSH_LOG"], "a") as handle:
        handle.write(json.dumps(words) + "\n")
if len(words) != 3 or words[:2] != ["-G", "--"] or words[2] in config.get("fail", []):
    print("ssh: could not read the config", file=sys.stderr)
    sys.exit(255)
host = words[2]
print(f"host {host}\nhostname {config.get('hosts', {}).get(host, host)}\nport 22")
