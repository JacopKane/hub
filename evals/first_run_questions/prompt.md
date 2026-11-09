---
description: A plain repo task on a fresh machine. hub has no settings yet, so its first run starts the setup agent, and the user sees one dialog with at most three questions. Run it with --scaffold, --allow-tools "Bash(python3 -I *)" Write, and --no-publish.
plugins: ["../../plugins/hub"]
runs: 1
max_turns: 30
allowed_tools: [Agent, Skill, AskUserQuestion, Read, Glob, Bash, Write]
---

Add a short README.md to this repo that says what it is for.
