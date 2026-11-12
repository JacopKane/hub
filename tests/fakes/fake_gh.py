#!/usr/bin/env python3
"""A stand-in for gh in the PR test gate scenarios. It never reaches GitHub.

It reads its answers from the JSON file in $FAKE_GH_CONFIG and adds each call's
words to $FAKE_GH_LOG, one JSON list a line. The keys:

- open: {branch: PR number} for `gh pr list --head <branch> --state open`
- heads: {PR number or branch: head commit} for `gh pr view <it> --json headRefOid`
- sleep: seconds to wait before answering, for a slow network
- fail: true makes every call exit 1, as when gh can not reach GitHub

Every other call exits 0 with no output.
"""
import json
import os
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


if words[:2] == ["pr", "list"]:
    number = config.get("open", {}).get(value_of("--head"))
    print(json.dumps([{"number": number}] if number else []))
elif words[:2] == ["pr", "view"] and len(words) > 2:
    head = config.get("heads", {}).get(words[2])
    if head is None:
        print(f"no pull requests found for {words[2]}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"headRefOid": head}))
