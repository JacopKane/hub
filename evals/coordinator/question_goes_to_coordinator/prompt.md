---
description: A topic chat that needs a decision from the user sends its question to the live coordinator, with context and options, and does not open a question dialog.
plugins: ["../../../plugins/hub"]
runs: 3
max_turns: 10
allowed_tools: [SendMessage, AskUserQuestion, Read]
append_system_prompt: You are the chat named WEB. The coordinator chat named HUB is live. Questions for the user go through HUB.
---

The cart code in repo/cart.py can round totals up or down, and the spec does not say which. The user has to decide. Ask them.
