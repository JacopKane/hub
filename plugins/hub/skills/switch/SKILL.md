---
name: switch
description: Turns hub's parts on or off, lists them, undoes a change or uninstalls hub, when the user asks in plain words, such as "turn off the em dash check", "switch on the researcher agent", "what does hub have on?", "undo that" or "uninstall hub".
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py *)
---

The user changes hub by asking in plain words. Run hub's step once for each request:

```
python3 -I ${CLAUDE_PLUGIN_ROOT}/lib/hub_switch.py <words>
```

- To see the parts and their flags, use `show`. Use it first when you are not sure which flag the user means.
- To turn a part or a flag on or off, use `on <flag>` or `off <flag>`, such as `off public_text_gate.dashes` for "turn off the em dash check".
- To give a flag a value, use `set <flag> <value>`.
- For "undo that", use `undo`. To undo an older change, find its id in the output of `screen --json` and use `undo <id>`.
- To check the setup, use `doctor`.
- For "uninstall hub", use `uninstall`. It removes hub's links and puts back the settings hub changed, and it keeps the user's own files.

Reply with the line hub printed, in a code block, and nothing more. It says what changed and how to undo it.

A protection, such as a gate or self-protection, changes only when the newest message the user typed asks for it by name. Uninstalling works the same way, so the user must ask to "uninstall hub" in their own words. If hub refuses, show its answer and tell the user the exact change you need. Do not look for another way around it.
