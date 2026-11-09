# AI-text tells and how to fix them

Read this when a passage still "sounds like a model" after the structural rewrite, or when the user specifically asks to remove AI-sounding language.

Each entry: what the tell is, why it happens, and a before/after pair. The fixes are illustrations, not templates; the goal is to make the text sound like a specific person wrote it for a specific reader.

## Contents

1. Openers and closers
2. Hedge stacks
3. Elevated filler vocabulary
4. Reflexive triads and forced parallelism
5. Equal-weight enumeration
6. Summary-of-the-summary
7. Rhetorical transitions
8. Over-definition
9. Punctuation as glue
10. Formatting as substitute for structure
11. Uniform rhythm
12. Abstract where concrete would land

---

## 1. Openers and closers

**Tell:** "Certainly!", "Great question!", "Absolutely.", "In conclusion,", "Overall,", "I hope this helps!", "Let me know if you need anything else."

**Why:** Conversational scaffolding from chat training. It signals a reply, not a piece of writing.

Before: *Certainly! Here's an overview of the key considerations for the Q3 budget.*
After: *The Q3 budget has one problem and it's headcount.*

## 2. Hedge stacks

**Tell:** "It's important to note that", "It's worth mentioning", "Generally speaking", "In many cases", "depending on your specific situation", "may or may not".

**Why:** The model covers every reading of an ambiguous prompt. A human writer knows their reader and commits.

Before: *It's worth noting that, in many cases, caching may improve performance depending on the access pattern.*
After: *Caching will help here because the same 200 records account for most reads.*

Keep a hedge only when the reader would act wrongly without it.

## 3. Elevated filler vocabulary

**Tell:** delve, leverage, robust, crucial, seamless, landscape, navigate, tapestry, foster, harness, unlock, streamline, empower, holistic, cutting-edge, in today's fast-paced world, at the end of the day, a testament to.

**Why:** Statistically safe words that sound authoritative and mean little.

Before: *We'll leverage a robust framework to seamlessly navigate the evolving landscape.*
After: *We'll use the same checklist the ops team already follows.*

Rule of thumb: if a word could be deleted with no loss, delete it; if it could be replaced by a plainer word, replace it.

## 4. Reflexive triads and forced parallelism

**Tell:** Three adjectives, three examples, three bullets, every time. Sentences that mirror each other's structure across a whole section.

**Why:** Three feels complete; parallel structure is a strong pattern. Humans notice the drumbeat.

Before: *This approach is fast, reliable, and scalable. It reduces cost, increases speed, and improves quality.*
After: *It's fast, and it doesn't fall over when traffic doubles.*

Vary the count. Two is often enough. Four with one odd item out sounds human.

## 5. Equal-weight enumeration

**Tell:** "There are several factors: First... Second... Third... Additionally... Finally..." with no ranking.

**Why:** The model lists everything true; it doesn't have a reader in mind who needs to know what matters most.

Before: *Several factors affect latency. First, network distance. Second, database indexing. Third, serialization format. Additionally, connection pooling.*
After: *Latency is almost entirely the missing index on `orders.customer_id`. Fix that and the network and serialization costs stop mattering.*

Decide what matters, say it, subordinate the rest.

## 6. Summary-of-the-summary

**Tell:** A closing paragraph that restates each section; an intro that previews each section; a final sentence on each paragraph that recaps it.

**Why:** Redundancy is cheap for a model reader and emphasizes for a model. For a human it reads as padding.

Fix: keep one restatement of the single main point, phrased differently from the opening. Cut all other recaps.

## 7. Rhetorical transitions

**Tell:** "So what does this mean for you?", "But here's the thing:", "Why does this matter?", "Let's break this down."

**Why:** Engagement patterns from explainer content.

Fix: replace with the actual transition. Before: *So what does this mean? It means...* After: *In practice,* or just start the next sentence.

## 8. Over-definition

**Tell:** Defining terms the reader obviously knows; restating the goal or constraints the reader gave; "as mentioned above".

**Why:** Writing *for* a model rewards explicitness. Writing for a person can trust shared context.

Before: *An API, or application programming interface, allows software systems to communicate. Your API, as described, returns JSON.*
After: *Your API returns JSON, so...*

## 9. Punctuation as glue

**Tell:** Colons introducing every explanation; semicolons chaining clauses that should be separate sentences.

**Why:** These let a model attach clauses without committing to a logical relationship.

Before: *The build failed; the config was wrong: the deploy didn't run.*
After: *The build failed because the config was wrong, so the deploy never ran.*

Name the relationship (because, so, but, then) instead of letting the punctuation imply one.

<!-- hub:section humanize.em_dash=warn -->
Em dashes in most sentences are the same tell. Leave each one in place and list it at the end with an ordinary sentence that could replace it, as the skill's em dash section says.
<!-- hub:end -->

<!-- hub:section humanize.em_dash=rewrite -->
Em dashes in most sentences are the same tell, with the same fix. Before, with each long dash written here as a hyphen: *The build failed - the config was wrong - which meant the deploy didn't run.* After: *The build failed because the config was wrong, so the deploy never ran.*
<!-- hub:end -->

## 10. Formatting as substitute for structure

**Tell:** Headers over three-sentence sections; bullets for a connected argument; bold on phrases that aren't more important than their neighbors; tables for two data points.

**Why:** Structure disambiguates for a model. For a human, over-formatting fragments the reading.

Fix: bullets for list-shaped content only (steps, options, items). Headers when a reader would want to skip to a section. Bold for at most one thing per screen. Prose for reasoning.

## 11. Uniform rhythm

**Tell:** Every paragraph the same length; every sentence 18 to 25 words; no short sentences.

**Why:** Averaging behavior.

Fix: after the rewrite, read for rhythm. Add a short sentence where the point lands. Let one paragraph be a single line if it's the pivot.

## 12. Abstract where concrete would land

**Tell:** "Various stakeholders", "a range of use cases", "significant improvements", "certain challenges".

**Why:** The model didn't have the specific, or hedged to cover all specifics.

Before: *This led to significant improvements across a range of metrics.*
After: *Checkout time dropped from 9 seconds to 3.*

If the specific isn't in the source, don't invent it. Either ask, or make the abstraction honest ("we don't have the numbers yet").
