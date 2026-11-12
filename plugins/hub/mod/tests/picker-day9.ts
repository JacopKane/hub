// The full screen model the feature picker reads, as `hubctl screen --json --all` prints it on
// the made-up day 9 fixture, with most children left out. tests/test_picker_model.py checks
// that each row here still equals the real row of that id.
import type { HubPickerScreen } from '../types'

export const PICKER_DAY9: HubPickerScreen = /* json */ {
  "tool": "claude_code",
  "header": {
    "machine": "laptop",
    "use": "",
    "parts_on": 7,
    "parts_total": 33,
    "start_text": 1210
  },
  "strip": [
    {
      "id": "offer:researcher",
      "kind": "offer",
      "mark": "+",
      "time": "2026-11-09T10:00:00Z",
      "text": "hub offers researcher, a reading agent for docs and pages, which reports what it read",
      "reason": "you started 6 long reading jobs this week",
      "buttons": [
        {
          "label": "Turn on",
          "args": [
            "on",
            "researcher"
          ]
        },
        {
          "label": "No",
          "args": [
            "no",
            "researcher"
          ]
        }
      ]
    },
    {
      "id": "d9dash000001",
      "kind": "change",
      "mark": "-",
      "time": "2026-11-09T09:00:00Z",
      "text": "tuner turned public_text_gate.dashes off",
      "reason": "you went ahead after 4 of 5 dash warnings this week",
      "buttons": [
        {
          "label": "Undo",
          "args": [
            "undo",
            "d9dash000001"
          ]
        }
      ]
    }
  ],
  "band": {
    "runs": true,
    "state": true,
    "notices": true,
    "offers": true,
    "chats": false,
    "problems": true,
    "first_digit": 1,
    "keep_hours": 24
  },
  "board": {
    "chats": null,
    "failed": 0,
    "coordinator": false
  },
  "relay": false,
  "families": [
    {
      "family": "setup",
      "parts_on": 1,
      "parts_total": 1,
      "parts": [
        {
          "id": "tuner",
          "name": "tuner",
          "depth": 0,
          "does": "Sets hub up on the first run. After that it keeps the set small, turning off what you go past and offering what you need.",
          "value": "on",
          "effective": "on",
          "runs": true,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "listing",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "tuner.model",
              "name": "model",
              "depth": 1,
              "does": "The model the setup agent runs on.",
              "value": "haiku",
              "effective": "haiku",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "option",
              "type": "choice",
              "values": [
                "haiku",
                "sonnet",
                "opus"
              ],
              "waits_for": []
            },
            {
              "id": "tuner.setup",
              "name": "setup",
              "depth": 1,
              "does": "Runs on the first session start, and again when you ask hub to run setup.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "tuner.setup.instructions",
              "name": "setup.instructions",
              "depth": 2,
              "does": "The sections of the setup agent's instructions.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "group",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "tuner.setup.instructions.one_dialog",
              "name": "setup.instructions.one_dialog",
              "depth": 3,
              "does": "Puts every question in one dialog, each one skippable.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "instruction",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            }
          ]
        }
      ]
    },
    {
      "family": "tiers",
      "parts_on": 3,
      "parts_total": 11,
      "parts": [
        {
          "id": "builder",
          "name": "builder",
          "depth": 0,
          "does": "An agent for work that needs judgement, such as building, debugging, review and text people read.",
          "value": "on",
          "effective": "on",
          "runs": true,
          "by": "user",
          "scope": "user",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "builder.model",
              "name": "model",
              "depth": 1,
              "does": "The model builder runs on. Cursor gets the mapped name.",
              "value": "opus",
              "effective": "opus",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "option",
              "type": "choice",
              "values": [
                "haiku",
                "sonnet",
                "opus"
              ],
              "waits_for": []
            },
            {
              "id": "builder.effort",
              "name": "effort",
              "depth": 1,
              "does": "How hard builder thinks before it answers.",
              "value": "high",
              "effective": "high",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "option",
              "type": "choice",
              "values": [
                "low",
                "medium",
                "high",
                "xhigh",
                "max"
              ],
              "waits_for": []
            },
            {
              "id": "builder.instructions",
              "name": "instructions",
              "depth": 1,
              "does": "The sections of builder's instructions.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "group",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "builder.instructions.read_first",
              "name": "instructions.read_first",
              "depth": 2,
              "does": "Reads every source that governs the work first, and names them.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "instruction",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "builder.instructions.test_first",
              "name": "instructions.test_first",
              "depth": 2,
              "does": "Writes or runs the test before the change.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "instruction",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            }
          ]
        },
        {
          "id": "context_budget",
          "name": "context_budget",
          "depth": 0,
          "does": "Tells a chat or an agent when its context gets big, because every step reads all of it again. It never stops a call.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "critic",
          "name": "critic",
          "depth": 0,
          "does": "A read-only agent for reviews where a miss is expensive, such as security, data loss and a review that decides a merge.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "handback_cap",
          "name": "handback_cap",
          "depth": 0,
          "does": "Keeps an agent's hand-back report under a word cap, so the main chat reads less.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "live_notes",
          "name": "live_notes",
          "depth": 0,
          "does": "Each agent a chat starts gets the chat's live notes file, so new rules reach agents the chat would forget.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "planner",
          "name": "planner",
          "depth": 0,
          "does": "An agent for plans, task splits and specs that code will follow. It writes only the plan file.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "researcher",
          "name": "researcher",
          "depth": 0,
          "does": "A read-only agent for reading and summing up docs, pages and code, with the source of each finding.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "offer",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": true,
          "children": [
            {
              "id": "researcher.model",
              "name": "model",
              "depth": 1,
              "does": "The model researcher runs on. Cursor gets the mapped name.",
              "value": "sonnet",
              "effective": "sonnet",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "option",
              "type": "choice",
              "values": [
                "haiku",
                "sonnet",
                "opus"
              ],
              "waits_for": [
                "researcher"
              ]
            },
            {
              "id": "researcher.instructions",
              "name": "instructions",
              "depth": 1,
              "does": "The sections of researcher's instructions.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "group",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "researcher"
              ]
            },
            {
              "id": "researcher.instructions.read_only",
              "name": "instructions.read_only",
              "depth": 2,
              "does": "Changes no file, and runs only commands that read.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "instruction",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "researcher"
              ]
            }
          ]
        },
        {
          "id": "reviewer",
          "name": "reviewer",
          "depth": 0,
          "does": "A read-only agent for code review, readiness checks and review of text people read.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "scout",
          "name": "scout",
          "depth": 0,
          "does": "A small, cheap agent for one mechanical job that ends in a report.",
          "value": "on",
          "effective": "on",
          "runs": true,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "tier_rule",
          "name": "tier_rule",
          "depth": 0,
          "does": "Start text that tells the main chat which agent tier fits a job.",
          "value": "on",
          "effective": "on",
          "runs": true,
          "by": "user",
          "scope": "user",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "rule",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "tier_rule.table",
              "name": "table",
              "depth": 1,
              "does": "A table of the agent tiers, with one row for each agent that runs.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "start text",
              "next_session": true,
              "claude_code_only": false,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "tier_rule.table.standard",
              "name": "table.standard",
              "depth": 2,
              "does": "The row for reading, research, test runs and edits that follow a plan, done by researcher and worker.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "start text",
              "next_session": true,
              "claude_code_only": false,
              "stage": null,
              "kind": "instruction",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "researcher or worker"
              ]
            },
            {
              "id": "tier_rule.guard",
              "name": "guard",
              "depth": 1,
              "does": "Stops an agent call that sets no model and starts no tier agent, and names the tiers that run. While it is off, it only counts such calls.",
              "value": "off",
              "effective": "off",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "tier_rule.guard.allowed",
              "name": "guard.allowed",
              "depth": 2,
              "does": "More agent names the guard lets through, such as a built-in agent you trust with the main chat's model.",
              "value": [],
              "effective": [],
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "option",
              "type": "list",
              "values": [],
              "waits_for": []
            }
          ]
        },
        {
          "id": "worker",
          "name": "worker",
          "depth": 0,
          "does": "An agent that carries out an exact plan, such as running tests, edits a plan spells out and rebases without conflicts.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "agent",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        }
      ]
    },
    {
      "family": "chats",
      "parts_on": 0,
      "parts_total": 6,
      "parts": [
        {
          "id": "chat_board",
          "name": "chat_board",
          "depth": 0,
          "does": "Shows every Claude Code chat as busy, idle, offline or needs you, with its current task and its tasks.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "chat_names",
          "name": "chat_names",
          "depth": 0,
          "does": "Messages sent by a chat's name reach the live chat of that name, never a dead row.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "coordinator",
          "name": "coordinator",
          "depth": 0,
          "does": "One chat the user talks to, which forwards work to the chat that owns it and relays questions back.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": true,
          "stage": null,
          "kind": "skill",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "coordinator.name",
              "name": "name",
              "depth": 1,
              "does": "The name of the coordinator chat. A chat takes it with /rename or --name.",
              "value": "HUB",
              "effective": "HUB",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "option",
              "type": "text",
              "values": [],
              "waits_for": [
                "coordinator"
              ]
            },
            {
              "id": "coordinator.start_nudge",
              "name": "start_nudge",
              "depth": 1,
              "does": "The chat with the coordinator's name is told to load hub-coordinator until it does.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "coordinator"
              ]
            },
            {
              "id": "coordinator.work_guard",
              "name": "work_guard",
              "depth": 1,
              "does": "Refuses topic edits, test runs and commits in the coordinator. Its subagents and other chats pass.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "none",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "coordinator"
              ]
            },
            {
              "id": "coordinator.work_guard.allowed",
              "name": "work_guard.allowed",
              "depth": 2,
              "does": "Paths the coordinator may still write, as globs. {state} is hub's state folder.",
              "value": [
                "*/live_notes.md",
                "~/.claude/projects/*/memory/*",
                "{state}/*"
              ],
              "effective": [
                "*/live_notes.md",
                "~/.claude/projects/*/memory/*",
                "{state}/*"
              ],
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "option",
              "type": "list",
              "values": [],
              "waits_for": [
                "coordinator"
              ]
            },
            {
              "id": "coordinator.instructions",
              "name": "instructions",
              "depth": 1,
              "does": "The sections of the hub-coordinator skill.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "group",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "coordinator"
              ]
            },
            {
              "id": "coordinator.instructions.forward",
              "name": "instructions.forward",
              "depth": 2,
              "does": "Forward the user's words to the chat that owns the topic.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "per use",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "instruction",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "coordinator"
              ]
            }
          ]
        },
        {
          "id": "permission_relay",
          "name": "permission_relay",
          "depth": 0,
          "does": "Shows the coordinator the permission prompts and auto mode blocks that wait in other chats, with a button that opens that chat's tab. It approves nothing.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "permission_relay.list",
              "name": "list",
              "depth": 1,
              "does": "Records each permission prompt that waits in another chat. The record goes when the prompt is answered, its turn ends or the chat ends.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "none",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "permission_relay"
              ]
            },
            {
              "id": "permission_relay.list.preview_chars",
              "name": "list.preview_chars",
              "depth": 2,
              "does": "How many characters of the command the dialog shows. 0 shows only the chat and the tool.",
              "value": 300,
              "effective": 300,
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "option",
              "type": "number",
              "values": [],
              "waits_for": [
                "permission_relay"
              ]
            }
          ]
        },
        {
          "id": "queue",
          "name": "queue",
          "depth": 0,
          "does": "A message to another idle main chat waits a short time, so two quick messages wake it once.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "restart",
          "name": "restart",
          "depth": 0,
          "does": "Restarts the running chats, each in its own terminal under its own name, and the calling chat last.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": true,
          "stage": null,
          "kind": "skill",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        }
      ]
    },
    {
      "family": "questions",
      "parts_on": 0,
      "parts_total": 3,
      "parts": [
        {
          "id": "away_guard",
          "name": "away_guard",
          "depth": 0,
          "does": "Holds question dialogs while the user is away, and lets only urgent ones through.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "knowledge_only",
          "name": "knowledge_only",
          "depth": 0,
          "does": "Reminds the chat to explain shared material, such as a link, a paste or an image, and to change nothing.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "question_check",
          "name": "question_check",
          "depth": 0,
          "does": "Opens a question dialog only when it carries what the user needs to answer it.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        }
      ]
    },
    {
      "family": "gates",
      "parts_on": 2,
      "parts_total": 5,
      "parts": [
        {
          "id": "chrome_lock",
          "name": "chrome_lock",
          "depth": 0,
          "does": "Lets one chat at a time drive Chrome. The others wait for the lock.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "public_text_gate",
          "name": "public_text_gate",
          "depth": 0,
          "does": "Checks text before it goes public in commits, pushes, tags, PRs, issues, reviews, releases, gists, discussions and other GitHub writes.",
          "value": "warn",
          "effective": "warn",
          "runs": true,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "hook",
          "type": "mode",
          "values": [
            "off",
            "warn",
            "block"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "public_text_gate.targets",
              "name": "targets",
              "depth": 1,
              "does": "The public steps the gate checks.",
              "value": "on",
              "effective": "warn",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "group",
              "type": "switch",
              "values": [
                "off",
                "on",
                "warn",
                "block"
              ],
              "waits_for": []
            },
            {
              "id": "public_text_gate.targets.commit",
              "name": "targets.commit",
              "depth": 2,
              "does": "Checks commit messages.",
              "value": "on",
              "effective": "warn",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on",
                "warn",
                "block"
              ],
              "waits_for": []
            },
            {
              "id": "public_text_gate.secrets",
              "name": "secrets",
              "depth": 1,
              "does": "Stops keys, tokens and chat session links, found by their shape.",
              "value": "on",
              "effective": "warn",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on",
                "warn",
                "block"
              ],
              "waits_for": []
            },
            {
              "id": "public_text_gate.private_words",
              "name": "private_words",
              "depth": 1,
              "does": "Stops a word from the private words list.",
              "value": "on",
              "effective": "warn",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on",
                "warn",
                "block"
              ],
              "waits_for": [
                "core.private_words or core.private_words_files"
              ]
            }
          ]
        },
        {
          "id": "self_protection",
          "name": "self_protection",
          "depth": 0,
          "does": "Stops a tool call that would lower hub's own protections, so only you change them.",
          "value": "on",
          "effective": "on",
          "runs": true,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "per use",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "self_protection.lowering",
              "name": "lowering",
              "depth": 1,
              "does": "Stops a tool call that would lower a gate, a guard, a privacy check or a pin, or uninstall hub, unless your newest message asks for that change.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            }
          ]
        },
        {
          "id": "sentry_write_gate",
          "name": "sentry_write_gate",
          "depth": 0,
          "does": "Experimental. Asks the user before a Sentry tool changes anything the team sees, such as a resolve or an assign.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "slack_write_gate",
          "name": "slack_write_gate",
          "depth": 0,
          "does": "Experimental. Lets a chat read Slack but never post, react, join or mark anything other people see.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        }
      ]
    },
    {
      "family": "rules",
      "parts_on": 0,
      "parts_total": 5,
      "parts": [
        {
          "id": "hub_style",
          "name": "hub_style",
          "depth": 0,
          "does": "An output style with a status block around each step, short plain replies, linked references and big jobs handed to agents. Cursor gets it as start text.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "style",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "humanize",
          "name": "humanize",
          "depth": 0,
          "does": "A writing skill that rewrites model text so a person can read it once and remember it.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": true,
          "claude_code_only": false,
          "stage": null,
          "kind": "skill",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "humanize.em_dash",
              "name": "em_dash",
              "depth": 1,
              "does": "What the skill does with an em dash. warn points out each one and leaves it, rewrite turns each into an ordinary sentence, and allow says nothing.",
              "value": "warn",
              "effective": "warn",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": false,
              "stage": null,
              "kind": "option",
              "type": "choice",
              "values": [
                "warn",
                "rewrite",
                "allow"
              ],
              "waits_for": [
                "humanize"
              ]
            }
          ]
        },
        {
          "id": "link_check",
          "name": "link_check",
          "depth": 0,
          "does": "Sends a reply back once when it names a PR, issue or ticket without a link, so every reference can be opened.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "hook",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "path_rules",
          "name": "path_rules",
          "depth": 0,
          "does": "Gives the rules for UI, tests or docs the first time a chat reads or changes a matching file, so they cost nothing until then.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "rule",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        },
        {
          "id": "working_rules",
          "name": "working_rules",
          "depth": 0,
          "does": "Start text with working rules for shared links, questions, reading first, writing, published text, delivery and judgement.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": null,
          "kind": "rule",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        }
      ]
    },
    {
      "family": "screens",
      "parts_on": 1,
      "parts_total": 1,
      "parts": [
        {
          "id": "status_band",
          "name": "status_band",
          "depth": 0,
          "does": "A line above the prompt with hub's state, the tuner's last change and open offers.",
          "value": "on",
          "effective": "on",
          "runs": true,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": true,
          "stage": null,
          "kind": "mod",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": [
            {
              "id": "status_band.notices",
              "name": "notices",
              "depth": 1,
              "does": "Shows the newest changes, each with Undo.",
              "value": "on",
              "effective": "on",
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": []
            },
            {
              "id": "status_band.notices.keep_hours",
              "name": "notices.keep_hours",
              "depth": 2,
              "does": "How many hours a line stays, unless you act on it first.",
              "value": 24,
              "effective": 24,
              "runs": true,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "option",
              "type": "number",
              "values": [],
              "waits_for": []
            },
            {
              "id": "status_band.chats",
              "name": "chats",
              "depth": 1,
              "does": "Shows your other chats with their state, and the question of a chat that needs you.",
              "value": "on",
              "effective": "on",
              "runs": false,
              "by": "default",
              "scope": "shipped",
              "relevant": true,
              "hidden_because": null,
              "cost": "",
              "next_session": false,
              "claude_code_only": true,
              "stage": null,
              "kind": "behavior",
              "type": "switch",
              "values": [
                "off",
                "on"
              ],
              "waits_for": [
                "chat_board"
              ]
            }
          ]
        }
      ]
    },
    {
      "family": "beta",
      "parts_on": 0,
      "parts_total": 1,
      "parts": [
        {
          "id": "cursor_link",
          "name": "cursor_link",
          "depth": 0,
          "does": "Gives Cursor the same parts, with hub's hooks, the start text and copies of the agents.",
          "value": "off",
          "effective": "off",
          "runs": false,
          "by": "default",
          "scope": "shipped",
          "relevant": true,
          "hidden_because": null,
          "cost": "",
          "next_session": false,
          "claude_code_only": false,
          "stage": "beta",
          "kind": "extra",
          "type": "switch",
          "values": [
            "off",
            "on"
          ],
          "waits_for": [],
          "new": false,
          "children": []
        }
      ],
      "label": "Beta, untested"
    }
  ],
  "hidden": 1,
  "everything": true,
  "command_clash": null,
  "tuner": {
    "pending": 0,
    "since": "",
    "side_call": true
  },
  "doors": "/home/sam/.local/state/hubctl/doors"
}
