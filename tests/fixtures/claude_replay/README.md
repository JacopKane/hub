# Recorded `claude` output

`claude_replay.py` prints these files by their arguments, as `index.json` maps them.

- `version.txt` is `claude --version` from Claude Code 2.1.319.
- `auth-status.json` has the keys `claude auth status` prints when signed in to claude.ai, as the 2.1.319 code builds them. It was not captured from a signed-in run, and the values are made up. A signed-out run in a temporary HOME printed the same keys without email, orgId, orgName, subscriptionType and planTier, with authMethod "none", and exited 1.
