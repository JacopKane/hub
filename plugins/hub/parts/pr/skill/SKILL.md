---
name: hub-pr
description: How to open and update pull requests in any repo the way the repo's own guide asks. Use it when the user asks to open, prepare, split or update a PR, to write or check a PR body, or to push a branch for review.
---

# Pull requests

Read this skill before the first PR step of a task. The user's newest instruction wins over the repo's written rules, and the repo's rules win over the defaults below. The three rules in "Always" are the exception, and nothing changes them.

## Always

These three rules hold in every repo. No repo guide, no setting and no other skill changes them.

- Steps other people can see, such as a push, a new PR, a body edit, a comment, a review or marking a PR ready, wait for the user's order.
- Nobody is @-mentioned unless the user asks for that person by name.
- Work and questions never go to a colleague. Bring them to the user instead.

Gather the steps that notify people into one list, and show it under this line:

> These steps notify people, so none of them runs until you order it. One answer can order the whole list, and then people get one round of notifications.

When a repo's guide asks for one of those steps, such as a review request from its code owners, put the step on the list and tell the user:

> {source} asks for {step}, but that step still waits for your order, as every step that notifies people does.

<!-- hub:section pr.instructions.repo_guide -->
## Read the repo's guide first

1. Before the first PR step in a repo, read its contributing guide, its PR template (`pull_request_template.md` in the root, `.github/` or `docs/`, or a `PULL_REQUEST_TEMPLATE/` folder), CLAUDE.md and AGENTS.md, and the branching or release guide they link to. Read them again on each task, because they change.
2. Note what they ask for: the base branch, the title pattern (such as a ticket id), draft or ready, the body template, labels, checks to run before a push, feature flag rules and version raises.
3. Follow them. Where they ask for something else than a section of this skill, leave that section out and say so:

> {source} asks for something else, so hub left out {methods}.

When the repo has none of these files, say so once:

> This repo has no contributing guide, PR template or agent instructions, so hub's PR defaults apply.
<!-- hub:end -->

<!-- hub:section pr.instructions.texts_first -->
## Texts first

When a change adds or rewrites words that users read, put the texts and every translation in their own small PR, which merges before the logic. Its body names the feature that will use them, and the logic PR builds on it. This is the one exception to slicing by feature.
<!-- hub:end -->

<!-- hub:section pr.instructions.slice_by_feature -->
## One feature per PR

- A PR is one slice of a feature that a reviewer can read in one go. Avoid both one PR per fix and one PR for everything.
- Slice by feature, not by layer. A PR that adds an endpoint or another contract also shows its first real use, so the reviewer sees both sides.
- When a change would make a PR too large to review, propose where to split it, and wait for the user before you open anything.
- Put a change on the open PR it belongs to. A fix that answers a review goes on the reviewed PR. Renames, style changes and refactors that the change does not need go in their own small PR. A fix that helps users without the feature goes in its own small PR from a fresh base, with a test that does not need the feature.
- Ship an open PR as it is. Do not fold, split or grow a PR under review to make it tidier.
<!-- hub:end -->

<!-- hub:section pr.instructions.body -->
## The body

Use the repo's PR template when it has one. Otherwise use this shape:

```markdown
## What it is

{what}

### To check it

{check}

### How it was tested

{tested}

## What is decided

{decided}

## What is open

{open}

## Env changes

{env}
```

- "What it is" says what changed and why it matters. "To check it" gives the exact steps or commands a reviewer can run. "How it was tested" says what ran, and what was not tested and why.
- "What is open" names open questions, never people. A question that gets an answer leaves the body.
- With no env changes, the last part says only "No env changes."
- Describe the change, not how it came about. Leave out how the problem was found, the branch history, a red-then-green CI story, test counts CI already shows, and anything about your machine or tools. A check that was not done is reported as not done.
- Link every PR, issue, ticket, commit and doc the body names.
- A reader should finish it in 30 to 60 seconds. Diagrams and `<details>` blocks do not count. Read each sentence as the reviewer and drop the ones they would not miss.
- Publish settled work only. When a decision could still change, publish the question instead.
- When the body needs an update, write it whole again from the PR's current state, in the same shape.

Before you show the body to the user, save it to a file and run `{{step}} pr-check <file>`, or pass `-` to read it from stdin. It names each finding with its line. Fix each one and run it again. A length notice is not a failure, so ask the user before you trim.
<!-- hub:end -->

<!-- hub:section pr.instructions.test_gate -->
## Tested before every push

Every push, to a draft PR too, waits until these steps are done.

1. Write or change the scenarios before the code. A few fat behaviour scenarios that walk the real flow from end to end beat many small tests. Name the edge cases each one covers in its title, such as empty, huge or odd input or a failing service. Mock only the outermost seam, such as a paid or outside service.
2. Check that each new scenario fails when the code is wrong.
3. Run the scenarios, and the repo's smoke run when it has one. A failing or flaky scenario stops the push. Fix the code, or the scenario when it was wrong, and say which. Never skip, loosen or delete a scenario to make it pass.
4. Fill the body's "How it was tested" part, and add one when the repo's template has none. Name the scenarios and the smoke run that ran, the mocks left and why, and what was not tested and why.
<!-- hub:end -->

<!-- hub:section pr.instructions.writing_pass -->
## A writing pass

Run the user's writing skill on the title, the body and every commit message before you show them to the user, and again after each edit. That is hub-humanize when it is on, or another skill whose description says it edits text for people to read. With none installed, write short sentences in plain words.
<!-- hub:end -->

<!-- hub:section pr.instructions.drafts -->
## Draft or ready

<!-- hub:section pr.open_as=draft -->
Open new PRs as drafts, unless the repo's guide or the user asks for ready PRs.
<!-- hub:end -->
<!-- hub:section pr.open_as=ready -->
Open new PRs ready for review, unless the repo's guide or the user asks for drafts.
<!-- hub:end -->
Marking a PR ready, or back to draft, notifies people, so it waits for the user's order too.
<!-- hub:end -->

<!-- hub:section pr.instructions.env_changes -->
## Env changes

When a PR adds, renames or removes an env key, a secret, a flag default or an infrastructure setting, or needs a value set on a deployed environment, tell the user before it merges and again when it lands. Put it first in your report, with the key names (never values), the file and line, which environments need a value, and whether a default keeps it safe without one.
<!-- hub:end -->

<!-- hub:section pr.instructions.night_hold -->
## Night hold

Before you run the steps the user ordered, run `{{step}} pr-night`. It prints JSON with `held`, `line`, `time_zone` and `until`.

- When `held` is true, show `line` and run none of the steps. Run them with the user's first message after `until`, or at once when the user says "send it now".
- When steps waited and `held` is false, show `line`, read the PR's comments and reviews again, since they may have changed overnight, and then run the steps.
<!-- hub:end -->

<!-- hub:section pr.instructions.handoff -->
## Other PR skills

When another skill that is on does one PR step, such as a review, getting a PR ready or restacking stacked branches, load it for that step and keep this line in mind:

> The {skill} skill is on and does {job}, so load it for that step. hub's three PR rules still hold while it runs. Steps other people can see wait for the user's order, and for the morning while the night hold is on. Nobody is @-mentioned unless the user asks. Work and questions never go to a colleague.
<!-- hub:end -->

<!-- hub:section pr.instructions.retire -->
## Retire replaced work

When new work replaces a PR or a branch, close the old PR only on the user's order, with a link to the new one, and keep its branch, so the record stays.
<!-- hub:end -->
