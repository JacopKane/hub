---
name: hub-humanize
description: Rewrite model-written text (and its diagrams) so a person can read it once and remember it. Use it when someone asks to make text more readable, more human, less robotic or less "AI-sounding", or to clean up, tighten or polish output from any assistant, even when they never say "AI". Also use it on any text other people will read before it is shown for a yes, committed or published, such as commit messages, PR titles and bodies, review comments, code comments, specs, docs, UI strings and messages.
---

# Humanize writing

Turn text that a model wrote, which often reads as if it were written for a model, into text that a person can read once, from top to bottom, and remember.

## The core moves

Work through these roughly in order. Not every text needs every move.

<!-- hub:section humanize.instructions.lead_with_point -->
### Find the point, then lead with it

Ask which one sentence the reader should remember. Put it first, in plain language. Model text often hides the answer under a warm-up ("Great question. There are several factors to consider...") or spreads it evenly across five equal sections. A reader builds their view of the text from its opening, so the opening has to carry the main point.

Delete the warm-up: thanks for the question, remarks that the topic is complex or important, and promises about what the text will cover.
<!-- hub:end -->

<!-- hub:section humanize.instructions.spine -->
### Give it a spine

People read by building a running story, so the text should go somewhere. Reorder it so each paragraph follows from the last (because of X, Y, which means Z). Replace a list of equal, unranked points with an order that has a reason: most important first, cause before effect, general before specific, or problem then solution.

If the source is a flat list of six items with equal weight, decide which two or three matter and fold in or cut the rest. Equal weight on everything is a model habit. A person needs to know what comes first.
<!-- hub:end -->

<!-- hub:section humanize.instructions.chunks -->
### Chunk for working memory

A reader holds about a sentence or two word for word. Keep each sentence to one idea. Break a paragraph where the idea changes, and aim for paragraphs a reader can take in as one unit, roughly three to six ordinary sentences. Put a break (a paragraph, a short heading, white space) wherever the reader would pause to sum up what they just read.

Use structure to make reading easier, not to show it off. Headings and bullets help when they let the reader skip or scan. They hurt when they cut up an argument that should flow. Turn a bullet list back into prose when its items are one line of reasoning, and keep bullets for things that really are lists, such as steps, options or a checklist.
<!-- hub:end -->

<!-- hub:section humanize.instructions.cut_repeats -->
### Cut repeats that do not help memory

Repeating helps a reader when it says a key idea again in new words later on, so it settles. It hurts when a summary repeats the intro, a closing paragraph recaps every section, or the same qualifier sits on every sentence. Keep one restatement of the main point, usually at the end and in different words, and cut the rest.
<!-- hub:end -->

<!-- hub:section humanize.instructions.front_load -->
### Plan for a tired reader

A model reads page 40 as well as page 1. A person does not. Put what matters first and make the end lighter: supporting detail, edge cases and caveats go later or into an appendix. Make the whole text shorter. Most model text can lose 20 to 40 percent with no loss of meaning.
<!-- hub:end -->

<!-- hub:section humanize.instructions.trust_context -->
### Trust what the reader knows

Writing for a model rewards saying everything: every term defined, every limit stated. Writing for a person can trust shared context. Remove definitions of terms the reader knows, drop assumptions the reader gave you, and cut hedges that try to cover every case ("in many situations, depending on context, it may be the case that..."). Say the thing. Keep a caveat only when the reader would be misled without it.
<!-- hub:end -->

<!-- hub:section humanize.instructions.surface_tells -->
### Fix the surface tells

Readers notice these as machine-written even when the content is fine. Cut or replace:

- Stock openers and closers ("Certainly!", "In conclusion,", "I hope this helps")
- Filler intensifiers and vague grand verbs ("delve", "leverage", "robust", "crucial", "seamless", "landscape", "navigate", "tapestry")
- A set of three every time (three adjectives, three examples), so vary the count
- Stacked hedges ("It's important to note that", "generally speaking", "in most cases")
- The same section shape used where the content does not have that shape
- Paragraphs of equal length, headings on short passages, bold on every other phrase
- Colons as glue between a label and its explanation, where an ordinary sentence works
- Rhetorical questions used to move on ("So what does this mean?")
- A small summary sentence at the end of every paragraph

See `references/tells-and-fixes.md` for a longer list with before and after pairs.
<!-- hub:end -->

<!-- hub:section humanize.em_dash=warn -->
### Em dashes

Leave each em dash (the long dash) where it is, and list it in one line at the end with an ordinary sentence that could replace it. The writer decides.
<!-- hub:end -->

<!-- hub:section humanize.em_dash=rewrite -->
### Em dashes

Rewrite every em dash (the long dash) as an ordinary sentence. Name the link between the two parts with a word such as because, so, but or then, or use a comma or a full stop.
<!-- hub:end -->

<!-- hub:section humanize.instructions.concrete -->
### Make it stick

People remember concrete things better than abstract ones. Where the source makes a general claim, add or bring back one specific: a number, an example, a named case or a comparison. One good example beats three vague ones. A short, memorable phrase for the main idea helps the reader carry it away.
<!-- hub:end -->

<!-- hub:section humanize.instructions.plain_words -->
### Use plain words

Many readers have English as a second language. Write in plain words, with short sentences, common words and no idioms. Keep a term of art when the reader needs it, and explain a new one once in plain words.
<!-- hub:end -->

<!-- hub:section humanize.instructions.diagrams -->
### Diagrams

When the text holds a diagram or diagram source, a person needs the rendered picture, not the source. Read `references/diagrams.md` for the rules.
<!-- hub:end -->

## Reference files

- Read `references/why-the-rewrite.md` when you need the reason a model's text fails a person.
- Read `references/worked-example.md` when you want a full before and after.

## Output format

Return the rewritten text directly. Keep the original's kind and tone (an email stays an email, a slide stays short, a technical memo stays technical) unless asked otherwise. Keep every fact, figure and promise in the source. The rewrite changes shape and emphasis, not substance. If something in the source is unclear and the rewrite would have to guess its meaning, keep it neutral and name it in one line at the end.

Do not explain what changed unless asked. If asked, give a note of two or three lines, not a list of every edit.

For a short passage of less than a screen, reply inline. For a long document, or anything the user will paste somewhere else, write a file (markdown unless they ask for another format).

<!-- hub:section humanize.instructions.self_check -->
## Quick check before you return it

- Does the first sentence carry the main point?
- Could a reader stop after the first paragraph and still know what matters?
- Is there a reason each paragraph follows the one before it?
- Do any two paragraphs say the same thing?
- Is anything defined, hedged or explained that the reader already knows?
- Read it aloud in your head. Does any sentence sound like a form letter?
- Is it shorter than the source?
- In text for a shared place (a wiki page, a PR, a chat channel), does it read as the author's own words? Remove "written by AI" notes, advice about how agents should behave, and any team position the author did not state.
<!-- hub:end -->
