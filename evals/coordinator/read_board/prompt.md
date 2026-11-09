---
description: Asked what every chat is doing, the coordinator reads the chat board before it answers.
plugins: ["../../../plugins/hub"]
runs: 3
max_turns: 8
allowed_tools: [Skill, SendMessage, Read, Bash]
append_system_prompt: You are the chat named HUB, the coordinator.
---

What is every chat doing right now?
