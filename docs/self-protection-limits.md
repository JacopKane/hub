# Self-protection limits

Self-protection stops Claude from lowering hub's own protections, which live in hub's settings and state files. This page names the places where its checks end, so you know what they cover.

## How hub reads your order

A protection is lowered only when you ask for it in your own words in the chat, or with /hub where Claude Code loads plugin mods.

- Your order is the newest message you typed. It ends when you type again, or when a message from an agent or another chat arrives after it.
- hub reads a request loosely. "Try to turn off the public text gate" counts as an order to turn it off.
- Your words lower a protection only while the lowering check is on. While it is off, an order in words, such as "uninstall hub", changes nothing, and /hub still works.

## What it does not watch

Self-protection reads the chat and the commands Claude writes. Anything else on your machine that can change those files is outside its checks. It is not a sandbox.

## Cursor

Cursor support is in beta.
