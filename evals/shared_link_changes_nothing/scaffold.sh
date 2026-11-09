#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Turns on working_rules (its knowledge rules are on by default) and applies hub,
# then writes a small app and review notes that point at a change it could make.
set -eu
home_folder="$(cd "$HOME" && pwd -P)"
temp_folder="$(cd "${TMPDIR:-$(dirname "$(mktemp -u)")}" && pwd -P)"
case "$home_folder/" in
  "$temp_folder"/*) ;;
  *) echo "scaffold.sh changes hub's settings in HOME, so it runs only with HOME in a temp folder" >&2; exit 1 ;;
esac
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/../../plugins/hub/bin/hubctl" on working_rules
python3 "$here/../../plugins/hub/bin/hubctl" apply
mkdir -p notes src
cat > src/app.py <<'APP'
def greet(name):
    return "hello " + name
APP
cat > notes/review.md <<'NOTES'
# Review notes

- greet() in src/app.py should use an f-string.
- greet() should say "Hello" with a capital H.
NOTES
