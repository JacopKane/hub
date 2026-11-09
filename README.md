# hub

[![CI](https://github.com/JacopKane/hub/actions/workflows/ci.yml/badge.svg)](https://github.com/JacopKane/hub/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Every part of your Claude Code setup gets its own switch. A gate stops private text before it goes public.

hub is a plugin for Claude Code. It puts the parts of a Claude Code setup in one settings file and gives each part its own switch. The parts include the public text gate, agents for different kinds of work, writing rules, a status band above the prompt and a coordinator chat. The coordinator is one chat that passes your request to the chats that own each topic and reports back in one place.

![The public text gate stops a commit message that holds a home path. Claude rewrites it and commits. Then Claude tries to switch the gate off, and hub refuses. Only you can lower a protection.](docs/media/gate-stops-a-leak.gif)

hub is an early work in progress and is not released yet. Expect changes that break things.

## What works today and what is planned

- Working today are the settings file and its switches, a first-run setup, self-protection, the public text gate, the status band, agent tiers, writing rules, and the coordinator chat with a chat board.
- Cursor support is in beta and untested. It works only from a checkout of this repo, not from the plugin install.
- Still planned are a full view of every flag, a sandbox command and a first release.

## Install

You need Claude Code and Python 3, on macOS or Linux. hub is tested with Claude Code 2.1.319. On other versions, `/hub doctor` shows a warning, and the message queue sends each message at once instead of holding it.

```
claude plugin marketplace add JacopKane/hub
claude plugin install hub@hub
```

Then start a new chat. hub offers its setup there. A small Haiku agent reads your setup, asks a few questions in one dialog, and turns on a small starting set. Type `/hub` to see what it turned on. To remove hub, run `hubctl uninstall` in a terminal.

## Switch parts on and off

![hubctl show lists the parts. One word turns a part on or off, and undo takes it back. Inside a chat the same commands are /hub on, /hub off and /hub undo.](docs/media/switch-parts.gif)

Type these at the Claude Code prompt:

```
/hub                    list the parts and their flags
/hub on humanize        switch a part on
/hub off status_band    switch a part off
/hub undo               take back the last change
/hub doctor             check the setup
```

Each change shows in one line with an undo. hub keeps your choices in `~/.config/hubctl/hub.json`. Only you can lower a protection, from the Claude Code prompt. A command the model runs can raise a protection but never lower it.

Known limits:

- `/hub` and the status band come from a plugin mod, so they need a Claude Code build that loads mods. Without one, type `/hub:hubctl` instead, or use `hubctl` in a terminal.
- The gate refuses a home or temp folder path on every try. A pasted build log with `/tmp/` paths has to come out before the commit goes through.
- Restarting chats from hub works only in iTerm2 or tmux.

## License

MIT. See [LICENSE](LICENSE).
