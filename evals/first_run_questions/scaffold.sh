#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Makes a git repo with a remote and a CI file and a Cursor folder, then applies hub,
# so ~/.claude/agents already holds the tuner agent and no hub.json exists yet.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"

. "$here/../home_guard.sh"

mkdir -p "$HOME/.cursor" .github/workflows
git init -q
git remote add origin https://example.invalid/demo/app.git
printf 'on: push\n' > .github/workflows/ci.yml
python3 -I "$here/../../plugins/hub/lib/hub_switch.py" apply
