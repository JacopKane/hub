#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Applies hub there and switches the coordinator on, named HUB.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
hubctl="$here/../../../plugins/hub/bin/hubctl"
python3 "$hubctl" apply
python3 "$hubctl" on coordinator
python3 "$hubctl" set coordinator.name HUB
mkdir -p repo
echo "def total(items): return sum(items)" > repo/cart.py
