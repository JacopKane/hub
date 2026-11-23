# Self-protection limits

Self-protection stops Claude from lowering hub's own protections in the commands it writes. It reads the words in a chat and the files that a command names. It is not a sandbox, and it does not watch everything a program does on your machine. This page names the places where its checks end, so you know what they cover.

## How hub reads your order

A protection is lowered only when you ask for it in your own words in the chat, or with /hub where Claude Code loads plugin mods.

- Your order is the newest message you typed. It ends when you type again, or when a message from an agent or another chat arrives after it.
- hub reads a request loosely. "Try to turn off the public text gate" counts as an order to turn it off.
- Your words lower a protection only while the lowering check is on. While it is off, an order in words, such as "uninstall hub", changes nothing, and /hub still works.

## Words that only look typed

hub counts a line as yours when it reads like a line a person typed in the chat. Some lines look typed without being typed by you. Some programs also read a script from their input, and hub reads those scripts only while the lowering check is on.

## Writes hub does not see

hub checks the files that a command names. A write to a file the command does not name is outside its checks.

Self-protection reads the chat and the commands Claude writes. Anything else on your machine that can change those files is outside its checks. It is not a sandbox.

## Cursor

Cursor support is in beta, and hub has not checked the input that Cursor's hooks send.
