#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Applies hub there, so ~/.claude/agents already holds scout when the run starts,
# then makes seven .log files among other files.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../../plugins/hub/bin/hubctl" apply
mkdir -p logs/old logs/archive/2024
for name in logs/a.log logs/b.log logs/old/c.log logs/old/d.log logs/archive/e.log logs/archive/2024/f.log logs/archive/2024/g.log; do
  echo "line" > "$name"
done
for name in logs/notes.txt logs/old/readme.md logs/archive/log.bak; do
  echo "line" > "$name"
done
