"""The public text gate.

Before a shell command makes text public (a commit, a push, an annotated
tag, a PR, an issue, a review, a release and its text assets, a gist, a
discussion, a commit comment, a repo or label description, a file written
through the contents API or any other gh api write),
it checks that text for private paths, secrets, private words, machine names
and em dashes. It reads only the text the command publishes: message values,
message files, heredoc bodies, $(cat file) and the commits a push sends,
never the paths the command itself names. A command inside $(...) or
backticks is checked like any other command, in an assignment, an argument,
a heredoc body or another substitution. Text it can not read, such as a
$VARIABLE, another command's output, a pipe or a missing file, counts as
unreadable. So does a command the shell fills in, such as eval "$CMD",
bash -c "$CMD" or a bare $CMD. eval "$(ssh-agent -s)" still runs, because
the gate can read the inner command and it names no public tool (git, gh,
curl or wget that writes or sends data, or a package publish). A script that
a shell reads from a heredoc is checked line by line, like any other command.
A private or unreadable item is refused on every try, in warn and in block.
The em dash follows the mode: warn refuses a text once and lets it through
when it comes again, and block refuses it every time. The answer names each
finding by its flag and never repeats the text it found.

Design limits. The gate catches accidental leaks in the commands the model
writes. It reads inside command substitutions and shell wrappers, and it
refuses text it can not read. It is not a sandbox, and it does not stop a
person who sets out to get around it.
"""
import base64
import binascii
import glob
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess

from hub import files

PART = "public_text_gate"
DASH = chr(0x2014)
MAX_TEXT = 200_000  # characters read from one body file or one push
MAX_COMMITS = 50

# The checks, in the order the tree lists them. A private item can not be
# taken back once it is public, so these refuse every time, in warn and in
# block. The style checks follow the mode: warn refuses a text once.
CHECKS = ["private_paths", "secrets", "private_words", "machine_names", "unreadable", "dashes"]
PRIVACY = {"private_paths", "secrets", "private_words", "machine_names", "unreadable"}
DESCRIBE = {
    "private_paths": "a home or temp folder path",
    "secrets": "what looks like a key, a token or a chat session link",
    "private_words": "a word from the private words list",
    "machine_names": "a host name from the profile",
    "unreadable": "text hub can not read before it goes out",
    "dashes": "an em dash",
}
PRIVACY_NOTE = "A private item is refused on every try until it is removed from the text."
TARGET_NAMES = {"commit": "commit", "push": "push", "tag": "tag", "pr": "PR text", "issue": "issue text",
                "review_comment": "review comment", "release": "release", "gist": "gist",
                "discussion": "discussion", "commit_comment": "commit comment", "contents": "file write",
                "api": "API write", "wrapped": "wrapped command"}
# When the shell fills in the script of a wrapped shell or eval, the script
# could run any step. So it is checked while any target is on.
WRAPPED = "wrapped"

PRIVATE_PATH = re.compile(
    r"(?<![\w.-])(?:/(?:Users|home)/[^\s/'\"]+|[A-Za-z]:\\(?:Users)\\[^\s\\'\"]+"
    r"|/private/(?:var|tmp)/|/var/(?:folders)/|/tmp/[^\s'\"]+)"
)
SECRET = re.compile(
    r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{35}"
    r"|\bglpat-[A-Za-z0-9_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
SESSION_LINK = re.compile(r"claude\.ai/code/session|\bClaude-Session:")
ESCAPED_DASH = "\\u2014"  # the escape that $'...' in a shell turns into the dash
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
# <<EOF, <<'EOF' or <<-"EOF", the rest of that line, the body, then the end line
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
CAT_FILE = re.compile(r"\$\((?:cat\s+|<\s*)(['\"]?)([^)'\"]+?)\1\s*\)")
PLACEHOLDER = "__hub_heredoc_{}__"
# Put before each spot the shell fills in at run time, so a text that holds
# one is unreadable. It never appears in a real command.
UNREADABLE = "\x00"
# Put before a command substitution whose source the gate can read and that
# names no public tool, such as $(ssh-agent -s). As text it is still
# unreadable, but as a command it is not hidden.
SUBSTITUTED = UNREADABLE + "\x01"
# The public tools a substitution's source may not name: git and gh, curl and
# wget with a write method or data, and a package publish.
GIT_TOOLS = re.compile(r"(?<![\w./-])(?:[\w./-]*/)?(?:git|gh)(?![\w-])")
WEB_TOOLS = re.compile(r"(?<![\w./-])(?:[\w./-]*/)?(?:curl|wget)(?![\w-])")
WEB_WRITE = re.compile(
    r"(?<!\S)(?:-X\s*|--request[=\s]+|--method[=\s]+)['\"]?(?!(?:GET|HEAD)\b)[A-Za-z]"
    r"|(?<!\S)-[A-Za-z]*[dFT]"
    r"|(?<!\S)(?:--data[\w-]*|--form[\w-]*|--json|--upload-file|--post-data|--post-file|--body-data|--body-file)"
    r"(?=[\s=]|$)"
)
PACKAGE_PUBLISH = re.compile(
    r"(?<![\w-])(?:npm|pnpm|yarn|bun)(?![\w-])[^;&|\n]*?(?<![\w-])publish(?![\w-])"
    r"|(?<![\w-])(?:cargo|poetry|uv|hatch|flit)\s+publish(?![\w-])"
    r"|(?<![\w-])twine\s+upload(?![\w-])|(?<![\w-])(?:gem|nuget)\s+push(?![\w-])"
)
# Shell words that come before the program of a command.
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"}
# Options of a shell that take the next word as their value.
SHELL_VALUE_OPTIONS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
REDIRECT = re.compile(r"\d*(?:[<>]{1,2}|[<>]&)")
HEREDOC_WORD = re.compile(r"__hub_heredoc_(\d+)__")
CAT_START = re.compile(r"\$\((?:cat\s+|<\s*)")
OPEN_HEREDOC_EXPANSION = re.compile(r"(?<!\\)(?:\$[\w{(@*#?!$-]|`)")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
# Paths that name the command's standard input. Opened in the gate, they would read the hook's own input instead.
STDIN_PATHS = {"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
FEEDERS = {"xargs", "parallel"}
# The text and file options of each gh action that writes. A type flag such as
# `gh pr review --comment` takes no value, so each action lists only its own.
BODY = (["--body", "-b"], ["--body-file", "-F"])
TITLE_BODY = (["--title", "-t", "--body", "-b"], ["--body-file", "-F"])
RELEASE = (["--title", "-t", "--notes", "-n"], ["--notes-file", "-F"])
MERGE = (["--subject", "-t", "--body", "-b"], ["--body-file", "-F"])
COMMENT = (["--comment", "-c"], [])
DESCRIPTION = (["--description", "-d"], [])
NO_TEXT = ([], [])
GH_WRITE_ACTIONS = {
    "pr": {"create": ("pr", TITLE_BODY), "edit": ("pr", TITLE_BODY), "comment": ("pr", BODY),
           "merge": ("pr", MERGE), "close": ("pr", COMMENT), "reopen": ("pr", COMMENT), "ready": ("pr", NO_TEXT),
           "review": ("review_comment", BODY)},
    "issue": {"create": ("issue", TITLE_BODY), "edit": ("issue", TITLE_BODY), "comment": ("issue", BODY),
              "close": ("issue", COMMENT), "reopen": ("issue", COMMENT)},
    "release": {"create": ("release", RELEASE), "edit": ("release", RELEASE)},
    "repo": {"create": ("api", DESCRIPTION), "edit": ("api", DESCRIPTION)},
    "label": {"create": ("api", DESCRIPTION), "edit": ("api", DESCRIPTION)},
}
RELEASE_VALUE_OPTIONS = {"-t", "--title", "-n", "--notes", "-F", "--notes-file", "--target", "-R", "--repo",
                         "--discussion-category", "--notes-start-tag"}
# Short options of git commit and git tag that take the rest of the word as their value.
VALUE_LETTERS = set("CctSu")
BINARY_PEEK = 8000  # bytes; a NUL byte here marks a binary file, as in git
GIST_VALUE_OPTIONS = {"-d", "--desc", "-f", "--filename", "-r", "--remove", "-a", "--add"}
GH_FIELD_OPTIONS = {"-f", "-F", "--field", "--raw-field"}
# REST endpoints by target, checked in order. Any other write is "api".
API_TARGETS = [
    (r"pulls/(?:\d+/)?(?:comments|reviews)|pulls/comments", "review_comment"),
    (r"issues(?:/\d+(?:/comments)?)?$|issues/comments", "issue"),
    (r"pulls(?:/\d+)?$", "pr"),
    (r"(?:^|/)releases(?:/|$)", "release"),
    (r"(?:^|/)gists(?:/|$)", "gist"),
    (r"(?:^|/)git/tags(?:/|$)", "tag"),
    (r"(?:^|/)discussions(?:/|$)", "discussion"),
    (r"commits/[^/]+/comments|^repos/[^/]+/[^/]+/comments(?:/|$)", "commit_comment"),
    (r"(?:^|/)contents(?:/|$)", "contents"),
]
GH_API_VALUE_OPTIONS = GH_FIELD_OPTIONS | {"-X", "--method", "--input", "-H", "--header", "-q", "--jq",
                                           "-t", "--template", "--hostname", "--cache", "-p", "--preview"}


# Finding the text a command makes public


class Command:
    """A command line with its heredoc bodies set aside, so a body never splits the line."""

    def __init__(self, line, cwd, script=False, parent=None):
        """With script, each line is its own command, as a shell runs a script.

        A command inside a substitution uses its parent's heredoc bodies,
        because the parent set them aside.
        """
        self.cwd = cwd
        self.heredocs = parent.heredocs if parent else []
        self.scripts = parent.scripts if parent else []  # each body as written, for a shell that runs it
        self.sources = []  # the command inside each $(...) and backtick pair, as written

        def stash(match):
            body = match.group(4)
            self.scripts.append(body)
            if not match.group(1):
                self.sources += heredoc_substitutions(body)  # the shell runs these when it fills in the body
                if OPEN_HEREDOC_EXPANSION.search(body):
                    body = UNREADABLE + body  # the shell fills in this body at run time
            self.heredocs.append(body)
            return PLACEHOLDER.format(len(self.heredocs) - 1) + match.group(3)

        self.flat = mark_expansions(HEREDOC.sub(stash, line), self.sources)
        if script:
            self.flat = end_lines(self.flat)

    def heredoc_script(self, word):
        """The script a heredoc holds, when the word is where the heredoc stood, or None."""
        match = HEREDOC_WORD.match(word)
        if not match or int(match.group(1)) >= len(self.scripts):
            return None
        return self.scripts[int(match.group(1))]

    def expand(self, value):
        """A message value with $(cat file) and heredoc bodies put in."""
        def cat(match):
            name = match.group(2).strip()
            for index, body in enumerate(self.heredocs):
                if name == PLACEHOLDER.format(index):
                    return body
            return read_file(name, self.cwd)

        value = CAT_FILE.sub(cat, value)
        for index, body in enumerate(self.heredocs):
            value = value.replace(PLACEHOLDER.format(index), body)
        return value

    def from_file(self, path):
        """The text of a message file, where - or /dev/stdin means the heredoc bodies, or a pipe when there are none."""
        if path == "-" or path in STDIN_PATHS:
            return "\n".join(self.heredocs) if self.heredocs else UNREADABLE
        return read_file(path, self.cwd)

    def segments(self):
        """The simple commands in the line, each as a list of words, or None when the line does not parse."""
        try:
            lexer = shlex.shlex(self.flat, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError:
            return None
        found, current = [], []
        for word in words:
            if word in SEPARATORS:
                if current:
                    found.append(current)
                current = []
            else:
                current.append(word)
        if current:
            found.append(current)
        return found


def mark_expansions(line, sources=None):
    """The line with UNREADABLE before each $VAR, ${...}, $(...) and backtick the shell would fill in.

    Single quotes and a backslash keep a $ as it is, and $(cat file) is read
    by the gate, so those stay unmarked. A substitution that may publish
    becomes UNREADABLE alone, so its words never mix with the outer command.
    Its inner command goes into sources and is checked on its own.
    """
    out, index, quote = [], 0, None
    while index < len(line):
        char = line[index]
        following = line[index + 1] if index + 1 < len(line) else ""
        if quote in ("'", "$'"):
            if quote == "$'" and char == "\\" and following:
                out.append(char + following)
                index += 2
                continue
            if char == "'":
                quote = None
            out.append(char)
            index += 1
            continue
        if char == "\\" and following:
            out.append(char + following)
            index += 2
            continue
        if char == '"':
            quote = None if quote == '"' else '"'
        elif char == "'" and quote is None:
            quote = "'"
        elif char == "`" or (char == "$" and following == "("):
            start = index + 1 if char == "`" else index + 2
            end = closing(line, index + 1) if char == "$" else line.find("`", start)
            if end > 0 and sources is not None:
                sources.append(line[start:end])  # even $(cat file), since its pipe could feed a public tool
            if char == "$" and CAT_START.match(line, index):
                pass  # the gate reads $(cat file) itself
            elif end > 0 and quiet_source(line[start:end]):
                out.append(SUBSTITUTED + line[index:end + 1])
                index = end + 1
                continue
            elif end > 0:
                out.append(UNREADABLE)
                index = end + 1
                continue
            else:
                out.append(UNREADABLE)
        elif char == "$" and following == "'" and quote is None:
            out.append("$'")
            quote = "$'"
            index += 2
            continue
        elif char == "$" and following and (following.isalnum() or following in "_{@*#?!$-"):
            out.append(UNREADABLE)
        out.append(char)
        index += 1
    return "".join(out)


def closing(line, start):
    """Where the ) that closes the ( at start is, or -1. Quotes and a backslash are skipped."""
    depth, index, quote = 0, start, None
    while index < len(line):
        char = line[index]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            index += 1
        elif quote == '"':
            quote = None if char == '"' else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def heredoc_substitutions(body):
    """The commands inside each $(...) and backtick pair of a heredoc body that the shell fills in.

    Quotes in such a body are plain text, so only a backslash keeps a $ as it is.
    """
    found, index = [], 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 2
            continue
        if char == "`" or body.startswith("$(", index):
            start = index + 1 if char == "`" else index + 2
            end = body.find("`", start) if char == "`" else closing(body, index + 1)
            if end < 0:
                break
            found.append(body[start:end])
            index = end + 1
            continue
        index += 1
    return found


def quiet_source(source):
    """Whether a command substitution runs a source the gate can read that names no public tool."""
    if any(mark in source for mark in ("$", "`", UNREADABLE, "\\", "__hub_heredoc_")):
        return False
    return not publishes(source)


def publishes(source):
    """Whether a command's text names git, gh, curl or wget with a write method or data, or a package publish."""
    return bool(GIT_TOOLS.search(source) or PACKAGE_PUBLISH.search(source)
                or (WEB_TOOLS.search(source) and WEB_WRITE.search(source)))


def hidden(text):
    """Whether the shell fills in part of the text from a variable or from a source the gate can not trust."""
    return UNREADABLE in text.replace(SUBSTITUTED, "")


def end_lines(text):
    """A script with each line end outside quotes made a separator, so every line is its own command.

    A backslash at the end of a line joins it to the next one, and a comment
    runs to the end of its line, as shlex reads it.
    """
    out, index, quote = [], 0, None
    while index < len(text):
        char = text[index]
        if char == "\\" and quote != "'" and index + 1 < len(text):
            out.append(" " if text[index + 1] == "\n" else text[index:index + 2])
            index += 2
            continue
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and index and not text[index - 1].isspace():
            # The shell reads a # inside a word, as in a#b or $#, as plain text, so shlex must not start a comment.
            out.append("\\#")
            index += 1
            continue
        elif char == "#":
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            out.append(text[index:end])
            index = end
            continue
        elif char == "\n":
            out.append("\n;")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def read_file(path, cwd):
    """A file's text, or UNREADABLE when the gate can not read it before the command runs.

    That covers a name the shell fills in at run time, and anything that is
    not a regular file. A pipe, a device or a folder has no text before the
    command runs, and reading a pipe would wait for a writer.
    """
    if not path or path == "-":
        return ""
    data = read_bytes(path, cwd)
    return UNREADABLE if data is None else data.decode("utf-8", errors="replace")


def read_bytes(path, cwd):
    """The first bytes of a regular file, or None when the gate can not read it before the command runs."""
    if UNREADABLE in path or path in STDIN_PATHS:
        return None
    full = os.path.join(cwd, os.path.expanduser(path))
    try:
        if not stat.S_ISREG(os.stat(full).st_mode):
            return None
        with open(full, "rb") as handle:
            return handle.read(MAX_TEXT)
    except OSError:
        return None


def asset_text(path, cwd):
    """The text of a release asset. A binary asset has no text to check, so it gives an empty string."""
    data = read_bytes(path, cwd) if path else None
    if data is None:
        return UNREADABLE
    if b"\0" in data[:BINARY_PEEK]:
        return ""
    return data.decode("utf-8", errors="replace")


def option_values(words, names, attached=False):
    """The values of options such as --body-file x or --body-file=x.

    With attached, a short option may also hold its value, as -bText or -b=Text.
    """
    found = []
    index = 0
    while index < len(words):
        word = words[index]
        for name in names:
            if word == name and index + 1 < len(words):
                found.append(words[index + 1])
                index += 1
                break
            if name.startswith("--") and word.startswith(name + "="):
                found.append(word[len(name) + 1:])
                break
            if attached and len(name) == 2 and len(word) > 2 and word.startswith(name) and not word.startswith("--"):
                value = word[2:]
                found.append(value[1:] if value.startswith("=") else value)
                break
        index += 1
    return found


def commit_texts(rest, command):
    """The message texts of git commit and git tag.

    It reads --message and --file, with a space or =, and the short -m and -F,
    whether alone, glued to the text (-m"Fix") or in a cluster (-am).
    """
    texts = [command.expand(value) for value in option_values(rest, ["--message"])]
    texts += [command.from_file(value) for value in option_values(rest, ["--file"])]
    index = 0
    while index < len(rest):
        word = rest[index]
        if word == "--":
            break  # paths follow
        if re.match(r"-[A-Za-z]", word):
            for position, letter in enumerate(word[1:], 1):
                if letter in "mF":
                    value = word[position + 1:]
                    if not value and index + 1 < len(rest):
                        index += 1
                        value = rest[index]
                    texts.append(command.expand(value) if letter == "m" else command.from_file(value))
                    break
                if letter in VALUE_LETTERS or not letter.isalpha():
                    break  # the rest of the word is this option's value
        index += 1
    return texts


def git_target(words, command):
    cwd = command.cwd
    index = 1
    while index < len(words):
        word = words[index]
        if word == "-C" and index + 1 < len(words):
            cwd = os.path.join(cwd, os.path.expanduser(words[index + 1]))
            index += 2
        elif word in ("-c", "--git-dir", "--work-tree", "--namespace") and index + 1 < len(words):
            index += 2
        elif word.startswith("-"):
            index += 1
        else:
            break
    if index >= len(words):
        return None
    action, rest = words[index], words[index + 1:]
    if action == "commit":
        command.cwd, outer = cwd, command.cwd
        texts = commit_texts(rest, command)
        command.cwd = outer
        return ("commit", "\n".join(texts)) if texts else None
    if action == "push":
        return "push", pushed_commits(rest, cwd)
    if action == "tag":
        command.cwd, outer = cwd, command.cwd
        texts = commit_texts(rest, command)
        command.cwd = outer
        if texts:
            return "tag", "\n".join(texts)
        annotated = any(word in ("--annotate", "--sign", "--local-user") or word.startswith("--local-user=")
                        or re.fullmatch(r"-[A-Za-z]*[asu][A-Za-z]*", word) for word in rest)
        return ("tag", UNREADABLE) if annotated else None  # an annotated tag with no message opens an editor
    return None


def pushed_commits(rest, cwd):
    """The log and diff of the commits a push sends, capped in size."""
    positional = [word for word in rest if not word.startswith("-")]
    remote = positional[0] if positional else None
    source = "HEAD"
    if len(positional) > 1:
        source = positional[1].lstrip("+").split(":", 1)[0] or "HEAD"
    remotes = f"--remotes={remote}" if remote else "--remotes"
    try:
        log = subprocess.run(
            ["git", "log", "-p", "--no-color", f"--max-count={MAX_COMMITS}", source, "--not", remotes],
            cwd=cwd, capture_output=True, text=True, errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return log.stdout[:MAX_TEXT] if log.returncode == 0 else ""


def graphql_target(text):
    mutation = re.search(r"\bmutation\b[^{]*\{\s*(\w+)", text)
    if not mutation:
        return None
    name = mutation.group(1)
    for part, target in (("Discussion", "discussion"), ("Review", "review_comment"), ("PullRequest", "pr"),
                         ("Issue", "issue"), ("Comment", "issue")):
        if part in name:
            return target
    return "api"


def gh_api_target(rest, texts):
    endpoints, skip = [], False
    for word in rest:
        if skip:
            skip = False
        elif word in GH_API_VALUE_OPTIONS:
            skip = True
        elif not word.startswith("-"):
            endpoints.append(word)
    if endpoints and endpoints[0] == "graphql":
        return graphql_target("\n".join(texts))
    writes = bool(texts) or any(word == "--input" or word.startswith("--input=") for word in rest)
    method = option_values(rest, ["-X", "--method"])
    if method:
        writes = method[-1].upper() != "GET"
    if not writes or not endpoints:
        return None
    endpoint = endpoints[0].split("?", 1)[0].strip("/")
    for pattern, target in API_TARGETS:
        if re.search(pattern, endpoint):
            return target
    return "api"


def gh_target(words, command):
    if len(words) < 2:
        return None
    group, rest = words[1], words[2:]
    if group == "api":
        texts = []
        for name in sorted(GH_FIELD_OPTIONS):
            for field in option_values(rest, [name]):
                key, _, value = field.partition("=")
                if name in ("-F", "--field") and value.startswith("@"):
                    value = command.from_file(value[1:])
                value = command.expand(value)
                texts.append(value)
                if key == "content":
                    texts.append(decoded(value))
        for path in option_values(rest, ["--input"]):
            body = command.from_file(path)
            texts += [body, decoded(json_field(body, "content"))]
        target = gh_api_target(rest, [text for text in texts if text])
        return (target, "\n".join(texts)) if target else None
    if group == "release" and rest and rest[0] == "upload":
        return "release", "\n".join(release_assets(rest[1:], command))
    if group == "gist" and rest and rest[0] in ("create", "new", "edit"):
        return "gist", gist_text(rest[0], rest[1:], command)
    action = GH_WRITE_ACTIONS.get(group, {}).get(rest[0] if rest else "")
    if action is None:
        return None
    target, (text_options, file_options) = action
    texts = [command.expand(value) for value in option_values(rest, text_options, attached=True)]
    texts += [command.from_file(path) for path in option_values(rest, file_options, attached=True)]
    if group == "release" and rest[0] == "create":
        texts += release_assets(rest[1:], command)
    return target, "\n".join(texts)


def release_assets(rest, command):
    """The labels and texts of the files that release create or upload sends.

    Each file follows the tag, as file#label.
    """
    positional, skip = [], False
    for word in rest:
        if skip:
            skip = False
        elif word in RELEASE_VALUE_OPTIONS:
            skip = True
        elif not word.startswith("-"):
            positional.append(word)
    texts = []
    for asset in positional[1:]:  # the first one is the tag
        path, _, label = asset.partition("#")
        path = command.expand(path)
        matches = []
        if any(char in path for char in "*?[") and not os.path.exists(os.path.join(command.cwd, path)):
            # expand the pattern as the shell would
            matches = sorted(glob.glob(os.path.join(command.cwd, os.path.expanduser(path))))
        texts.append(command.expand(label))
        texts += [asset_text(match, command.cwd) for match in matches] or [asset_text(path, command.cwd)]
    return texts


def decoded(value):
    """The text of a base64 value, as the contents API takes a file, or "" when it is not base64."""
    if not value or UNREADABLE in value:
        return ""
    try:
        return base64.b64decode(value, validate=False).decode("utf-8")
    except (binascii.Error, ValueError):
        return ""


def json_field(text, key):
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    return data.get(key, "") if isinstance(data, dict) and isinstance(data.get(key), str) else ""


def gist_text(action, rest, command):
    """The description and the files a gist create or edit sends. With no file, create reads a pipe."""
    texts = [command.expand(value) for value in option_values(rest, ["--desc", "-d"], attached=True)]
    texts += [command.from_file(path) for path in option_values(rest, ["--add", "-a"], attached=True)]
    positional, skip = [], False
    for word in rest:
        if skip:
            skip = False
        elif word in GIST_VALUE_OPTIONS:
            skip = True
        elif word == "-" or not word.startswith("-"):
            positional.append(word)
    if action == "edit":
        positional = positional[1:]  # the first one names the gist
    texts += [command.from_file(path) for path in positional]
    if not positional and (action != "edit" or not (texts or option_values(rest, ["--remove", "-r"]))):
        texts.append(command.from_file("-"))  # create reads a pipe, and edit opens an editor
    return "\n".join(texts)


def shell_script(options, command):
    """What a shell runs, from the words after its name.

    It gives ("-c", script) for bash -c, ("input", script) for a heredoc or a
    here-string the shell reads as its script, or None for a script file.
    """
    reads_input, skip, script = False, False, None
    for spot, word in enumerate(options):
        following = options[spot + 1] if spot + 1 < len(options) else None
        if skip:
            skip = False
        elif command.heredoc_script(word) is not None:
            script = command.heredoc_script(word) if script is None else script
        elif word == "<<<":
            script = following if script is None else script
            skip = True
        elif word.startswith("<<<"):
            script = word[3:] if script is None else script
        elif REDIRECT.fullmatch(word):
            skip = True  # the target of the redirect
        elif re.match(r"\d*[<>]", word):
            continue
        elif re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", word):
            return ("-c", following) if following else None
        elif word in SHELL_VALUE_OPTIONS:
            skip = True
        elif word.startswith(("-", "+")):
            reads_input = reads_input or (not word.startswith("--") and "s" in word[1:])
        elif not reads_input:
            return None  # a script file, which the gate does not read
    return ("input", script) if script is not None else None


def wrapped_steps(words, index, command, depth):
    """Steps run inside bash -c, sh -c, eval or a script a shell reads from a heredoc.

    The text of a step inside bash -c or eval is unreadable, since the shell
    fills it in. A script from a heredoc is read line by line and its steps
    are checked like any other command. When a variable, a file the gate can
    not read or the output of a public tool fills in part of the script, the
    script could run any step.
    """
    program = os.path.basename(words[index])
    if program == "eval":
        how, script = "-c", " ".join(words[index + 1:])
    elif program in SHELLS:
        how, script = shell_script(words[index + 1:], command) or (None, None)
    else:
        return []
    if not script:
        return []
    if how == "-c":
        script = command.expand(script)  # $(cat file) and heredoc bodies
    if hidden(script) or depth > 3:
        return [(WRAPPED, UNREADABLE)]
    steps = public_steps(script, command.cwd, depth + 1, script=True)
    return steps if how == "input" else [(target, UNREADABLE) for target, _text in steps]


def rough_steps(command):
    """For a line that does not parse: the whole line, when it clearly makes text public."""
    line = command.expand(command.flat)
    if re.search(r"\bgit\s+commit\b", line):
        return [("commit", line)]
    group = re.search(r"\bgh\s+(pr|issue|api)\b", line)
    if group:
        return [({"api": "issue"}.get(group.group(1), group.group(1)), line)]
    return []


def substituted_steps(command, depth):
    """The steps inside the command's substitutions, each checked like any other command.

    Past the nesting limit, every substitution counts as unreadable, since the
    gate does not read what it runs.
    """
    steps = []
    for source in command.sources:
        if depth > 3:
            steps.append((WRAPPED, UNREADABLE))
        else:
            steps += public_steps(source, command.cwd, depth + 1, script=True, parent=command)
    return steps


def public_steps(line, cwd, depth=0, script=False, parent=None):
    """[(target, text)] for each step in the command line that makes text public.

    That includes the steps inside every $(...) and backtick pair, such as
    in an assignment, an argument or another substitution.
    """
    command = Command(line, cwd, script, parent)
    segments = command.segments()
    if segments is None:
        return rough_steps(command) + substituted_steps(command, depth)
    steps = []
    for words in segments:
        program_word = next((word for word in words
                             if not re.match(r"[A-Za-z_]\w*=", word) and word not in KEYWORDS), "")
        if hidden(os.path.basename(program_word)):
            steps.append((WRAPPED, UNREADABLE))  # a variable names the program, so it could be any step
            continue
        # newlines are spaces to shlex, so look at every git or gh word in the segment
        for index, word in enumerate(words):
            program = os.path.basename(word)
            if program in SHELLS or program == "eval":
                steps += wrapped_steps(words, index, command, depth)
                continue
            if program not in ("git", "gh"):
                continue
            found = (git_target if program == "git" else gh_target)(words[index:], command)
            if found and any(os.path.basename(before) in FEEDERS for before in words[:index]):
                found = (found[0], UNREADABLE)  # xargs fills in the text from a pipe
            if found:
                steps.append(found)
    return steps + substituted_steps(command, depth)


# The checks


def word_list(flags):
    words = list(flags.value("core.private_words") or [])
    for path in flags.value("core.private_words_files") or []:
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as handle:
                words += [line.strip() for line in handle if line.strip() and not line.startswith("#")]
        except OSError:
            continue  # a file that is missing on this machine is skipped
    return [word for word in words if isinstance(word, str) and word]


def host_names(profile):
    names = []
    machines = profile.get("machines") if isinstance(profile, dict) else None
    for machine in (machines or {}).values():
        host = machine.get("host") if isinstance(machine, dict) else None
        if isinstance(host, str) and host:
            names += [host, host.split(".")[0]]
    return names


def any_word(words, text, edge=r"\w"):
    for word in words:
        if re.search(rf"(?<!{edge}){re.escape(word)}(?!{edge})", text, re.IGNORECASE):
            return True
    return False


def findings(text, flags):
    checks = {
        "private_paths": lambda: bool(PRIVATE_PATH.search(text)),
        "secrets": lambda: bool(SECRET.search(text) or SESSION_LINK.search(text)),
        "private_words": lambda: any_word(word_list(flags), text),
        "machine_names": lambda: any_word(host_names(flags.profile), text, edge=r"[\w-]"),
        "unreadable": lambda: UNREADABLE in text,
        "dashes": lambda: DASH in text or ESCAPED_DASH in text,
    }
    return [name for name in CHECKS if flags.runs(f"{PART}.{name}") and checks[name]()]


# Repos and the warn-once memory


def repo_root(cwd):
    folder = os.path.realpath(cwd)
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def repo_is_public(cwd, profile):
    root = repo_root(cwd)
    repos = profile.get("repos") if isinstance(profile, dict) else None
    if root is None or not isinstance(repos, dict):
        return False
    for path, repo in repos.items():
        if isinstance(repo, dict) and os.path.realpath(os.path.expanduser(path)) == root:
            return bool(repo.get("public"))
    return False


def seen_path(flags, session):
    name = hashlib.sha256(str(session).encode()).hexdigest()[:16]
    return os.path.join(flags.state_dir, "gate", name + ".json")


def seen_before(flags, session, digest):
    """Whether this warning came before in the session. Calls at the same time wait for each other."""
    path = seen_path(flags, session)
    with files.locked(os.path.relpath(path, flags.state_dir) + ".lock"):
        seen = files.read_json(path, {"seen": []})
        if digest in seen["seen"]:
            return True
        seen["seen"] = (seen["seen"] + [digest])[-200:]
        files.write_json(path, seen)
    return False


# The handler


def reason_text(targets, found, flags, warned_once):
    names = sorted({TARGET_NAMES[target] for target in targets}, key=list(TARGET_NAMES.values()).index)
    lines = [f"hub's public text gate stopped this {' and '.join(names)}, because it contains:"]
    lines += [f"- {DESCRIBE[name]} ({PART}.{name})" for name in found]
    if "unreadable" in found:
        lines.append(flags.text(f"{PART}.unreadable.hint"))
    if "dashes" in found:
        lines.append(flags.text(f"{PART}.dashes.hint"))
    if PRIVACY & set(found):
        lines.append(PRIVACY_NOTE)
    if warned_once:
        lines.append(flags.text(f"{PART}.instructions.after_warning"))
    return "\n".join(line for line in lines if line)


def target_runs(flags, target):
    if target == WRAPPED:
        return any(flags.runs(f"{PART}.targets.{name}") for name in TARGET_NAMES if name != WRAPPED)
    return flags.runs(f"{PART}.targets.{target}")


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or hook_input.get("tool_name") != "Bash":
        return
    command = (hook_input.get("tool_input") or {}).get("command") or ""
    cwd = hook_input.get("cwd") or os.getcwd()
    steps = [(target, text) for target, text in public_steps(command, cwd) if target_runs(flags, target)]
    if not steps:
        return
    if flags.runs(f"{PART}.public_repos_only") and not repo_is_public(cwd, flags.profile):
        return
    text = "\n".join(text for _target, text in steps)
    found = findings(text, flags)
    if not found:
        return
    stopping = [name for name in found if name in PRIVACY or flags.value(f"{PART}.{name}") == "block"]
    warning = [name for name in found if name not in stopping]
    new_warnings = []
    if warning:
        digest = hashlib.sha256("\0".join(["Bash", text, *warning]).encode()).hexdigest()
        if not seen_before(flags, hook_input.get("session_id", ""), digest):
            new_warnings = warning
        elif not stopping:
            for name in warning:
                answer.signal(f"{PART}.{name}", "went_ahead")
            return
    shown = [name for name in found if name in stopping or name in new_warnings]
    if not shown:
        return
    for name in stopping:
        answer.signal(f"{PART}.{name}", "blocked")
    for name in new_warnings:
        answer.signal(f"{PART}.{name}", "warned")
    answer.refuse(reason_text([target for target, _text in steps], shown, flags, warned_once=bool(new_warnings)))
