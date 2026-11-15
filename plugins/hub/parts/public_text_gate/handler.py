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
bash -c "$CMD", a bare $CMD or a program name that a substitution prints,
and a script that a shell reads from a pipe, a process substitution or
standard input, as in printf ... | bash. eval "$(ssh-agent -s)" still
runs, because the substitution is the whole script and its command is a
known setup tool, such as ssh-agent or brew shellenv. A script that a
shell reads from a heredoc is checked line by line, like any other command.
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
import functools
import glob
import hashlib
import itertools
import json
import os
import re
import select
import shlex
import signal
import stat
import subprocess
import sys
import time

from hub import files

PART = "public_text_gate"
DASH = chr(0x2014)
MAX_TEXT = 200_000  # characters read from one body file or one push
# Reading 5,000 small commits takes git about a tenth of a second, so this many fit well inside the gate's time.
# Past about 800 commits even the smallest ones pass MAX_TEXT.
MAX_COMMITS = 1_000
# Seconds from when hub's parts start answering until the text gate's git calls must end. The hook may run for 15
# seconds, and the PR test gate's lookups end 10 seconds after the parts start.
TEXT_SECONDS = 8.0
# git push's long options, so a shortened one such as --mirr or --no-dry reads as the option git takes it for
PUSH_LONG_OPTIONS = ("all", "atomic", "branches", "delete", "dry-run", "exec", "follow-tags", "force",
                     "force-if-includes", "force-with-lease", "ipv4", "ipv6", "mirror", "no-verify", "porcelain",
                     "progress", "prune", "push-option", "quiet", "receive-pack", "recurse-submodules", "repo",
                     "set-upstream", "signed", "tags", "thin", "verbose")
NEVER_NEGATED = {"ipv4", "ipv6"}
PUSH_ALIASES = {"--branches": "--all"}
PUSH_SHORT_OPTIONS = {"d": "--delete", "n": "--dry-run", "o": "--push-option", "u": "--set-upstream", "f": "--force",
                      "q": "--quiet", "v": "--verbose", "4": "--ipv4", "6": "--ipv6"}
PUSH_VALUE_OPTIONS = {"--push-option", "--repo", "--receive-pack", "--exec", "--recurse-submodules"}
# The name push_items gives a shortened option that starts more than one of git push's options
AMBIGUOUS_OPTION = "ambiguous"
CONFIG_OPTIONS = {"-c", "--config-env"}
# The setting sections that may change where a push goes or what it sends
PUSH_SETTING_SECTIONS = {"push", "remote", "url", "branch", "include", "includeif"}
# The settings that decide what a push with no branch named sends
BARE_PUSH_SETTINGS = r"^(push\.default|remote\..+\.(push|mirror))$"
FALSE_WORDS = {"false", "no", "off", "0"}
# The folders in a repo's git folder where git also reads a remote from a file
REMOTE_FILE_FOLDERS = ("remotes", "branches")
# git's own options and variables that point it at another repo, work tree, namespace or settings than the folder
# it runs in, whose commits the gate reads
OTHER_REPO_OPTIONS = {"--git-dir", "--work-tree", "--namespace", "--bare"}
REPO_VARIABLE = re.compile(r"\bGIT_(?:DIR|WORK_TREE|COMMON_DIR|NAMESPACE|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES"
                           r"|CONFIG\w*)\b")

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
LONG_PUSH_NOTE = ("This push sends more than hub can check before it goes out. hub reads at most "
                  f"{MAX_COMMITS:,} commits and {MAX_TEXT:,} characters of change in one push. Push fewer commits at "
                  "a time, as in git push origin main~20:main, then push the rest. If one commit is too large on its "
                  f"own, only the user can let it through, by saying \"turn off {PART}.unreadable\". Tell the user, "
                  "and do not change the setting yourself.")
UNTRACKED_PUSH_NOTE = ("hub can not see which commits this remote already has, because the push goes to a URL, a "
                       "folder or a remote with no remote-tracking branches. So it counts every commit of the branch "
                       f"as new, and that is more than the {MAX_COMMITS:,} it reads. Add the remote by name and fetch "
                       "it first, as in git remote add backup <url> and then git fetch backup. Then push to backup.")
SLOW_PUSH_NOTE = ("git did not list the commits of this push in time, so hub could not check them. Send the command "
                  "again. If it is still slow, push fewer commits at a time.")
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
OPERATORS = re.compile(r"[;&|()]+")  # shlex gives operators that touch as one word, such as )|
PIPE = re.compile(r"(?<![|&])\|(?!\|)")  # a | or |& that feeds the next command, not a ||
# <<EOF, <<'EOF' or <<-"EOF", the rest of that line, the body, then the end line
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
CAT_FILE = re.compile(r"\$\((?:cat\s+|<\s*)(['\"]?)([^)'\"]+?)\1\s*\)")
PLACEHOLDER = "__hub_heredoc_{}__"
# Put before each spot the shell fills in at run time, so a text that holds
# one is unreadable. It never appears in a real command.
UNREADABLE = "\x00"
# Put before a command substitution whose source the gate can read and that
# names no public tool, such as $(ssh-agent -s). As text and as a program
# name it is still unreadable, but as the whole script for eval or a shell it
# is not hidden.
SUBSTITUTED = UNREADABLE + "\x01"
# Put after SUBSTITUTED when the substitution stands outside quotes, so the
# shell splits its output into words. As a program name it is then unreadable,
# even before a / that would otherwise name only a folder.
SPLIT = "\x02"
# Marks for a push the gate can not read in full. Each holds UNREADABLE, so it is refused like one, with its own note:
# more commits or more text than the gate reads at once, too many commits for a remote with no remote-tracking
# branches to compare with, or git not listing the commits in time.
LONG_PUSH = UNREADABLE + "\x03"
UNTRACKED_PUSH = UNREADABLE + "\x04"
SLOW_PUSH = UNREADABLE + "\x05"
PUSH_NOTES = {LONG_PUSH: LONG_PUSH_NOTE, UNTRACKED_PUSH: UNTRACKED_PUSH_NOTE, SLOW_PUSH: SLOW_PUSH_NOTE}
# The setup tools whose output may be the whole script for eval or a shell, as
# in eval "$(ssh-agent -s)". Any other source could print a public command.
# The name has no folder, other than the two that Homebrew's install steps
# print for brew, and the words are on one line.
SETUP_SOURCE = re.compile(
    r"(?:ssh-agent(?:[ \t]+-[sc])?|(?:/opt/homebrew/bin/|/usr/local/bin/)?brew[ \t]+shellenv(?:[ \t]+\w+)?"
    r"|(?:pyenv|rbenv|nodenv|goenv|jenv)[ \t]+init(?:[ \t]+-{1,2}[\w-]*)*(?:[ \t]+\w+)?"
    r"|(?:direnv[ \t]+hook|starship[ \t]+init|zoxide[ \t]+init|mise[ \t]+activate)[ \t]+\w+)[ \t]*")
# The characters before a word starts, where zsh reads =(...) as a process substitution.
WORD_START = " \t\n;&|()<>"
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
# Builtins that run a script file in the current shell.
SOURCES = {"source", "."}
# Paths that name the command's standard input. Opened in the gate, they would read the hook's own input instead.
STDIN_PATHS = {"/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
FEEDERS = {"xargs", "parallel"}
# Programs that run the next word as a program, with their options that take a value.
WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T", "-R", "--user", "--group", "--chdir",
             "--host", "--prompt", "--role", "--type", "--other-user", "--command-timeout", "--chroot"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string", "-P"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-I", "-n", "-L", "-P", "-s", "-d", "-E", "-a", "--max-args", "--max-lines", "--max-procs",
              "--max-chars", "--delimiter", "--eof", "--arg-file"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "caffeinate": {"-t", "-w"},
    "time": {"-f", "-o", "--format", "--output"},
    "exec": {"-a"},
    "nohup": set(), "command": set(), "builtin": set(), "noglob": set(), "nocorrect": set(),
    "arch": {"-e", "-d"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"},
}
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
# git's own options that take the next word as their value, before its action, as git.c reads them. git read
# --super-prefix this way up to 2.39. self_protection reads this set too, so both find the same action.
GIT_VALUE_OPTIONS = {"-C", "-c", "--config-env", "--git-dir", "--work-tree", "--namespace", "--attr-source",
                     "--shallow-file", "--super-prefix"}
GIST_VALUE_OPTIONS = {"-d", "--desc", "-f", "--filename", "-r", "--remove", "-a", "--add"}
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
# The options of gh api, by long name, as gh reads them: short letters that take a value or not, and long names.
GH_API_SHORT_VALUES = {"F": "--field", "f": "--raw-field", "X": "--method", "H": "--header", "p": "--preview",
                       "t": "--template", "q": "--jq"}
GH_API_SHORT_SWITCHES = {"i": "--include", "h": "--help"}
GH_API_LONG_VALUES = {"--field", "--raw-field", "--method", "--header", "--preview", "--template", "--jq",
                      "--hostname", "--input", "--cache"}
GH_API_LONG_SWITCHES = {"--include", "--slurp", "--paginate", "--silent", "--verbose", "--help"}
# While gh looks for its command and action, help is the one option every command has that takes no value. Any
# other option, known or not, takes the next word as its value unless it holds "=" or is a cluster of short
# letters, as in gh. gh's own --version takes no value either, but gh stops with an error when it comes before a
# command, so reading it as taking a value changes nothing.
GH_COMMAND_SWITCHES = {"-h", "--help"}
# The gh commands whose next command word is the action the gate reads.
GH_GROUPS_WITH_ACTIONS = {*GH_WRITE_ACTIONS, "gist"}


# Finding the text a command makes public


class Command:
    """A command line with its heredoc bodies set aside, so a body never splits the line."""

    def __init__(self, line, cwd, parent=None, read_log=True, deadline=None):
        """Each line is its own command, since the shell runs a tool call's command as a script.

        A command inside a substitution uses its parent's heredoc bodies,
        because the parent set them aside, and reads the log when its parent does.
        Without read_log, a push names its step but not the commits it sends.
        Its git calls end by deadline, a time.monotonic() value, or TEXT_SECONDS from now.
        """
        self.cwd = cwd
        self.read_log = parent.read_log if parent else read_log
        self.deadline = parent.deadline if parent else deadline or time.monotonic() + TEXT_SECONDS
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

        self.fed = getattr(line, "fed", False)  # a process substitution's command reads the pipe that >(...) gets
        self.flat = end_lines(mark_expansions(joined_lines(HEREDOC.sub(stash, line)), self.sources))

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
        """The simple commands in the line, each as a list of words, or None when the line does not parse.

        A segment is fed when a pipe gives it its standard input, or when it
        stands in a ( ) or { } group that a pipe feeds, since every command in
        the group reads that pipe.
        """
        try:
            lexer = shlex.shlex(self.flat, posix=True, punctuation_chars=";&|()")
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError:
            return None
        found, groups, piped = [], [], self.fed

        def start():
            segment = Segment()
            segment.fed = piped or any(groups)
            return segment

        current = start()
        for word in words:
            if word in SEPARATORS or OPERATORS.fullmatch(word) or (not current and word in ("{", "}")):
                if current:
                    found.append(current)
                    piped = False
                for operator in re.findall(r"\|\||&&|\|&|;;|[;&|(){}]", word):
                    if operator in ("(", "{"):
                        groups.append(piped or any(groups))
                        piped = False
                    elif operator in (")", "}"):
                        piped = False
                        if groups:
                            groups.pop()
                    else:
                        piped = operator in ("|", "|&")
                current = start()
            else:
                current.append(word)
        if current:
            found.append(current)
        return found


class Segment(list):
    """The words of one simple command. fed is True when a pipe gives it its standard input."""
    fed = False


def mark_expansions(line, sources=None):
    """The line with UNREADABLE before each $VAR, ${...}, $(...) and backtick the shell would fill in.

    Single quotes and a backslash keep a $ as it is, and $(cat file) is read
    by the gate, so those stay unmarked. A substitution that may publish
    becomes UNREADABLE alone, so its words never mix with the outer command.
    Its inner command goes into sources and is checked on its own, and so
    does the command of a process substitution, <(...), >(...) or zsh's =(...),
    whose text is a stream the gate can not read. The shell keeps each
    substitution in one word, so the gate quotes the ones it keeps, and its
    brackets, spaces and quotes never split the command. A $'...' string
    becomes the text the shell makes of it, in plain single quotes, because
    the gate's lexer does not know its escapes.
    """
    out, index, quote = [], 0, None
    while index < len(line):
        char = line[index]
        following = line[index + 1] if index + 1 < len(line) else ""
        if quote is None and following == "(" and (char in "<>" or (char == "=" and (
                index == 0 or line[index - 1] in WORD_START))):
            end = closing(line, index + 1)
            if end > 0:
                if sources is not None:
                    source = Source(line[index + 2:end])
                    source.fed = char == ">"
                    sources.append(source)
                out.append(UNREADABLE + one_word(line[index:end + 1], quote))
                index = end + 1
                continue
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
                if end > 0 and not any(mark in line[start:end] for mark in ("$", "`", UNREADABLE)):
                    out.append(one_word(line[index:end + 1], quote))  # the gate reads $(cat file) itself
                    index = end + 1
                    continue
                if end > 0:
                    out.append(UNREADABLE)  # the shell fills in the file name
                    index = end + 1
                    continue
            elif end > 0 and quiet_source(line[start:end]):
                out.append(SUBSTITUTED + ("" if quote else SPLIT) + one_word(line[index:end + 1], quote))
                index = end + 1
                continue
            elif end > 0:
                out.append(UNREADABLE)
                index = end + 1
                continue
            else:
                out.append(UNREADABLE)
        elif char == "$" and following == "'" and quote is None:
            body = ANSI_C_BODY.match(line, index + 2)
            if body:
                out.append(one_word(ansi_c_text(body.group(1)), None))
                index = body.end()
                continue
            out.append("$'")
            quote = "$'"
            index += 2
            continue
        elif char == "$" and following and (following.isalnum() or following in "_{@*#?!$-"):
            out.append(UNREADABLE)
        out.append(char)
        index += 1
    return "".join(out)


# The text of a $'...' string up to its closing quote, where a backslash keeps the next character.
ANSI_C_BODY = re.compile(r"((?:[^\\']|\\.)*)'", re.S)
ANSI_C_PIECE = re.compile(
    r"\\(?:(?P<octal>[0-7]{1,3})|x(?P<byte>[0-9a-fA-F]{1,2})|u(?P<short>[0-9a-fA-F]{1,4})"
    r"|U(?P<long>[0-9a-fA-F]{1,8})|c(?P<control>.)|(?P<letter>.))|(?P<plain>[^\\]+)", re.S)
ANSI_C_LETTERS = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                  "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def ansi_c_text(body):
    """The text the shell makes of a $'...' string's body, with each escape filled in.

    Hex and octal escapes are bytes, so a character written as its UTF-8
    bytes reads as that character.
    """
    return b"".join(map(ansi_c_bytes, ANSI_C_PIECE.finditer(body))).decode("utf-8", "replace")


def ansi_c_bytes(piece):
    """The bytes the shell writes for one escape, or for a run of plain text, in a $'...' string."""
    found = piece.groupdict()
    code = found["short"] or found["long"]
    if found["octal"]:
        return bytes([int(found["octal"], 8) & 0xFF])
    if found["byte"]:
        return bytes([int(found["byte"], 16)])
    if code:
        return chr(min(int(code, 16), sys.maxunicode)).encode("utf-8", "surrogatepass")
    if found["control"]:
        return bytes([ord(found["control"]) & 0x1F])
    if found["plain"] is not None:
        return found["plain"].encode("utf-8", "surrogateescape")
    return ANSI_C_LETTERS.get(found["letter"], "\\" + found["letter"]).encode("utf-8", "surrogateescape")


def one_word(text, quote):
    """The text in single quotes, so the gate's lexer keeps it as one word with the same value.

    Inside double quotes it closes them first and opens them again after.
    """
    kept = "'" + text.replace("'", "'\\''") + "'"
    return '"' + kept + '"' if quote == '"' else kept


class Source(str):
    """The command of a substitution. fed is True for >(...), whose command reads what is written to it."""
    fed = False


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


def joined_lines(text):
    """The text with each backslash at a line end taken out together with that line end, as the shell joins the
    two lines into one word. Single quotes, a $'...' string and a comment keep both, as in the shell."""
    out, index, quote = [], 0, None
    while index < len(text):
        char, pair = text[index], text[index:index + 2]
        if quote in ("'", "$'"):
            step = 2 if quote == "$'" and char == "\\" else 1
            quote = None if char == "'" and step == 1 else quote
            out.append(text[index:index + step])
            index += step
            continue
        if pair == "\\\n":
            index += 2
            continue
        if char == "\\" or (pair == "$'" and quote is None):
            quote = "$'" if char == "$" else quote
            out.append(pair)
            index += 2
            continue
        if quote == '"':
            quote = None if char == '"' else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and starts_a_word(out):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            out.append(text[index:end])
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def end_lines(text):
    """A script with each line end outside quotes made a separator, so every line is its own command.

    A pipe, && or || at the end of a line joins it to the next one, as in
    the shell, and so does a backslash, which goes with the line end. A
    comment runs to the end of its line, as shlex reads it.
    """
    out, index, quote = [], 0, None
    while index < len(text):
        char = text[index]
        if char == "\\" and quote != "'" and index + 1 < len(text):
            out.append("" if text[index + 1] == "\n" else text[index:index + 2])
            index += 2
            continue
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#" and not starts_a_word(out):
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
            out.append("\n" if ends_in_a_pipe_or_list(out) else "\n;")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def starts_a_word(pieces):
    """Whether the shell starts a new word after the text so far, so a # there opens a comment.

    It does at the start, after a blank and after each operator character, as
    in ;# or |#, but not after an escaped character such as \\;.
    """
    last = pieces[-1] if pieces else " "
    return not last.startswith("\\") and (last[-1].isspace() or last[-1] in ";&|()<>")


def ends_in_a_pipe_or_list(pieces):
    """Whether the text so far ends with |, |&, && or ||, so the shell reads the next line as part of the same
    command. A comment after the mark does not count, since the shell drops it. A lone & ends its command,
    and an escaped \\| is plain text."""
    marks = list(itertools.islice((piece for piece in reversed(pieces) if not (
        piece.isspace() or piece.startswith("#"))), 2))
    last, before = (marks + ["", ""])[:2]
    return last == "|" or (last == "&" and before in ("&", "|"))


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
    if UNREADABLE in full:  # a relative path in a folder the shell fills in
        return None
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
            folder = words[index + 1]
            cwd = os.path.join(cwd, folder if UNREADABLE in folder else os.path.expanduser(folder))
            index += 2
        elif word in GIT_VALUE_OPTIONS and index + 1 < len(words):
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
        if not command.read_log:
            return "push", ""
        options = words[1:index]
        if any(UNREADABLE in word for word in options):
            return "push", UNREADABLE  # a git option the shell fills in, such as --git-dir, may name other commits
        if any(option.split("=", 1)[0] in OTHER_REPO_OPTIONS for option in options):
            return "push", UNREADABLE  # git pushes the commits of another repo or namespace than the folder's
        return "push", pushed_commits(rest, cwd, settings_set_by_options(options), command.deadline)
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


def source_and_destination(spec):
    """A refspec's source and destination, split at its last colon as git splits it. The destination is empty when
    none is named, and the source is empty for a delete."""
    source, colon, destination = spec.rpartition(":")
    return (source, destination) if colon else (spec, "")


def searches_commit_messages(source):
    """Whether a refspec's source names its commit by a search of commit messages, as :/text does."""
    return source.startswith(":/")


def without_redirects(words):
    """The words of a command without its redirects and their targets, such as 2>&1 or > push.log."""
    kept, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif REDIRECT.fullmatch(word):
            skip = True
        elif not REDIRECT.match(word):
            kept.append(word)
    return kept


def option_spellings(option):
    """(spelling, option, whether it turns the option on) for each way git writes a long option out in full."""
    if option.startswith("no-"):
        return [(option, option, True), (option[3:], option, False)]
    return [(option, option, True)] + ([] if option in NEVER_NEGATED else [("no-" + option, option, False)])


PUSH_SPELLINGS = [spelling for option in PUSH_LONG_OPTIONS for spelling in option_spellings(option)]


def full_push_option(name):
    """(the long option git reads for a word such as --mirr or --no-dry, whether the word turns it on). A word
    that starts only one option is that option, as in git, and a word that starts more than one is
    AMBIGUOUS_OPTION."""
    word = name[2:]
    exact = [(f"--{option}", on) for spelling, option, on in PUSH_SPELLINGS if spelling == word]
    shortened = {(f"--{option}", on) for spelling, option, on in PUSH_SPELLINGS if spelling.startswith(word)}
    if not exact and len(shortened) > 1:
        return AMBIGUOUS_OPTION, True
    option, on = (exact or sorted(shortened) or [(name, True)])[0]
    return PUSH_ALIASES.get(option, option), on


def push_items(words):
    """(the option in full, its value) for each of git push's words in order, or (None, word) for a positional
    word. A switch's value is whether it is on. A value option takes its value the way git does."""
    remaining = iter(words)
    for word in remaining:
        if word == "--":
            yield from ((None, positional) for positional in remaining)
        elif word.startswith("--"):
            name, equals, attached = word.partition("=")
            option, on = full_push_option(name)
            takes_next_word = option in PUSH_VALUE_OPTIONS and on and not equals
            yield option, next(remaining, "") if takes_next_word else attached if equals else on
        elif word.startswith("-") and len(word) > 1:
            yield from short_push_items(word[1:], remaining)
        else:
            yield None, word


def short_push_items(letters, remaining):
    """The options in a cluster such as -uo, where -o takes the rest of the word, or else the next word."""
    for position, letter in enumerate(letters):
        option = PUSH_SHORT_OPTIONS.get(letter, "-" + letter)
        if option in PUSH_VALUE_OPTIONS:
            yield option, letters[position + 1:] or next(remaining, "")
            return
        yield option, True


def last_switch(items, option):
    """Whether a switch is on after every word that names it, so -n --no-dry-run is not a dry run."""
    return next((value is not False for name, value in reversed(items) if name == option), False)


def push_refspecs(words, deletes):
    """The refspecs git push makes of the words after the remote: tag v1 is refs/tags/v1 under its own name, and
    with --delete each word deletes that name on the remote. A tag with no name after it stays the word tag."""
    specs, remaining = [], iter(words)
    for word in remaining:
        name = next(remaining, None) if word == "tag" else None
        if name is not None:
            specs.append(f":refs/tags/{name}" if deletes else f"refs/tags/{name}:refs/tags/{name}")
        else:
            specs.append(f":{word}" if deletes and word != "tag" else word)
    return specs


class TooSlow(Exception):
    """git did not end before the text gate's deadline."""


def pushed_commits(rest, cwd, one_command_settings, deadline):
    """The log and diff of the commits a push sends, from the source of each refspec it names, and every tag with
    --tags. Its words are read the way git push reads them, so an option's value or the word tag is not a
    refspec. one_command_settings are the name=value settings git's -c options set for this push.

    When the shell fills in the folder or a word of the push, a source
    searches commit messages or holds a pattern, an option matches more than
    one of git's, the push sends every branch or every branch that matches
    on the remote, a push with no branch named may send more than HEAD, or
    git fails, the gate can not tell which commits it sends, so the push
    counts as unreadable. A push of more commits or more text than the gate
    reads at once is LONG_PUSH, or UNTRACKED_PUSH when the remote has no
    remote-tracking branches, and one that git does not list by the deadline
    is SLOW_PUSH.
    """
    try:
        return commits_read(rest, cwd, one_command_settings, deadline)
    except TooSlow:
        return SLOW_PUSH


def commits_read(rest, cwd, one_command_settings, deadline):
    if UNREADABLE in cwd or any(UNREADABLE in word for word in rest):
        return UNREADABLE
    items = list(push_items(without_redirects(rest)))
    if any(option == AMBIGUOUS_OPTION for option, _value in items):
        return UNREADABLE
    positional = [value for option, value in items if option is None]
    remote = positional[0] if positional else None
    specs = push_refspecs(positional[1:], last_switch(items, "--delete"))
    if last_switch(items, "--all") or last_switch(items, "--mirror") or any(spec.lstrip("+") == ":" for spec in specs):
        return UNREADABLE  # every branch, or every branch that has a match on the remote
    tags = last_switch(items, "--tags")
    if not specs and not tags and sends_more_than_head(cwd, remote, one_command_settings, deadline):
        return UNREADABLE
    sources = [source for source in (source_and_destination(spec.lstrip("+"))[0] for spec in specs)
               if source and not source.startswith("^")]
    if any(searches_commit_messages(source) or source.startswith("-") or "*" in source for source in sources):
        return UNREADABLE
    if specs and not sources and not tags:
        return ""  # deletes and ^ entries send no commits
    remotes = f"--remotes={remote}" if remote else "--remotes"
    revisions = [*(sources or ["HEAD"]), *(["--tags"] if tags else []), "--not", remotes, "--"]
    counted = git_run(["rev-list", "--count", f"--max-count={MAX_COMMITS + 1}", *revisions], cwd, deadline)
    if counted is None or counted[0] != 0 or not counted[1].strip().isdigit():
        return UNREADABLE
    if int(counted[1]) > MAX_COMMITS:
        return UNTRACKED_PUSH if remote and not has_tracking_branches(cwd, remote, deadline) else LONG_PUSH
    # --cc shows the text a merge adds beyond its parents, such as a settled conflict, and plain -p shows none.
    log = git_run(["log", "-p", "--cc", "--no-color", *revisions], cwd, deadline, limit=MAX_TEXT)
    if log is None:
        return UNREADABLE
    if len(log[1]) > MAX_TEXT:
        return LONG_PUSH
    return log[1] if log[0] == 0 else UNREADABLE


def has_tracking_branches(cwd, remote, deadline):
    """Whether the repo has a remote-tracking branch for the remote, which a push to a URL or a folder never has.
    A failed lookup counts as having one."""
    found = git_run(["for-each-ref", "--count=1", "--format=%(refname)", f"refs/remotes/{remote}/"], cwd, deadline)
    return found is None or found[0] != 0 or bool(found[1].strip())


def git_run(words, cwd, deadline, limit=None):
    """git's exit code and output, or None when git can not start. With a limit, git stops once its output passes
    that many characters, and the output is cut just past it, so a caller sees it is too long without reading it
    all. Raises TooSlow when git does not end by the deadline."""
    try:
        process = subprocess.Popen(["git", *words], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    with process:
        # A character takes at most four bytes, so output past this many bytes is past the limit
        output, cut = output_until(process, deadline, None if limit is None else 4 * (limit + 1))
        if cut:
            os.killpg(process.pid, signal.SIGKILL)
        try:
            code = process.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            raise TooSlow from None
    return code, output.decode("utf-8", errors="replace")


def output_until(process, deadline, most_bytes):
    """(the bytes process writes until it ends or passes most_bytes, whether it passed most_bytes). Kills it and
    raises TooSlow at the deadline."""
    chunks = []
    while most_bytes is None or sum(map(len, chunks)) <= most_bytes:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise TooSlow
        chunk = os.read(process.stdout.fileno(), 65536)
        if not chunk:
            return b"".join(chunks), False
        chunks.append(chunk)
    return b"".join(chunks), True


def settings_set_by_options(options):
    """The name=value settings that git's -c and --config-env options set, read from git's own options."""
    separate = [value for option, value in zip(options, options[1:]) if option in CONFIG_OPTIONS]
    attached = [option.split("=", 1)[1] for option in options if option.startswith("--config-env=")]
    return separate + attached


def names_a_push_setting(setting):
    """Whether a setting such as push.default=matching may change where a push goes or what it sends."""
    return setting.split("=", 1)[0].split(".", 1)[0].lower() in PUSH_SETTING_SECTIONS


def sends_more_than_head(cwd, remote, one_command_settings, deadline):
    """Whether a push that names no branch may send more than HEAD's commits: by push.default matching, a remote's
    push or mirror setting, a file for the remote in the repo's git folder, or a push setting set for this one
    command. With no remote named, any remote's settings count. A failed lookup counts as more."""
    if any(map(names_a_push_setting, one_command_settings)):
        return True
    listed = git_run(["config", "--get-regexp", BARE_PUSH_SETTINGS], cwd, deadline)
    common = git_run(["rev-parse", "--git-common-dir"], cwd, deadline)
    if listed is None or listed[0] not in (0, 1) or common is None or common[0] != 0:
        return True  # git config ends with 1 when no setting matches
    settings = [tuple((line.split(" ", 1) + [""])[:2]) for line in listed[1].splitlines()]
    mode = next((value for key, value in reversed(settings) if key == "push.default"), "")
    remote_settings = [(key, value) for key, value in settings if key != "push.default"
                       and (remote is None or key in (f"remote.{remote}.push", f"remote.{remote}.mirror"))]
    return (mode.lower() == "matching"
            or any(key.endswith(".push") or value.lower() not in FALSE_WORDS for key, value in remote_settings)
            or has_remote_file(os.path.join(cwd, common[1].strip()), remote))


def has_remote_file(git_folder, remote):
    """Whether the repo's remotes or branches folder holds a file for the remote, or any file when no remote is
    named. git reads a remote from such a file, which git config does not show. A remote name never holds a
    slash, so a path or URL remote has none."""
    folders = [os.path.join(git_folder, name) for name in REMOTE_FILE_FOLDERS]
    if remote is None:
        return any(os.path.isfile(os.path.join(folder, name))
                   for folder in folders if os.path.isdir(folder) for name in os.listdir(folder))
    if "/" in remote or remote in ("", ".", ".."):
        return False
    return any(os.path.isfile(os.path.join(folder, remote)) for folder in folders)


def pushes_read_in_place(steps, line):
    """The steps, with each push unreadable when the line names a variable that points git at another repo, work
    tree, namespace or settings, since the gate reads the commits of the folder git runs in. That holds wherever
    the variable stands, as in an export earlier on the line."""
    if not REPO_VARIABLE.search(line):
        return steps
    return [(target, UNREADABLE if target == "push" else text) for target, text in steps]


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


def gh_api_items(words):
    """The words of a gh api command, read the way gh reads them.

    Each item is (long option name, value), (None, word) for a word that is not an option, or
    ("unknown", word) for an option gh does not have. A short option that takes a value takes the
    rest of its word, after an optional "=", or else the next word, so -fbody=x, -F=body=x and
    -ifbody=x all hold a field. Long options are never shortened, and "--" ends the options.
    """
    items, index = [], 0
    while index < len(words):
        if words[index] == "--":
            return items + [(None, word) for word in words[index + 1:]]
        found, used = gh_api_item(words[index], words[index + 1:index + 2])
        items, index = items + found, index + used
    return items


def gh_api_item(word, following):
    """The items one word of gh api gives, with how many words it uses: 1, or 2 when its value is the next word."""
    if word.startswith("--"):
        name, equals, value = word.partition("=")
        if name in GH_API_LONG_SWITCHES or (name in GH_API_LONG_VALUES and equals):
            return [(name, value)], 1
        if name in GH_API_LONG_VALUES and following:
            return [(name, following[0])], 2
        return [("unknown", word)], 1
    if not word.startswith("-") or word == "-":
        return [(None, word)], 1
    letters = word[1:]
    switches = len(letters) - len(letters.lstrip("".join(GH_API_SHORT_SWITCHES)))
    found = [(GH_API_SHORT_SWITCHES[letter], "") for letter in letters[:switches]]
    if switches == len(letters):
        return found, 1
    letter, attached = letters[switches], letters[switches + 1:]
    if letter not in GH_API_SHORT_VALUES or not (attached or following):
        return found + [("unknown", word)], 1
    if attached:
        return found + [(GH_API_SHORT_VALUES[letter], attached[1:] if attached.startswith("=") else attached)], 1
    return found + [(GH_API_SHORT_VALUES[letter], following[0])], 2


def gh_api_target(items, texts):
    """What a gh api command writes to, or None for a read.

    gh sends POST when the command has a field or --input and no --method. An option gh does not
    have counts as a write, since the gate can not tell what it does.
    """
    endpoints = [value for name, value in items if name is None]
    unknown = any(name == "unknown" for name, _ in items)
    if endpoints and endpoints[0] == "graphql":
        return graphql_target("\n".join(texts)) or ("api" if unknown else None)
    methods = [value for name, value in items if name == "--method"]
    sends = any(name in ("--field", "--raw-field", "--input") for name, _ in items)
    writes = methods[-1].upper() != "GET" if methods else sends
    if not endpoints or not (writes or unknown):
        return "api" if unknown else None
    endpoint = endpoints[0].split("?", 1)[0].strip("/")
    for pattern, target in API_TARGETS:
        if re.search(pattern, endpoint):
            return target
    return "api"


def gh_command(words):
    """gh's words in the order gh reads them: the program, its command, the action, then every other word.

    gh finds its command and action by skipping its options, so gh -R o/r pr create and gh pr --help=false
    create both run pr create. Options that came before the command or the action follow the action, where gh
    reads them too. Without a command the result is the program alone. Returns None when the shell fills in a
    word that gh may read as its command, its action or an option before them.
    """
    program, rest = words[0], list(words[1:])
    group_at = next_gh_command_word(rest)
    if group_at is None:
        return None
    if group_at == len(rest):
        return [program]
    group, others = rest[group_at], rest[:group_at] + rest[group_at + 1:]
    if group not in GH_GROUPS_WITH_ACTIONS:
        return [program, group, *others]
    action_at = next_gh_command_word(others)
    if action_at is None:
        return None
    if action_at == len(others):
        return [program, group, *others]
    return [program, group, others[action_at], *others[:action_at], *others[action_at + 1:]]


def next_gh_command_word(words):
    """Where gh finds its next command word in words, len(words) when there is none, or None when the shell fills in
    a word gh may read as that command or as an option before it. "--" ends the search, as in gh."""
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--":
            return len(words)
        before_mark = word.split(UNREADABLE, 1)[0]
        if UNREADABLE in word and not (before_mark.startswith("-") and "=" in before_mark):
            return None
        if word and not word.startswith("-"):
            return index
        index += 2 if gh_option_takes_next_word(word) else 1
    return len(words)


def gh_option_takes_next_word(word):
    """Whether gh takes the next word as the value of this option while it looks for a command."""
    lone = word.startswith("--") or (word.startswith("-") and len(word) == 2)
    return lone and "=" not in word and word not in GH_COMMAND_SWITCHES


def gh_target(words, command):
    words = gh_command(words)
    if words is None:
        return WRAPPED, UNREADABLE  # the shell fills in which gh command runs
    if len(words) < 2:
        return None
    group, rest = words[1], words[2:]
    if group == "api":
        items = gh_api_items(rest)
        texts = []
        for name, field in items:
            if name not in ("--field", "--raw-field"):
                continue
            key, _, value = field.partition("=")
            if name == "--field" and value.startswith("@"):
                value = command.from_file(value[1:])
            value = command.expand(value)
            texts.append(value)
            if key == "content":
                texts.append(decoded(value))
        for path in [value for name, value in items if name == "--input"]:
            body = command.from_file(path)
            texts += [body, decoded(json_field(body, "content"))]
        texts += [UNREADABLE for name, _ in items if name == "unknown"]
        target = gh_api_target(items, [text for text in texts if text])
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


def shell_script(options, command, files=False):
    """What a shell runs, from the words after its name.

    It gives ("-c", script) for bash -c, ("input", script) for a heredoc or a
    here-string the shell reads as its script, and ("input", None) when -s
    makes it read standard input. A script file gives None, or ("file", path)
    with files.
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
            return ("file", word) if files else None  # a script file, which the gate does not read
    return ("input", script) if script is not None or reads_input else None


def reads_hidden_script(words, spot, command):
    """Whether the shell or source at spot reads its script from a place the gate can not read.

    That is its standard input when a pipe or a < redirect feeds it or -s
    tells it to read it, a path such as /dev/stdin, or words that xargs fills
    in. The gate sees none of these, since the shell gets them only at run time.
    """
    program = os.path.basename(words[spot]).casefold()  # macOS runs BASH as bash
    following = words[spot + 1] if spot + 1 < len(words) else ""
    if program in SOURCES:
        return following in STDIN_PATHS or following.startswith("/dev/fd/")
    if program not in SHELLS:
        return False
    if any(os.path.basename(word) in FEEDERS for word in words[:spot]):
        return True
    how, script = shell_script(words[spot + 1:], command, files=True) or (None, None)
    if how == "file":
        return script in STDIN_PATHS or script.startswith("/dev/fd/")
    if how == "input":
        return script is None
    return how is None and (getattr(words, "fed", False)
                            or any(re.match(r"\d*<(?!<)", word) for word in words[spot + 1:]))


def wrapped_steps(words, index, command, depth, first=False):
    """Steps run inside bash -c, sh -c, eval or a script a shell reads from a heredoc.

    The text of a step inside bash -c or eval is unreadable, since the shell
    fills it in. A script from a heredoc is read line by line and its steps
    are checked like any other command. When a variable, a file the gate can
    not read or the output of a public tool fills in part of the script, the
    script could run any step.
    """
    program = os.path.basename(words[index]).casefold()  # macOS runs BASH as bash
    if program == "eval":
        how, script = "-c", " ".join(words[index + 1:])
    elif program in SHELLS:
        how, script = shell_script(words[index + 1:], command, files=True) or (None, None)
    elif program in SOURCES:
        how, script = "file", words[index + 1] if index + 1 < len(words) else None
    else:
        return []
    if not script:
        return []
    if how == "file":
        return [(WRAPPED, UNREADABLE)] if hidden(script) else []  # the shell fills in which file runs
    script = command.expand(script)  # $(cat file) and heredoc bodies
    sources = quiet_sources(script, command) if first else None
    if sources is not None and depth <= 3:
        # the whole script is what a tool prints, as in eval "$(ssh-agent -s)".
        # A public step that its source names could print other text, so it is unreadable.
        return [(target, UNREADABLE) for source in sources
                for target, _text in public_steps(source, command.cwd, depth + 1, script=True,
                                                  log=command.read_log, deadline=command.deadline)]
    if hidden(script) or depth > 3:
        return [(WRAPPED, UNREADABLE)]
    steps = public_steps(script, command.cwd, depth + 1, script=True, opens_the_call=first,
                         log=command.read_log, deadline=command.deadline)
    return steps if how == "input" else [(target, UNREADABLE) for target, _text in steps]


def quiet_sources(script, command):
    """The source of the substitution that is the whole script, when a known setup tool runs there, or None.

    Only a setup tool such as ssh-agent -s or brew shellenv counts. Any other
    source, even one that names no public tool, could print a public command.
    The caller asks only for the first command of the tool call, or the first
    line of a script that command runs, since an earlier command could change
    what the tool's name runs.
    """
    script = script.strip()
    for source in command.sources:
        for whole in ("$(" + source + ")", "`" + source + "`"):
            if script in (SUBSTITUTED + whole, SUBSTITUTED + SPLIT + whole) and SETUP_SOURCE.fullmatch(source):
                return [source]
    return None


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


def program_hidden(word):
    """Whether the shell fills in the program's name, after the last /.

    That includes the output of a substitution that names no public tool,
    such as $(printf x), since that output could be the name of one.
    """
    tail = word.rsplit("/", 1)[-1]
    return (UNREADABLE in tail or SUBSTITUTED + SPLIT in word
            or (UNREADABLE in word and any(char in tail for char in ")`")))


def program_spot(words):
    """(index, hidden) of the program word, past assignments, keywords and wrappers such as sudo and env.

    The index is None when the segment runs no program. hidden is True when
    the shell fills in the program's name, so it could be any public tool.
    """
    index = 0
    while index < len(words):
        word = words[index]
        name = os.path.basename(word).casefold()
        if re.match(r"[A-Za-z_]\w*=", word) or (word in KEYWORDS and name not in WRAPPERS):
            index += 1
            continue
        if program_hidden(word):
            return index, True
        options = WRAPPERS.get(name)
        if options is None:
            return index, False
        index += 1
        durations = 1 if name == "timeout" else 0
        while index < len(words):
            option = words[index]
            if option == "--":
                index += 1
                break
            if not option.startswith("-") or option == "-":
                if not durations:
                    break
                durations -= 1
                index += 1
                continue
            index += wrapper_option(option, options)
    return None, False


def wrapper_option(option, options):
    """How many words a wrapper's option takes, itself included.

    A short cluster such as -iu is read letter by letter. The first letter
    that takes a value takes the rest of the cluster, or the next word.
    """
    if option.startswith("--"):
        key, equals, _value = option.partition("=")
        return 2 if not equals and key in options else 1
    for spot in range(1, len(option)):
        if "-" + option[spot] in options:
            return 1 if option[spot + 1:] else 2
    return 1


def public_steps(line, cwd, depth=0, script=False, parent=None, opens_the_call=False, log=True, deadline=None):
    """[(target, text)] for each step in the command line that makes text public.

    That includes the steps inside every $(...) and backtick pair, such as
    in an assignment, an argument or another substitution. opens_the_call
    says a script is the first thing the tool call runs, as the script of a
    shell that is the call's first command is. With log False, a push is
    named with no text, so a caller that needs only the steps runs no git.
    Every git call ends by deadline, as Command takes it.
    """
    command = Command(line, cwd, parent, read_log=log, deadline=deadline)
    segments = command.segments()
    if segments is None:
        return rough_steps(command) + substituted_steps(command, depth)
    steps = []
    for position, words in enumerate(segments):
        spot, program_is_hidden = program_spot(words)
        if program_is_hidden:
            steps.append((WRAPPED, UNREADABLE))  # the shell fills in the program, so it could be any step
            continue
        if spot is not None and reads_hidden_script(words, spot, command):
            steps.append((WRAPPED, UNREADABLE))  # the shell reads a script the gate can not see
        # a wrapper such as timeout or sudo can stand before git or gh, so look at every word in the segment
        for index, word in enumerate(words):
            program = os.path.basename(word).casefold()  # macOS runs BASH as bash and GIT as git
            if program in SHELLS or program in SOURCES or program == "eval":
                first = (opens_the_call or not script) and position == 0 and index == 0
                steps += wrapped_steps(words, index, command, depth, first)
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


def reason_text(targets, found, text, flags, warned_once):
    names = sorted({TARGET_NAMES[target] for target in targets}, key=list(TARGET_NAMES.values()).index)
    lines = [f"hub's public text gate stopped this {' and '.join(names)}, because it contains:"]
    lines += [f"- {DESCRIBE[name]} ({PART}.{name})" for name in found]
    if "unreadable" in found:
        lines += [note for mark, note in PUSH_NOTES.items() if mark in text]
    if "unreadable" in found and UNREADABLE in without_push_marks(text):
        lines.append(flags.text(f"{PART}.unreadable.hint"))
    if "dashes" in found:
        lines.append(flags.text(f"{PART}.dashes.hint"))
    if PRIVACY & set(found):
        lines.append(PRIVACY_NOTE)
    if warned_once:
        lines.append(flags.text(f"{PART}.instructions.after_warning"))
    return "\n".join(line for line in lines if line)


def without_push_marks(text):
    """The text with each mark of a push the gate could not read in full taken out, since those have their own note."""
    return functools.reduce(lambda rest, mark: rest.replace(mark, ""), PUSH_NOTES, text)


def target_runs(flags, target):
    if target == WRAPPED:
        return any(flags.runs(f"{PART}.targets.{name}") for name in TARGET_NAMES if name != WRAPPED)
    return flags.runs(f"{PART}.targets.{target}")


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or hook_input.get("tool_name") != "Bash":
        return
    command = (hook_input.get("tool_input") or {}).get("command") or ""
    cwd = hook_input.get("cwd") or os.getcwd()
    deadline = answer.started + TEXT_SECONDS
    steps = [(target, text) for target, text in pushes_read_in_place(public_steps(command, cwd, deadline=deadline),
                                                                     command)
             if target_runs(flags, target)]
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
    answer.refuse(reason_text([target for target, _text in steps], shown, text, flags,
                              warned_once=bool(new_warnings)))
