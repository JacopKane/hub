# Sourced by an eval scaffold before it applies hub.
# hub writes into HOME, and into CLAUDE_CONFIG_DIR, XDG_CONFIG_HOME and
# XDG_STATE_HOME when they are set. XDG_DATA_HOME is checked too. Each must be
# inside a temporary folder.
real_path() { python3 -I -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }
user_home="$(python3 -I -c 'import os, pwd; print(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))')"
stop_unless_temporary() {
  folder="$(real_path "$2")"
  inside_temp=no
  for root in "${TMPDIR:-/tmp}" /tmp /var/folders; do
    real_root="$(cd "$root" 2>/dev/null && pwd -P)" || continue
    case "$folder" in "$real_root"/?*) inside_temp=yes ;; esac
  done
  if [ "$inside_temp" = no ] || [ "$folder" = "$user_home" ]; then
    echo "scaffold.sh stopped. $1 is $2, and that is not inside a temporary folder. Run it only with the eval run's own $1." >&2
    exit 1
  fi
}
if [ -z "${HOME:-}" ] || ! [ -d "$HOME" ]; then
  echo "scaffold.sh stopped. HOME is ${HOME:-empty}, and that is not inside a temporary folder. Run it only with the eval run's own HOME." >&2
  exit 1
fi
stop_unless_temporary HOME "$HOME"
for name in CLAUDE_CONFIG_DIR XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME; do
  value="$(printenv "$name" || true)"
  if [ -n "$value" ]; then
    stop_unless_temporary "$name" "$value"
  fi
done
