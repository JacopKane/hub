"""Self protection: a tool call can not lower hub's own protections.

Before a tool call runs, it refuses three kinds of call:

- hub's switch step, when it would lower a protected flag, uninstall hub,
  or read the screen as the band does, which marks the tuner's notices as
  shown. A flag is protected when it is a privacy check, the mode of a gate,
  a guard or a flag under one, or a pin (a value the user set). Lowering
  means a weaker value, such as off or warn after block, a loosening option
  switched on, an item taken out of a guarded list, or any change to a pin.
  Turning off a part also lowers a flag when a protected flag or a value the
  user set by hand would stop running under it. The change runs after all
  when the newest message the user typed in this chat asks for it (see
  said.py), and then this part leaves a words ticket for the step. A step
  call that only reads, or that raises a flag, still runs, and so do flags
  for style only. The part also reads the
  command lines and keys that tmux, screen, ssh, script, expect and similar
  programs run, because the step started there has no tool-call marker.
  claude plugin disable or uninstall of hub counts too.
- A command that unsets or overrides the marker that tells hub a tool call is
  running (AI_AGENT, CURSOR_AGENT, HUB_DOOR): env -u, env -i, unset, an
  assignment, exec -c, or interpreter code that names the marker.
- A write to hub's settings or state files: the user's settings folder, the
  state folder and a repo's .hub/hub*.json files, through Edit, Write or
  Delete, or through a shell redirect, tee, sed -i, cp, mv, rm, dd or an
  interpreter one-liner. Reading them with cat, ls, grep, jq, git diff and
  the like still runs, and so does a copy out of them. hub's own plugin
  files count as its settings here.

It reads the command with hub's shell parser (command_steps), so it looks
inside bash -c, eval, heredoc scripts and command substitutions as the PR
test gate does. It runs before the other gates. The refusal tells the model to ask the
user to ask for the change in their own words.
When hub can not read its settings, the dispatcher still asks this part, with
every check on, so a broken hub still refuses a call that touches hub.

Design limits. This part stops the model from helpfully unblocking itself in
the commands it writes. It is not a sandbox, and a model set on getting around
local checks on purpose can do so. The user lowers a protection by asking
for it in the newest message they type, or with the /hub door where mods
load, with a single-use ticket.
"""
import argparse
import contextlib
import fnmatch
import io
import os
import re
import shlex
import sys

from hub import command_steps, files

PART = "self_protection"
TEXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "text", "ask_the_user.md")
LEAVE_FOLDER = os.path.join(os.path.dirname(TEXT), "leave_hub_folder.md")
CHECKS = ("lowering", "agent_marker", "settings_files")
MAX_DEPTH = 4

MARKERS = ("AI_AGENT", "CURSOR_AGENT", "HUB_DOOR")
MARKER_WORD = re.compile(r"(?<![\w$])(?:%s)(?!\w)" % "|".join(MARKERS))
MARKER_ASSIGNMENT = re.compile(r"(?:%s)\+?=" % "|".join(MARKERS))
ENV_PROGRAMS = {"env", "unset", "export", "declare", "typeset", "local", "readonly", "setenv", "unsetenv"}
# hubctl as a word of code, not as a folder name in a path such as ~/.config/hubctl/hub.json
HUBCTL_IN_CODE = re.compile(r"(?<![\w./-])hubctl(?![\w/.-])|\bhub\.cli\b|\bhub_switch(?:\.py)?\b", re.I)
# Interpreter code that loads hub's own modules, which could write a ticket or change a flag past the switch step.
HUB_IMPORT = re.compile(r"\bfrom\s+hub(?:\.\w+)*\s+import\b|\bimport\s+(?:[\w.]+\s*,\s*)*hub\b|"
                        r"import(?:_module)?\s*\(\s*['\"]hub\b|\b(?:leave|take)_(?:words|uninstall)_ticket\b")
# The names of hub's switch step: the internal step, and hubctl, its name before it left the PATH.
STEP_NAMES = {"hub_switch.py", "hubctl"}

INTERPRETERS = re.compile(r"(?:python[\d.]*|pypy[\d.]*|perl|ruby|node|nodejs|deno|bun|php|lua|osascript|tclsh|expect|rscript)")
CODE_OPTIONS = {"-c", "-e", "-E", "--eval", "-r", "-p", "--print", "-x"}
# Python switches that take no value, run no code and load no module, so before the step's path they name no file.
PLAIN_PYTHON_SWITCHES = {"-I", "-B", "-s", "-S", "-u", "-q"}
# Programs that name hubctl without running it.
NAMES_ONLY = {"cat", "less", "more", "head", "tail", "ls", "stat", "file", "wc", "grep", "egrep", "fgrep", "rg",
              "ag", "jq", "diff", "cmp", "du", "tree", "readlink", "realpath", "echo", "printf", "test", "[",
              "which", "type", "whence", "where", "hash", "basename", "dirname", "git", "cp", "mv", "rm", "ln",
              "chmod", "chown", "install", "rsync", "ditto", "find", "sed", "md5", "md5sum", "shasum",
              "sha256sum", "xxd", "hexdump", "od", "strings", "bat", "cd", "pushd", "touch", "mkdir", "open"}
# Programs that read the paths they name and write none of them.
READERS = {"cat", "less", "more", "head", "tail", "ls", "stat", "file", "wc", "grep", "egrep", "fgrep", "rg",
           "ag", "jq", "diff", "cmp", "du", "tree", "readlink", "realpath", "echo", "printf", "test", "[",
           "basename", "dirname", "md5", "md5sum", "shasum", "sha256sum", "xxd", "hexdump", "od", "strings",
           "bat", "cd", "pushd", "which", "type"}
GIT_READS = {"diff", "log", "show", "status", "blame", "ls-files", "add", "grep", "cat-file"}
COPIERS = {"cp", "rsync", "install", "ln", "ditto", "scp"}
REMOVERS = {"rm", "rmdir", "mv", "chmod", "chown", "chflags", "unlink", "truncate", "shred", "srm", "trash"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
CHANGE_VERBS = {"on", "off", "set", "undo"}
# A word that runs hubctl or the claude plugin command when another program reads it as a command line.
RELAYED = re.compile(HUBCTL_IN_CODE.pattern + r"|(?<![\w.-])claude(?:-code)?\b[^\n]*\bplugins?\b", re.I)
CLAUDE_PROGRAM = re.compile(r"claude(?:-code)?(?:@.*)?")
# Programs that read the commands they run, or the keys they type, from their input.
STDIN_RUNNERS = {"ssh", "mosh", "crontab", "at", "batch", "expect", "script", "tclsh"}
# A shell function, as in name() { ... } or function name { ... }. Its body may print a script that the line runs.
FUNCTION_DEFINITION = re.compile(r"(?:^|[\s;&|(){}])(?:function[ \t]+[^\s;&|()<>]|[\w.:@+-]+[ \t]*\([ \t]*\))")
PLUGIN_VERBS = {"disable", "uninstall", "remove", "rm"}
PLUGIN_VALUE_OPTIONS = {"--scope", "-s"}
# tmux key names and what they type. A word that is not a key name is typed as it is. Keys that end the line
# or drop part of it map to the control character a terminal reads for them. Any other key moves the cursor,
# recalls or completes a line, so hub can not tell what the shell would run, and it maps to MOVES.
MOVES = "\x1b"
TMUX_KEYS = {"enter": "\n", "kpenter": "\n", "c-m": "\n", "c-j": "\n", "^m": "\n", "^j": "\n",
             "space": " ", "bspace": "\x7f", "c-h": "\x08", "^h": "\x08", "c-?": "\x7f", "^?": "\x7f",
             "c-u": "\x15", "^u": "\x15", "c-c": "\x03", "^c": "\x03", "c-w": "\x17", "^w": "\x17"}
# What each control character does to the line a terminal is typing: drop a character, the line or a word.
DROP_CHAR, DROP_LINE, DROP_WORD = "\x7f\x08", "\x15\x03", "\x17"
TMUX_KEY = re.compile(r"(?i:[CMS]-.+|\^.|F\d{1,2}|KP\w+|Enter|Space|Tab|BSpace|BTab|Escape|DC|End|Home|IC|NPage|PageDown|"
                      r"PgDn|PPage|PageUp|PgUp|Up|Down|Left|Right|Any)")
TMUX_TYPING = {"send-keys", "send", "set-buffer", "setb"}
TMUX_VALUE_OPTIONS = {"-t", "-N", "-c", "-b", "-T", "-n"}
ATTACHED_OUTPUT = re.compile(r"\d*(?:>>?|>\||&>>?)(?!&)(.+)")
# zsh ends a command at &! or &|, so a # right after one starts a comment. bash reads the same text
# differently, and the Bash tool may run either shell, so a command with these is checked both ways.
ZSH_DISOWN = re.compile(r"&[!|]")
# The variable that once named a door. No door reads it now, and a tool call that names it in any case is refused.
DOOR_VARIABLE = re.compile(r"hub_door", re.I)
# Programs that run their arguments as a command, often without the tool-call marker. Each argument with shell
# syntax is read as a command line of its own, and all of them together as one. A word hub can not read in
# them is refused.
RUNNERS = {"screen", "ssh", "mosh", "script", "watch", "su", "sudo", "doas", "runuser", "pkexec", "flock",
           "timeout", "nohup", "nice", "ionice", "taskset", "chrt", "env", "stdbuf", "setsid", "unbuffer",
           "caffeinate", "chroot", "systemd-run"}
# ssh and watch join their arguments with spaces and hand the text to a shell.
SPACE_JOINERS = {"ssh", "mosh", "watch"}
# tmux commands that start a command line, long and short names.
TMUX_RUNS = {"new", "new-session", "new-window", "neww", "split-window", "splitw", "respawn-pane", "respawnp",
             "respawn-window", "respawnw", "run-shell", "run", "if-shell", "if", "pipe-pane", "pipep",
             "display-popup", "popup"}
TMUX_GLOBAL_VALUES = {"-S", "-L", "-f", "-c", "-T"}
SHELL_SYNTAX = re.compile(r"[\s;&|<>()$`'\"\\]")
# git options whose value is a command line, by git command: the long name and its short letter.
GIT_COMMAND_OPTIONS = {"rebase": {"--exec": "x"}, "difftool": {"--extcmd": "x"},
                       "clone": {"--upload-pack": "u"}, "fetch": {"--upload-pack": None},
                       "pull": {"--upload-pack": None}, "ls-remote": {"--upload-pack": None},
                       "archive": {"--exec": None}, "push": {"--receive-pack": None, "--exec": None}}
# Variables that hand git a command line.
GIT_COMMAND_VARIABLES = re.compile(r"(?:GIT_\w+|PAGER|EDITOR|VISUAL)=")
FIND_RUNS = {"-exec", "-execdir", "-ok", "-okdir"}
# hubctl verbs that may run when another program starts hubctl: reads, and flag changes, which the checks
# below read as usual. That program may drop the tool-call marker, and then hubctl takes any other verb, such
# as pin, no, take or profile set, for the user's own, so the others are refused.
RELAYED_VERBS = {"show", "screen", "doctor", "offers", "on", "off", "set"}
# Claude Code reads a prompt line whose first word is one of these as the user's /hub. Only the user types
# them, so a tool call that types or pastes one into a terminal is refused, whatever it does.
DOOR_COMMANDS = {"/hub", "/hub-flags", "/hubctl", "/hub:hubctl", "/switch", "/hub:switch"}
# Interpreters whose code can type keys or fill the clipboard, and how their strings are written and joined.
TYPING_INTERPRETERS = {"osascript", "expect"}
STRING_CHAIN = re.compile(r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?:\s*[&+]\s*(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))*""")
STRING = re.compile(r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'""")
GLOB = re.compile(r"[*?\[{]")
PROJECT_NAMES = ("hub.json", "hub.local.json")
MAX_BRACES = 1024
VARIABLE = re.compile(r"\$\{(\w+)(?::?-([^}]*))?\}|\$(\w+)")
# Runners that start the next program with the caller's environment and read no command line of their own. Under
# them a word hub can not read passes in one place only: the value of a literal env setting that names no program.
ENV_KEEPING = {"env", "timeout", "nohup", "nice", "ionice", "taskset", "chrt", "stdbuf", "setsid", "unbuffer",
               "caffeinate"}
SETTING = re.compile(r"[A-Za-z_]\w*=")
# Settings whose value picks no program and no code, so a variable may fill in their value.
KEPT_SETTINGS = re.compile(r"(?:TMPDIR|LANG|LC_\w+|TERM|TZ|NO_COLOR|COLUMNS|LINES|PYTHONDONTWRITEBYTECODE"
                           r"|PYTHONUNBUFFERED)=")
# Fixed folders in front of a plain $PATH, as the parser marks the word: each $ the shell fills in has UNREADABLE.
PATH_SETTING = re.compile(r"PATH=(?:[^\x00-\x08:$`]+:)+\x00\$(?:PATH|\{PATH\})")
HELP_WORDS = ("--help", "-h")
# A line that changes these, or that runs code hub does not see, may point hub's switch step at another home.
# BASH_ENV, ENV and ZDOTDIR name a file or folder a shell runs at start, so they run code hub does not see.
HOME_NAMES = r"HOME|CLAUDE_CONFIG_DIR|XDG_\w+|BASH_ENV|ENV|ZDOTDIR"
EVAL_OR_SOURCE = re.compile(r"(?<![\w./-])(?:eval|source)(?![\w./-])")
DOT_COMMAND = re.compile(r"(?:^|[;&|(){}\n'\"`])\s*\.[ \t]")
# A builtin that sets a variable it names, when a variable or a substitution may fill in that name.
HIDDEN_ASSIGNMENT = re.compile(r"(?<![\w./-])(?:export|declare|typeset|local|readonly|read|unset|let|getopts|mapfile"
                               r"|readarray|vared|(?:printf|print)[ \t]+-v)(?![\w./-])[^;&|\n]*[$`]")
# zsh's path array, which sets PATH: path=(...), path+=(...), path[1]=..., or a builtin or a loop that names it.
ZSH_PATH = re.compile(r"(?<![\w./$-])path(?:\[[^\]]*\])?\+?=|(?<![\w./-])(?:export|declare|typeset|local|readonly"
                      r"|read|unset|getopts|mapfile|readarray|vared|for|select|printf|print)(?:[ \t]+[^\s;&|]+)*?[ \t]+path"
                      r"(?![\w./-])")
# The details of the refusals that carry their own hint in place of asking the user.
QUOTE = "a command line another program runs, with a word hub can not read"
SED_FILE = "a sed file name hub can not read, which sed could take as an option"
MOVED_HOME = "hub's switch step on a line that may move the home folder"
HINTS = {QUOTE: os.path.join(os.path.dirname(TEXT), "quote_the_value.md"),
         SED_FILE: os.path.join(os.path.dirname(TEXT), "sed_file_name.md"),
         MOVED_HOME: os.path.join(os.path.dirname(TEXT), "use_a_container.md")}
# A word may start with the user's home folder. hub can not read HOME, but it holds an absolute path or nothing,
# so the word starts with / either way.
HOME_STARTS = ("$HOME/", "${HOME}/")

WHAT = {
    "lowering": ("lower one of hub's protections, disable or uninstall hub, hide its notices, or run hub's "
               "switch step or claude plugin through a program hub can not read"),
    "agent_marker": "unset or change the marker that tells hub a tool call is running, or name HUB_DOOR",
    "settings_files": ("write hub's settings or state files, or start a program that hub can not read or that "
                       "would change a switch the user did not ask for"),
}
TOO_DEEP = "a line nested too deep for hub to check"


# The hook input of the call being checked, for the checks that read the chat.
CALL = {}


class Stop(Exception):
    """A check found a call it refuses."""

    def __init__(self, check, detail=""):
        super().__init__(check)
        self.check = check
        self.detail = detail


# The shell parser


def gate():
    """hub's reader of shell commands, which lists each step that makes text public."""
    return command_steps


def plain(word):
    """A word with the gate's marks taken out."""
    return word.replace(gate().SUBSTITUTED, "").replace(gate().UNREADABLE, "").replace(gate().SPLIT, "")


def hidden(word):
    return gate().UNREADABLE in word


def base(word):
    """The program name a word runs, in lower case.

    macOS and Windows ignore letter case in file names, so HUBCTL, ./HubCtl
    and /usr/local/bin/Hubctl all run hubctl there. hub reads them the same
    way on every platform, so no check depends on where hub runs.
    """
    return os.path.basename(plain(word)).casefold()


def program_index(words):
    keywords = gate().KEYWORDS
    for index, word in enumerate(words):
        if not re.match(r"[A-Za-z_]\w*\+?=", word) and word not in keywords:
            return index
    return None


def without_redirects(words):
    """The words with each redirect and its target taken out."""
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif gate().REDIRECT.fullmatch(word) or word in ("&>", "&>>", ">|", "<<<"):
            skip = True
        elif re.match(r"\d*(?:[<>]|&>)", word) or gate().HEREDOC_WORD.match(word):
            continue
        else:
            out.append(word)
    return out


# Paths


def expand(text):
    """A path as the shell would read it: ~ and the variables of this process filled in."""
    text = plain(text)
    if text == "~" or text.startswith("~/"):
        text = files.home() + text[1:]

    def fill(match):
        name = match.group(1) or match.group(3)
        value = os.environ.get(name)
        if value is None and match.group(1) and match.group(2) is not None:
            return match.group(2)
        return value if value is not None else match.group(0)

    return VARIABLE.sub(fill, text)


def hub_roots():
    """The user's settings folder and its old name, the state folder and the folder hub's own code runs from.

    hub moves what it finds in the old folder into the settings folder, so a write there is a write to the settings.
    """
    return (files.config_dir(), files.old_config_dir(), files.state_dir(), files.PLUGIN_ROOT)


def hub_folders():
    return [folded(os.path.realpath(folder)) for folder in hub_roots()]


def folded(path):
    """The path with its case folded, since macOS ignores letter case. So ~/.CONFIG/HUBCTL is hub's folder too."""
    return path.casefold()


# The folder in a repo's git folder where hub's pr-tested step writes the reports the PR test gate reads.
# The same name as FOLDER in pr_test_gate/report.py.
REPORTS = "pr-test-reports"
REPORTS_FOLDER = re.compile(r"(?:^|/)%s(?:/|$)" % re.escape(REPORTS))

# The settings folder under ~/.config, by its name and by its old name.
CONFIG_NAMES = "|".join(re.escape(name) for name in (files.CONFIG_NAME, files.OLD_CONFIG_NAME))
HUB_NAME = re.compile(r"\.config/(?:%s)(?:/|$)|\.local/state/hubctl|(?:^|/)hubctl/" % CONFIG_NAMES)
HUB_TEXT = re.compile(r"\.config/(?:%s)(?![\w.-])|\.local/state/hubctl|\.hub/hub|(?<![\w.-])%s(?![\w.-])"
                      % (CONFIG_NAMES, re.escape(REPORTS)))
PROJECT_FILE = re.compile(r"(?:^|/)\.hub(?:/(?:hub[^/]*\.json)?)?/?$")


def names_hub_by_name(path):
    """Whether a path's own words name hub's settings or state, a repo's .hub folder or settings file, or the
    folder of hub's test reports."""
    path = folded(path)
    return bool(HUB_NAME.search(path) or PROJECT_FILE.search(path) or REPORTS_FOLDER.search(path))


def under_hub_folder(path):
    path = folded(path)
    return any(path == folder or path.startswith(folder + os.sep) for folder in hub_folders())


def is_hub_path(word, cwd, globs=True):
    """Whether a word names hub's settings or state, or a repo's .hub folder or settings file.

    Each path is checked three ways. As written with dots and double slashes
    taken out, from the working folder, and after following its symlinks. So a
    link to a repo's .hub/hub.json counts as the project file. A glob counts
    when it could name one of them. With globs False, a glob counts only when
    its words name hub.
    """
    text = expand(word)
    if not text:
        return False
    if names_hub_by_name(text) or names_hub_by_name(os.path.normpath(text)):
        return True
    if "$" in text:
        return False
    if globs and GLOB.search(text):
        return glob_could_name_hub(text, cwd)
    if any(char in text for char in "*?["):
        return False
    joined = os.path.normpath(os.path.join(cwd, text))
    real = os.path.realpath(joined)
    # Only the project file's name counts here, so a repo folder of its own named hubctl is not hub's folder.
    return any(PROJECT_FILE.search(folded(path)) or REPORTS_FOLDER.search(folded(path)) or under_hub_folder(path)
               for path in (joined, real))


def braces(text):
    """The words a shell makes of a word with braces, such as a{b,c} or a{1..3}. None when there are too many."""
    words, done = [text], []
    while words:
        word = words.pop()
        found = None
        for start, char in enumerate(word):
            if char != "{":
                continue
            depth = 0
            for end in range(start, len(word)):
                depth += {"{": 1, "}": -1}.get(word[end], 0)
                if depth == 0:
                    break
            else:
                continue
            inner = word[start + 1:end]
            parts, depth, last = [], 0, 0
            for spot, inner_char in enumerate(inner):
                depth += {"{": 1, "}": -1}.get(inner_char, 0)
                if inner_char == "," and depth == 0:
                    parts.append(inner[last:spot])
                    last = spot + 1
            parts.append(inner[last:])
            sequence = re.fullmatch(r"(-?\d+|.)\.\.(-?\d+|.)(?:\.\.-?\d+)?", inner)
            if len(parts) > 1:
                found = (start, end, parts)
            elif sequence:
                low, high = sequence.groups()
                numbers = low.lstrip("-").isdigit() and high.lstrip("-").isdigit()
                low, high = (int(low), int(high)) if numbers else (ord(low), ord(high))
                if abs(high - low) >= MAX_BRACES:
                    return None
                step = 1 if low <= high else -1
                found = (start, end, [str(value) if numbers else chr(value) for value in range(low, high + step, step)])
            if found:
                break
        if found is None:
            done.append(word)
            continue
        start, end, parts = found
        if len(parts) + len(words) + len(done) > MAX_BRACES:
            return None
        words.extend(word[:start] + part + word[end + 1:] for part in parts)
    return done


def pattern_parts(pattern):
    return [part for part in folded(pattern).split(os.sep) if part]


def names_folder_or_under(pattern, folder):
    """Whether a glob's parts could match the folder's parts, or a path under the folder. ** matches any depth."""
    def match(at, depth):
        if depth == len(folder):
            return True
        if at == len(pattern):
            return False
        if pattern[at] == "**":
            return match(at + 1, depth) or match(at, depth + 1)
        return fnmatch.fnmatchcase(folder[depth], pattern[at]) and match(at + 1, depth + 1)
    return match(0, 0)


def ends_with(pattern, tail):
    """Whether a glob's parts could match a path that ends in the tail's parts."""
    def match(at, depth):
        if at == len(pattern):
            return depth == len(tail)
        if pattern[at] == "**":
            return match(at + 1, depth) or (depth < len(tail) and match(at, depth + 1))
        return depth < len(tail) and fnmatch.fnmatchcase(tail[depth], pattern[at]) and match(at + 1, depth + 1)
    return any(match(start, 0) for start in range(len(pattern)))


def glob_could_name_hub(text, cwd):
    """Whether a glob could name a hub folder, a path under one, or a repo's .hub folder or hub*.json file.

    It is read part by part with case folded, braces expanded and ** at any
    depth, whether or not anything matches now. So a write to ~/.config/hub*/x
    is refused before ~/.config/hubctl exists. The Bash tool's zsh lets * match
    a name that starts with a dot, so a bare * could name a repo's .hub folder.
    """
    expanded = braces(text)
    if expanded is None:  # too many words to read, so it counts when it could name hub at all
        return bool(re.search(r"[*?\[]|hub", folded(text)))
    folders = [pattern_parts(folder) for root in hub_roots() for folder in {os.path.abspath(root), os.path.realpath(root)}]
    tails = [[".hub"]] + [[".hub", name] for name in PROJECT_NAMES]
    for word in expanded:
        joined = os.path.normpath(os.path.join(cwd, word))
        first = GLOB.search(joined)
        fixed = os.path.dirname(joined[:first.start() + 1]) if first else os.path.dirname(joined)
        spellings = {joined, os.path.join(os.path.realpath(fixed), joined[len(fixed):].lstrip(os.sep))}
        for spelling in spellings:
            parts = pattern_parts(spelling)
            if any(names_folder_or_under(parts, folder) for folder in folders):
                return True
            if any(ends_with(parts, tail) for tail in tails):
                return True
            if any(fnmatch.fnmatchcase(REPORTS, part) for part in parts):
                return True
    return False


def holds_hub(word, cwd):
    """Whether a word names a folder that holds hub's settings or state, or a glob that may match them."""
    text = expand(word)
    if not text or "$" in text:
        return False
    glob_at = min([text.index(char) for char in "*?[" if char in text] or [len(text)])
    prefix = os.path.join(cwd, text[:glob_at])
    if glob_at < len(text):
        prefixes = {folded(prefix), folded(os.path.normpath(prefix)), folded(os.path.realpath(prefix))}
        return any(folder.startswith(start) for folder in hub_folders() for start in prefixes)
    path = folded(os.path.realpath(prefix))
    return any(folder.startswith(path.rstrip(os.sep) + os.sep) for folder in hub_folders())


def names_hub(word, cwd):
    """Whether a word, or the value after its = (as in of=path or --output=path), names a hub path."""
    if is_hub_path(word, cwd):
        return True
    _name, equals, value = plain(word).partition("=")
    return bool(equals) and is_hub_path(value, cwd)


# What a whole line may change


def touches_names(text, names):
    """Whether the text names one of these variables in any form but a plain read, $NAME or ${NAME}."""
    def is_plain_read(match):
        before, after = text[:match.start()], text[match.end():]
        return before.endswith("$") or (before.endswith("${") and after.startswith("}"))
    return any(not is_plain_read(match) for match in re.finditer(rf"(?<!\w)(?:{names})(?!\w)", text))


def runs_hidden_code(text):
    """Whether the text runs code hub does not read as part of the line: eval, source, the . command, or a builtin
    that sets a variable whose name a variable or a substitution may fill in."""
    return bool(EVAL_OR_SOURCE.search(text) or DOT_COMMAND.search(text) or HIDDEN_ASSIGNMENT.search(text))


def moves_home(whole):
    """Whether a line may give a program another HOME, XDG folder or CLAUDE_CONFIG_DIR than the user's own."""
    return touches_names(whole, HOME_NAMES) or runs_hidden_code(whole)


def changes_path(whole, setting):
    """Whether a line may change PATH other than through this one env setting, given as plain text.

    The line may hold the setting as it is, in double quotes as a whole, or
    with its value in double quotes, as in PATH="/opt/tools/bin:$PATH".
    """
    name, _equals, value = setting.partition("=")
    written = next((text for text in (setting, f'{name}="{value}"') if text in whole), None)
    if written is None:
        return True  # hub can not find the setting as written, so it can not tell the rest apart
    at = whole.find(written)
    rest = whole[:at] + whole[at + len(written):]
    return touches_names(rest, "PATH") or bool(ZSH_PATH.search(rest)) or runs_hidden_code(rest)


def is_step_change(args):
    """Whether the switch step's words may change something: anything but show, or help on its own."""
    return not (args and plain(args[0]) == "show" or only_help(args))


def only_help(words):
    """Whether the words are one help option and nothing else."""
    return len(words) == 1 and not hidden(words[0]) and plain(words[0]) in HELP_WORDS


# The checks of one simple command


def interpreter_code(words, command):
    """The code an interpreter runs from its options, a here-string or a heredoc, or None."""
    index = program_index(words)
    if index is None or not INTERPRETERS.fullmatch(base(words[index])):
        return None
    code = []
    for spot, word in enumerate(words[index + 1:], index + 1):
        following = words[spot + 1] if spot + 1 < len(words) else ""
        if word in CODE_OPTIONS or word == "<<<":
            code.append(following)
        elif re.fullmatch(r"-[A-Za-z]*[ceE]", word) and not word.startswith("--"):
            code.append(following)
        elif word.startswith("<<<"):
            code.append(word[3:])
        elif command.heredoc_script(word) is not None:
            code.append(command.heredoc_script(word))
    return "\n".join(code) if code else None


def hubctl_words(words, line):
    """The words after hubctl when this command runs it, or None. Raises Stop when it can not tell."""
    index = program_index(words)
    if index is None:
        return None
    program = base(words[index])
    if hidden(os.path.basename(words[index])) and any(name in line.casefold() for name in ("hubctl", "hub_switch")):
        raise Stop("lowering", "a variable names the program")
    if program in NAMES_ONLY or (program == "command" and any(word in ("-v", "-V") for word in words)):
        return None
    for spot in range(index, len(words)):
        word = words[spot]
        if base(word) in STEP_NAMES:
            found = spot + 1
        elif plain(word) == "-m" and spot + 1 < len(words) and plain(words[spot + 1]) in ("hub.cli", "hub"):
            found = spot + 2
        else:
            continue
        if any(base(before) in gate().FEEDERS for before in words[:spot]):
            raise Stop("lowering", "xargs fills in its words")
        return without_redirects(words[found:])
    return None


def parsed_hubctl(args):
    """hubctl's words as its own parser reads them, so an abbreviated option counts.

    None when hubctl would reject them and run nothing.
    """
    from hub import cli
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return cli.parser(cli.part_commands()).parse_args([plain(word) for word in args])
    except SystemExit:
        return None


def check_hubctl(args, flags, cwd, relayed=False):
    """Refuse hubctl words that lower a protection.

    relayed is True when another program starts hubctl. That program may drop
    the tool-call marker, so hubctl would take the call for the user's own.
    Then only the RELAYED_VERBS run, and only with words hubctl accepts.
    """
    if not args:
        return
    if relayed and only_help(args):
        return  # help on its own prints text and changes nothing
    verb = args[0]
    if hidden(verb):
        raise Stop("lowering", "a variable fills in the verb")
    if verb.startswith("-") and not relayed:
        return
    if verb in CHANGE_VERBS | {"uninstall", "screen"} and any(hidden(word) for word in args):
        raise Stop("lowering", "a variable fills in a word")
    try:
        parsed = parsed_hubctl(args)
    except Exception:  # hub's own parser broke, so it can not tell what hubctl would do
        if relayed or verb in CHANGE_VERBS | {"uninstall", "screen"}:
            raise Stop("lowering", "hub could not read the words") from None
        return
    if relayed and (parsed is None or parsed.command not in RELAYED_VERBS or getattr(parsed, "band", False)):
        # find and xargs fill in words hub can not see, so words hubctl would reject count too
        raise Stop("lowering", "a hubctl verb that only the user may run, started by another program")
    if parsed is None:
        return
    if parsed.command == "uninstall":
        if relayed or not asked_to_uninstall():
            raise Stop("lowering", "uninstall")
        from hub import protect
        protect.leave_uninstall_ticket(CALL.get("session_id"))
        return
    if parsed.command == "screen" and parsed.band:
        raise Stop("lowering", "the band's read, which marks notices as shown")
    if parsed.command not in CHANGE_VERBS:
        return
    if flags.record is None:  # hub could not read its settings, so every change counts
        raise Stop("lowering", parsed.command)
    from hub import changes, cli, tree as tree_module
    tree = tree_module.load(files.PARTS_DIR)
    if parsed.command == "undo":
        from hub import history
        try:
            line = history.undoable(parsed.id) if parsed.id else history.last_undoable()
        except changes.ChangeError:
            return
        if not line or "flag" not in line:
            return
        flag_id, new = line["flag"], line["old"]
        scope, cwd = line.get("scope", "user"), line.get("repo") or cwd
    else:
        scope = parsed.scope
        flag_id = parsed.flag
        node = tree.get(flag_id)
        if node is None:
            return
        try:
            if parsed.command == "off":
                new = "off"
            elif parsed.command == "on":
                new = changes.on_value(node)
            else:
                new = cli.value_for(node, parsed.values)
        except changes.ChangeError:
            return
    node = tree.get(flag_id)
    record = flags.record(flag_id)
    if node is None or record is None:
        return
    old = record.get("set", record.get("value"))
    if new == old:
        return
    lowering = is_protected(node) and lowers(node, new, old)
    if record.get("by") == "user":
        stop = Stop("lowering", f"{flag_id} is pinned")
    elif lowering:
        stop = Stop("lowering", flag_id)
    else:
        try:
            check_stopped(tree, node, new, scope, cwd)
            return
        except Stop as found:
            stop, lowering = found, True
    if relayed or not asked_in_words(flag_id, lowering):
        raise stop
    try:
        value = changes.check_value(node, new)
    except changes.ChangeError:
        raise stop from None
    from hub import protect
    protect.leave_words_ticket(flag_id, value, scope, CALL.get("session_id"))


def asked_in_words(flag_id, lowering):
    """Whether the newest message the user typed in this chat asks for this change. An agent's call never counts."""
    if CALL.get("agent_id"):
        return False
    from hub import said
    return said.user_asked(CALL.get("transcript_path"), flag_id, lowering)


def asked_to_uninstall():
    """Whether the newest message the user typed in this chat asks to uninstall hub. An agent's call never counts."""
    if CALL.get("agent_id"):
        return False
    from hub import said
    return said.user_asked_to_uninstall(CALL.get("transcript_path"))


def check_stopped(tree, node, value, scope, cwd):
    """Refuse a change that hubctl would refuse from a tool call, such as turning off a part that would
    stop its running guard or a value the user set by hand."""
    from hub import changes
    try:
        value = changes.check_value(node, value)
        repo = changes.repo_for(scope, cwd)
    except changes.ChangeError:
        return  # hubctl itself rejects the words or the scope, and changes nothing
    path, place = changes.settings_path(scope, repo), repo or cwd
    try:
        changes.check_change(tree, node, value, "model", scope, path, place, None,
                             lambda: changes.resolved_files(tree, place))
    except changes.ChangeError:
        raise Stop("lowering", node.id) from None
    except Exception:  # a settings file hub can not read, so it can not tell what the change stops
        raise Stop("lowering", f"{node.id}, since hub could not read its settings") from None


def is_protected(node):
    """Whether only the user may lower a flag: a privacy check, a gate's mode, or a guard or a flag under one."""
    if node.privacy not in (None, "none"):
        return True
    if node.parent is None and node.family == "gates" and node.type == "mode":
        return True
    current = node
    while current is not None:
        if current.name == "guard" or current.name.endswith("_guard"):
            return True
        current = current.parent
    return False


def lowers(node, new, old):
    """Whether new is weaker than old. A change that hub can not compare counts as weaker."""
    from hub import resolve
    if new == old:
        return False
    if node.privacy == "loosens":
        return resolve.lowers(node, new, old)
    if node.type == "list":
        if node.privacy == "guard":
            return any(item not in (new or []) for item in (old or []))
        return True  # a list under a guard, such as the agents it allows: adding to it loosens the guard
    if node.type in ("switch", "mode"):
        new_strength, old_strength = resolve.strength(node, new), resolve.strength(node, old)
        return new_strength is None or old_strength is None or new_strength < old_strength
    return True


def check_marker(words, command):
    for word in words:
        if MARKER_ASSIGNMENT.match(plain(word)):
            raise Stop("agent_marker", "an assignment")
    for spot, word in enumerate(words):
        program = base(word)
        if program in ENV_PROGRAMS and any(MARKER_WORD.search(plain(later)) for later in words[spot + 1:]):
            raise Stop("agent_marker", program)
        if program == "env":
            for later in words[spot + 1:]:
                later = plain(later)
                if later in ("-", "--ignore-environment") or re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", later):
                    raise Stop("agent_marker", "env -i")
                if not later.startswith("-") and not re.match(r"\w+=", later):
                    break
        if program == "exec" and spot + 1 < len(words) and re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", plain(words[spot + 1])):
            raise Stop("agent_marker", "exec -c")
    code = interpreter_code(words, command)
    if code and MARKER_WORD.search(plain(code)):
        raise Stop("agent_marker", "interpreter code")


def check_files(words, command, cwd):
    for spot, word in enumerate(words):
        following = words[spot + 1] if spot + 1 < len(words) else ""
        if gate().REDIRECT.fullmatch(word) and ">" in word or word in ("&>", "&>>", ">|"):
            if names_hub(following, cwd):
                raise Stop("settings_files", "a redirect")
        else:
            attached = ATTACHED_OUTPUT.fullmatch(word)
            if attached and names_hub(attached.group(1), cwd):
                raise Stop("settings_files", "a redirect")
    index = program_index(words)
    # An assignment before the program, such as PYTHONPATH=<hub's code>, points the program at hub's files.
    if any(names_hub(word, cwd) for word in words[:len(words) if index is None else index]
           if re.match(r"[A-Za-z_]\w*\+?=", plain(word))):
        raise Stop("settings_files", "an assignment")
    if index is None:
        return
    program = base(words[index])
    operands = without_redirects(words[index + 1:])
    code = interpreter_code(words, command)
    if code and (HUB_TEXT.search(folded(code))
                 or any(is_hub_path(word, cwd, globs="/" in word) for word in code.split())):
        raise Stop("settings_files", "interpreter code")
    if program == "git":
        # A read only when git's action reads. The first word that is not an option must read too, as before.
        # The folder after -C names no action, so git -C <folder> grep reads as a read.
        first = next((plain(word) for spot, word in enumerate(operands)
                      if not plain(word).startswith("-") and not (spot and plain(operands[spot - 1]) == "-C")), "")
        if first in GIT_READS and git_action(operands) in GIT_READS:
            if any(names_hub(word, cwd) for word in git_output_files(operands)):
                raise Stop("settings_files", "git --output")
            return
    elif program == "sed":
        if not any(re.fullmatch(r"-[A-Za-z]*i.*", plain(word)) or plain(word).startswith("--in-place")
                   for word in operands):
            return
    elif program == "find":
        if not any(plain(word) in FIND_WRITES for word in operands):
            return
        if any(holds_hub(word, cwd) for word in operands if not plain(word).startswith("-")):
            raise Stop("settings_files", "find")
    elif program in READERS:
        return
    elif program == "ln":
        if any(names_hub(word, cwd) or holds_hub(word, cwd) for word in operands if not plain(word).startswith("-")):
            raise Stop("settings_files", "ln")
        return
    elif program in COPIERS:
        paths = [word for word in operands if not plain(word).startswith("-")]
        target = next((plain(value) for name, value in zip(operands, operands[1:])
                       if plain(name) in ("-t", "--target-directory")), None)
        destination = target or (paths[-1] if len(paths) > 1 else "")
        if destination and names_hub(destination, cwd):
            raise Stop("settings_files", program)
        return
    elif program in REMOVERS:
        if any(names_hub(word, cwd) or holds_hub(word, cwd) for word in operands if not plain(word).startswith("-")):
            raise Stop("settings_files", program)
        return
    # The switch step writes hub's settings by design, and the lowering check judges what it changes.
    if runs_the_step(words[index], cwd):
        operands = judged_step_words(operands)
    elif INTERPRETERS.fullmatch(program):
        script = next((spot for spot, word in enumerate(operands) if not plain(word).startswith("-")), None)
        if script is not None and runs_the_step(operands[script], cwd):
            switches = [word for word in operands[:script] if not is_plain_python_switch(program, word)]
            operands = switches + judged_step_words(operands[script + 1:])
    if any(names_hub(word, cwd) for word in operands):
        raise Stop("settings_files", program)


def git_output_files(words):
    """The words that name the file a git read, such as diff, log or show, writes its output to. That is the word
    with --output=<file>, or the word after --output, also when the option's name is cut short."""
    def is_output_option(word):
        name = plain(word).partition("=")[0]
        return len(name) >= 4 and "--output".startswith(name)
    return [word if "=" in plain(word) else following
            for word, following in zip(words, words[1:] + [""]) if is_output_option(word)]


def is_plain_python_switch(program, word):
    """Whether a word before the step's path is one of Python's switches that take no value and run no code.

    Every other word stays judged as a path, such as a switch with a value
    or code joined to it, a module, a group of switches, or another
    interpreter's switch.
    """
    return bool(re.fullmatch(r"python[\d.]*", program)) and not hidden(word) and plain(word) in PLAIN_PYTHON_SWITCHES


def judged_step_words(args):
    """The switch step's words that the files check still judges as paths.

    The step's own fixed words name no file, so a read such as show --json
    runs even from inside hub's folder. They are the verb, an option's name,
    an option's value from its fixed list, a flag or part that hub's tree
    knows, an undo id that hub's history knows, and the tuner's step. Every
    other word stays judged: a word with =, a word after --, a value, a file,
    a profile word, and every word when hub's parser rejects them or hub can
    not read them.
    """
    try:
        fixed = fixed_step_spots(args)
    except Exception:  # hub could not read its parser, tree or history, so every word stays judged
        fixed = set()
    return [word for spot, word in enumerate(args) if spot not in fixed]


def fixed_step_spots(args):
    """The places in the step's words that hold its own fixed words, by its parser's own options."""
    if not args or any(hidden(word) for word in args) or parsed_hubctl(args) is None:
        return set()
    from hub import cli, history, tree as tree_module
    top = cli.parser(cli.part_commands())
    commands = next(action for action in top._actions if isinstance(action, argparse._SubParsersAction))
    command = commands.choices.get(plain(args[0]))
    if command is None:
        return set()
    tree = tree_module.load(files.PARTS_DIR)
    known_ids = {line.get("id") for line in history.lines() if isinstance(line, dict)}
    options = {name: action for action in command._actions for name in action.option_strings}
    positionals = iter([action for action in command._actions if not action.option_strings])
    fixed, spot = {0}, 1
    while spot < len(args):
        text = plain(args[spot])
        if text == "--":
            break
        if "=" in text:
            spot += 1
            continue
        if text.startswith("-"):
            action = option_named(options, text)
            if action is not None:
                fixed.add(spot)
                if action.nargs != 0:
                    value = plain(args[spot + 1]) if spot + 1 < len(args) else None
                    if value is not None and action.choices and value in action.choices:
                        fixed.add(spot + 1)
                    spot += 1
            spot += 1
            continue
        action = next(positionals, None)
        if action is None or action.nargs not in (None, "?"):
            break  # a list of values, a profile's words or a part's own words
        if is_fixed_word(action, text, tree, known_ids):
            fixed.add(spot)
        spot += 1
    return fixed


def option_named(options, text):
    """The option a word names, by its full name or a unique start of a long name, as argparse reads it."""
    if text in options:
        return options[text]
    names = [name for name in options if text.startswith("--") and name.startswith(text)]
    return options[names[0]] if len(names) == 1 else None


def is_fixed_word(action, text, tree, known_ids):
    """Whether a positional word is one hub fixes: a choice, a flag or part in hub's tree, or a known undo id."""
    if action.choices:
        return text in action.choices
    if action.dest in ("flag", "part"):
        return tree.get(text) is not None
    if action.dest == "id":
        return text in known_ids
    return False


def runs_the_step(word, cwd):
    """Whether a word is the path of hub's own switch step, as the script an interpreter runs."""
    text = expand(word)
    if not text or "$" in text or any(char in text for char in "*?["):
        return False
    return os.path.realpath(os.path.join(cwd, text)) == os.path.realpath(files.STEP)


# Walking a command line


def check_claude(words):
    """Refuse claude plugin disable, uninstall or remove of hub, or of a plugin hub can not name."""
    index = program_index(words)
    if index is None or base(words[index]) in NAMES_ONLY:
        return
    for spot in range(index, len(words) - 1):
        named = CLAUDE_PROGRAM.fullmatch(base(words[spot]).casefold())
        if not (named or hidden(words[spot])) or plain(words[spot + 1]).casefold() not in ("plugin", "plugins"):
            continue
        if any(base(before) in gate().FEEDERS for before in words[:spot]):
            raise Stop("lowering", "xargs fills in the words of claude plugin")
        rest = words[spot + 2:]
        positional = [(at, plain(word).casefold()) for at, word in enumerate(rest) if not plain(word).startswith("-")]
        if not positional:
            continue
        verb_at, verb = positional[0]
        if verb == "marketplace":
            if len(positional) > 1 and positional[1][1] in PLUGIN_VERBS and not only_help(rest[positional[1][0] + 1:]):
                raise Stop("lowering", "claude plugin marketplace remove")
            continue
        if verb not in PLUGIN_VERBS or only_help(rest[verb_at + 1:]):
            continue
        targets, skip = [], False
        for word in rest[verb_at + 1:]:
            if skip:
                skip = False
            elif plain(word) in PLUGIN_VALUE_OPTIONS:
                skip = True
            elif plain(word) in ("--all", "-a") or hidden(word):
                raise Stop("lowering", f"claude plugin {verb}")
            elif not plain(word).startswith("-"):
                targets.append(plain(word).casefold())
        if not targets or any(target == "hub" or target.startswith("hub@") for target in targets):
            raise Stop("lowering", f"claude plugin {verb}")


def tmux_typed(words):
    """The texts tmux types into a pane, or keeps in a buffer to paste, from send-keys and set-buffer.

    Key names become the characters a terminal reads for them. A key that
    moves the cursor, recalls or completes a line becomes MOVES.
    """
    typed, current, skip, literal = [], None, False, False
    for word in words:
        text = plain(word)
        if text == ";":
            current = None
        elif current is None:
            if text in TMUX_TYPING:
                current, literal = text, False
                typed.append("")
        elif skip:
            skip = False
        elif text in TMUX_VALUE_OPTIONS and not typed[-1]:
            skip = True
        elif re.fullmatch(r"-[A-Za-z]+", text) and not typed[-1]:
            literal = literal or "l" in text
            if "H" in text:
                current = "hex"
        elif current == "hex":
            try:
                typed[-1] += chr(int(text, 16))
            except ValueError:
                typed[-1] += text
        elif not literal and current in ("send-keys", "send") and TMUX_KEY.fullmatch(text):
            typed[-1] += TMUX_KEYS.get(text.casefold(), MOVES)
        else:
            typed[-1] += text
    return typed


def screen_typed(words):
    """The texts screen's stuff command types into a window, with its ^X and backslash escapes read."""
    texts = [plain(later) for word, later in zip(words, words[1:]) if plain(word) == "stuff"]

    def control(match):
        letter = match.group(1).upper()
        if letter == "?":
            return "\x7f"
        return chr(ord(letter) ^ 0x40) if "@" <= letter <= "_" else match.group(0)

    out = []
    for text in texts:
        text = re.sub(r"\\([0-7]{1,3})", lambda match: chr(int(match.group(1), 8)), text)
        text = re.sub(r"\\[nr]", "\n", text)
        out.append(re.sub(r"\^(.)", control, text))
    return out


def typed_texts(words):
    index = program_index(words)
    if index is None:
        return []
    program = base(words[index])
    if program == "tmux":
        return tmux_typed(words[index + 1:])
    if program == "screen":
        return screen_typed(words[index + 1:])
    return []


def types_a_door(text):
    """Whether typed text holds a line whose first word is a /hub command, in any case."""
    for line in re.split(r"[\r\n]", text):
        words = line.split()
        if words and words[0].casefold() in DOOR_COMMANDS:
            return True
    return False


def code_types_a_door(code):
    """Whether interpreter code holds a string, or strings joined with & or +, that types a /hub command."""
    escapes = {"n": "\n", "r": "\r", "t": "\t"}
    for chain in STRING_CHAIN.finditer(code):
        joined = "".join(double + single
                         for double, single in STRING.findall(chain.group(0)))
        if types_a_door(re.sub(r"\\(.)", lambda match: escapes.get(match.group(1), match.group(1)), joined)):
            return True
    return False


def typed_lines(text):
    """The lines a shell could run from typed keys: after the terminal's line editing, and as typed.

    Backspace drops a character, Ctrl-U and Ctrl-C drop the line, and Ctrl-W
    drops a word. A pasted buffer may keep those keys as text, so the lines
    are read a second time with them taken out. A key that moves the cursor,
    recalls or completes a line, or any other control character, makes a line
    hub can not read, so it raises Stop.
    """
    edited, line = [], ""
    for char in text:
        if char in "\r\n":
            edited.append(line)
            line = ""
        elif char in DROP_CHAR:
            line = line[:-1]
        elif char in DROP_LINE:
            line = ""
        elif char in DROP_WORD:
            line = re.sub(r"\S*$", "", line.rstrip())
        elif ord(char) < 0x20:
            raise Stop("lowering", "keys that move the cursor, recall or complete a line")
        else:
            line += char
    as_typed = re.split(r"[\r\n]", re.sub(f"[{DROP_CHAR}{DROP_LINE}{DROP_WORD}]", "", text))
    return list(dict.fromkeys(line for line in edited + [line] + as_typed if line.strip()))


def relayed_scripts(words):
    """The command lines another program runs from this command's words: tmux, screen, ssh, script and the like.

    A word that names hubctl or the claude plugin command is a command line of
    its own when it holds more than one word, and the words from the first
    such word to the end are one too, as ssh and screen join them. A word with
    a variable or a substitution in it counts the same way, because it may
    fill in the program. A program that only names them, such as echo or git,
    runs none.
    """
    index = program_index(words)
    if index is None or base(words[index]) in NAMES_ONLY:
        return []
    start = index + 1
    if INTERPRETERS.fullmatch(base(words[index])):
        # An interpreter that runs the switch step itself relays nothing: the step's own words are checked as such.
        script = next((spot for spot in range(start, len(words)) if not plain(words[spot]).startswith("-")), None)
        if script is not None and base(words[script]) in STEP_NAMES:
            start = script + 1
    scripts, joined = [], False
    for spot in range(start, len(words)):
        text = plain(words[spot])
        named = RELAYED.search(text)
        if (named or hidden(words[spot])) and not joined:
            scripts.append(" ".join(plain(word) for word in words[spot:]))
            joined = True
        if (named or hidden(words[spot])) and re.search(r"[\s;&|]", text):
            # the plain text, so the parser marks any variable or substitution in it again
            scripts.append(text)
    return list(dict.fromkeys(scripts))


def runner_check(flags):
    """The check a refused runner counts under: hubctl when it runs, else settings_files."""
    return "lowering" if flags.runs(f"{PART}.lowering") else "settings_files"


def unreadable_runner(words, flags):
    """Refuse a word hub can not read in a command line another program runs."""
    if any(hidden(word) for word in words):
        raise Stop(runner_check(flags), QUOTE)


def unreadable_env_setting(chain, marked, flags, whole):
    """Refuse a word hub can not read under a runner that keeps the environment, unless it is a setting env
    passes on. chain holds the runner and its words, and marked the same words as the parser marks them for a
    reader that asks for many_words, or no words when that reading does not line up."""
    for spot, word in enumerate(chain):
        if hidden(word) and not is_kept_setting(chain, marked, spot, whole):
            raise Stop(runner_check(flags), QUOTE)


def is_kept_setting(chain, marked, spot, whole):
    """Whether the word at spot is the value of a literal env setting that names no program.

    env must be a literal word, with no runner, shell or eval between the
    first program and it, and only NAME=value words between it and this one.
    The name is on a short list, or it is PATH with fixed folders in front of
    a plain $PATH on a line that changes PATH nowhere else. In both cases the
    shell must keep the value as one word, as it does in double quotes.
    """
    env_at = next((at for at in range(spot - 1, -1, -1) if base(chain[at]) == "env" and not hidden(chain[at])), None)
    if env_at is None or len(marked) != len(chain):
        return False
    runs_another = RUNNERS | gate().SHELLS | {"eval"}
    if any(hidden(word) or base(word) in runs_another for word in chain[1:env_at]):
        return False
    if not all(SETTING.match(word) for word in chain[env_at + 1:spot + 1]):
        return False
    value = marked[spot]
    if gate().MANY_WORDS in value or gate().WORD_CHANGE in value:
        return False
    if KEPT_SETTINGS.match(value):
        return True
    return bool(PATH_SETTING.fullmatch(value)) and not changes_path(whole, plain(chain[spot]))


def lined_up(marked, words):
    """The marked words when they line up with the words, else no words, so every check on them fails."""
    return list(marked) if marked is not None and len(marked) == len(words) else []


def is_fixed_start(word, marked, spot):
    """Whether a word the shell fills in part of stays one word and starts with fixed text that is not an option,
    as "./notes/$name" does. marked holds the words as the parser marks them for a reader that asks for many_words."""
    if not stays_one_word(marked, spot):
        return False
    return not word.startswith(gate().UNREADABLE) and bool(plain(word)) and not plain(word).startswith("-")


def is_home_start(word, marked, spot, whole):
    """Whether a word the shell fills in part of stays one word and starts with the user's home folder, as
    "$HOME/notes.txt" does, on a line that does not change HOME or run code hub does not see."""
    if not stays_one_word(marked, spot):
        return False
    starts = tuple(gate().UNREADABLE + start for start in HOME_STARTS)
    return word.startswith(starts) and not touches_names(whole, "HOME") and not runs_hidden_code(whole)


def stays_one_word(marked, spot):
    """Whether the shell keeps the marked word at spot as one word, with no change to it, as in double quotes."""
    return spot < len(marked) and gate().MANY_WORDS not in marked[spot] and gate().WORD_CHANGE not in marked[spot]


def as_scripts(words, raw=False):
    """Each word with shell syntax as a command line of its own, and all of them as one.

    raw joins the words with spaces, as ssh does. Otherwise each is quoted, as
    a program that runs its arguments directly passes them.
    """
    scripts = [plain(word) for word in words if SHELL_SYNTAX.search(plain(word))]
    if words:
        scripts.append(" ".join(plain(word) if raw else shlex.quote(plain(word)) for word in words))
    return scripts


def tmux_runs(words):
    """The arguments of each tmux command in the words that starts a command line."""
    found, at = [], 0
    while at < len(words) and plain(words[at]).startswith("-"):
        at += 2 if plain(words[at]) in TMUX_GLOBAL_VALUES else 1
    while at < len(words):
        end = next((spot for spot in range(at, len(words)) if plain(words[spot]) in (";", "\\;")), len(words))
        if plain(words[at]) in TMUX_RUNS:
            found.append(words[at + 1:end])
        at = end + 1
    return found


def option_value(words, spot, long_name, short):
    """The value of a git option at this word, or None: --name=value, --name value, a unique prefix of
    --name, -Xvalue, -X value, or X at the end of a cluster such as -iX value. Returns (value, next spot)."""
    text = plain(words[spot])
    following = words[spot + 1] if spot + 1 < len(words) else ""
    name, equals, value = text.partition("=")
    if text.startswith("--") and len(name) >= 4 and long_name.startswith(name):
        return (value, spot + 1) if equals else (plain(following), spot + 2)
    if short and re.fullmatch(r"-[A-Za-z]+.*", text) and not text.startswith("--") and short in text:
        rest = text[text.index(short) + 1:]
        return (rest, spot + 1) if rest else (plain(following), spot + 2)
    return None


def git_option_words(option):
    """How many words one of git's own options takes up: two when it takes the next word as its value.

    The set of those options is the shell parser's, so both find the same
    action. A bare --exec-path makes git print a path and stop. It still
    takes up two words here, as it did before the set was shared.
    """
    return 2 if option in gate().GIT_VALUE_OPTIONS or option == "--exec-path" else 1


def git_action(words):
    """git's action in the words after git: the first word past git's own options and their values."""
    at = 0
    while at < len(words) and plain(words[at]).startswith("-"):
        at += git_option_words(plain(words[at]))
    return plain(words[at]) if at < len(words) else ""


def git_scripts(before, words):
    """The command lines git runs from its words, as (script, the word it comes from) pairs.

    They are -c values, config values, the command options of rebase,
    difftool, clone, fetch, pull, ls-remote, archive and push, bisect run,
    submodule foreach, filter-branch filters, and the GIT_ and PAGER
    variables set before git. An alias that starts with ! is a shell command,
    so the ! is taken off. --config-env reads a value from a variable hub can
    not see, so it raises Stop.
    """
    pairs, at = [], 0
    for word in before:
        if GIT_COMMAND_VARIABLES.match(plain(word)):
            pairs.append((plain(word).partition("=")[2], word))
    while at < len(words) and plain(words[at]).startswith("-"):
        option = plain(words[at])
        if option.startswith("--config-env"):
            raise Stop("lowering", "git --config-env, which reads a value hub can not see")
        if option == "-c" and at + 1 < len(words):
            pairs.append((plain(words[at + 1]).partition("=")[2], words[at + 1]))
        at += git_option_words(option)
    subcommand = plain(words[at]) if at < len(words) else ""
    rest = words[at + 1:]
    operands = [word for word in rest if not plain(word).startswith("-")]
    if subcommand == "config":
        pairs += [(plain(word), word) for word in operands[1:]]
    elif (subcommand, plain(operands[0]) if operands else "") in (("bisect", "run"), ("submodule", "foreach")):
        start = rest.index(operands[0]) + 1
        pairs += [(script, rest[start:]) for script in as_scripts(rest[start:])]
    elif subcommand == "filter-branch":
        pairs += [(plain(rest[spot + 1]), rest[spot + 1]) for spot, word in enumerate(rest[:-1])
                  if re.fullmatch(r"--[\w-]*-filter|--setup", plain(word))]
    elif subcommand in GIT_COMMAND_OPTIONS:
        spot = 0
        while spot < len(rest):
            for long_name, short in GIT_COMMAND_OPTIONS[subcommand].items():
                found = option_value(rest, spot, long_name, short)
                if found:
                    pairs.append((found[0], rest[spot:found[1]]))
                    spot = found[1] - 1
                    break
            spot += 1
    out = []
    for script, source in pairs:
        script = script[1:] if script.startswith("!") else script
        if script.strip():
            out.append((script, source if isinstance(source, list) else [source]))
    return out


def find_scripts(words):
    """The command lines find runs with -exec, -execdir, -ok and -okdir, up to ; or +, and their words."""
    pairs, spot = [], 0
    while spot < len(words):
        if plain(words[spot]) in FIND_RUNS:
            end = next((later for later in range(spot + 1, len(words)) if plain(words[later]) in (";", "+")),
                       len(words))
            pairs += [(script, words[spot + 1:end]) for script in as_scripts(words[spot + 1:end])]
            spot = end
        spot += 1
    return pairs


def sed_file(path, cwd):
    """The text of a sed script file, or None when hub can not read it."""
    if plain(path) in ("", "-") or hidden(path):
        return None
    return files.read_text(os.path.join(cwd, expand(path)))


def sed_scripts(words, cwd):
    """The scripts a sed command reads from -e, -f, their long names, or its first operand, as the words
    hold them, so a variable in one stays marked. None for a script file hub can not read."""
    scripts, _file_spots, _dashes_at = sed_reading(words, cwd)
    return [script for script in scripts if script is not None], None in scripts


def sed_reading(words, cwd):
    """sed's scripts as the words hold them, with None for a script file hub can not read, the places of the
    words it reads as files, and the place of --, or len(words) when it has none."""
    scripts, operands, spot, dashes_at = [], [], 0, len(words)
    while spot < len(words):
        text = plain(words[spot])
        following = words[spot + 1] if spot + 1 < len(words) else ""
        if hidden(words[spot]) and text.startswith("-"):
            scripts.append(words[spot])  # an option with a variable in it may hold a script
            spot += 1
            continue
        name, equals, value = text.partition("=")
        if text == "--":
            operands += range(spot + 1, len(words))
            dashes_at = spot
            break
        if text.startswith("--"):
            if len(name) >= 3 and "--expression".startswith(name):
                scripts.append(value if equals else following)
                spot += 0 if equals else 1
            elif len(name) >= 3 and "--file".startswith(name):
                scripts.append(sed_file(value if equals else following, cwd))
                spot += 0 if equals else 1
            elif len(name) >= 3 and "--line-length".startswith(name) and not equals:
                spot += 1
        elif text.startswith("-") and len(text) > 1:
            for at, letter in enumerate(text[1:], 1):
                if letter == "i":  # the rest is the backup suffix
                    break
                if letter in "efl":
                    attached = text[at + 1:]
                    if letter == "e":
                        scripts.append(attached or following)
                    elif letter == "f":
                        scripts.append(sed_file(attached or following, cwd))
                    spot += 0 if attached else 1
                    break
        else:
            operands.append(spot)
        spot += 1
    if scripts:
        return scripts, operands, dashes_at
    return [words[at] for at in operands[:1]], operands[1:], dashes_at  # the first operand is the script


def sed_runs_a_command(script):
    """Whether a sed script holds the e command, or an s command with the e flag. Both run a shell command.

    The command they run is the pattern space, which hub can not read, so either one is refused.
    """
    at, size = 0, len(script)

    def skip_regex(at):
        delimiter = script[at]
        at += 1
        while at < size and script[at] != delimiter:
            at += 2 if script[at] == "\\" else 1
        return at + 1

    def skip_address(at):
        if at < size and script[at] == "\\" and at + 1 < size:
            return skip_regex(at + 1)
        if at < size and script[at] == "/":
            return skip_regex(at)
        while at < size and (script[at].isdigit() or script[at] in "$~+"):
            at += 1
        return at

    while at < size:
        while at < size and script[at] in " \t\n;{}":
            at += 1
        if at >= size:
            break
        at = skip_address(at)
        while at < size and script[at] in "IM":
            at += 1
        if at < size and script[at] == ",":
            at = skip_address(at + 1)
        while at < size and script[at] in " \t!":
            at += 1
        if at >= size:
            break
        letter = script[at]
        if letter == "e":
            return True
        if letter in "sy" and at + 1 < size:
            at = skip_regex(skip_regex(at + 1) - 1)
            flags = ""
            while at < size and script[at] not in ";\n}w":
                flags += script[at]
                at += 1
            if letter == "s" and "e" in flags:
                return True
            if at < size and script[at] == "w":
                while at < size and script[at] != "\n":
                    at += 1
        elif letter in "aicrRwW#":
            while at < size and script[at] != "\n":
                at += 1
        else:
            at += 1
            while at < size and script[at] not in ";\n}":
                at += 1
    return False


def runner_scripts(words, flags, cwd, marked=lambda: None, whole=""):
    """The command lines that a program in this command runs from its arguments.

    git, find and sed run them from known places only, so a commit message
    that names hubctl still runs. tmux, ssh, sudo and the other RUNNERS read
    every argument as a command line, since they may drop the tool-call
    marker. A word hub can not read in those arguments is refused, except a
    setting that env passes on under a runner that keeps the environment.
    marked gives the words as the parser marks them for a reader that asks
    for many_words, or None, and whole is the line the words come from.
    """
    index = program_index(words)
    if index is None:
        return []
    program, rest = base(words[index]), words[index + 1:]
    if program == "git":
        pairs = git_scripts(words[:index], rest)
    elif program == "find":
        pairs = find_scripts(rest)
    elif program == "sed":
        scripts, file_spots, dashes_at = sed_reading(rest, cwd)
        if None in scripts or any(hidden(script) or sed_runs_a_command(plain(script))
                                  for script in scripts if script is not None):
            raise Stop(runner_check(flags), "sed's e command, or a sed script hub can not read")
        # A file name the shell fills in before -- could be an option, such as -e..., that adds a script.
        open_files = [spot for spot in file_spots if hidden(rest[spot]) and spot < dashes_at]
        marked_rest = lined_up(marked(), words)[index + 1:] if open_files else []
        if not all(is_fixed_start(rest[spot], marked_rest, spot) or is_home_start(rest[spot], marked_rest, spot, whole)
                   for spot in open_files):
            raise Stop(runner_check(flags), SED_FILE)
        return []
    elif program == "tmux":
        pairs = [(script, arguments) for arguments in tmux_runs(rest) for script in as_scripts(arguments)]
    elif program in RUNNERS:
        pairs = [(script, rest) for script in as_scripts(rest, raw=program in SPACE_JOINERS)]
    else:
        return []
    if program in ENV_KEEPING and any(hidden(word) for word in rest):
        unreadable_env_setting(words[index:], lined_up(marked(), words)[index:], flags, whole)
        return [script for script, _source in pairs]
    for _script, source in pairs:
        unreadable_runner(source, flags)
    return [script for script, _source in pairs]


def stdin_scripts(command, segments):
    """The command lines a shell, ssh or crontab reads from a pipe, a here-string or a heredoc in this line."""
    parser = gate()
    scripts = []
    for words in segments:
        index = program_index(words)
        if index is None:
            continue
        program = base(words[index])
        if program not in STDIN_RUNNERS and program not in parser.SHELLS and not (
                program == "tmux" and "load-buffer" in [plain(word) for word in words]):
            continue
        for spot, word in enumerate(words):
            if plain(word) == "<<<" and spot + 1 < len(words):
                scripts.append(words[spot + 1])
            elif plain(word).startswith("<<<"):
                scripts.append(plain(word)[3:])
            elif command.heredoc_script(word) is not None:
                scripts.append(command.heredoc_script(word))
        if getattr(words, "fed", False):  # the gate's reading of the line, so both guards split it alike
            for other in segments:
                scripts += printed(other, command)
    if runs_printed_text(command, segments):
        for other in segments:
            scripts += printed(other, command)
    return list(dict.fromkeys(scripts))


def runs_printed_text(command, segments):
    """Whether the line may run what its echo or printf prints, other than through a pipe.

    eval, a shell or source with a word the shell fills in, such as
    eval "$(name)", runs whatever that word holds. A function the line defines
    can print a script for it, so a line that defines one counts too.
    """
    if FUNCTION_DEFINITION.search(command.flat):
        return True
    for words in segments:
        for spot, word in enumerate(words):
            if (base(word) == "eval" or base(word) in gate().SHELLS or base(word) in gate().SOURCES) and any(
                    hidden(other) for other in words[spot + 1:]):
                return True
    return False


def printed(words, command):
    """What echo, printf or cat writes to a pipe, as far as the line shows it."""
    index = program_index(words)
    if index is None:
        return []
    program = base(words[index])
    if program in ("echo", "printf"):
        operands = [word for word in words[index + 1:] if not re.fullmatch(r"-[neE]+", plain(word))]
        text = " ".join(word for word in without_redirects(operands))
        return [re.sub(r"\\n", "\n", text)]
    return [command.heredoc_script(word) for word in words if command.heredoc_script(word) is not None] + [
        words[spot + 1] for spot, word in enumerate(words[:-1]) if plain(word) == "<<<"]


def check_relayed_programs(command):
    """Refuse a hidden program name in a command line that another program runs.

    A name built from a variable or a substitution, such as $(printf hub)ctl,
    counts as hubctl when a later word is a change verb, uninstall or screen.
    hub can not read such a name. The other program runs it without the
    tool-call marker, so hubctl itself would not refuse it either.
    """
    risky = CHANGE_VERBS | {"uninstall", "screen"}
    for words in command.segments() or []:
        index = program_index(words)
        if index is not None and hidden(words[index]) and any(plain(word) in risky for word in words[index + 1:]):
            raise Stop("lowering", "a variable names the program another program runs")


def refuse_too_deep(flags):
    """Refuse a line hub can not read in full, under the first check that runs."""
    running = [name for name in WHAT if flags.runs(f"{PART}.{name}")]
    if running:
        raise Stop(running[0], TOO_DEEP)


def check_line(line, cwd, flags, depth=0, parent=None, relayed=False, whole=None):
    """Raise Stop for the first step in the line that a running check refuses. whole is the tool call's line that
    this line comes from, for the checks that read what the rest of it changes."""
    if depth > MAX_DEPTH:
        # hub does not read a script relayed past the limit, so the script could do anything
        refuse_too_deep(flags)
        return
    parser = gate()
    command = parser.Command(line, cwd, parent)
    segments = command.segments()
    if segments is None:
        rough_check(line, flags)
        segments = []
    hubctl_check = flags.runs(f"{PART}.lowering")
    reads_runners = hubctl_check or flags.runs(f"{PART}.settings_files")
    if hubctl_check and relayed:
        check_relayed_programs(command)
    whole = line if whole is None else whole
    marked = parser.marked_reader(line, cwd, len(segments))
    after_tmux = False
    for position, words in enumerate(segments):
        # tmux's \; starts its next command, and the parser reads that as a command of its own
        tmux_words = ["tmux"] + words if after_tmux and words and plain(words[0]) in TMUX_RUNS | TMUX_TYPING else words
        index = program_index(words)
        after_tmux = after_tmux or (index is not None and base(words[index]) == "tmux")
        if hubctl_check:
            args = hubctl_words(words, line)
            if args is not None and is_step_change(args) and moves_home(whole):
                # The step's background jobs belong to the whole account, so a test home still changes them.
                raise Stop("lowering", MOVED_HOME)
            if args is not None:
                check_hubctl(args, flags, cwd, relayed)
            check_claude(words)
            code = interpreter_code(words, command)
            if code and (HUBCTL_IN_CODE.search(plain(code)) or HUB_IMPORT.search(plain(code))):
                raise Stop("lowering", "interpreter code")
            if code and base(words[index]) in TYPING_INTERPRETERS and code_types_a_door(plain(code)):
                raise Stop("lowering", "a /hub command typed into a prompt")
            for text in typed_texts(tmux_words):
                for typed in typed_lines(text):
                    if types_a_door(typed):
                        raise Stop("lowering", "a /hub command typed into a prompt")
                    check_line(typed, cwd, flags, depth + 1, relayed=True, whole=whole)
        if flags.runs(f"{PART}.agent_marker"):
            check_marker(words, command)
        if flags.runs(f"{PART}.settings_files"):
            check_files(words, command, cwd)
        cwd = moved(words, cwd)
        for script_text in wrapped_scripts(words, command):
            check_line(script_text, cwd, flags, depth + 1, whole=whole)
        if hubctl_check:
            for script_text in relayed_scripts(words):
                check_line(script_text, cwd, flags, depth + 1, relayed=True, whole=whole)
        if reads_runners:
            # tmux's words gain a word in front, so the marked reading lines up only with the words as they are
            marked_words = (lambda at=position: marked(at)) if tmux_words is words else (lambda: None)
            for script_text in runner_scripts(tmux_words, flags, cwd, marked_words, whole):
                check_line(script_text, cwd, flags, depth + 1, relayed=True, whole=whole)
    if hubctl_check:
        for script_text in stdin_scripts(command, segments):
            if types_a_door(plain(script_text)):
                raise Stop("lowering", "a /hub command typed into a prompt")
            check_line(script_text, cwd, flags, depth + 1, relayed=True, whole=whole)
    for source in command.sources:
        check_line(source, cwd, flags, depth + 1, parent=command, whole=whole)


def rough_check(line, flags):
    """For a line the parser can not split: refuse when it names hubctl, a marker or a hub path."""
    if flags.runs(f"{PART}.lowering") and (HUBCTL_IN_CODE.search(line) or HUB_IMPORT.search(line)):
        raise Stop("lowering", "a line hub can not read")
    if flags.runs(f"{PART}.agent_marker") and MARKER_WORD.search(line):
        raise Stop("agent_marker", "a line hub can not read")
    if flags.runs(f"{PART}.settings_files") and HUB_TEXT.search(folded(line)):
        raise Stop("settings_files", "a line hub can not read")


def moved(words, cwd):
    """The working folder after a cd, so a later relative path is read from there."""
    if not words or plain(words[0]) not in ("cd", "pushd"):
        return cwd
    target = next((word for word in words[1:] if not plain(word).startswith("-")), "~")
    if hidden(target) and "$" not in plain(target):
        return cwd
    return os.path.normpath(os.path.join(cwd, expand(target)))


def wrapped_scripts(words, command):
    """The scripts a shell or eval in this command runs: bash -c, sh with a heredoc, eval."""
    parser = gate()
    scripts = []
    for index, word in enumerate(words):
        program = base(word)
        if program == "eval":
            scripts.append(" ".join(plain(part) for part in words[index + 1:]))
        elif program in parser.SHELLS:
            found = parser.shell_script(words[index + 1:], command)
            if found and found[1]:
                how, script = found
                scripts.append(plain(command.expand(script)) if how == "-c" else script)
    return scripts


def file_paths(tool_input):
    """The paths an Edit, Write or Delete call names. Cursor support is beta."""
    return [value for key in ("file_path", "path", "target_file", "notebook_path")
            if isinstance(value := tool_input.get(key), str) and value]


def find(hook_input, flags):
    """The Stop for a tool call, or None when it may run."""
    tool_input = hook_input.get("tool_input") or {}
    cwd = hook_input.get("cwd") or os.getcwd()
    CALL.clear()
    CALL.update({key: hook_input.get(key) for key in ("transcript_path", "agent_id", "session_id")})
    try:
        if hook_input.get("tool_name") == "Bash":
            # the shell runs each line of the command on its own, as in a script
            command = tool_input.get("command") or ""
            if flags.runs(f"{PART}.agent_marker") and DOOR_VARIABLE.search(command):
                raise Stop("agent_marker", "HUB_DOOR")
            check_line(command, cwd, flags)
            if ZSH_DISOWN.search(command):
                check_line(ZSH_DISOWN.sub("& ", command), cwd, flags)
        elif flags.runs(f"{PART}.settings_files"):
            for path in file_paths(tool_input):
                if is_hub_path(path, cwd):
                    raise Stop("settings_files", hook_input.get("tool_name", ""))
    except Stop as stop:
        return stop
    return None


def reason(stop, *hints):
    if stop.detail == TOO_DEEP:
        # not a settings change, so the hint about asking the user does not fit
        return (f"hub stopped this call, because the command is nested too deep for hub to check "
                f"({PART}.{stop.check}). Please send a simpler command, with fewer substitutions inside each other.")
    lines = [f"hub stopped this call, because it would {WHAT[stop.check]} ({PART}.{stop.check})."]
    return "\n".join(lines + [hint for hint in hints if hint])


def leave_folder_hint(stop, hook_input):
    """For a write refused while the shell sits inside one of hub's folders: the hint to leave it, or ""."""
    if stop.check != "settings_files" or not isinstance(hook_input, dict) or not hook_input.get("cwd"):
        return ""
    if hook_input.get("tool_name") != "Bash":  # Edit and Write take full paths, so leaving the folder changes nothing
        return ""
    try:
        here = folded(os.path.realpath(hook_input["cwd"]))
        roots = [os.path.realpath(root) for root in hub_roots()]
        inside = next((root for root in roots if here == folded(root) or here.startswith(folded(root) + os.sep)), None)
        text = files.read_text(LEAVE_FOLDER) if inside else None
        return text.strip().replace("{folder}", inside) if text else ""
    except Exception:  # the hint only helps, so the refusal stands without it
        return ""


def own_hint(stop):
    """The hint of a refusal that has its own, such as quoting a value or using a container, or "".

    The user can not lower these by asking, so it takes the place of the hint to ask the user.
    """
    try:
        text = files.read_text(HINTS[stop.detail]) if stop.detail in HINTS else None
        return text.strip() if text else ""
    except Exception:  # the hint only helps, so the refusal stands without it
        return ""


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or not flags.runs(PART):
        return
    stop = find(hook_input, flags)
    if stop is None:
        return
    answer.signal(f"{PART}.{stop.check}", "blocked")
    answer.refuse(reason(stop, leave_folder_hint(stop, hook_input),
                         own_hint(stop) or flags.text(f"{PART}.ask_the_user")))


class EveryCheck:
    """Flags for when hub can not read its settings: every check runs, and no record is known."""

    record = None

    def runs(self, flag_id):
        return True


def refusal_without_settings(hook_input):
    """The refusal for a call when hub could not read its settings, or None."""
    if not isinstance(hook_input, dict):
        return None
    stop = find(hook_input, EveryCheck())
    if stop is None:
        return None
    ask_the_user = files.read_text(TEXT).strip() if files.read_text(TEXT) else ""
    return reason(stop, leave_folder_hint(stop, hook_input), own_hint(stop) or ask_the_user)


if __name__ == "__main__":
    sys.exit("the self_protection handler runs from the dispatcher")
