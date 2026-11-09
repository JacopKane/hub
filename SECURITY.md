# Security

## Report a problem

Please report a security problem in private, with the "Report a vulnerability" button on this repo's Security tab on GitHub. Do not open a public issue for it. Say what you saw, how to repeat it, and which hub and Claude Code versions you use.

## What hub reads, writes and sends

hub runs on your machine, in the hooks that Claude Code and Cursor run for each session and tool call. It sends no telemetry and opens no network connection of its own.

- hub reads its own settings and the hook input for each tool call. At setup it also scans the machine for its name and system, tool versions, the terminal, the sign-in method and the time zone. It also notes which sync apps (iCloud Drive, Dropbox, Syncthing, Google Drive) have a folder in your home, whether git has a user name, whether the current repo has a remote, CI or compose files, and the names of your own rules, agents and MCP servers. It keeps a short hash of the account, never the email.
- hub writes its settings to `~/.config/hubctl/` and its state to `~/.local/state/hubctl/`, or under `XDG_CONFIG_HOME` and `XDG_STATE_HOME` when they are set. Parts you switch on add links under `~/.claude/` and keys in `~/.claude/settings.json`. hub records the old value of each key and puts it back when you uninstall.
- hub sends nothing by itself. Its start text and setup questions go into your chat, so your model provider sees them like any other chat text. At setup, the hub-setup skill reads your own CLAUDE.md and rules files and passes their contents, with the scan, to the tuner agent. The tuner uses them to find which of hub's instructions your rules already hold. Later, when the tuner picks which of hub's suggested changes to make, it gets only that list and its instructions. Both steps run as agents in Claude Code. Parts that pass messages between chats use local sockets.

## Design limits

hub's checks stop a model from lowering hub's protections by accident, or to unblock itself. They are not a sandbox.

The checks run on your machine, in the hooks every tool call passes through. hub refuses a tool call that would switch off a gate, a guard or a privacy check, change a value you pinned, uninstall hub, or write to hub's settings and state files. The model is told to ask you instead. Only you can lower a protection, with the /hub command in Claude Code and a single-use ticket.

A model with a shell can still get around local checks on purpose. So hub also keeps every change in view. Each change hub makes to its flags, whether you, setup or the tuner asked for it, shows in one line with an undo.
