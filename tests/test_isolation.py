"""No test may read or write the real home folder.

Scenarios run hub as processes in a temporary HOME. Code that a test imports
runs in pytest's own process, so pytest's HOME and XDG folders must point at
a temporary folder too.
"""
import os
import sys

from conftest import PLUGIN, hub_entries, new_hub_entries, real_home
from steps import and_, given, then, when


def inside(path, folder):
    return os.path.realpath(path).startswith(os.path.realpath(folder) + os.sep)


def test_code_imported_by_a_test_and_every_test_process_use_a_temp_home(home):
    with given("hub's file module, imported into pytest's own process"):
        sys.path.insert(0, os.path.join(PLUGIN, "lib"))
        from hub import files
    with then("its settings and state folders are outside the real home"):
        assert not inside(files.config_dir(), real_home()) and not inside(files.state_dir(), real_home())
        assert os.path.realpath(os.environ["HOME"]) != real_home()
        for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            assert name in os.environ and not inside(os.environ[name], real_home()), name
    with and_("each scenario's processes get XDG folders inside their own temp HOME"):
        for name, folder in [("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", os.path.join(".local", "state")),
                             ("XDG_DATA_HOME", os.path.join(".local", "share")), ("XDG_CACHE_HOME", ".cache")]:
            assert home.env[name] == os.path.join(home.root, folder), name


def test_the_end_of_session_check_sees_a_hub_entry_added_to_a_tool_folder(tmp_path):
    with given("a home whose agents folder holds the user's own file and an older hub entry"):
        agents = tmp_path / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "mine.md").write_text("x")
        (agents / "hub-old.md").write_text("x")
        before = hub_entries(str(tmp_path))
    with when("a run adds a hub agent, a Cursor agent and the Cursor plugin folder, and the user adds a file"):
        (agents / "hub-new.md").write_text("x")
        (agents / "other.md").write_text("x")
        (tmp_path / ".cursor" / "agents").mkdir(parents=True)
        (tmp_path / ".cursor" / "agents" / "hub-scout.md").write_text("x")
        (tmp_path / ".cursor" / "plugins" / "local" / "hub").mkdir(parents=True)
    with then("only the three hub entries are reported"):
        found = new_hub_entries(before, hub_entries(str(tmp_path)))
        assert sorted(os.path.relpath(path, str(tmp_path)) for path in found) == [
            ".claude/agents/hub-new.md", ".cursor/agents/hub-scout.md", ".cursor/plugins/local/hub"]
