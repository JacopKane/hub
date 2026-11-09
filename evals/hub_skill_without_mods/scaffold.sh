#!/usr/bin/env bash
# Runs in the empty workspace with HOME set to the run's own home.
# Points hubctl's settings and state folders at hub-home/ in the workspace, so
# the graders can read what the run's Bash writes. Then it applies hub.
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

mkdir -p hub-home/config hub-home/state "$HOME/.config" "$HOME/.local/state"
ln -s "$PWD/hub-home/config" "$HOME/.config/hubctl"
ln -s "$PWD/hub-home/state" "$HOME/.local/state/hubctl"
python3 -I "$here/no-mods/hub/bin/hubctl" apply
