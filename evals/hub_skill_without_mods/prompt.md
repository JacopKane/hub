---
description: Typing /hubctl in a copy of hub without the mod turns the dashes flag off through hubctl. Run make-copy.sh first, then run the eval with --scaffold, --allow-tools "Bash(python3 -I *)" and --no-publish.
plugins: ["no-mods/hub"]
runs: 3
max_turns: 6
allowed_tools: [Skill, Bash]
---

/hubctl off public_text_gate.dashes
