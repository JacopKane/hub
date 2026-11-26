---
name: switch
description: Turns hub's parts on or off, lists them, undoes a change or uninstalls hub, when the user asks in plain words, such as "turn off texts first", "switch on the researcher agent", "what does hub have on?", "undo that" or "uninstall hub".
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py *)
---

The user changes hub by asking in plain words. Run hub's step once for each request:

```
python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py <words>
```

- To see the parts and their flags, use `show`. Use it first when you are not sure which flag the user means.
- To turn a part or a flag on or off, use `on <flag>` or `off <flag>`, such as `off pr.instructions.texts_first` for "turn off texts first".
- To give a flag a value, use `set <flag> <value>`.
- For "undo that", use `undo`. To undo an older change, find its id with `history` and use `undo <id>`.
- For "what did hub change?", use `history`. It lists the last 20 changes, newest first, with their ids.
- To check the setup, use `doctor`.
- For "uninstall hub", use `uninstall`. It removes hub's links and puts back the settings hub changed, and it keeps the user's own files. Run it only when the user asks for it.

Reply with the line hub printed, in a code block, and nothing more. It says what changed and how to undo it.

Claude can raise a protection, such as a gate, but only the user can lower one, with /hub where Claude Code loads plugin mods. If hub refuses, show its answer and tell the user the exact change you need. Do not look for another way around it.
