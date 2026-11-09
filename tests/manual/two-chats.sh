#!/usr/bin/env bash
# Manual check for the coordinator, run by hand in the test account where hub
# is installed and the coordinator is on. It starts two real chats on a private
# tmux server (never the default one), HUB as the coordinator and WEB as a topic
# chat, asks WEB a question that needs the user, sends HUB two quick messages,
# and prints what each pane shows. Look for the question arriving in HUB and
# one wake for the two messages.
#
# Usage: tests/manual/two-chats.sh [wait_seconds]
set -eu

socket="zztest"
wait_seconds="${1:-45}"
workdir="$(mktemp -d)"
tmux_private() { tmux -L "$socket" "$@"; }

cleanup() {
  tmux_private kill-server 2>/dev/null || true
  rm -rf "$workdir"
}
trap cleanup EXIT

show_pane() {
  echo "----- $1 -----"
  tmux_private capture-pane -p -t "$1" | sed -e 's/[[:space:]]*$//' | tail -n 40
}

if tmux_private has-session 2>/dev/null; then
  echo "A tmux server named $socket is already running. Stop it first." >&2
  exit 1
fi

tmux_private new-session -d -s chats -n HUB -x 160 -y 50 -c "$workdir" "claude --name HUB"
tmux_private new-window -t chats -n WEB -c "$workdir" "claude --name WEB"
sleep 15

tmux_private send-keys -t chats:WEB \
  "You are the topic chat WEB. The cart code can round totals up or down and the spec does not say which. The user has to decide. Ask." Enter
sleep "$wait_seconds"

tmux_private send-keys -t chats:HUB "Message one for WEB: say hello." Enter
tmux_private send-keys -t chats:HUB "Message two for WEB: say goodbye." Enter
sleep "$wait_seconds"

show_pane chats:HUB
show_pane chats:WEB
