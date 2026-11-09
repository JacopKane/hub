hub is installed, and this is its first run on this machine. Run hub setup with the hub-setup skill before the user's task.

1. Start the tuner agent with the scan below and the user's own rules.
2. Save its JSON answer to a file and run `{hubctl} setup --answer <file>`. It prints the questions.
3. {how_to_ask}
4. Run `{hubctl} setup --reply '<json>'` with the answers. Key each answer by the id that --answer printed for its question, such as {"3f9a1c2e": "work"}.
5. Show the user the summary it prints.

The scan:
{scan}
