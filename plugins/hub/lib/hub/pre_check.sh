#!/bin/sh
# Starts the dispatcher only when a part of this hook group may run. The
# three PreToolUse groups, the two PostToolUse groups, PostToolUseFailure,
# UserPromptSubmit, Stop and SubagentStop use it.
# Apply leaves a marker in the state folder while a part runs:
# tool_parts/<tool> for a PreToolUse part that names the tool,
# every_tool_parts for a PreToolUse part that handles every tool,
# post_tool_parts/<tool> for a PostToolUse part that names the tool,
# post_tool_use_every_parts for a PostToolUse part that handles every tool
# (both PostToolUse groups check it), and post_tool_use_failure_parts,
# user_prompt_parts, stop_parts and subagent_stop_parts for the others.
# It writes markers_done after every marker and after resolved.json. While
# that mark is missing, unreadable or older than resolved.json, no marker is
# trusted and the dispatcher starts. So does a tool name this check can not
# read for sure. UserPromptSubmit also starts it while a notice waits for a
# chat, since the dispatcher hands those over whichever parts run. The
# dispatcher also starts when the settings changed after the last apply, or
# when the repo has its own hub settings, since it must resolve them first,
# and while the settings folder still has its old name, so the move happens.
# A state folder under its old name has no markers_done under the new one,
# so the dispatcher starts and moves it too.
# The coordinator is on by default, but it has nothing to do while no chat
# has its name. So a marker that names only the coordinator starts the
# dispatcher only while a chat in coordinator_chats may run: one whose pid
# runs as this user. A mark with no pid counts as gone, and kill -0 fails on
# another user's pid, which only a reused pid can be. On UserPromptSubmit a
# hook input with a session_title starts it too, so a /rename in a running
# chat is seen. The coordinator's handler writes and prunes
# coordinator_chats.
# In every other case the hook ends at once with no answer. The dispatcher
# gets only the event name.
config_name=  # the settings and state folders' name, from the one file that holds it
read -r config_name 2>/dev/null < "${0%/*}/config_name" || :
[ -n "$config_name" ] || exec python3 -I -S "${0%/*}/dispatch.py" "$1"
state="${XDG_STATE_HOME:-$HOME/.local/state}/$config_name"
config="${XDG_CONFIG_HOME:-$HOME/.config}/$config_name"
old_config="${XDG_CONFIG_HOME:-$HOME/.config}/hubctl"
event=$1
piped=
dispatch() {
  if [ -n "$piped" ]; then  # this check read the hook input, so it passes it on
    printf '%s\n' "$input" | python3 -I -S "${0%/*}/dispatch.py" "$event"
    exit $?
  fi
  exec python3 -I -S "${0%/*}/dispatch.py" "$event"
}

asleep() {  # whether the marker $1 names only the coordinator, and no chat with its name may run
  [ -f "$1" ] && [ -r "$1" ] || return 1
  first= second=
  { read -r first; read -r second; } < "$1" || :
  [ "$first" = coordinator ] && [ -z "$second" ] || return 1
  for chat in "$state/coordinator_chats"/*; do
    [ -e "$chat" ] || break
    pid=
    read -r pid < "$chat" 2>/dev/null || :
    case $pid in ''|*[!0-9]*) continue ;; esac
    kill -0 "$pid" 2>/dev/null && return 1
  done
  return 0
}

named_tool() {  # starts the dispatcher unless the call's tool surely has no marker in the folder $1
  tools="$state/$1"
  { [ -d "$tools" ] && [ -r "$tools" ] && [ -x "$tools" ]; } || dispatch
  input=$(cat)
  piped=1
  rest=${input#*'"tool_name"'}
  [ "$rest" = "$input" ] && dispatch
  case $rest in *'"tool_name"'*) dispatch ;; esac
  gap=${rest%%'"'*}
  [ "$gap" = "$rest" ] && dispatch
  case $gap in *:*) ;; *) dispatch ;; esac
  case $gap in *[!\ :]*) dispatch ;; esac
  rest=${rest#*'"'}
  tool=${rest%%'"'*}
  case $tool in ''|*[!A-Za-z0-9_-]*) dispatch ;; esac
  if [ -e "$tools/$tool" ]; then
    asleep "$tools/$tool" || dispatch
  fi
}

done_mark="$state/markers_done"
[ -f "$done_mark" ] && [ -r "$done_mark" ] || dispatch
[ "$done_mark" -ot "$state/resolved.json" ] && dispatch
case "$1" in
  PreToolUse)
    marker=every_tool_parts
    [ -e "$state/$marker" ] && dispatch
    named_tool tool_parts ;;
  PostToolUse)
    marker=post_tool_use_every_parts
    if [ "$2" != every ]; then
      [ -e "$state/$marker" ] && dispatch
      named_tool post_tool_parts
    fi ;;
  PostToolUseFailure) marker=post_tool_use_failure_parts ;;
  UserPromptSubmit)
    marker=user_prompt_parts
    for notice in "$state"/chat_notices/*.jsonl; do
      [ -e "$notice" ] && dispatch
      break
    done ;;
  Stop) marker=stop_parts ;;
  SubagentStop) marker=subagent_stop_parts ;;
  *) dispatch ;;
esac
if [ -e "$state/$marker" ]; then
  asleep "$state/$marker" || dispatch
  if [ "$1" = UserPromptSubmit ]; then
    input=$(cat)
    piped=1
    case $input in *'"session_title"'*) dispatch ;; esac
  fi
fi
[ -e "$state/resolved.json" ] || dispatch
if [ -d "$old_config" ] && [ ! -L "$old_config" ] && [ ! -e "$config" ] && [ ! -L "$config" ]; then
  dispatch
fi
for settings in "$config/hub.json" "$config/profile.json"; do
  [ "$settings" -nt "$state/resolved.json" ] && dispatch
done
folder=$PWD
while [ -n "$folder" ]; do
  if [ -e "$folder/.git" ]; then
    if [ -e "$folder/.hub/hub.json" ] || [ -e "$folder/.hub/hub.local.json" ]; then
      dispatch
    fi
    exit 0
  fi
  folder=${folder%/*}
done
exit 0
