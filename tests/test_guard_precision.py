"""How hub reads quoted settings, help reads, git -C and sed files, and the moved-home refusal.

Under env, timeout, nice and the other runners that keep the caller's
environment, a word hub can not read passes in one place only: the value of a
literal env setting for a short list of names, in double quotes, or fixed
folders in front of a plain $PATH. Every other hidden word under a runner
stays refused, with a hint to write it in full. A sed file name that holds a
variable passes after --, after fixed text or after "$HOME/", and else gets a
hint to put -- before the files. A change by hub's switch step
on a line that does more than read HOME, an XDG folder or CLAUDE_CONFIG_DIR,
or that runs eval, source or the . command, is refused with a hint to use a
container, because the step's background jobs belong to the whole account.

The plugin is copied into the temp home by hand. hub's own step never runs
here, since every scenario only asks the hook about a line.
"""
import os
import shutil

from conftest import DASH, PLUGIN
from steps import given, then, when

PART = "self_protection"
QUOTE_HINT = "Write the program's path and any hub command in full"
SED_HINT = "Put -- before the file names"
CONTAINER_HINT = "use a Docker or Podman container with no mounts"
ASK_THE_USER = "only the user can lower hub's protections"


def plugin_copied(home):
    """hub's plugin copied into the temp home, as install copies it, with no step run."""
    shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))


def pinned(home, flag_id, value):
    """A value the user set by hand, written into the user's settings file."""
    home.write_json(home.user_file, {"version": 1, "flags": {flag_id: {"by": "user", "value": value}}})


def answers(home, tool, lines):
    return {line: home.hook(tool, "PreToolUse", line) for line in lines}


def refused_lines(results):
    return [line for line, result in results.items() if result.refused]


def ran_lines(results):
    return [line for line, result in results.items() if result.ran]


def by_self_protection(result):
    return result.refused and f"({PART}." in result.reason


def without_hint(results, hint):
    return [line for line, result in results.items() if hint not in result.reason]


QUOTED_SETTINGS = [
    'env "PATH=/opt/tools/bin:$PATH" python -m pytest tests/test_demo.py',
    'env "PATH=/opt/tools/bin:${PATH}" python -m pytest tests/test_demo.py',
    'env "TMPDIR=/srv/scratch/run-$name" python -m pytest tests/test_demo.py',
    'timeout 600 env "LANG=$lang" "TMPDIR=/srv/scratch/$name" python -m pytest tests/test_demo.py',
    'env PATH="/opt/tools/bin:$PATH" python -m pytest tests/test_demo.py',
]
UNQUOTED_OR_HIDDEN = [
    "env PATH=/opt/tools/bin:$PATH python -m pytest tests/test_demo.py",
    'timeout 60 "$TOOLS"/python -m pytest tests/test_demo.py',
]
STILL_REFUSED = [
    'env "PATH=$TOOLS/bin:$PATH" python',
    'env "PATH=/opt/tools/bin:$PATH:h" python',
    'export PATH=/opt/other:$PATH; env "PATH=/opt/tools/bin:$PATH" python',
    'env PATH="/opt/tools/bin:$PATH" PATH="/opt/other/bin:$PATH" python',
    'export PATH=/opt/other:$PATH; env PATH="/opt/tools/bin:$PATH" python',
    'env "HOME=$X" python -m pytest',
    'sudo bash -c "$X"',
    'sudo env "PATH=/opt/tools/bin:$PATH" pytest',
    'env "PATH=/opt/tools/bin:$PATH" bash -c "$X"',
    'timeout 60 python -c "$CODE"',
    'env "$NAME=1" pytest',
    'env "GIT_DIR=$X" git status',
    'printf -v PATH %s /opt/other; env "PATH=/opt/tools/bin:$PATH" python',
    ': "${PATH:=/opt/other}"; env "PATH=/opt/tools/bin:$PATH" python',
    'path+=(/opt/other); env "PATH=/opt/tools/bin:$PATH" python',
    'nice sudo env "TMPDIR=/srv/scratch/$name" pytest',
]


def test_quoted_env_settings_and_help_reads_run_while_hidden_program_and_code_words_stay_refused(home, tool):
    """As a user, when I let Claude run tests and reads with quoted values, I expect them to run while hidden
    program and code words stay refused."""
    with given("hub's plugin is in the temp home with every check at its default"):
        plugin_copied(home)
    with when("Claude runs tests under env with quoted settings and fixed folders in front of PATH"):
        quoted = answers(home, tool, QUOTED_SETTINGS)
    with then("each one runs"):
        assert not refused_lines(quoted), {line: quoted[line].reason for line in refused_lines(quoted)}
    with when("the same PATH setting is not quoted, or a variable fills in the program's folder"):
        unquoted = answers(home, tool, UNQUOTED_OR_HIDDEN)
    with then("each one is refused, with the hint to quote the setting or write the path in full"):
        assert not ran_lines(unquoted), ran_lines(unquoted)
        assert all(by_self_protection(result) for result in unquoted.values())
        assert not without_hint(unquoted, QUOTE_HINT), without_hint(unquoted, QUOTE_HINT)
        assert not [line for line, result in unquoted.items() if ASK_THE_USER in result.reason.casefold()]
    with when("a hidden word could pick the program or the code, change PATH elsewhere, or run under sudo"):
        still = answers(home, tool, STILL_REFUSED)
    with then("each one is refused as before"):
        assert not ran_lines(still), ran_lines(still)
        assert all(by_self_protection(result) for result in still.values())
    with when("sed reads a file whose name holds a variable, in quotes after fixed text or the home folder, or "
              "after --"):
        sed_read = answers(home, tool, [
            'sed -n 1,5p "./notes/$name"',
            'sed -n 1,5p -- "$name"',
            'sed -n 1,40p "$HOME/notes.txt"',
            'sed -n 1,40p "${HOME}/notes.txt"',
            'for f in *.md; do sed -n 1,5p -- "$f"; done',
        ])
    with then("each one runs"):
        assert not refused_lines(sed_read), {line: sed_read[line].reason for line in refused_lines(sed_read)}
    with when("the whole file name is a variable before --, the variable is not quoted, or the line changes HOME"):
        sed_hidden = answers(home, tool, [
            'sed -n 1,5p "$name"',
            "sed -n 1,5p ./notes/$name",
            'sed -e p "$name"',
            'for f in *.md; do sed -n 1,5p "$f"; done',
            "sed -n 1,40p $HOME/notes.txt",
            'read HOME; sed -n 1,40p "$HOME/notes.txt"',
        ])
    with then("sed is refused, since the value could add a script, with the hint to put -- before the files"):
        assert not ran_lines(sed_hidden), ran_lines(sed_hidden)
        assert all(by_self_protection(result) for result in sed_hidden.values())
        assert not without_hint(sed_hidden, SED_HINT), without_hint(sed_hidden, SED_HINT)
        assert not [line for line, result in sed_hidden.items() if QUOTE_HINT in result.reason]
        assert not [line for line, result in sed_hidden.items() if DASH in result.reason]
    with when("sed edits hub's settings file in place"):
        sed_write = home.hook(tool, "PreToolUse", f"sed -i '' 's/on/off/' {home.user_file}")
    with then("it is refused as a write to hub's settings, as before"):
        assert sed_write.refused and "(self_protection.settings_files)" in sed_write.reason, sed_write.reason
    with when("Claude reads hub's own part files with git -C another folder"):
        parts = os.path.join(home.plugin, "parts")
        git_read = home.hook(tool, "PreToolUse", f"git -C {home.work} grep -n queue -- {parts}")
        git_config = home.hook(tool, "PreToolUse", f"git -c core.quotepath=off grep -n queue -- {parts}")
    with then("the git -C read runs, and a -c value before the action is judged as before"):
        assert git_read.ran, git_read.reason
        assert git_config.refused and "(self_protection.settings_files)" in git_config.reason
    with when("a git read writes its output into hub's settings file, with or without -C"):
        git_output = answers(home, tool, [
            f"git -C {home.work} diff --output={home.user_file}",
            f"git -C {home.work} log -p --output {home.user_file}",
            f"git show --output={home.user_file} HEAD",
        ])
    with then("each one is refused as a write to hub's settings"):
        assert not ran_lines(git_output), ran_lines(git_output)
        assert all("(self_protection.settings_files)" in result.reason for result in git_output.values()), {
            line: result.reason for line, result in git_output.items()}
    with when("Claude asks a plugin command or hub's step under env for its help text"):
        helps = answers(home, tool, ["claude plugin marketplace remove --help", "claude plugin disable -h",
                                     "env TERM=dumb hubctl --help"])
    with then("each one runs"):
        assert not refused_lines(helps), {line: helps[line].reason for line in refused_lines(helps)}
    with when("the same commands name a plugin or a part command"):
        named = answers(home, tool, ["claude plugin marketplace remove hub-market", "claude plugin disable hub",
                                     "claude plugin disable --help hub", "env TERM=dumb hubctl board --help"])
    with then("each one is refused as before"):
        assert not ran_lines(named), ran_lines(named)


MOVED_HOME = [
    'env HOME=/srv/scratch/home XDG_CONFIG_HOME=/srv/scratch/config "PATH=/opt/tools/bin:$PATH" hubctl on queue',
    "env HOME=/srv/scratch/home XDG_CONFIG_HOME=/srv/scratch/config PATH=/opt/tools/bin hubctl on queue",
    "HOME=/srv/scratch/home hubctl on queue",
    "export XDG_STATE_HOME=/srv/scratch/state; hubctl on queue",
    "CLAUDE_CONFIG_DIR=/srv/scratch/claude hubctl off queue",
    'HOME=/srv/scratch/home bash -c "hubctl on queue"',
    "eval 'export HOME=/srv/scratch/home'; hubctl on queue",
    ". ./env.sh; hubctl on queue",
    "source ./env.sh; hubctl on queue",
    ': "${XDG_STATE_HOME:=/srv/scratch/state}"; hubctl on queue',
    "printf -v HOME %s /srv/scratch/home; hubctl on queue",
    "for HOME in /srv/scratch/home; do hubctl on queue; done",
    "env -S 'HOME=/srv/scratch/home' hubctl on queue",
    'export "$NAME=/srv/scratch/home"; hubctl on queue',
    "HOME=/srv/scratch/home python3 -I lib/hub_switch.py set queue on",
    "BASH_ENV=./env.sh bash -c 'hubctl on queue'",
    "ENV=./env.sh sh -c 'hubctl on queue'",
    "ZDOTDIR=/srv/scratch/zsh zsh -c 'hubctl on queue'",
]
STAYS_ALLOWED = [
    "hubctl on queue",
    "env HOME=/srv/scratch/home python -m pytest tests/test_demo.py",
    'echo "$HOME" && hubctl on queue',
    "source .venv/bin/activate && hubctl show",
    "HOME=/srv/scratch/home hubctl --help",
    "NODE_ENV=test hubctl on queue",
]


def test_hubs_switch_step_on_a_line_that_moves_the_home_folder_is_refused_with_the_container_hint(home, tool):
    """As a user, when I let Claude run hub's switch step with HOME set to a test folder on my machine, I expect hub
    to refuse it and point me to a container."""
    with given("hub's plugin is in the temp home and nothing is pinned"):
        plugin_copied(home)
    with when("Claude runs the step's change on lines that move HOME, an XDG folder or CLAUDE_CONFIG_DIR, name a "
              "file the shell runs at start, or run eval, source or the . command"):
        moved = answers(home, tool, MOVED_HOME)
    with then("each one is refused under lowering, with the container hint in place of asking the user"):
        assert not ran_lines(moved), ran_lines(moved)
        assert all("(self_protection.lowering)" in result.reason for result in moved.values()), {
            line: result.reason for line, result in moved.items()}
        assert not without_hint(moved, CONTAINER_HINT), without_hint(moved, CONTAINER_HINT)
        assert not [line for line, result in moved.items() if ASK_THE_USER in result.reason.casefold()]
    with when("the step runs alone, a test runs with its own HOME, HOME is only read, or the step only shows or helps"):
        allowed = answers(home, tool, STAYS_ALLOWED)
    with then("each one runs"):
        assert not refused_lines(allowed), {line: allowed[line].reason for line in refused_lines(allowed)}
    with given("the user pinned queue off by hand"):
        pinned(home, "queue", "off")
    with when("Claude turns queue on with a moved HOME, and then on its own line"):
        moved_pinned = home.hook(tool, "PreToolUse", "HOME=/srv/scratch/home hubctl on queue")
        alone_pinned = home.hook(tool, "PreToolUse", "hubctl on queue")
    with then("the moved line gets the container hint, and the plain line is refused for the pin as before"):
        assert moved_pinned.refused and CONTAINER_HINT in moved_pinned.reason, moved_pinned.reason
        assert alone_pinned.refused and CONTAINER_HINT not in alone_pinned.reason, alone_pinned.reason
