#!/usr/bin/env python3
"""A stand-in for a running chat's own process.

    chat_process.py <python> <dispatch.py> <SessionStart input file> [--commands]

It runs the SessionStart hook as a child through sh, as Claude Code runs its
hooks, so the hook finds this process as its chat. The hook's answer goes to
<input file>.answer.json, as the chat would read it. It prints "ready" and then
waits until it is stopped, so a test can end the chat by ending the process.
With --commands it reads one JSON list of arguments per line on stdin, runs
each as its own child, as a chat's Bash tool does, and prints one JSON line
with the exit code and the output.
"""
import json
import shlex
import signal
import subprocess
import sys
import time

python, dispatch, input_path = sys.argv[1:4]
command = f"{shlex.quote(python)} -I {shlex.quote(dispatch)} SessionStart < {shlex.quote(input_path)}"
with open(input_path + ".answer.json", "w") as answer:
    subprocess.run(["sh", "-c", command], check=True, stdout=answer)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print("ready", flush=True)
if "--commands" in sys.argv[4:]:
    for line in sys.stdin:
        done = subprocess.run(json.loads(line), capture_output=True, text=True)
        print(json.dumps({"code": done.returncode, "stdout": done.stdout, "stderr": done.stderr}), flush=True)
    sys.exit(0)
while True:
    time.sleep(60)
