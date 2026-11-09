# Made-up setup agent answers

The setup agent runs on a cheap model, which costs money, so the tests feed `hubctl setup --answer` these files instead. They are written by hand in the shape the agent's text asks for.

- `work_laptop.json` has two good questions and a third one whose only option sets a flag hub does not have, so setup drops it.
- `compare_rules.json` names one instruction the user's own CLAUDE.md already holds, and one id hub does not have.
- `nothing.json` sets up a machine with no questions and no changes.
- `tuner_picks_more.json` is the tuner model's answer. It picks the dash check, which the rule filter found, and two flags the filter did not pick, so hub makes only the first change.
