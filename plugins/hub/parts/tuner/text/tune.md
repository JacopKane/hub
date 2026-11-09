hub's tuner found changes it may make to hub's flags. Handle them before the user's task, and do not ask the user about them.

1. Start the tuner agent with the list below.
2. Save its JSON answer to a file and run `{hubctl} tuner decide --answer <file>`.
3. Say nothing to the user about it. hub shows them each change in one line, with an undo.

The list:
{candidates}
