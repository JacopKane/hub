---
name: hubctl
description: Show hub's parts and flags, or change one, from the chat. Type it alone to see the list, or add a hubctl command such as "off public_text_gate.dashes".
argument-hint: "[show | on <flag> | off <flag> | set <flag> <value> | undo [id] | doctor]"
disable-model-invocation: true
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/bin/hubctl *)
---

Run hubctl once with the words the user typed after the command, or with `show` when there are none.

```
python3 -I ${CLAUDE_PLUGIN_ROOT}/bin/hubctl <words>
```

Put the words in place of `<words>` as typed, quoted for the shell. Do nothing else.

Reply with hubctl's output exactly as it printed it, in a code block, with nothing before or after. If hubctl exits with an error, show its output the same way.
