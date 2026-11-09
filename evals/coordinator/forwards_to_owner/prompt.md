---
description: The coordinator chat gets a request about a repo and forwards it to the chat that owns the repo, instead of doing it.
plugins: ["../../../plugins/hub"]
runs: 3
max_turns: 10
allowed_tools: [Skill, SendMessage, Read, Edit, Write, Bash]
append_system_prompt: You are the chat named HUB, the coordinator. The chat named WEB is running and owns the repo folder of this workspace.
---

Please add a discount parameter to the total function in repo/cart.py and run its tests.
