#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Points hub's settings and state folders at hub-home/ in the workspace, so
# the graders can read what the run's Bash writes. Then it applies hub.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"

. "$here/../home_guard.sh"

mkdir -p hub-home/config hub-home/state "$HOME/.config" "$HOME/.local/state"
ln -s "$PWD/hub-home/config" "$HOME/.config/$(cat "$here/no-mods/hub/lib/hub/config_name")"
ln -s "$PWD/hub-home/state" "$HOME/.local/state/hubctl"
python3 -I "$here/no-mods/hub/lib/hub_switch.py" apply
