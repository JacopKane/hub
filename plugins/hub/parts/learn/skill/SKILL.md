---
name: hub-learn
description: Turn a correction or a new preference from the user into one lasting instruction in the right place (a rules file, a hook, a skill, an agent file or the project memory folder), so they never have to say it twice, then apply it to the work in hand. Use it when the user says "remember this", "instruct yourself", "update your instructions", "add this to your instructions", "so this never happens again", or corrects the same mistake a second time. It checks who can read a file before it writes there.
argument-hint: "[the rule, in the user's words]"
---

# Learn

This skill makes a correction stick, so the user never has to say it twice. A correction usually comes back for one of two reasons. A rule already existed and was not applied, or it was written for the one case instead of the rule behind it.

Read `references/batch.md` when several learnings are collected at once, for example from threads, past chats or a review.

## Steps

1. **Pin the rule.** Quote the user's exact words with the date, and name the incident in one line. Then write the general rule behind it, not the one case. "A reviewer who knows the code outranks an automated review" holds for every reviewer. "Trust this one reviewer over the bot" holds for one.

2. **Pick the mechanism before writing.** Take the first that fits.
<!-- hub:section learn.instructions.hooks -->
   - If a script can check it, write a hook with a test next to it, wired in `~/.claude/settings.json`. Write the failing case first. Let a near miss pass, and test both the near miss that passes and the clear miss that is refused.
<!-- hub:end -->
   - If it matters only for some files, write a rule file in `~/.claude/rules/<topic>.md` with a `paths:` list of globs.
   - If it is a procedure the user or a task starts, write a skill in `~/.claude/skills/`.
   - If it is about who does the work (model, tools, standing brief), change an agent file in `~/.claude/agents/`.
   - Otherwise, write one short line in a rules file, placed by step 3.
<!-- hub:section learn.instructions.hooks -->
   - If the same correction came back a few times, the text did not hold. Ask the user with the question tool whether to make it a hook.
<!-- hub:end -->

3. **Pick the one right place** for text.
   - `~/.claude/CLAUDE.md` holds how the user works in every project. It is the default home for a personal rule.
   - `~/.claude/rules/<topic>.md` holds a standing rule set on one topic. Without `paths:`, every chat and every agent loads it.
   - The project memory folder (`~/.claude/projects/<project>/memory/`) holds setup facts, ids and tool quirks only.
<!-- hub:section learn.instructions.ledger_and_memory -->
   - The coordinator's ledger holds live, dated state and open items. The coordinator chat's start notice gives its path. Dated state never goes into a rules file or the memory folder.
<!-- hub:end -->
   - A skill in `~/.claude/skills/` is only for a procedure the user starts on purpose.
   - The repo's `CLAUDE.md`, `AGENTS.md`, `.claude/` and `.cursor/rules/` belong to everyone who pulls the repo. A personal rule never goes there. Write there only when the user asks for a team rule, and only after the zone check in step 5.

4. **Search before writing.** Search the user's CLAUDE.md, rules files and memory folder for the topic and its synonyms. If a rule already exists, sharpen it instead of adding a second one, so there is one source of truth. If the miss happened even though the rule existed, add a concrete check that would have caught it, for example "before sending, scan the draft for bare ticket ids".

5. **Check who can read the file.** A rule is sync content. It may follow the user to their own devices, but no further. Before each write, run `{{step}} zone <path> --for sync` and read the first word of its answer.
   - If the first word is `ask`, write nothing. Ask the user the zone question it printed, word for word, with the question tool. If they keep it local, write the rule to the place it offers, or to the user's own rules file. If they pick Share it, wait for their own typed "share <folder>" before you write.
   - Any other first word means the file may go there. Write it.

   hub also checks this itself. Once this skill has run in a chat, hub refuses a write to a rules file whose folder reaches further, until the user answers.

6. **Write in the existing format.**
   - In a rules file, match its voice and headings. Read the whole section first and keep its structure. Write one short imperative line that states the rule, then the user's words in quotes, so a later reader knows where it came from. Leave out ticket ids and the story of the incident.
   - In a memory file, use the front matter the other files in the folder use, then the user's quote, `**Why:**` and `**How to apply:**`. Add one index line to the folder's `MEMORY.md`, in the same format as the lines around it.
   - Never soften or delete one of the user's rules to fit the new one. If two rules conflict, show both and ask which wins.
<!-- hub:section learn.instructions.editor_copies -->
   - If the changed file has a Cursor copy, such as a rule in `.cursor/rules/` or an `AGENTS.md` both tools read, change it in the same turn, after the same zone check.
<!-- hub:end -->

7. **Apply it now.** Redo the thing the user corrected in the same turn (fix the link, run the check again, read the thread again). Tell running agents the new rule if it touches their work.

## Report

Two or three lines:
- which file changed, with the exact new or changed lines in a code block;
- what you redid under the new rule.

No reasoning story. The user reads the diff.
