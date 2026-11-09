#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Makes a git repo with a remote and a CI file and a Cursor folder, then applies hub,
# so ~/.claude/agents already holds the tuner agent and no hub.json exists yet.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"

# The links below go into $HOME. A real home must never get them.
home="$(cd "$HOME" 2>/dev/null && pwd -P)" || home=""
user_home="$(python3 -I -c 'import os, pwd; print(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))')"
inside_temp=no
for root in "${TMPDIR:-/tmp}" /tmp /var/folders; do
  real_root="$(cd "$root" 2>/dev/null && pwd -P)" || continue
  case "$home" in "$real_root"/?*) inside_temp=yes ;; esac
done
if [ -z "$home" ] || [ "$inside_temp" = no ] || [ "$home" = "$user_home" ]; then
  echo "scaffold.sh stopped. HOME is $HOME, and that is not inside a temporary folder. Run it only with the eval run's own HOME." >&2
  exit 1
fi

mkdir -p "$HOME/.cursor" .github/workflows
git init -q
git remote add origin https://example.invalid/demo/app.git
printf 'on: push\n' > .github/workflows/ci.yml
python3 -I "$here/../../plugins/hub/bin/hubctl" apply
