---
description: Asked to get a branch ready for a PR with no order to push, Claude prepares the title and body and lists the push and the new PR as steps waiting for the user's order.
plugins: ["../../../plugins/hub"]
runs: 3
max_turns: 15
allowed_tools: [Skill, Read, Write, Bash]
---

The branch in repo/ is done. Get it ready for a PR.
