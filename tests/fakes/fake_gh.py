#!/usr/bin/env python3
"""A stand-in for gh in the PR test gate scenarios. It never reaches GitHub.

It reads its answers from the JSON file in $FAKE_GH_CONFIG and adds each call's
words to $FAKE_GH_LOG, one JSON list a line. The keys:

- open: {branch: PR number} for `gh pr list --head <branch> --state open`; the PR's head commit is the one
  origin's bare repo has for the branch, as GitHub would say
- heads: {PR number or branch: head commit} for `gh pr view [<it>] --json headRefOid,baseRefName`; with no
  number, gh's own way to find the current branch's PR, the branch checked out in the folder is the key; with
  -R or --repo, or else a GH_REPO that is not empty, the key is <repo>#<number>
- bases: {PR number or branch: the branch the PR goes into}, main when not named
- default: the repo's default branch for `gh api repos/<owner>/<repo> --jq .default_branch`, main when not named
- sleep: seconds to wait before answering, for a slow network
- fail: true makes every call exit 1, as when gh can not reach GitHub

`gh api repos/<owner>/<repo>/git/ref/heads/<branch>`, or the same under repositories/<id>, answers with the
commit origin's bare repo has for the branch, and fails like GitHub's 404 when it has none. A GraphQL query for
a repo node's ref (-f ref=refs/heads/<branch>) answers the same way, with null when origin has none, and one for
its defaultBranchRef answers like default.

Every other call exits 0 with no output.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

REPO = r"(?:repos/[^/]+/[^/]+|repositories/\d+)"  # the two ways GitHub's REST API names a repo
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


def repo_named():
    """The repo picked with -R or --repo, in any spelling gh reads, else GH_REPO when it is not empty, or None."""
    attached = [word[len("--repo="):] if word.startswith("--repo=") else word[2:].lstrip("=") for word in words
                if word.startswith("--repo=") or (word.startswith("-R") and len(word) > 2)]
    separate = [value for option, value in zip(words, words[1:]) if option in ("-R", "--repo")]
    return (separate + attached or [None])[-1] or os.environ.get("GH_REPO") or None


def pushed_head(branch):
    """The commit origin's bare repo has the branch at, or None."""
    done = subprocess.run(["git", "ls-remote", "origin", f"refs/heads/{branch}"], capture_output=True, text=True)
    return next((line.split()[0] for line in done.stdout.splitlines() if line.endswith(f"\trefs/heads/{branch}")),
                None)


def base_of(key):
    return config.get("bases", {}).get(key, "main")


if words[:2] == ["pr", "list"]:
    branch = value_of("--head")
    number = config.get("open", {}).get(branch)
    print(json.dumps([{"number": number, "baseRefName": base_of(branch), "headRefOid": pushed_head(branch)}]
                     if number else []))
elif words[:2] == ["pr", "view"]:
    key = words[2] if len(words) > 2 and not words[2].startswith("-") else current_branch()
    key = f"{repo_named()}#{key}" if repo_named() else key
    head = config.get("heads", {}).get(key)
    if head is None:
        print(f"no pull requests found for {key}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"headRefOid": head, "baseRefName": base_of(key)}))
elif words[:2] == ["api", "graphql"]:
    fields = dict(value.split("=", 1) for option, value in zip(words, words[1:]) if option == "-f" and "=" in value)
    query = fields.get("query", "")
    if "ref(qualifiedName" in query:
        print(pushed_head(fields.get("ref", "").removeprefix("refs/heads/")) or "null")
    elif "defaultBranchRef" in query:
        print(config.get("default", "main"))
elif words[:1] == ["api"] and len(words) > 1:
    ref = re.fullmatch(REPO + r"/git/ref/heads/(.+)", words[1])
    if ref:
        commit = pushed_head(urllib.parse.unquote(ref.group(1)))
        if commit is None:
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        print(commit if value_of("--jq") == ".object.sha" else json.dumps({"object": {"sha": commit}}))
    elif re.fullmatch(REPO, words[1]) and value_of("--jq") == ".default_branch":
        print(config.get("default", "main"))
