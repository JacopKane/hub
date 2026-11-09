"""tier_rule.guard: an agent call starts a tier agent or sets its model.

An Agent call that sets no model runs on the main chat's model, which is the
most expensive choice. The guard lets a call through when it sets a model, is
a fork, starts a hub agent that runs, starts one of the user's own agents (in
~/.claude/agents or the repo's .claude/agents), or names an agent in
tier_rule.guard.allowed. Any other call is refused, and the refusal names
each tier row that runs with its agents, model and effort.

While the guard is off, such a call only logs the signal untiered, so the
tuner can offer the guard. The handler runs in Claude Code only.
"""
import os

from hub import files, settings, tree as tree_module

GUARD = "tier_rule.guard"
ALLOWED = "tier_rule.guard.allowed"
TABLE = "tier_rule.table"
FORK = "fork"


def hub_agents(flags):
    """The ids of hub's agent parts that run."""
    try:
        names = os.listdir(files.PARTS_DIR)
    except OSError:
        return set()
    return {name for name in names
            if os.path.isfile(os.path.join(files.PARTS_DIR, name, "agent.md")) and flags.runs(name)}


def user_agents(cwd):
    from hub import links  # only needed when the name is not one of hub's
    found = set(links.user_agents())
    repo = settings.find_repo(cwd)
    if repo is not None:
        found |= set(links.user_agents(os.path.join(repo, ".claude", "agents")))
    return found


def tiered(tool_input, flags, cwd):
    if tool_input.get("model"):
        return True
    name = tool_input.get("subagent_type") or ""
    if name == FORK or name in hub_agents(flags) or name in (flags.value(ALLOWED) or []):
        return True
    return bool(name) and name in user_agents(cwd)


def agent_line(agent, flags):
    model = flags.value(f"{agent}.model") or "model not set"
    effort = flags.value(f"{agent}.effort") or "effort not set"
    return f"{agent} ({model}, {effort})"


def tier_lines(flags):
    table = tree_module.load(files.PARTS_DIR).get(TABLE)
    lines = []
    for row in table.children if table else []:
        if not flags.runs(row.id):
            continue
        agents = [need for need in row.needs + row.needs_any if flags.runs(need)]
        lines.append(f"- {row.name.capitalize()}: " + ", ".join(agent_line(agent, flags) for agent in agents))
    return lines


def refusal(name, flags):
    what = f"starts {name!r}, which is not a tier agent," if name else "names no agent"
    lines = [f"hub stopped this agent call. It {what} and sets no model. It would run on the main "
             f"chat's model, which costs the most."]
    tiers = tier_lines(flags)
    if tiers:
        lines += ["Pick an agent by its tier, or pass a model:", *tiers]
    else:
        lines.append("No tier agent runs now, so pass a model.")
    if name:
        lines.append(f"Only the user can let this name through. They set {ALLOWED} with /hub "
                     "in Claude Code. Do not change that setting yourself.")
    return "\n".join(lines) + f"\n({GUARD})"


def handle(event, hook_input, flags, answer):
    if event != "PreToolUse" or hook_input.get("tool_name") != "Agent":
        return
    tool_input = hook_input.get("tool_input") or {}
    if tiered(tool_input, flags, hook_input.get("cwd")):
        return
    if not flags.runs(GUARD):
        answer.signal(GUARD, "untiered")
        return
    answer.signal(GUARD, "refused")
    answer.refuse(refusal(tool_input.get("subagent_type") or "", flags))
