---
name: hub-restart
description: Restart the running Claude Code chats, with an optional Claude Code upgrade in between, and the calling chat last. Each chat is asked first and stopped only when it is idle with nothing to lose. It comes back in its own tmux pane, iTerm tab or the background, under its own name and flags. Chats that are not safe are held and named in one report. Use for "restart all chats", "restart everything", "upgrade claude and restart", "restart WEB", and when another chat asks whether it may be restarted.
---

# Restart the chats

The script is `<part>/restart.py`, where `<part>` is the folder above this skill's own folder. Run it with `python3`. It never stops a chat that has something to lose. A chat with an open question, a dialog, typed text in its box, background work it did not release, or a changed tab is held, left as it was, and named in the report with its reason.

## When you run the restart

1. Plan first. Planning only reads and types nothing. Run `python3 <part>/restart.py plan --caller <your session id>`. Add `--only A,B` or `--skip C` when the user named chats, and `--upgrade` when they asked for a Claude Code upgrade. Show the user the plan in a few lines. A chat in a plain terminal, or one hub has no record of, will be held.
2. Ask each chat in the plan (not yourself) with SendMessage. Start the message with `[urgent]`, so a message queue sends it at once: "[urgent] May I restart you? When you are at a safe point, run `python3 <part>/restart.py ready`. If you can not stop now, run `python3 <part>/restart.py hold <why>`. If a shell or agent you started may stop, add `--jobs-may-stop` to ready."
3. Wait until each chat has answered, or a few minutes have passed, then read the plan again to see the answers. Start the run within 15 minutes of the answers. An answer expires after that, and each run uses up the answers it read, so ask again before the next run.
4. Start the run with `python3 <part>/restart.py run --caller <your session id>`, with the same `--only`, `--skip` and `--upgrade`. It runs on its own and prints where its log and report go. You are restarted last, and a prompt then tells you to read the report.
5. After you come back, read the report. Tell the user in a few lines which chats are back, which were held and why, and the version change. A chat marked NOT BACK has the command that resumes it by hand.

With `restart.ask_first` off, a chat that is idle with nothing to lose restarts without an answer. With `restart.upgrade` off, `--upgrade` is refused, until the user turns `restart.upgrade` on again.

## When you are asked whether you may be restarted

Finish or park your current step first. Save what you would need to carry on, such as notes on open steps. Then answer once:

- `python3 <part>/restart.py ready` when nothing would be lost. Add `--jobs-may-stop` only when a shell, agent or watcher you started may stop with you.
- `python3 <part>/restart.py hold <why>` when you must not stop now, such as "the tests still run".

Then stay idle and type nothing until the restart reaches you. After the restart, a prompt names the file that holds your screen from before the stop. Read it, start again any waiter or watcher you had, and carry on.
