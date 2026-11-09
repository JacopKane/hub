"""Slice 2: an apply that stops partway leaves a record that the next apply and uninstall follow.

The stop is real. ~/.cursor/agents is the user's plain file, so the first
write into it fails after hub made its first Claude Code link.
"""
import json
import os
import shutil

from conftest import PLUGIN
from steps import and_, given, then, when
from test_start_text_and_links import agent_link, cursor_agent, hub_entries, read, use_cursor_beta

USER_TEXT = "my notes, not a folder\n"


def install_with_a_blocked_cursor_folder(home):
    """Install with Cursor on while ~/.cursor/agents is the user's plain file. Returns the failed apply."""
    blocker = os.path.join(home.root, ".cursor", "agents")
    os.makedirs(os.path.dirname(blocker))
    with open(blocker, "w") as handle:
        handle.write(USER_TEXT)
    os.makedirs(home.config)
    # The tuner is the first agent part in tree order. With it off and builder on, which a new user has off,
    # builder is the first link apply makes.
    home.write_json(home.user_file, {"version": 1, "flags": {"tuner": {"value": "off", "by": "user"},
                                                            "builder": {"value": "on", "by": "user"}}})
    use_cursor_beta(home)
    shutil.copytree(PLUGIN, home.plugin, ignore=shutil.ignore_patterns("__pycache__"))
    return home.hubctl("apply", check=False)


def recorded(home):
    path = os.path.join(home.state, "links.json")
    return [entry["path"] for entry in home.state_file("links.json")["links"]] if os.path.exists(path) else []


def test_apply_that_stops_between_two_links_is_finished_by_the_next_apply(home):
    blocker = os.path.join(home.root, ".cursor", "agents")
    with given("apply stopped after it made the builder link and before it made the scout link"):
        failed = install_with_a_blocked_cursor_folder(home)
        assert failed.returncode != 0
        assert os.path.islink(agent_link(home, "builder"))
        assert not os.path.lexists(agent_link(home, "scout"))
    with then("every link apply made is in the record"):
        assert agent_link(home, "builder") in recorded(home)
    with when("the user moves the plain file away and apply runs again"):
        moved = os.path.join(home.root, "my-notes.txt")
        os.rename(blocker, moved)
        result = home.hubctl("apply")
    with then("apply finishes every link, and says no path is in its way"):
        assert "is not a file hub made" not in result.stdout
        assert "changed since hub made it" not in result.stdout
        for path in (agent_link(home, "builder"), agent_link(home, "scout")):
            assert os.readlink(path).startswith(home.plugin + os.sep)
        assert os.path.isfile(cursor_agent(home, "builder")) and os.path.isfile(cursor_agent(home, "scout"))
        assert set(hub_entries(home)) <= set(recorded(home))
    with and_("the record holds no step left half done"):
        assert not [entry for entry in home.state_file("links.json")["links"] if "state" in entry]
        assert read(moved) == USER_TEXT
    with when('the user runs "hubctl uninstall"'):
        home.hubctl("uninstall")
    with then("every hub link and copy is gone"):
        assert hub_entries(home) == []


def test_uninstall_right_after_a_stopped_apply_removes_the_link_hub_made(home):
    blocker = os.path.join(home.root, ".cursor", "agents")
    with given("apply stopped after it made the builder link"):
        assert install_with_a_blocked_cursor_folder(home).returncode != 0
        assert os.path.islink(agent_link(home, "builder"))
    with when('the user runs "hubctl uninstall"'):
        home.hubctl("uninstall")
    with then("the builder link is gone, and the user's plain file is unchanged"):
        assert not os.path.lexists(agent_link(home, "builder"))
        assert hub_entries(home) == []
        assert read(blocker) == USER_TEXT


def test_a_users_file_or_link_at_a_path_a_stopped_apply_planned_is_never_touched(home):
    blocker = os.path.join(home.root, ".cursor", "agents")
    mine = os.path.join(home.root, "my-agents", "builder.md")
    with given("apply stopped after it made the builder link, before it wrote Cursor's builder copy"):
        assert install_with_a_blocked_cursor_folder(home).returncode != 0
        assert os.path.islink(agent_link(home, "builder"))
    with and_("the user puts a folder with their own hub-builder.md where Cursor's copy was planned"):
        os.unlink(blocker)
        os.makedirs(blocker)
        with open(cursor_agent(home, "builder"), "w") as handle:
            handle.write(USER_TEXT)
    with and_("the user replaces the builder link hub made with a link of their own"):
        os.makedirs(os.path.dirname(mine))
        with open(mine, "w") as handle:
            handle.write("---\nname: builder\ndescription: mine\n---\n\nMine.\n")
        os.unlink(agent_link(home, "builder"))
        os.symlink(mine, agent_link(home, "builder"))
    with when("apply runs again"):
        result = home.hubctl("apply")
    with then("hub finishes its other links and leaves both of the user's paths, one line for each"):
        assert os.readlink(agent_link(home, "scout")).startswith(home.plugin + os.sep)
        for path in (agent_link(home, "builder"), cursor_agent(home, "builder")):
            assert len([line for line in result.stdout.splitlines() if path in line]) == 1
            assert path not in recorded(home)
        assert os.readlink(agent_link(home, "builder")) == mine
        assert read(cursor_agent(home, "builder")) == USER_TEXT
    with when('the user runs "hubctl uninstall"'):
        home.hubctl("uninstall")
    with then("hub's own links are gone, and the user's link and file are unchanged"):
        assert not os.path.lexists(agent_link(home, "scout"))
        assert os.readlink(agent_link(home, "builder")) == mine
        assert read(cursor_agent(home, "builder")) == USER_TEXT


def test_a_link_of_the_users_in_hubs_cursor_folder_is_never_written_through_or_removed(home):
    cursor_folder = os.path.join(home.root, ".cursor", "plugins", "local", "hub")
    hooks_link = os.path.join(cursor_folder, "cursor", "hooks.json")
    manifest_link = os.path.join(cursor_folder, ".cursor-plugin")
    theirs = os.path.join(home.root, "my-cursor", "hooks.json")
    their_folder = os.path.join(home.root, "my-cursor", "plugin-folder")
    with given("hub is installed with Cursor"):
        os.makedirs(home.config)
        use_cursor_beta(home)
        home.install()
    with and_("the user replaces hooks.json with a link to their file, and the .cursor-plugin folder with a link to their empty folder"):
        os.makedirs(their_folder)
        with open(theirs, "w") as handle:
            handle.write(USER_TEXT)
        os.unlink(hooks_link)
        os.symlink(theirs, hooks_link)
        shutil.rmtree(manifest_link)
        os.symlink(their_folder, manifest_link)
    with when("apply runs twice"):
        first = home.hubctl("apply")
        second = home.hubctl("apply")
    with then("the user's files are unchanged and both links still point where they did"):
        assert read(theirs) == USER_TEXT
        assert os.listdir(their_folder) == []
        assert os.readlink(hooks_link) == theirs
        assert os.readlink(manifest_link) == their_folder
    with and_("each apply names each clash in one line"):
        for result in (first, second):
            for path in (hooks_link, manifest_link):
                assert len([line for line in result.stdout.splitlines() if "hub left" in line and path in line]) == 1
    with and_("hubctl doctor names the clash with the user's link"):
        doctor = [line for line in home.hubctl("doctor").stdout.splitlines() if line.startswith("warning:")]
        assert len([line for line in doctor if hooks_link in line]) == 1
        assert len([line for line in doctor if manifest_link in line]) == 1
    with when('the user runs "hubctl uninstall"'):
        home.hubctl("uninstall")
    with then("the user's links and files are still unchanged"):
        assert read(theirs) == USER_TEXT
        assert os.readlink(hooks_link) == theirs
        assert os.readlink(manifest_link) == their_folder
        assert os.listdir(their_folder) == []


def test_the_record_marks_a_step_before_a_stop_can_leave_it_half_done(home):
    side = os.path.join(home.root, "record-at-the-stop.json")
    crash = os.path.join(home.root, "crash")
    with given("apply stopped while it wrote Cursor's builder copy"):
        assert install_with_a_blocked_cursor_folder(home).returncode != 0
    with then("the record held the planned mark for that copy at the stop"):
        entries = {entry["path"]: entry for entry in home.state_file("links.json")["links"]}
        assert entries[cursor_agent(home, "builder")].get("state") == "planned"
    with given("hub is installed with scout on, in a home where removing scout's link stops"):
        os.unlink(os.path.join(home.root, ".cursor", "agents"))
        home.hubctl("apply")
        assert os.path.islink(agent_link(home, "scout"))
        os.makedirs(crash)
        with open(os.path.join(crash, "sitecustomize.py"), "w") as handle:
            handle.write(
                "import os, shutil\n"
                "real_unlink = os.unlink\n"
                "def unlink(path, *args, **kwargs):\n"
                f"    if str(path).endswith('hub-scout.md') and os.path.islink(path):\n"
                f"        shutil.copy({os.path.join(home.state, 'links.json')!r}, {side!r})\n"
                "        raise OSError('stopped')\n"
                "    return real_unlink(path, *args, **kwargs)\n"
                "os.unlink = unlink\n")
        home.env["PYTHONPATH"] = crash
    with when("the user turns scout off and the removal stops"):
        assert home.hubctl("off", "scout", check=False).returncode != 0
    with then("the record held the removing mark for scout's link at the stop"):
        with open(side) as handle:
            held = {entry["path"]: entry for entry in json.load(handle)["links"]}
        assert held[agent_link(home, "scout")].get("state") == "removing"
    with when("apply runs again without the stop"):
        del home.env["PYTHONPATH"]
        home.hubctl("apply")
    with then("scout's link is gone and the record holds no half done step"):
        assert not os.path.lexists(agent_link(home, "scout"))
        assert not [entry for entry in home.state_file("links.json")["links"] if "state" in entry]
