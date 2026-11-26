"""The newest hub copy wins the shared built files.

Each open chat runs the hub copy it started with, and every copy builds the same
shared files: the links in ~/.claude, the built skills, agents and style, the
slots and resolved.json. An apply records the version of the copy that built
them in built_by.json. An older copy never builds over files that a newer copy
built. Its hooks resolve the flags in memory for themselves and print nothing
extra, and its apply step says in one line why it changed nothing. A newer copy
and a copy of the same version build as before. A missing or broken record, a
copy with no version, and a newer copy that Claude Code took out (its folder is
gone or marked .orphaned_at) all leave the build as it was before this check.
"""
import json
import os
import shutil
import sys

from steps import and_, given, then, when

STAMP = "built_by.json"
OWN_LINKS = (os.path.join("skills", "hub-pr_gif"), os.path.join("agents", "hub-scout.md"))


def set_version(plugin, version):
    path = os.path.join(plugin, ".claude-plugin", "plugin.json")
    with open(path) as handle:
        manifest = json.load(handle)
    if version is None:
        manifest.pop("version", None)
    else:
        manifest["version"] = version
    with open(path, "w") as handle:
        json.dump(manifest, handle, indent=2)


def second_copy(home, name, version):
    """A second hub copy beside the first, as Claude Code keeps one folder per version in its cache."""
    plugin = os.path.join(home.root, "plugin-cache", name, "hub")
    shutil.copytree(home.plugin, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    set_version(plugin, version)
    return plugin


def step(home, plugin, *args, check=True):
    return home.run([sys.executable, os.path.join(plugin, "lib", "hub_switch.py"), *args], check=check)


def session_start(home, plugin, session):
    """The eight SessionStart slot hooks of a chat that runs this copy, one after another. Returns what they print."""
    return "".join(home.run([sys.executable, "-I", os.path.join(plugin, "lib", "hub", "slot_hook.py"), str(number)],
                            stdin=json.dumps({"session_id": session})).stdout for number in range(1, 9))


def tool_call(home, plugin, session):
    """The dispatcher for one Bash call of a chat that runs this copy."""
    data = home.hook_input("claude_code", "PreToolUse", "ls", session=session)
    return home.run([sys.executable, "-I", os.path.join(plugin, "lib", "hub", "dispatch.py"), "PreToolUse"],
                    stdin=json.dumps(data))


def link_targets(home):
    """{link under ~/.claude: its target} for the links into a plugin copy."""
    found = {}
    for name in OWN_LINKS:
        path = os.path.join(home.root, ".claude", name)
        found[name] = os.readlink(path) if os.path.islink(path) else None
    return found


def points_into(home, plugin):
    targets = link_targets(home).values()
    return all(target and target.startswith(plugin + os.sep) for target in targets)


def shared_files(home):
    """The bytes and times of every shared file an apply builds, so a run that wrote any of them shows."""
    found = {}
    for top in (home.state, os.path.join(home.root, ".claude")):
        for parent, _names, file_names in os.walk(top):
            for name in file_names:
                path = os.path.join(parent, name)
                relative = os.path.relpath(path, home.root)
                if name.endswith(".lock") or "calls" in relative.split(os.sep) or name in ("errors.log",):
                    continue
                if os.path.islink(path):
                    found[relative] = ("link", os.readlink(path))
                else:
                    info = os.stat(path)
                    with open(path, "rb") as handle:
                        found[relative] = ("file", handle.read(), info.st_mtime_ns)
    return found


def stamp(home):
    with open(os.path.join(home.state, STAMP)) as handle:
        return json.load(handle)


def test_an_older_copy_leaves_a_newer_build_alone_and_a_newer_copy_takes_over(home):
    with given("hub 0.20.10 is installed with a part that has start text, and has built the shared files"):
        home.switch_on("tier_rule")
        home.install()
        set_version(home.plugin, "0.20.10")
        newer = home.plugin
        step(home, newer, "apply")
        assert points_into(home, newer)
        start_text = session_start(home, newer, "s-newer")
    with and_("a chat that still runs hub 0.20.9 from its own cache folder"):
        older = second_copy(home, "0.20.9", "0.20.9")
    with and_("the build is recorded as 0.20.10's, which a string compare would rank below 0.20.9"):
        assert stamp(home)["version"] == "0.20.10"
        before = shared_files(home)

    with when("the older chat starts and makes a tool call"):
        started = session_start(home, older, "s-older")
        called = tool_call(home, older, "s-older")
    with then("no shared file changed and the links still point into the newer copy"):
        assert shared_files(home) == before
        assert points_into(home, newer)
    with and_("the older chat still gets the start text, and its hooks add no notice and log no error"):
        assert "additionalContext" in started and started == start_text
        assert called.stdout.strip() == ""
        assert home.errors() == ""

    with when("the older copy's apply step runs"):
        applied = step(home, older, "apply")
    with then("it changes nothing and says why in one line"):
        assert shared_files(home) == before
        assert "0.20.10" in applied.stdout and "0.20.9" in applied.stdout
        assert len(applied.stdout.strip().splitlines()) == 1

    with when("the user turns pr_gif off from the older chat"):
        switched = step(home, older, "off", "pr_gif")
    with then("the setting is saved, the built files stay the newer copy's, and the step says the newer copy applies it"):
        assert home.settings()["flags"]["pr_gif"]["value"] == "off"
        assert os.path.islink(os.path.join(home.root, ".claude", "skills", "hub-pr_gif"))
        assert "0.20.10" in switched.stdout
    with and_("the older chat's own hooks already follow the new setting"):
        shown = json.loads(step(home, older, "show", "--json").stdout)
        assert shown["flags"]["pr_gif"]["value"] == "off"

    with when("the newer chat makes its next tool call"):
        tool_call(home, newer, "s-newer")
    with then("the newer copy applies the change"):
        assert not os.path.lexists(os.path.join(home.root, ".claude", "skills", "hub-pr_gif"))
        assert stamp(home)["version"] == "0.20.10"
        assert home.errors() == ""

    with when("the user turns pr_gif back on and a chat on hub 0.20.11 starts"):
        step(home, newer, "on", "pr_gif")
        newest = second_copy(home, "0.20.11", "0.20.11")
        session_start(home, newest, "s-newest")
    with then("the newest copy builds over the 0.20.10 build"):
        assert points_into(home, newest)
        assert stamp(home)["version"] == "0.20.11"


def test_a_copy_of_the_same_version_rebuilds_as_before(home):
    with given("hub 0.20.10 has built the shared files"):
        home.install()
        set_version(home.plugin, "0.20.10")
        step(home, home.plugin, "apply")
    with and_("a second copy of 0.20.10 in another folder, as a reinstall leaves"):
        again = second_copy(home, "0.20.10-again", "0.20.10")
    with when("a chat on the second copy starts"):
        session_start(home, again, "s-again")
    with then("it builds, and the links point into it"):
        assert points_into(home, again)
        assert stamp(home)["version"] == "0.20.10"
        assert stamp(home)["root"] == again


def test_without_a_trustworthy_record_an_older_copy_builds_as_before(home):
    with given("hub 0.20.10 has built the shared files, and a chat runs hub 0.20.9"):
        home.install()
        set_version(home.plugin, "0.20.10")
        newer = home.plugin
        step(home, newer, "apply")
        older = second_copy(home, "0.20.9", "0.20.9")

    with when("the record is missing and the older chat starts"):
        os.unlink(os.path.join(home.state, STAMP))
        session_start(home, older, "s-older")
    with then("the older copy builds, as hub did before the record"):
        assert points_into(home, older)
        assert stamp(home)["version"] == "0.20.9"

    for broken in ("{not json", json.dumps({"version": "not a version", "root": newer}), json.dumps(["0.20.10"])):
        with when(f"the newer copy built again and the record then reads {broken!r}"):
            step(home, newer, "apply")
            assert points_into(home, newer)
            with open(os.path.join(home.state, STAMP), "w") as handle:
                handle.write(broken)
            session_start(home, older, "s-older")
        with then("the older copy builds and writes a good record"):
            assert points_into(home, older)
            assert stamp(home)["version"] == "0.20.9"
            assert home.errors() == ""

    with when("a copy with no version in its manifest, such as a local checkout, builds"):
        unversioned = second_copy(home, "local", None)
        session_start(home, unversioned, "s-local")
    with then("it builds, and its record lets any copy build after it"):
        assert points_into(home, unversioned)
        assert stamp(home)["version"] is None
        session_start(home, older, "s-older")
        assert points_into(home, older)

    with when("a copy with no version runs after a newer build"):
        step(home, newer, "apply")
        session_start(home, unversioned, "s-local")
    with then("it builds as before, since it can not tell which is newer"):
        assert points_into(home, unversioned)


def test_an_older_copy_builds_again_once_the_newer_copy_is_taken_out(home):
    with given("hub 0.20.10 has built the shared files, and a chat runs hub 0.20.9"):
        home.install()
        set_version(home.plugin, "0.20.10")
        newer = home.plugin
        step(home, newer, "apply")
        older = second_copy(home, "0.20.9", "0.20.9")
        session_start(home, older, "s-older")
        assert points_into(home, newer)

    with when("the user goes back to 0.20.9 and Claude Code marks the 0.20.10 folder as no longer installed"):
        with open(os.path.join(newer, ".orphaned_at"), "w") as handle:
            handle.write("1764200000000")
        session_start(home, older, "s-older")
    with then("the older copy builds again"):
        assert points_into(home, older)
        assert stamp(home)["version"] == "0.20.9"

    with when("a record names a newer copy whose folder is gone"):
        with open(os.path.join(home.state, STAMP), "w") as handle:
            json.dump({"version": "0.21.0", "root": os.path.join(home.root, "plugin-cache", "gone", "hub")}, handle)
        step(home, older, "on", "pr_gif")
        session_start(home, older, "s-older")
    with then("the older copy builds too"):
        assert stamp(home)["version"] == "0.20.9"
        assert points_into(home, older)
