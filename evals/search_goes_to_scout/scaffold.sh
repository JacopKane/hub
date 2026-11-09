#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Turns on the tier rule, which is off for a new user, and applies hub, so the
# start text names the tiers and ~/.claude/agents already holds scout when the
# run starts. Then it makes seven .log files among other files.
set -eu
home_folder="$(cd "$HOME" && pwd -P)"
temp_folder="$(cd "${TMPDIR:-$(dirname "$(mktemp -u)")}" && pwd -P)"
case "$home_folder/" in
  "$temp_folder"/*) ;;
  *) echo "scaffold.sh changes hub's settings in HOME, so it runs only with HOME in a temp folder" >&2; exit 1 ;;
esac
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../../plugins/hub/lib/hub_switch.py" on tier_rule
python3 "$here/../../plugins/hub/lib/hub_switch.py" apply
mkdir -p logs/old logs/archive/2024
for name in logs/a.log logs/b.log logs/old/c.log logs/old/d.log logs/archive/e.log logs/archive/2024/f.log logs/archive/2024/g.log; do
  echo "line" > "$name"
done
for name in logs/notes.txt logs/old/readme.md logs/archive/log.bak; do
  echo "line" > "$name"
done
