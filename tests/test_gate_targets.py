"""Every command that makes text public goes through the gate.

The gate checks releases, gists, tags, discussions, commit comments, files
written through the contents API and any other gh api write, on top of
commits, pushes, PRs, issues and reviews. When it can not read the text a
public command sends, because it comes from a variable, another command, a
pipe, a wrapped shell or a file that is not there, it refuses the command
every time and asks for the text in a file.
"""
import base64
import os
import shlex

from conftest import commit
from steps import and_, given, then, when

GATE = "public_text_gate"


def gate_text(home, name):
    with open(os.path.join(home.plugin, "parts", GATE, "text", name)) as handle:
        return handle.read().strip()


def write(home, name, text):
    path = os.path.join(home.work, name)
    with open(path, "w") as handle:
        handle.write(text)
    return path


def test_releases_gists_tags_discussions_and_other_api_writes_are_checked(home, tool):
    content = base64.b64encode(b"Setup notes\nSee /home/sam/c.md\n").decode()
    with given("hub is installed, with a notes file that holds a home path and a clean file"):
        home.install()
        write(home, "notes.md", "Release notes\n\nSee /home/sam/n.md\n")
        write(home, "clean.md", "Nothing private here\n")
    with when("Claude publishes a home path through each of these writes"):
        commands = {
            "release": "gh release create v1 --title One --notes 'See /home/sam/a.md'",
            "release notes file": "gh release edit v1 -F notes.md",
            "gist file": "gh gist create notes.md --desc Notes",
            "gist description": "gh gist create --desc 'See /home/sam/g.md' clean.md",
            "annotated tag": "git tag -a v1 -m 'See /home/sam/t.md'",
            "tag message file": "git tag -F notes.md v2",
            "tag cluster": "git tag -am 'See /home/sam/u.md' v3",
            "release api": "gh api repos/o/r/releases -f tag_name=v4 -f body='See /home/sam/r.md'",
            "gist api": "gh api gists -f 'files[a.md][content]=See /home/sam/s.md'",
            "commit comment": "gh api repos/o/r/commits/abc123/comments -f body='See /home/sam/k.md'",
            "contents": f"gh api -X PUT repos/o/r/contents/setup.md -f message=Add -f content={content}",
            "discussion": "gh api graphql -f query='mutation { createDiscussion(input: "
                          "{body: \"See /home/sam/d.md\"}) { clientMutationId } }'",
            "team discussion": "gh api orgs/o/teams/t/discussions -f title=Plan -f body='See /home/sam/p.md'",
            "other api write": "gh api -X PATCH repos/o/r/labels/bug -f description='See /home/sam/l.md'",
        }
        results = {name: home.hook(tool, "PreToolUse", command) for name, command in commands.items()}
    with then("each one is refused for the home path"):
        assert {name: result.findings for name, result in results.items()} == {
            name: ["private_paths"] for name in commands}
    with and_("the answer names what kind of write it stopped"):
        assert "release" in results["release"].reason and "gist" in results["gist file"].reason
        assert "tag" in results["annotated tag"].reason and "discussion" in results["discussion"].reason
        assert "commit comment" in results["commit comment"].reason
    with when("Claude only reads, makes a tag with no message or deletes a release"):
        reads = ["gh release view v1", "gh gist list", "git tag -l", "git tag v5",
                 "gh api repos/o/r/releases", "gh api -X GET repos/o/r/contents/setup.md -f ref=/home/sam/x",
                 "gh api -X DELETE repos/o/r/releases/1"]
        read_results = [home.hook(tool, "PreToolUse", command) for command in reads]
    with then("each runs with no answer"):
        assert [result.output for result in read_results] == [{}] * len(reads)
    with when("the user turns off the release target"):
        home.hubctl_from_mod("off", f"{GATE}.targets.release")
    with then("a release goes through, and a gist is still checked"):
        assert home.hook(tool, "PreToolUse", commands["release"]).ran
        assert home.hook(tool, "PreToolUse", commands["gist file"]).findings == ["private_paths"]


def test_a_public_command_whose_text_the_gate_can_not_read_is_refused_until_the_text_is_in_a_file(home, tool):
    with given("hub is installed with the gate at warn"):
        home.install()
    with when("Claude sends public commands whose text comes from somewhere the gate can not read"):
        commands = {
            "variable": 'git commit -m "$MESSAGE"',
            "braced variable": 'git commit -m "Fix ${TICKET}"',
            "other command": 'git commit -m "$(git log -1 --format=%s)"',
            "backticks": 'git commit -m "Built `date`"',
            "pipe into a body file": "printf 'Done' | gh pr comment 3 -F -",
            "pipe into a message file": "echo Done | git commit -F -",
            "bash -c": 'bash -c "gh pr comment 3 --body hello"',
            "sh -c": "sh -c 'git commit -m hello'",
            "xargs": "ls | xargs -I{} gh issue comment 4 --body {}",
            "missing message file": "git commit -F missing.txt",
            "missing cat file": 'git commit -m "$(cat missing.txt)"',
            "missing gist file": "gh gist create missing.md",
            "open heredoc": "git commit -F - <<EOF\nFix $THING\nEOF",
            "tag in an editor": "git tag -a v1",
        }
        results = {name: [home.hook(tool, "PreToolUse", command) for _ in range(2)]
                   for name, command in commands.items()}
    with then("each is refused on every try as unreadable, with the hint to put the text in a file"):
        assert {name: [result.findings for result in pair] for name, pair in results.items()} == {
            name: [["unreadable"], ["unreadable"]] for name in commands}
        assert all(gate_text(home, "unreadable_hint.md") in pair[0].reason for pair in results.values())
    with when("the text is plain, or the shell does not expand it, or the command is not public"):
        readable = [
            "git commit -m 'Costs $5 a month'",
            'git commit -m "Costs \\$5 a month"',
            "git commit -F - <<'EOF'\nKeep $THING as it is\nEOF",
            "git commit -m $'Tidy it'",
            "echo $HOME | grep x",
            "bash -c 'ls -la'",
            "ls | xargs -I{} echo {}",
        ]
        readable_results = [home.hook(tool, "PreToolUse", command) for command in readable]
    with then("each runs with no answer"):
        assert [result.output for result in readable_results] == [{}] * len(readable)
    with when("the text goes into a file and the command passes the file"):
        path = write(home, "message.txt", "Fix the save button\n")
        from_file = home.hook(tool, "PreToolUse", f"git commit -F {shlex.quote(path)}")
    with then("it runs"):
        assert from_file.ran and from_file.output == {}
    with when("the user turns the unreadable check off by hand"):
        home.hubctl_from_mod("off", f"{GATE}.unreadable")
    with then("the command with a variable runs, because off means off"):
        assert home.hook(tool, "PreToolUse", commands["variable"]).ran
    with and_("a readable text is still checked"):
        assert home.hook(tool, "PreToolUse", commit("See /home/sam/z.md")).findings == ["private_paths"]


def test_a_body_file_that_is_not_a_regular_file_counts_as_unreadable(home, tool):
    with given("hub is installed with the gate at warn, and the repo holds a folder and a named pipe"):
        home.install()
        os.makedirs(os.path.join(home.work, "notes"))
        os.mkfifo(os.path.join(home.work, "pipe"))
    with when("Claude passes the standard input, a pipe or a folder as the file that holds the text"):
        commands = {
            "dev stdin": "gh pr comment 3 --body-file /dev/stdin",
            "fd 0": "git commit -F /dev/fd/0",
            "attached form": "gh issue create --title Bug --body-file=/dev/stdin",
            "api field": "gh api repos/o/r/issues -F body=@/dev/stdin",
            "named pipe": "gh pr comment 3 -F pipe",
            "folder": "git commit -F notes",
        }
        results = {name: home.hook(tool, "PreToolUse", command) for name, command in commands.items()}
    with then("each is refused as unreadable, because the gate can not read that text before it goes out"):
        assert {name: result.findings for name, result in results.items()} == {
            name: ["unreadable"] for name in commands}
    with when("the standard input is a heredoc in the same command"):
        clean = home.hook(tool, "PreToolUse", "git commit -F /dev/stdin <<'EOF'\nFix the save button\nEOF")
        private = home.hook(tool, "PreToolUse", "git commit -F /dev/stdin <<'EOF'\nSee /home/sam/a.md\nEOF")
    with then("the gate reads the heredoc, so a clean text runs and a home path is refused"):
        assert clean.output == {}
        assert private.findings == ["private_paths"]


def test_a_wrapped_shell_or_eval_whose_script_the_shell_fills_in_counts_as_unreadable(home, tool):
    with given("hub is installed with the gate at warn, and a script file that posts a comment"):
        home.install()
        write(home, "run.sh", "gh pr comment 3 --body 'Done'\n")
    with when("Claude runs a wrapped shell or eval whose script comes from a variable, a command or a file"):
        commands = {
            "bash -c variable": 'bash -c "$CMD"',
            "bash -lc variable": 'bash -lc "$CMD"',
            "sh -c command": 'sh -c "$(printf %s "$CMD")"',
            "sh -c script file": 'sh -c "$(cat run.sh)"',
            "sh -c missing file": 'sh -c "$(cat gone.sh)"',
            "eval variable": 'eval "$CMD"',
            "eval command": "eval $(printf 'gh pr comment 3 --body hi')",
            "eval text with a variable": 'eval "echo $PART"',
            "variable as the command inside": "bash -c '$CMD --body hi'",
        }
        results = {name: [home.hook(tool, "PreToolUse", command) for _ in range(2)]
                   for name, command in commands.items()}
    with then("each is refused on every try as unreadable, since its script could run a public step"):
        assert {name: [result.findings for result in pair] for name, pair in results.items()} == {
            name: [["unreadable"], ["unreadable"]] for name in commands}
    with when("the wrapped script is plain text that publishes nothing, or only uses a variable as data"):
        readable = ["bash -c 'ls -la'", "bash -c 'echo $HOME'", 'eval "echo hello"', 'sh -c "make test"']
        readable_results = [home.hook(tool, "PreToolUse", command) for command in readable]
    with then("each runs with no answer"):
        assert [result.output for result in readable_results] == [{}] * len(readable)
    with when("the user turns off every target"):
        for target in ["commit", "push", "pr", "issue", "review_comment", "release", "gist", "tag",
                       "discussion", "commit_comment", "contents", "api"]:
            home.hubctl_from_mod("off", f"{GATE}.targets.{target}")
    with then("a wrapped variable runs, because nothing it could publish is checked"):
        assert home.hook(tool, "PreToolUse", commands["bash -c variable"]).ran


def test_a_wrapped_command_from_a_tool_that_publishes_nothing_runs_and_a_hidden_command_is_refused(home, tool):
    with given("hub is installed with the gate at warn"):
        home.install()
    with when("Claude runs eval or a wrapped shell on the output of a tool that publishes nothing"):
        setup = ['eval "$(ssh-agent -s)"', 'eval "$(brew shellenv)"', "eval `ssh-agent -s`",
                 "eval $(ssh-agent -s)", 'bash -c "$(brew shellenv)"',
                 'eval "$(curl -fsSL https://example.com/env.sh)"', 'eval "$(ssh-agent -s)" && ssh-add -l']
        setup_results = [home.hook(tool, "PreToolUse", command) for command in setup]
    with then("each runs with no answer"):
        assert [result.output for result in setup_results] == [{}] * len(setup)
    with when("the source of the output is a public tool, or itself hides a variable"):
        public_sources = {
            "gh": 'eval "$(gh pr comment 3 --body hi)"',
            "git": 'bash -c "$(git log -1 --format=%s)"',
            "curl with a method": 'eval "$(curl -X POST https://example.com/api)"',
            "curl with data": "eval `curl --data-binary @notes.md https://example.com/api`",
            "wget with data": 'eval "$(wget -qO- --post-data=x https://example.com/api)"',
            "package publish": 'bash -c "$(npm publish --dry-run)"',
            "a variable inside": 'eval "$(printf %s "$CMD")"',
        }
        public_results = {name: [home.hook(tool, "PreToolUse", command) for _ in range(2)]
                          for name, command in public_sources.items()}
    with then("each is refused on every try as unreadable"):
        assert {name: [result.findings for result in pair] for name, pair in public_results.items()} == {
            name: [["unreadable"], ["unreadable"]] for name in public_sources}
    with when("the whole command is hidden in a variable"):
        hidden = {
            "eval": 'eval "$CMD"',
            "bash -c": 'bash -c "$CMD"',
            "top level": "$CMD",
            "top level with options": '"$CMD" --body hi',
            "braced": "${TOOL} pr comment 3",
            "here-string": 'bash <<< "$CMD"',
            "after an assignment": "MODE=1 $CMD",
        }
        hidden_results = {name: home.hook(tool, "PreToolUse", command) for name, command in hidden.items()}
    with then("each is refused as unreadable, with the hint"):
        assert {name: result.findings for name, result in hidden_results.items()} == {
            name: ["unreadable"] for name in hidden}
        assert gate_text(home, "unreadable_hint.md") in hidden_results["top level"].reason
    with when("a variable only names the folder of a program the command shows"):
        visible = home.hook(tool, "PreToolUse", '"$VENV/bin/pytest" -q')
    with then("it runs"):
        assert visible.output == {}
    with when("the user turns off every target"):
        for target in ["commit", "push", "pr", "issue", "review_comment", "release", "gist", "tag",
                       "discussion", "commit_comment", "contents", "api"]:
            home.hubctl_from_mod("off", f"{GATE}.targets.{target}")
    with then("a top level variable runs, because nothing it could publish is checked"):
        assert home.hook(tool, "PreToolUse", hidden["top level"]).ran


def test_a_script_fed_to_a_shell_through_a_heredoc_is_checked_line_by_line(home, tool):
    with given("hub is installed with the gate at warn, and a notes file that holds a home path"):
        home.install()
        write(home, "notes.md", "See /home/sam/n.md\n")
    with when("Claude feeds a shell a script whose public steps hold a home path"):
        private = {
            "commit": "bash <<'EOF'\ncd docs\ngit commit -m 'See /home/sam/a.md'\nEOF",
            "open heredoc": "sh <<EOF\necho start\ngh pr comment 3 --body 'See /home/sam/b.md'\nEOF",
            "body file": "bash -s <<'EOF'\ngh issue create --title Bug --body-file notes.md\nEOF",
            "heredoc inside": "bash <<'SCRIPT'\ngit commit -F - <<'EOF'\nSee /home/sam/c.md\nEOF\nSCRIPT",
            "in a block, with a redirect": "zsh <<'EOF' > out.log\nif true; then\n"
                                           "  git commit -m 'See /home/sam/d.md'\nfi\nEOF",
            "after a comment": "bash <<'EOF'\n# post it\ngh pr comment 3 --body 'See /home/sam/e.md'\nEOF",
        }
        private_results = {name: home.hook(tool, "PreToolUse", command) for name, command in private.items()}
    with then("each is refused for the home path"):
        assert {name: result.findings for name, result in private_results.items()} == {
            name: ["private_paths"] for name in private}
    with when("a line of the script hides a command or the text it publishes"):
        hidden = {
            "own line": "bash <<'EOF'\necho start\n$CMD\nEOF",
            "after then": "bash <<'EOF'\nif true; then $CMD --body hi; fi\nEOF",
            "variable in a message": 'bash <<EOF\ngit commit -m "Fix $THING"\nEOF',
        }
        hidden_results = {name: [home.hook(tool, "PreToolUse", command) for _ in range(2)]
                          for name, command in hidden.items()}
    with then("each is refused on every try as unreadable"):
        assert {name: [result.findings for result in pair] for name, pair in hidden_results.items()} == {
            name: [["unreadable"], ["unreadable"]] for name in hidden}
    with when("the script is clean"):
        clean = [
            "bash <<'EOF'\ngit commit -m 'Fix the save button'\ngh pr comment 3 --body 'Done'\nEOF",
            "bash <<'EOF'\neval \"$(ssh-agent -s)\"\nssh-add -l\nEOF",
            "bash <<'EOF'\n# set up\necho $HOME\nmake test\nEOF",
        ]
        clean_results = [home.hook(tool, "PreToolUse", command) for command in clean]
    with then("each runs with no answer"):
        assert [result.output for result in clean_results] == [{}] * len(clean)


def test_glued_messages_repo_and_label_descriptions_and_text_release_assets_are_checked(home, tool):
    with given("hub is installed with the gate at warn, a text file and a binary file in the repo"):
        home.install()
        write(home, "notes.txt", "See /home/sam/release.md\n")
        write(home, "clean.txt", "Fixes the save button\n")
        with open(os.path.join(home.work, "app.bin"), "wb") as handle:
            handle.write(b"\x7fELF\x00\x01/home/sam/build\x00\xff\xfe")
    with when("Claude sends a home path in a glued message, a description, a release asset or its label"):
        commands = {
            "glued -m": 'git commit -m"See /home/sam/a.md"',
            "glued -am": 'git commit -am"See /home/sam/a.md"',
            "long message with =": 'git commit --message="See /home/sam/a.md"',
            "tag message with =": "git tag -a v1 --message='See /home/sam/a.md'",
            "repo edit": "gh repo edit --description 'See /home/sam/r.md'",
            "repo create": "gh repo create demo --public -d 'See /home/sam/r.md'",
            "label create": "gh label create bug --color f00 --description 'See /home/sam/l.md'",
            "label edit": "gh label edit bug -d 'See /home/sam/l.md'",
            "release create asset": "gh release create v1 notes.txt --notes Clean",
            "release upload asset": "gh release upload v1 clean.txt notes.txt --clobber",
            "asset label": "gh release upload v1 'app.bin#See /home/sam/app'",
            "asset glob": "gh release upload v1 *.txt",
        }
        results = {name: home.hook(tool, "PreToolUse", command) for name, command in commands.items()}
    with then("each is refused for the home path"):
        assert {name: result.findings for name, result in results.items()} == {
            name: ["private_paths"] for name in commands}
    with when("a release asset is missing"):
        missing = home.hook(tool, "PreToolUse", "gh release upload v1 gone.txt")
    with then("it is refused as unreadable"):
        assert missing.findings == ["unreadable"]
    with when("the assets are a clean text file and a binary file, and the texts are clean"):
        clean = ["gh release create v1 clean.txt app.bin#App --notes Clean -t One",
                 "gh release upload v1 app.bin", "gh release upload v1 *.bin", 'git commit -m"Fix it"',
                 "gh label create bug -d 'Broken'"]
        clean_results = [home.hook(tool, "PreToolUse", command) for command in clean]
    with then("each runs, because a binary asset is not text and is allowed"):
        assert [result.output for result in clean_results] == [{}] * len(clean)


def test_a_public_command_inside_a_command_substitution_is_checked_like_any_other_command(home, tool):
    with given("hub is installed with the gate at warn"):
        home.install()
    with when("Claude runs a public command inside $(...) or backticks, wherever the substitution stands"):
        private = {
            "assignment": "URL=\"$(gh pr create --title Fix --body 'See /home/sam/a.md')\"",
            "assignment with inner quotes": 'URL="$(gh pr create --title Fix --body "See /home/sam/a.md")"',
            "bare assignment": "URL=$(gh pr create --title Fix --body 'See /home/sam/a.md')",
            "backticks": "URL=`gh pr create --title Fix --body 'See /home/sam/b.md'`",
            "exported": "export URL=\"$(gh pr create --title Fix --body 'See /home/sam/c.md')\"",
            "argument": "echo \"Opened $(gh pr create --title Fix --body 'See /home/sam/d.md')\"",
            "nested": "echo \"$(printf '%s' \"$(gh issue create --title Bug --body 'See /home/sam/e.md')\")\"",
            "heredoc inside": "URL=\"$(gh pr create --title Fix --body-file - <<'EOF'\nSee /home/sam/f.md\nEOF\n)\"",
            "open heredoc body": "cat <<EOF\nOpened $(gh pr comment 3 --body 'See /home/sam/g.md')\nEOF",
            "condition": "if out=\"$(git commit -m 'See /home/sam/h.md')\"; then echo ok; fi",
            "in a heredoc script": "bash <<'EOF'\nURL=\"$(gh pr create --title Fix --body 'See /home/sam/i.md')\"\nEOF",
        }
        private_results = {name: home.hook(tool, "PreToolUse", command) for name, command in private.items()}
    with then("each is refused for the home path, as the same command would be on its own"):
        assert {name: result.findings for name, result in private_results.items()} == {
            name: ["private_paths"] for name in private}
    with and_("the answer names the write it stopped"):
        assert "PR text" in private_results["assignment"].reason
        assert "commit" in private_results["condition"].reason
    with when("the command inside hides its text or is itself hidden"):
        hidden = {
            "pipe after cat": 'echo "$(cat notes.md | gh gist create -)"',
            "variable body": 'URL="$(gh pr create --title Fix --body "$BODY")"',
            "hidden command": 'OUT="$($CMD)"',
            "hidden in backticks": "OUT=`$CMD --body hi`",
        }
        hidden_results = {name: [home.hook(tool, "PreToolUse", command) for _ in range(2)]
                          for name, command in hidden.items()}
    with then("each is refused on every try as unreadable"):
        assert {name: [result.findings for result in pair] for name, pair in hidden_results.items()} == {
            name: [["unreadable"], ["unreadable"]] for name in hidden}
    with when("the substitutions are clean or publish nothing"):
        clean = [
            "URL=\"$(gh pr create --title Fix --body 'Fixes the save button')\"",
            'DIR="$(dirname "$0")"',
            "NOW=$(date +%s)",
            'echo "Last: $(git log -1 --format=%s)"',
            "COUNT=$(( 1 + 2 ))",
            'eval "$(ssh-agent -s)"',
            'gh pr create --title Fix --body "$(cat <<\'EOF\'\nFixes the save button\nEOF\n)"',
        ]
        clean_results = [home.hook(tool, "PreToolUse", command) for command in clean]
    with then("each runs with no answer"):
        assert [result.output for result in clean_results] == [{}] * len(clean)


def substitutions(levels, inner):
    """A line with that many substitutions inside each other, and inner in the last one."""
    return "echo $(" * levels + inner + ")" * levels


def test_a_substitution_past_the_gates_reading_depth_is_refused_whatever_it_names(home, tool):
    with given("hub is installed with the gate at warn, and self_protection off, so the gate alone answers"):
        home.install()
        home.hubctl_from_mod("off", "self_protection")
    with when("a command names no public tool four substitutions deep, as deep as the gate reads"):
        within = home.hook(tool, "PreToolUse", substitutions(4, "date"))
    with then("the gate reads it and it runs"):
        assert within.output == {}
    with when("the same command sits five substitutions deep, one more than the gate reads"):
        past = home.hook(tool, "PreToolUse", substitutions(5, "date"))
    with then("it is refused as unreadable, since the gate can not tell what the deepest one runs"):
        assert past.findings == ["unreadable"]
