---
name: hub-setup
description: Sets hub up on this machine, or sets it up again. It looks at the machine first, asks a few questions in one dialog, and turns on a small starting set. Use it when hub's start text says to run hub setup, or when the user asks to set hub up.
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py setup *), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py show --all), Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py profile)
---

Look first, ask little, and start small.

1. Look. The scan is in hub's start text. If this machine was set up before, there is none, so run `python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py profile` instead. Then run `python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py show --all` for hub's parts and instruction ids, and read the files the scan lists under "Your own rules".
2. Start the tuner agent with the scan, those rules and the output of show. It answers with one JSON object.
3. Save the answer to a file and run `python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py setup --answer <file>`. hub drops what it does not know and prints the questions, each with Skip.
4. Ask only those questions. In Claude Code, ask them all in one AskUserQuestion dialog. In Cursor, ask them in one plain message. If there are none, go on.
5. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py setup --reply '<json>'` with the answers. Key each answer by the id that step 3 printed for its question, such as {"3f9a1c2e": "work"}. Leave out each question the user skipped.
6. Show the summary exactly as hub printed it. It already says what is on and why. Add one line of your own at most, then go back to the user's task.

Never ask what the scan already shows, such as the tools, the repo or the sign-in. Turn on nothing beyond what setup turned on, because an offer stays off until the user says yes. If hub stops with an error, show its output and end setup there.
