# hub

hub is a plugin for Claude Code. It puts the parts of a Claude Code setup in one settings file and gives each part its own switch. The parts include checks that stop private text from going public, agents for different kinds of work, writing rules and a status band above the prompt.

hub is an early work in progress and is not released yet. Expect changes that break things.

## What works today and what is planned

- Working today are the settings file and its switches, a first-run setup, self-protection, the public text gate, the status band, agent tiers, writing rules, and a coordinator chat with a chat board.
- Cursor support is in beta and untested.
- Still planned are a full view of every flag, a sandbox command and a first release.

## Install

You need Claude Code and Python 3, on macOS or Linux.

```
claude plugin marketplace add JacopKane/hub
claude plugin install hub@hub
```

Then start a new chat. hub offers its setup there. It looks at your machine, asks a few questions in one dialog, and turns on a small starting set.

## Switch parts on and off

Type these at the Claude Code prompt:

```
/hub                    list the parts and their flags
/hub on humanize        switch a part on
/hub off status_band    switch a part off
/hub undo               take back the last change
/hub doctor             check the setup
```

Each change shows in one line with an undo. hub keeps your choices in `~/.config/hubctl/hub.json`. Only you can lower a protection, from the Claude Code prompt. A command the model runs can raise a protection but never lower it.

## License

MIT. See [LICENSE](LICENSE).
