#!/usr/bin/env python3
"""A stand-in for gh in the PR test gate scenarios. It never reaches GitHub.

It reads its answers from the JSON file in $FAKE_GH_CONFIG and adds each call's
words to $FAKE_GH_LOG, one JSON list a line. The keys:

- open: {branch: PR} for `gh pr list --head <branch> --state open`, where a PR is its number, or
  {"number": n, "owner": login, "head": commit}, or a list of those, as GitHub lists every open PR whose head
  branch has that name in any fork. The head commit is the one origin's bare repo has for the branch, and the
  head repo's owner (headRepositoryOwner) is the owner of the repo listed, acme for gh's own repo, unless the PR
  names them; owner null says GitHub gives none, as for a deleted fork. gh prints only the --json fields asked
  for, and at most --limit PRs
- open_in: {repo: {branch: PR}} for the same list in the repo -R, --repo or GH_REPO names, such as
  github.com/acme/demo; a repo it does not name gets open
- heads: {PR number or branch: head commit} for `gh pr view [<it>] --json headRefOid,baseRefName`; with no
  number, gh's own way to find the current branch's PR, the branch checked out in the folder is the key; with
  -R or --repo, or else a GH_REPO that is not empty, the key is <repo>#<number>
- bases: {PR number or branch: the branch the PR goes into}, main when not named
- default: the repo's default branch for `gh api repos/<owner>/<repo> --jq .default_branch`, main when not named
- parents: {REST path such as repos/acme/fork: [full names]} for `gh api <path> --jq` with the gate's query for
  the repo's own full name and the repos a fork comes from, its parent and its source; a repo it does not name
  is no fork. The own name comes first, from the path
- renamed: {REST path: full name} for a repo GitHub now knows by another owner or name, so the same query gives
  that name first
- unknown_forks: REST paths that the same lookup answers like GitHub's 404 for
- unknown_repos: repos, as GH_REPO names them, that gh api answers like GitHub's 404 for when the endpoint
  says {owner}/{repo}
- issues: plain issues, as numbers or as <GH_REPO>#<number> for one repo only; `gh api <repo>/issues/<n> --jq
  'has("pull_request")'` says false for them and true for any other number, as for a PR
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
GITHUB_NAMES = "[.full_name, .parent.full_name, .source.full_name | strings]"
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


def listed_pr(spec, branch, owner):
    """A PR as gh pr list --json prints it, from a number or a {"number", "owner", "head"} object."""
    spec = spec if isinstance(spec, dict) else {"number": spec}
    login = spec.get("owner", owner)
    return {"number": spec["number"], "baseRefName": base_of(branch),
            "headRefOid": spec.get("head", pushed_head(branch)),
            "headRepositoryOwner": {"id": "", "login": ""} if login is None else {"id": "U_" + login, "login": login}}


if words[:2] == ["pr", "list"]:
    branch = value_of("--head")
    repo = repo_named()
    listed = config.get("open_in", {}).get(repo, config.get("open", {}))
    found = listed.get(branch) or []
    owner = repo.rstrip("/").split("/")[-2] if repo and repo.count("/") >= 1 else "acme"
    fields = (value_of("--json") or "").split(",")
    prs = [listed_pr(spec, branch, owner) for spec in (found if isinstance(found, list) else [found])]
    limit = int(value_of("--limit") or 30)
    print(json.dumps([{key: pr[key] for key in fields if key in pr} for pr in prs][:limit]))
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
elif words[:1] == ["api"] and len(words) > 1 and "{owner}/{repo}" in words[1] \
        and os.environ.get("GH_REPO") in config.get("unknown_repos", []):
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
elif words[:1] == ["api"] and len(words) > 1 and re.fullmatch(r".*/issues/\d+", words[1]) \
        and value_of("--jq") == 'has("pull_request")':
    number = int(words[1].rsplit("/", 1)[1])
    plain = {number, f"{os.environ.get('GH_REPO')}#{number}"} & set(config.get("issues", []))
    print("false" if plain else "true")
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
    elif re.fullmatch(REPO, words[1]) and value_of("--jq") == GITHUB_NAMES:
        if words[1] in config.get("unknown_forks", []):
            print("gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        own = config.get("renamed", {}).get(words[1], words[1].split("/", 1)[1])
        print(json.dumps([own, *config.get("parents", {}).get(words[1], [])]))
