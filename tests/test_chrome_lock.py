"""chrome_lock: only the chat that holds the Chrome lock drives Chrome."""
import os
import subprocess
import sys
import time

from dialogs import SESSION_DOCS, SESSION_WEB, bash, set_clock, tool_call
from steps import and_, given, then, when

CHROME_TOOL = "mcp__claude-in-chrome__navigate"


def lock_command(home, session, *words, check=True):
    env = dict(home.env, CLAUDE_CODE_SESSION_ID=session)
    return subprocess.run([sys.executable, os.path.join(home.plugin, "bin", "hubctl"), "browser-lock", *words],
                          cwd=home.work, env=env, capture_output=True, text=True, timeout=60, check=check)


def test_only_the_chat_that_holds_the_chrome_lock_drives_chrome(home):
    with given('chrome_lock is on, and WEB ran hubctl browser-lock take "docs check"'):
        home.install()
        home.hubctl("on", "chrome_lock")
        home.env["HUB_LOCK_POLL"] = "0.1"
        set_clock(home, "2026-03-02T09:00:00Z")
        taken = lock_command(home, SESSION_WEB, "take", "docs check")
        assert "docs check" in taken.stdout

    with when("DOCS calls a Claude in Chrome tool"):
        result = tool_call(home, CHROME_TOOL, {"url": "https://example.com"}, session=SESSION_DOCS)
    with then("the call is refused, and the reason names WEB, the job and the start time"):
        assert result.refused
        assert SESSION_WEB in result.reason and '"docs check"' in result.reason
        assert "2026-03-02T09:00:00Z" in result.reason
        assert 'hubctl browser-lock wait "<your job>"' in result.reason
    with and_("a tool from another server is not touched"):
        assert tool_call(home, "mcp__docs__search", {"query": "x"}, session=SESSION_DOCS).ran

    with when("a subagent of WEB calls the same tool"):
        result = tool_call(home, CHROME_TOOL, {"url": "https://example.com"}, session=SESSION_WEB,
                           agent_id="a0000000000000031")
    with then("it runs"):
        assert result.ran

    with when("DOCS tries to take the lock while WEB holds it"):
        refused = lock_command(home, SESSION_DOCS, "take", "page check", check=False)
    with then("it is not taken, and the status names the holder"):
        assert refused.returncode == 1 and SESSION_WEB in refused.stdout
        assert lock_command(home, SESSION_DOCS, "status", check=False).returncode == 1

    with when('DOCS runs hubctl browser-lock wait "page check", and WEB releases the lock'):
        env = dict(home.env, CLAUDE_CODE_SESSION_ID=SESSION_DOCS)
        waiter = subprocess.Popen([sys.executable, os.path.join(home.plugin, "bin", "hubctl"), "browser-lock",
                                   "wait", "page check", "30"], cwd=home.work, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.3)
        assert waiter.poll() is None, "the wait ended before the lock was free"
        assert "released" in lock_command(home, SESSION_WEB, "release").stdout
        out, _err = waiter.communicate(timeout=30)
    with then("DOCS holds the lock, and its Chrome calls run while WEB's are refused"):
        assert waiter.returncode == 0 and "page check" in out
        assert tool_call(home, CHROME_TOOL, session=SESSION_DOCS).ran
        assert tool_call(home, CHROME_TOOL, session=SESSION_WEB).refused

    with when("WEB tries to release the lock DOCS holds"):
        result = lock_command(home, SESSION_WEB, "release", check=False)
    with then("the lock stays with DOCS"):
        assert result.returncode == 1 and SESSION_DOCS in result.stdout

    with when("a chat runs hubctl browser-lock clear"):
        result = bash(home, "hubctl browser-lock clear", session=SESSION_WEB)
    with then("the call is refused, because only the user clears a lock"):
        assert result.refused and "only the user" in result.reason.lower()
    with and_("the user runs it in a terminal, and the lock is free"):
        assert "clearing" in home.hubctl("browser-lock", "clear").stdout
        assert lock_command(home, SESSION_WEB, "status").returncode == 0

    with when("the user turns chrome_lock.one_chat off, and DOCS takes the lock again"):
        lock_command(home, SESSION_DOCS, "take", "page check")
        home.hubctl("off", "chrome_lock.one_chat")
    with then("WEB's Chrome calls run"):
        assert tool_call(home, CHROME_TOOL, session=SESSION_WEB).ran
