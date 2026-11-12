"""The task list shows each agent and background job a chat starts, with its state and cost so far."""

TEXTS = {
    "empty": "No tasks yet. Agents, background jobs, questions, waits and timers show up here when they start.",
    "empty_brief": "No tasks yet.",
    "estimate": "Costs are API-equivalent estimates, not a bill.",
    "none_done": "Nothing finished today.",
    "missing": "No task is called \"{name}\". /hub tasks lists the names.",
    "brief_needs_you": "Needs you {count}: {rows}",
    "brief_running": "Running {count}: {rows}",
    "brief_counts": "Running {running} · done today {done}",
    "brief_cost": "agents ~${usd} so far today",
    "brief_open": "needs you {needs_you} · waits on a chat {waits} · held {held}",
    "show_started": "{name}, started {time}",
    "show_cost": "~${usd} so far, API-equivalent estimate",
    "show_now": "its last tool call, {age} ago",
    "permission": "Allow {tool}: {what}",
    "show_waits_on": "Waits on",
    "group_needs_you": "NEEDS YOU",
    "group_running": "RUNNING",
    "group_waits": "WAITS ON A CHAT",
    "group_held": "HELD",
    "group_done": "DONE TODAY",
}
LABELS = ("Owner", "Call", "Model", "Now", "Cost", "Source")
OUTCOMES = ("ok", "failed", "stopped", "gone")
KINDS = ("agent", "job")
SOURCES = {"agent": "agent", "shell": "background shell", "monitor": "monitor", "question": "question",
           "permission": "permission prompt", "message": "message", "timer": "timer"}
