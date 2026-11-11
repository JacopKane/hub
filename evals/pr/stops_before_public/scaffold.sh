#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Applies hub there, where pr is on by default, and makes a repo on a feature branch
# whose remote is a bare repo in the workspace, so nothing can reach GitHub.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
. "$here/../../home_guard.sh"
step="$here/../../../plugins/hub/lib/hub_switch.py"
python3 "$step" apply
git init -q --bare origin.git
git init -q repo
cd repo
git config user.name Sam
git config user.email sam@example.com
git remote add origin ../origin.git
printf 'def total(items):\n    return sum(items)\n' > cart.py
git add . && git commit -q -m "Start the cart"
git push -q origin HEAD:main
git checkout -q -b feat/cart-discount
printf 'def total(items, discount=0):\n    return sum(items) - discount\n' > cart.py
git commit -q -am "Take a discount off the total"
