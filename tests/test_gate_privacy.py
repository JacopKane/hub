"""The public text gate keeps private items out in every mode.

These scenarios carry over what the gate this part was ported from already
did: it reads only the text a command makes public, follows heredocs and
message files, and stops a private item on every try. Only the style check,
the em dash, warns once.
"""
import os
import shlex

from conftest import DASH, commit
from steps import and_, given, then, when

GATE = "public_text_gate"
TOKEN = "gh" + "p_" + "A1b2C3d4" * 5
PRIVATE_TEXTS = {
    "private_paths": "Read /home/sam/notes.md first",
    "secrets": f"Use the key {TOKEN}",
    "private_words": "Ship quillbrook today",
    "machine_names": "Built on sams-tower",
}


def gate_text(home, name):
    with open(os.path.join(home.plugin, "parts", GATE, "text", name)) as handle:
        return handle.read().strip()


def test_privacy_findings_refuse_every_time_in_warn_and_block_and_only_off_lets_them_through(home, tool):
    with given("hub is installed with a private word and a machine in the profile, the em dash check on, "
               "and the gate at warn"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
        home.hubctl("set", "core.private_words", "Quillbrook")
        home.write_json(os.path.join(home.config, "profile.json"), {
            "version": 1, "machines": {"desktop": {"host": "sams-tower", "use": "home"}}})
    for mode in ("warn", "block"):
        home.hubctl("set", GATE, mode)
        for check, message in PRIVATE_TEXTS.items():
            with when(f"a commit holding {check} comes three times while the gate is at {mode}"):
                results = [home.hook(tool, "PreToolUse", commit(message)) for _ in range(3)]
            with then("every try is refused, naming only that check, with no promise of a next try"):
                assert [result.refused for result in results] == [True, True, True], check
                assert all(result.findings == [check] for result in results), check
                assert all(gate_text(home, "after_warning.md") not in result.reason for result in results)
    with and_("the signal log says blocked for each try and never went_ahead"):
        privacy = [line for line in home.signals() if line["flag"] != f"{GATE}.dashes"]
        assert {line["signal"] for line in privacy} == {"blocked"} and len(privacy) == 24
    with when("the gate is back at warn and a text holds a home path and an em dash"):
        home.hubctl_from_mod("set", GATE, "warn")
        message = f"Notes {DASH} see /home/sam/notes.md"
        first, second = [home.hook(tool, "PreToolUse", commit(message)) for _ in range(2)]
    with then("the first try names both, and the second names only the path, because the dash warned once"):
        assert first.findings == ["private_paths", "dashes"]
        assert second.refused and second.findings == ["private_paths"]
    with when("the user turns off the path check by hand"):
        home.hubctl_from_mod("off", f"{GATE}.private_paths")
    with then("the same path goes through, because off means off"):
        assert home.hook(tool, "PreToolUse", commit("Read /home/sam/notes.md first")).ran


def test_the_gate_reads_the_text_a_command_publishes_and_not_the_paths_it_names(home, tool):
    with given("a clean message file and a message file with a home path, both in a temp folder"):
        home.install()
        clean = os.path.join(home.root, "clean.txt")
        dirty = os.path.join(home.root, "dirty.txt")
        with open(clean, "w") as handle:
            handle.write("Fix the save button\n")
        with open(dirty, "w") as handle:
            handle.write("Fix it\n\nSee /home/sam/log.txt\n")
    with when("Claude commits from the clean file, with the repo named by its full path"):
        by_file = home.hook(tool, "PreToolUse", f"git -C {shlex.quote(home.work)} commit -F {shlex.quote(clean)}")
        by_cat = home.hook(tool, "PreToolUse", f'git commit -m "$(cat {shlex.quote(clean)})"')
    with then("both run, since the paths in the command are not published"):
        assert by_file.ran and by_file.output == {}
        assert by_cat.ran and by_cat.output == {}
    with when("the message comes from the file with the home path, through -F and through $(cat)"):
        dirty_file = home.hook(tool, "PreToolUse", f"git commit -F {shlex.quote(dirty)}")
        dirty_cat = home.hook(tool, "PreToolUse", f'git commit -m "$(cat {shlex.quote(dirty)})"')
    with then("both are refused for the path inside the file"):
        assert dirty_file.findings == ["private_paths"] and dirty_cat.findings == ["private_paths"]
    with when("a heredoc message holds a semicolon, a pipe and an ampersand before a home path"):
        piped = "git commit -F - <<'EOF'\nFix it; keep a | b & c\n\nSee /home/sam/x.md\nEOF"
        inline = "git commit -m \"$(cat <<'EOF'\nTidy; then & more\nSee /home/sam/y.md\nEOF\n)\""
        results = [home.hook(tool, "PreToolUse", command) for command in (piped, inline)]
    with then("the whole heredoc body is checked, and both are refused"):
        assert [result.findings for result in results] == [["private_paths"], ["private_paths"]]
    with when("the commit is the second line of the command, the quotes do not close, or the method comes first"):
        lines = [
            "git add -A\ngit commit -m 'See /home/sam/z.md'",
            'git commit -m "See /home/sam/q.md',
            "gh api -X PATCH repos/o/r/pulls/1 -f body='See /home/sam/e.md'",
        ]
        results = [home.hook(tool, "PreToolUse", command) for command in lines]
    with then("each is still refused for the path, and the one whose quote does not close is unreadable too"):
        assert [result.findings for result in results] == [["private_paths"], ["private_paths", "unreadable"],
                                                           ["private_paths"]]
    with and_("a commit that adds no new text runs"):
        assert home.hook(tool, "PreToolUse", "git commit --amend --no-edit").ran
        assert home.hook(tool, "PreToolUse", "git log --oneline -3 | grep /home/sam").ran


def test_chat_session_links_and_escaped_dashes_are_caught(home, tool):
    with given("hub is installed with the gate at warn and the em dash check on"):
        home.install()
        home.hubctl("on", f"{GATE}.dashes")
    with when("a commit carries a chat session link, or a session trailer alone"):
        link = commit("Fix it\n\nSee https://claude." + "ai/code/session_00000000AbCdEf")
        trailer = commit("Fix it\n\nClaude-Session: 01AbCdEf")
        results = [home.hook(tool, "PreToolUse", command) for command in (link, trailer, link)]
    with then("each is refused as a secret, every time"):
        assert [result.findings for result in results] == [["secrets"]] * 3
    with when("a message writes the em dash as hex escapes of its bytes, which every shell turns into a dash"):
        escaped = home.hook(tool, "PreToolUse", "git commit -m $'Tidy \\xe2\\x80\\x94 done'")
    with then("the dash check names it"):
        assert escaped.refused and escaped.findings == ["dashes"]
    with when("a message writes the em dash as \\u2014, which zsh turns into a dash and macOS bash 3.2 keeps"):
        unicode_escape = home.hook(tool, "PreToolUse", "git commit -m $'Tidy \\u2014 done'", session="unicode-escape")
    with then("the gate can not tell which text the shell makes, so it refuses the message as unreadable, and "
              "the dash check names the written escape too"):
        assert unicode_escape.refused and unicode_escape.findings == ["unreadable", "dashes"], unicode_escape.findings


def test_merges_closes_and_graphql_mutations_are_checked(home, tool):
    with given("hub is installed with the gate at warn"):
        home.install()
    with when("Claude merges with a body, closes an issue with a comment and posts through a graphql mutation"):
        merge = home.hook(tool, "PreToolUse", "gh pr merge 3 --squash --subject Done --body 'See /home/sam/a.md'")
        close = home.hook(tool, "PreToolUse", "gh issue close 4 --comment 'See /home/sam/b.md'")
        mutation = home.hook(tool, "PreToolUse", "gh api graphql -f query='mutation { addComment(input: "
                                                 "{subjectId: \"x\", body: \"See /home/sam/c.md\"}) { clientMutationId } }'")
        heredoc = ("gh api graphql -F query=@- <<'Q'\nmutation { addComment(input: {subjectId: \"x\", "
                   "body: \"See /home/sam/e.md\"}) { clientMutationId } }\nQ\ncat <<'B' > /dev/null\nclean\nB")
        from_heredoc = home.hook(tool, "PreToolUse", heredoc)
    with then("each is refused for the home path, also a mutation in the step's own heredoc beside another heredoc"):
        assert [result.findings for result in (merge, close, mutation, from_heredoc)] == [["private_paths"]] * 4
    with when("a graphql query only reads, with a home path in a field or a field the shell fills in"):
        query = home.hook(tool, "PreToolUse", "gh api graphql -f query='query { viewer { login } }' -f note=/home/sam/d")
        filled = home.hook(tool, "PreToolUse", "gh api graphql -f query='query { viewer { login } }' -f note=\"$NOTE\"")
    with then("each runs, since the gate reads the whole query and it names no mutation"):
        assert [(result.ran, result.output) for result in (query, filled)] == [(True, {})] * 2
    with when("a graphql call names its query field with a word the shell fills in, and a field holds a home path"):
        named = home.hook(tool, "PreToolUse", "gh api graphql -f \"$K\"='query { viewer { login } }' "
                                              "-f note=/home/sam/d")
    with then("the gate can not tell which field is the query, so it checks the fields and refuses the home path"):
        assert named.findings == ["private_paths"], named.findings


def test_a_graphql_mutation_is_checked_whatever_path_or_method_names_graphql(home, tool):
    with given("hub is installed with the gate at warn"):
        home.install()
    comment = ("query='mutation { addComment(input: {subjectId: \"x\", body: \"See /home/sam/c.md\"}) "
               "{ clientMutationId } }'")
    spellings = [f"gh api -X GET graphql -f {comment}", f"gh api -X GET /graphql -f {comment}",
                 f"gh api --method get https://api.github.com/graphql -f {comment}",
                 f"gh api --method=GET graphql/ -f {comment}",
                 f"gh api -XGET https://ghe.example.com/api/graphql -f {comment}",
                 f"gh api -X GET api/graphql -f {comment}", f"gh api -X GET 'https://[x/graphql' -f {comment}"]
    with when("Claude posts through a graphql mutation by GET or by another path or URL to graphql, or by a URL "
              "the gate can not split"):
        results = {command: home.hook(tool, "PreToolUse", command) for command in spellings}
    with then("each is refused for the home path, as the plain graphql call is"):
        assert {command: result.findings for command, result in results.items()} == \
            {command: ["private_paths"] for command in spellings}
    with when("a graphql query only reads, by GET with a home path in a field, or by URL"):
        reads = [home.hook(tool, "PreToolUse", command) for command in
                 ("gh api -X GET /graphql -f query='query { viewer { login } }' -f note=/home/sam/d",
                  "gh api https://api.github.com/graphql -f query='query { viewer { login } }' -f note=done")]
        posted = home.hook(tool, "PreToolUse", "gh api https://api.github.com/graphql "
                                               "-f query='query { viewer { login } }' -f note=/home/sam/d")
    with then("each runs, and a home path posted to a graphql URL is still refused, as any posted gh api call is"):
        assert [(result.ran, result.output) for result in reads] == [(True, {})] * 2
        assert posted.findings == ["private_paths"], posted.findings


def test_a_review_body_is_read_whatever_the_review_type(home, tool):
    with given("hub is installed and a review body file holds a home path"):
        home.install()
        with open(os.path.join(home.work, "review.md"), "w") as handle:
            handle.write("Looks fine, see /home/sam/r.md\n")
    with when("Claude posts reviews whose type flag comes before the body, in each spelling"):
        commands = [
            "gh pr review 3 --comment -b 'See /home/sam/a.md'",
            "gh pr review 3 -c --body 'See /home/sam/b.md'",
            "gh pr review 3 --comment --body='See /home/sam/c.md'",
            "gh pr review 3 --request-changes -F review.md",
            "gh pr review 3 --approve -b'See /home/sam/d.md'",
        ]
        results = [home.hook(tool, "PreToolUse", command) for command in commands]
    with then("each body is read and refused for the home path"):
        assert [result.findings for result in results] == [["private_paths"]] * len(commands)
    with and_("a close with a comment is still read, and an approval with no body runs"):
        assert home.hook(tool, "PreToolUse", "gh pr close 3 --comment 'See /home/sam/e.md'").findings == ["private_paths"]
        assert home.hook(tool, "PreToolUse", "gh pr review 3 --approve").ran


def test_gh_options_before_the_command_and_action_are_read_the_way_gh_reads_them(home, tool):
    with given("hub is installed with the gate at warn"):
        home.install()
    with when("Claude puts gh's own options, or an option of the action, before the command or the action"):
        commands = [
            "gh pr -R acme/demo edit 7 --body 'See /home/sam/a.md'",
            "gh -R acme/demo pr create --title T --body 'See /home/sam/b.md'",
            "gh --help=false pr create --title T --body 'See /home/sam/c.md'",
            "gh pr --repo=acme/demo comment 7 --body 'See /home/sam/d.md'",
            "gh issue -Racme/demo comment 4 --body 'See /home/sam/e.md'",
            "gh pr --body 'See /home/sam/f.md' create --title T",
            "gh '' pr create --title T --body 'See /home/sam/g.md'",
            "gh pr --help create --title T --body 'See /home/sam/k.md'",
            "gh --help=false api -X PATCH repos/o/r/pulls/1 -f body='See /home/sam/h.md'",
        ]
        results = [home.hook(tool, "PreToolUse", command) for command in commands]
    with then("each is read as the action gh runs and refused for the home path"):
        assert [result.findings for result in results] == [["private_paths"]] * len(commands)
    with when("the shell fills in a word gh may read as its command or action"):
        hidden = [home.hook(tool, "PreToolUse", command) for command in
                  ("gh pr $ACTION 7 --body 'See /home/sam/i.md'", "gh $GROUP create --body x", "gh -$FLAG pr create",
                   "gh pr -R\"$X\" create --title T --body x")]
    with then("each is refused, since the gate can not tell what gh runs"):
        assert [result.findings for result in hidden] == [["unreadable"]] * len(hidden)
    with when("an option takes the next word as its value, a value the shell fills in, and reads"):
        taken = home.hook(tool, "PreToolUse", "gh -X pr create --body 'See /home/sam/j.md'")
        filled = home.hook(tool, "PreToolUse", "gh pr --repo=$REPO view 7")
        reads = [home.hook(tool, "PreToolUse", command) for command in
                 ("gh -R acme/demo pr view 7", "gh --help=false pr list", "brew upgrade gh --verbose", "gh --version")]
    with then("they run, as gh takes pr as the value of -X and finds no command named create"):
        assert taken.ran and taken.output == {}, taken.findings
        assert filled.ran and filled.output == {}, filled.findings
        assert all(result.ran and result.output == {} for result in reads), [result.findings for result in reads]
