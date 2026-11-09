#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Applies hub there and switches the coordinator on, named HUB, with the chat board.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
step="$here/../../../plugins/hub/lib/hub_switch.py"
python3 "$step" apply
python3 "$step" on coordinator
python3 "$step" set coordinator.name HUB
python3 "$step" on chat_board
