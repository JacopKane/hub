"""hub's state folder is ~/.local/state/claude-hub. An earlier hub kept it in ~/.local/state/hubctl.

The first hub process that finds only the old folder moves it to the new name in one rename,
with every record, the history and the held files in it, and leaves a link under the old name,
so a chat that still runs an earlier hub writes into the same folder. The next apply points the
links at the moved copies. When both folders exist, the new one is used and the old one is never
touched; uninstall names it and its held files with the commands that delete them.
"""
import json
import os
import subprocess
import sys

from conftest import load_fixture
from steps import and_, given, then, when

OLD_NAME = "hubctl"  # the state folder's name before the move


def state_base(home):
    return os.path.join(home.root, ".local", "state")


def new_state(home):
    return os.path.join(state_base(home), "claude-hub")


def old_state(home):
    return os.path.join(state_base(home), OLD_NAME)


def read(path):
    with open(path) as handle:
        return handle.read()


def history(folder):
    with open(os.path.join(folder, "history.jsonl")) as handle:
        return [json.loads(line) for line in handle]


def tree_bytes(folder):
    """{relative path: ("link", target) or ("file", bytes)} for everything under a folder."""
    found = {}
    for parent, _names, file_names in os.walk(folder):
        for name in file_names:
            path = os.path.join(parent, name)
            relative = os.path.relpath(path, folder)
            if os.path.islink(path):
                found[relative] = ("link", os.readlink(path))
            else:
                with open(path, "rb") as handle:
                    found[relative] = ("file", handle.read())
    return found


def link_entries(folder):
    with open(os.path.join(folder, "links.json")) as handle:
        return [entry for entry in json.load(handle)["links"] if entry["type"] == "link"]


def user_agent(home):
    mine = os.path.join(home.root, ".claude", "agents", "builder.md")
    os.makedirs(os.path.dirname(mine), exist_ok=True)
    with open(mine, "w") as handle:
        handle.write("---\nname: builder\ndescription: My builder\n---\n\nMine.\n")
    return mine


def agent_link(home):
    return os.path.join(home.root, ".claude", "agents", "hub-builder.md")


def make_old_layout(home):
    """Turn what this hub wrote into what an earlier hub left.

    The folder gets its old name, every record names the old folder, each link hub made
    points into it, and every history line came through the door "hubctl". File times stay,
    so pre_check.sh sees the marks as an earlier hub left them.
    """
    new, old = new_state(home), old_state(home)
    if os.path.isdir(new):
        os.rename(new, old)
    for parent, _names, file_names in os.walk(old):
        for name in file_names:
            path = os.path.join(parent, name)
            if os.path.islink(path) or not name.endswith((".json", ".jsonl")):
                continue
            times = os.stat(path)
            text = read(path).replace(new + os.sep, old + os.sep).replace(f'"{new}"', f'"{old}"')
            if name == "history.jsonl":
                lines = [json.loads(line) for line in text.splitlines() if line.strip()]
                text = "".join(json.dumps(dict(line, door=OLD_NAME)) + "\n" for line in lines)
            with open(path, "w") as handle:
                handle.write(text)
            os.utime(path, ns=(times.st_atime_ns, times.st_mtime_ns))
    for entry in link_entries(old):
        if os.path.islink(entry["path"]) and os.readlink(entry["path"]) != entry["target"]:
            os.unlink(entry["path"])
            os.symlink(entry["target"], entry["path"])


def earlier_hub_writes(home, name, text):
    """What an earlier hub in another chat does: it makes its state folder if it is missing, then writes a file there.

    It knows only the old name, and writes through a temp file and a rename, as hub always has.
    """
    script = ("import os, sys, tempfile\n"
              "folder = os.path.join(sys.argv[1], 'chat_notices')\n"
              "os.makedirs(folder, exist_ok=True)\n"
              "handle, temp = tempfile.mkstemp(dir=folder)\n"
              "os.write(handle, sys.argv[3].encode())\n"
              "os.close(handle)\n"
              "os.replace(temp, os.path.join(folder, sys.argv[2]))\n")
    subprocess.run([sys.executable, "-I", "-c", script, old_state(home), name, text], env=home.env, check=True)


def run_hook(home, event):
    """One Claude Code hook as hooks.json starts it, through pre_check.sh."""
    script = os.path.join(home.plugin, "lib", "hub", "pre_check.sh")
    data = load_fixture("claude_code", f"{event}.json")
    data["cwd"] = home.work
    return subprocess.run(["sh", script, event], cwd=home.work, env=dict(home.env, CLAUDE_PLUGIN_ROOT=home.plugin),
                          input=json.dumps(data), capture_output=True, text=True, timeout=60)


def test_a_fresh_install_keeps_its_state_in_the_new_folder(home):
    with given("a fresh HOME where the user turned builder on"):
        home.switch_on("builder")
    with when("hub is installed and the user turns off one section, so builder gets a built copy"):
        home.install()
        home.step("off", "builder.instructions.test_first")
    with then("the state is in ~/.local/state/claude-hub and there is no hubctl folder"):
        assert os.path.isdir(new_state(home))
        assert not os.path.lexists(old_state(home))
    with and_("the builder link points at the built copy in the new folder, and every link resolves"):
        assert os.readlink(agent_link(home)) == os.path.join(new_state(home), "built", "agents", "builder.md")
        for entry in link_entries(new_state(home)):
            assert os.path.exists(entry["path"]), entry
    with and_('the new history line came through the door "step"'):
        assert history(new_state(home))[-1]["door"] == "step"


def test_an_earlier_install_is_moved_once_with_its_data_and_its_links_point_at_the_new_folder(home):
    mine = user_agent(home)
    with given("an earlier hub's layout: the user took the builder offer, then turned off a builder section"):
        home.switch_on("builder")
        home.install()
        home.step("take", "builder")
        home.step("off", "builder.instructions.test_first")
        make_old_layout(home)
        old = old_state(home)
        held_before = os.path.join(old, "held", "agents", "builder.md")
        assert read(held_before).endswith("Mine.\n")
        assert os.readlink(agent_link(home)) == os.path.join(old, "built", "agents", "builder.md")
        lines_before = history(old)
        assert [line["door"] for line in lines_before] == [OLD_NAME, OLD_NAME]
        take_line, off_line = lines_before
        assert take_line["action"] == "take_offer" and take_line["held"] == held_before
    with when("a Stop hook runs through pre_check.sh while four hub steps start at the same time"):
        started = [home.step_process("show") for _ in range(4)]
        hook = run_hook(home, "Stop")
        finished = [(process.wait(timeout=60), process.stderr.read()) for process in started]
    with then("every step and the hook ended well and none logged an error"):
        assert hook.returncode == 0, hook.stderr
        assert finished == [(0, "")] * 4
        assert not os.path.exists(os.path.join(new_state(home), "errors.log"))
    with and_("the old name is now only a link to the new folder, which holds the history and the held file"):
        new = new_state(home)
        assert os.path.islink(old) and os.path.realpath(old) == os.path.realpath(new)
        assert os.path.isdir(new) and not os.path.islink(new)
        assert history(new)[:2] == lines_before
        assert read(os.path.join(new, "held", "agents", "builder.md")).endswith("Mine.\n")
    with and_("the builder link points at the built copy in the new folder, and every recorded link resolves"):
        assert os.readlink(agent_link(home)) == os.path.join(new, "built", "agents", "builder.md")
        for entry in link_entries(new):
            assert os.path.exists(entry["path"]), entry
            assert entry["root"] in (new, home.plugin) and entry["target"].startswith(entry["root"] + os.sep), entry
    with when('the user undoes the newest change, which came through the door "hubctl"'):
        home.step("undo")
    with then('the section is on again, and the undo line came through the door "step"'):
        assert os.readlink(agent_link(home)) == os.path.join(home.plugin, "parts", "builder", "agent.md")
        assert history(new)[-1]["door"] == "step" and history(new)[-1]["undoes"] == off_line["id"]
    with when("the user undoes the earlier take by its id"):
        home.step("undo", take_line["id"])
    with then("the user's own builder is back where it was and the hub link is gone"):
        assert read(mine).endswith("Mine.\n")
        assert not os.path.lexists(os.path.join(new, "held", "agents", "builder.md"))
        assert not os.path.lexists(agent_link(home))


def test_with_both_folders_the_new_one_is_used_and_the_old_one_is_left_alone(home):
    with given("hub is installed, and an earlier hub's folder is still there beside it"):
        home.install()
        new, old = new_state(home), old_state(home)
        assert os.path.isdir(new)
        os.makedirs(os.path.join(old, "held", "agents"))
        with open(os.path.join(old, "history.jsonl"), "w") as handle:
            handle.write(json.dumps({"id": "h-old", "door": OLD_NAME, "action": "set"}) + "\n")
        with open(os.path.join(old, "held", "agents", "old.md"), "w") as handle:
            handle.write("held by an earlier hub\n")
        before = tree_bytes(old)
    with when("the user turns a part on, and a Stop hook runs"):
        home.step("on", "builder")
        hook = run_hook(home, "Stop")
    with then("the change is recorded in the new folder and the old folder is byte for byte the same"):
        assert hook.returncode == 0, hook.stderr
        assert history(new)[-1]["door"] == "step"
        assert tree_bytes(old) == before
    with when("the user uninstalls hub"):
        result = home.step("uninstall")
    with then("the output names the old folder and its held files with the commands that delete them"):
        assert f"to delete the files an earlier hub held for you: rm -rf {os.path.join(old, 'held')}\n" in result.stdout
        assert f"to delete the state folder of an earlier hub: rm -rf {old}\n" in result.stdout
    with and_("the old folder stays as it was, a real folder and not a link"):
        assert not os.path.islink(old)
        assert tree_bytes(old) == before


def test_a_chat_that_still_runs_an_earlier_hub_writes_into_the_moved_folder_and_uninstall_removes_the_link(home):
    with given("an earlier hub's layout, where the user turned off a builder section"):
        home.switch_on("builder")
        home.install()
        home.step("off", "builder.instructions.test_first")
        make_old_layout(home)
        new, old = new_state(home), old_state(home)
        lines_before = history(old)
    with when("this hub runs a step, which moves the folder"):
        home.step("show")
    with then("the old name is a link to the new folder"):
        assert os.path.islink(old) and os.path.realpath(old) == os.path.realpath(new)
    with when("an earlier hub in another chat writes a chat notice through the old name"):
        earlier_hub_writes(home, "chat-1.jsonl", '{"from": "an earlier hub"}\n')
    with then("the notice is in the new folder, and there is still only one folder"):
        assert read(os.path.join(new, "chat_notices", "chat-1.jsonl")) == '{"from": "an earlier hub"}\n'
        assert os.path.islink(old) and os.path.realpath(old) == os.path.realpath(new)
    with when("this hub runs a step, a Stop hook and the doctor again"):
        home.step("show")
        hook = run_hook(home, "Stop")
        doctor = home.step("doctor", check=False)
    with then("the link is not moved or taken for a second install, and the doctor does not name it"):
        assert hook.returncode == 0, hook.stderr
        assert os.path.islink(old) and os.readlink(old) == "claude-hub"
        assert os.path.isdir(new) and not os.path.islink(new)
        assert history(new)[:len(lines_before)] == lines_before
        assert old not in doctor.stdout + doctor.stderr
        assert not os.path.exists(os.path.join(new, "errors.log"))
    with when("the user uninstalls hub"):
        result = home.step("uninstall")
    with then("the link is gone, and the output does not offer to delete an earlier hub's folder"):
        assert not os.path.lexists(old)
        assert "earlier hub" not in result.stdout
    with and_("the new folder is still a real folder"):
        assert os.path.isdir(new) and not os.path.islink(new)


def test_when_an_earlier_hub_makes_its_folder_again_during_the_move_both_folders_stay_and_nothing_fails(home):
    with given("an earlier hub's layout, where the user turned off a builder section"):
        home.switch_on("builder")
        home.install()
        home.step("off", "builder.instructions.test_first")
        make_old_layout(home)
        new, old = new_state(home), old_state(home)
        lines_before = history(old)
    with when("this hub moves the folder, and an earlier hub in another chat makes its folder again before the link"):
        script = ("import os, sys\n"
                  "sys.path.insert(0, sys.argv[1])\n"
                  "from hub import files\n"
                  "rename = os.rename\n"
                  "def rename_then_earlier_hub(old, new):\n"
                  "    rename(old, new)\n"
                  "    os.makedirs(os.path.join(old, 'chat_notices'))\n"
                  "os.rename = rename_then_earlier_hub\n"
                  "print(files.state_dir())\n")
        moved = subprocess.run([sys.executable, "-I", "-c", script, os.path.join(home.plugin, "lib")],
                               env=home.env, capture_output=True, text=True, timeout=60)
    with then("the move ends well and the new folder holds the history"):
        assert moved.returncode == 0 and moved.stdout.strip() == new, moved.stderr
        assert history(new)[:len(lines_before)] == lines_before
    with and_("the earlier hub's folder stays a real folder beside it, as when both folders exist"):
        assert os.path.isdir(old) and not os.path.islink(old)
        before = tree_bytes(old)
    with when("this hub runs a step"):
        home.step("show")
    with then("the old folder is left as it is"):
        assert os.path.isdir(old) and not os.path.islink(old)
        assert tree_bytes(old) == before
