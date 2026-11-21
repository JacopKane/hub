# Self-protection limits

Self-protection stops Claude from lowering hub's own protections in the commands it writes. It reads the words in a chat and the files that a command names. It is not a sandbox, and it does not watch everything a program does on your machine. This page names the places where its checks end, so you know what they cover. Where a fix is planned, the page says so.

## How hub reads your order

A protection is lowered only when you ask for it in your own words in the chat, or with /hub where Claude Code loads plugin mods.

- Your order is the newest message you typed. It ends when you type again, or when a message from an agent or another chat arrives after it.
- hub plans to keep your order through such messages until the end of its turn, until you interrupt Claude, or for 10 minutes, whichever comes first.
- hub reads a request loosely. "Try to turn off the public text gate" counts as an order to turn it off.
- Your words lower a protection only while the lowering check is on. While it is off, an order in words, such as "uninstall hub", changes nothing, and /hub still works. hub plans to make such orders work with the check off too.

## Words that only look typed

hub counts a line as yours when it reads like a line a person typed in the chat. Some lines look typed without being typed by you.

- A line written straight into a chat's saved history looks like a line you typed. hub reads what the chat shows, and it can not tell such a line from a typed one.
- Keys that a program types into a terminal look like your own typing. hub reads them only while the lowering check is on, and only for some programs it knows.
- A Claude run that Claude starts with a prompt writes that prompt into its own chat, and hub can not tell it from words you typed there. A check of such prompts is planned, but hub does not see every way a run can start.
- Some programs read a script from their input. hub reads those scripts only while the lowering check is on.

## Steps that another program starts

Claude can start hub's switch step through another program, and the step then runs without the marker that tells hub a tool call is running. While the lowering check is on, hub refuses such a step's change to a protected flag or to a value you set by hand. It reads a fixed set of these programs, not every program that can start a command. While the check is off, hub does not judge these steps. hub plans to refuse their change to a value you set by hand with the check off too.

## Writes hub does not see

- hub checks the files that a command names. A write to a file the command does not name is outside its checks.
- Inside a repo with its own hub files, a value in those files takes the place of your own setting, even one you pinned. In the repo's project file, and in a local file that git tracks, hub skips a value only if it would lower a privacy flag, such as one of self-protection's own checks. hub plans to skip every repo value that would change a value you pinned or weaken a protection.
- No check inside hub stops a program that changes hub's own code.

## Cursor

Cursor support is in beta, and hub has not checked the input that Cursor's hooks send. hub plans to count a line as your order only when it carries the marks of typed text. If Cursor does not write those marks, words will then not lower a protection in Cursor.
