---
description: Asked to prepare a PR, Claude loads hub-pr, reads the repo's contributing guide and template first, and follows them over hub's own defaults.
plugins: ["../../../plugins/hub"]
runs: 3
max_turns: 15
allowed_tools: [Skill, Read, Write, Bash]
---

Write the title and the body for a PR of the branch in repo/, and save them to pr.md in the workspace. Do not push or open anything.
