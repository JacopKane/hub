#!/usr/bin/env python3
"""A stand-in for gh in the PR test gate scenarios. It never reaches GitHub.

It reads its answers from the JSON file in $FAKE_GH_CONFIG and adds each call's
words to $FAKE_GH_LOG, one JSON list a line. The keys:

- open: {branch: PR number} for `gh pr list --head <branch> --state open`
- heads: {PR number or branch: head commit} for `gh pr view [<it>] --json headRefOid,baseRefName`; with no
  number, gh's own way to find the current branch's PR, the branch checked out in the folder is the key
- bases: {PR number or branch: the branch the PR goes into}, main when not named
- sleep: seconds to wait before answering, for a slow network
- fail: true makes every call exit 1, as when gh can not reach GitHub

Every other call exits 0 with no output.
"""
import json
import os
import subprocess
import sys
import time

words = sys.argv[1:]
config = {}
if os.environ.get("FAKE_GH_CONFIG") and os.path.exists(os.environ["FAKE_GH_CONFIG"]):
    with open(os.environ["FAKE_GH_CONFIG"]) as handle:
        config = json.load(handle)
if os.environ.get("FAKE_GH_LOG"):
    with open(os.environ["FAKE_GH_LOG"], "a") as handle:
        handle.write(json.dumps(words) + "\n")
time.sleep(config.get("sleep", 0))
if config.get("fail"):
    print("error connecting to api.github.com", file=sys.stderr)
    sys.exit(1)


def value_of(name):
    return words[words.index(name) + 1] if name in words and words.index(name) + 1 < len(words) else None


def current_branch():
    done = subprocess.run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip()


def base_of(key):
    return config.get("bases", {}).get(key, "main")


if words[:2] == ["pr", "list"]:
    branch = value_of("--head")
    number = config.get("open", {}).get(branch)
    print(json.dumps([{"number": number, "baseRefName": base_of(branch)}] if number else []))
elif words[:2] == ["pr", "view"]:
    key = words[2] if len(words) > 2 and not words[2].startswith("-") else current_branch()
    head = config.get("heads", {}).get(key)
    if head is None:
        print(f"no pull requests found for {key}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"headRefOid": head, "baseRefName": base_of(key)}))
