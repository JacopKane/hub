#!/usr/bin/env python3
"""Shape scan for every commit and for the release tree.

It looks for shapes that should never be published: home and temp paths,
email addresses, UUIDs, host-like names, token shapes, the em dash and
links into chat, note and document pages.
It prints the file, the line and the class of each hit, never the value,
and exits 1 when it finds anything.

    scrub_scan.py PATH...      scan files and folders
    scrub_scan.py --staged     scan what is staged in the current git repo
"""
import os
import re
import subprocess
import sys

# Made-up names the tests and docs may use. One list, used by every class.
MADE_UP_NAMES = ["sam", "alex", "acme"]
MADE_UP_DOMAINS = ["example.com", "example.org", "example.net"]
# A UUID made of zeros in its first group is a test value.
MADE_UP_UUID = re.compile(r"^0{8}-")
PAGE_HOSTS = r"(?:claude\.ai|notion\.so|notion\.site|docs\.google\.com|drive\.google\.com)"
# A link into a page host is a test value when its only id is eight or more zeros, perhaps after a page title,
# and every other part of its path is a short word. It may sit under www or a made-up name, and has no query.
MADE_UP_LINK = re.compile(
    r"(?:(?:www|" + "|".join(MADE_UP_NAMES) + r")\.)?" + PAGE_HOSTS
    + r"(?:/[A-Za-z]{1,12})*/(?:[A-Za-z]+[-_])*0{8,}(?:-0+)*(?:/[A-Za-z]{1,12})*/?",
    re.IGNORECASE,
)
# Marks that end a sentence, a markdown link or a quote, not the link itself.
AFTER_LINK = "`'\").,;:!?]>*"
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}

NAME = r"[A-Za-z0-9._-]+"
CLASSES = [
    ("home_path", re.compile(r"(?:/(?:Users|home)/|[A-Za-z]:\\Users\\)(" + NAME + ")")),
    # Written in pieces so the scan does not find its own patterns.
    ("temp_path", re.compile(r"/private/(?:tmp|var)\b|/var/(?:folders)/|/tmp/(?:claude)\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    (
        "host_name",
        re.compile(
            r"\b[A-Za-z0-9-]+\.(?:local|lan|internal|home\.arpa)(?!\.?\w)"
            r"|\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
        ),
    ),
    (
        "token",
        re.compile(
            r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"
            r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
            r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
            r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
            r"|\bAKIA[0-9A-Z]{16}\b"
            r"|\bAIza[0-9A-Za-z_-]{35}"
            r"|\bglpat-[A-Za-z0-9_-]{20,}"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    ("em_dash", re.compile(chr(0x2014))),
    (
        "private_link",
        re.compile(
            r"(?<![A-Za-z0-9.-])(?:[A-Za-z0-9-]+\.)*" + PAGE_HOSTS + r"/\S+",
            re.IGNORECASE,
        ),
    ),
]


def allowed(kind, match):
    if kind == "home_path":
        return match.group(1).lower() in MADE_UP_NAMES
    if kind == "email":
        return match.group(1).lower() in MADE_UP_DOMAINS
    if kind == "uuid":
        return bool(MADE_UP_UUID.match(match.group(0)))
    if kind == "private_link":
        return bool(MADE_UP_LINK.fullmatch(match.group(0).rstrip(AFTER_LINK)))
    return False


def scan_text(text, allow=allowed):
    """Yield (line number, class) for each hit in the text."""
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern in CLASSES:
            if any(not allow(kind, match) for match in pattern.finditer(line)):
                yield number, kind


def is_binary(data):
    return b"\0" in data[:8192]


def walk(paths):
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(files):
                    yield os.path.join(root, name)
        else:
            yield path


def read_files(paths):
    for path in walk(paths):
        try:
            with open(path, "rb") as handle:
                yield path, handle.read()
        except OSError:
            continue


def read_staged():
    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        check=True, capture_output=True,
    ).stdout.decode().split("\0")
    for name in filter(None, names):
        data = subprocess.run(["git", "show", ":" + name], check=True, capture_output=True).stdout
        yield name, data


def main(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    files = read_staged() if argv == ["--staged"] else read_files(argv)
    hits = 0
    for name, data in files:
        if is_binary(data):
            continue
        for number, kind in scan_text(data.decode("utf-8", errors="replace")):
            print(f"{name}:{number}: {kind}")
            hits += 1
    if hits:
        print(f"scrub_scan: {hits} hit(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
