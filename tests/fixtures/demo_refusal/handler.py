"""The made-up gate the tests add: it refuses a shell command that holds REFUSE_ME, and lets every other call run."""
WORD = "REFUSE_ME"
REASON = "demo_refusal stops a command that holds REFUSE_ME."


def handle(event, hook_input, flags, answer):
    command = (hook_input.get("tool_input") or {}).get("command") or ""
    if WORD in command:
        answer.refuse(REASON)
        answer.signal("demo_refusal", "blocked")
