#!/usr/bin/env python3
"""Check each commit of a PR on its own, for four things:

- the time zone of the author and committer dates. The GitHub noreply
  address must carry +0000, so a commit does not show where it was made.
- the message, with the shape scan's classes. Trailers are part of the
  message, so a real-looking address in a trailer fails. GitHub noreply
  addresses are allowed.
- purity. A commit that changes .hub/maintainers or .hub/revoked changes
  nothing else. A commit that changes a file named in .hub/checker-files
  changes only those files, the list itself and the plugin version.
- symlinks. A commit may not add or change one.

With --squash it also checks purity for the whole range as one change, the
way a squash merge lands it on main.

It prints the short commit id, or "squash", then the check and the line or
path of each hit, never an address or a value.

    commit_check.py RANGE             for example main..HEAD or <sha>^!
    commit_check.py --squash BASE..HEAD

Exit 0 when clean, 1 with hits, 2 on a usage error or a bad range.
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scrub_scan  # noqa: E402

NOREPLY = re.compile(r"^\d+\+[A-Za-z0-9-]+@users\.noreply\.github\.com$")
TRUST_FILES = {".hub/maintainers", ".hub/revoked"}
CHECKER_LIST = ".hub/checker-files"
VERSION_FILE = "plugins/hub/.claude-plugin/plugin.json"
SYMLINK_MODE = "120000"


class BadRange(Exception):
    pass


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True)
    if result.returncode != 0:
        raise BadRange()
    return result.stdout.decode("utf-8", errors="replace")


def commits(spec):
    return git("rev-list", "--reverse", spec, "--").split()


def allow_noreply(kind, match):
    if kind == "email" and NOREPLY.match(match.group(0)):
        return True
    return scrub_scan.allowed(kind, match)


def offset_hits(sha):
    fields = git("show", "-s", "--date=raw", "--format=%ae%x00%ad%x00%ce%x00%cd", sha).strip().split("\0")
    for role, email, date in (("author", fields[0], fields[1]), ("committer", fields[2], fields[3])):
        offset = date.split()[-1]
        if NOREPLY.match(email) and offset != "+0000":
            yield f"{role}_offset: {offset}"


def message_hits(sha):
    message = git("show", "-s", "--format=%B", sha)
    for number, kind in scrub_scan.scan_text(message, allow=allow_noreply):
        yield f"message line {number}: {kind}"


def changes(sha):
    """Return (path, new mode) for each file the commit changes.

    A merge commit counts only the files that differ from every parent, so
    the changes it brings in from a parent are checked where they were made.
    """
    return parse_raw(git("diff-tree", "-r", "-z", "-c", "--no-renames", "--root", "--no-commit-id", sha))


def parse_raw(raw):
    parts = raw.split("\0")
    found = []
    for index in range(0, len(parts) - 1, 2):
        parents = len(parts[index]) - len(parts[index].lstrip(":"))
        fields = parts[index].lstrip(":").split()
        if parents and len(fields) == 2 * parents + 3:
            found.append((parts[index + 1], fields[parents]))
    return found


def checker_files(*trees):
    """The checker's files named in the list, in each of the trees."""
    names = {CHECKER_LIST}
    for tree in trees:
        try:
            text = git("show", f"{tree}:{CHECKER_LIST}")
        except BadRange:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line)
    return names


def purity_hits(trees, paths):
    if paths & TRUST_FILES and paths - TRUST_FILES:
        yield "mixed_trust: " + ", ".join(sorted(paths))
        return
    checker = checker_files(*trees)
    if paths & checker:
        outside = paths - checker - {VERSION_FILE}
        if outside:
            yield "mixed_checker: " + ", ".join(sorted(outside))


def check(sha):
    changed = changes(sha)
    yield from offset_hits(sha)
    yield from message_hits(sha)
    yield from purity_hits((sha, sha + "^"), {path for path, _ in changed})
    for path, mode in changed:
        if mode == SYMLINK_MODE:
            yield f"symlink: {path}"


def squash_hits(spec):
    """Purity of the change a squash merge of the range lands, from the merge base to the head."""
    if spec.count("..") != 1 or "..." in spec:
        raise BadRange()
    base, head = (git("rev-parse", "--verify", (side or "HEAD") + "^{commit}").strip() for side in spec.split(".."))
    start = git("merge-base", base, head).strip()
    changed = parse_raw(git("diff-tree", "-r", "-z", "--no-renames", start, head))
    for hit in purity_hits((head, start), {path for path, _ in changed}):
        yield f"squash: {hit}"


def main(argv):
    squash = argv[:1] == ["--squash"]
    argv = argv[1:] if squash else argv
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        hits = [f"{sha[:7]}: {hit}" for sha in commits(argv[0]) for hit in check(sha)]
        if squash:
            hits += list(squash_hits(argv[0]))
    except BadRange:
        print(f"commit_check: not a range git knows: {argv[0]}", file=sys.stderr)
        return 2
    for hit in hits:
        print(hit)
    if hits:
        print(f"commit_check: {len(hits)} hit(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
