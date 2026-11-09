---
description: A plain count of matching files is a small mechanical job, so the run should hand it to scout.
plugins: ["../../plugins/hub"]
runs: 3
max_turns: 12
allowed_tools: [Agent, Glob, Read, Grep]
---

How many .log files are in the logs folder of this workspace, including its subfolders? Give me just the number.
