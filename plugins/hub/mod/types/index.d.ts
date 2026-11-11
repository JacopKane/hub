// The part of hub's screen model that the mod reads, as the step's `screen --json` prints it.
// plugins/hub/lib/hub/screen.py builds it, and tests/test_doors.py checks the fixture in mod/tests.

export type HubButton = { label: string; args: string[] }

export type HubStripLine = {
  id: string
  kind: 'change' | 'undo' | 'offer' | 'update'
  mark: '+' | '-' | '~'
  time: string
  text: string
  reason: string
  buttons: HubButton[]
}

export type HubBandFlags = {
  runs: boolean
  state: boolean
  notices: boolean
  offers: boolean
  chats: boolean
  problems: boolean
  first_digit: number
  keep_hours: number
}

// What the mod's last session start found, as the step keeps it for the doctor.
export type HubCommandClash = { used: string | null; refused: Record<string, string>; folder: string; time: string }

// The band's chats zone: the other chats in full and folded to counters, with their width in
// terminal cells, and the question of the first other chat that needs the user.
export type HubChatsZone = {
  full: string
  full_cells: number
  folded: string
  folded_cells: number
  question: { chat: string; session: string; line: string } | null
}

export type HubScreen = {
  tool: string
  header: { machine: string; use: string; parts_on: number; parts_total: number; start_text: number }
  strip: HubStripLine[]
  band: HubBandFlags
  board: { chats: HubChatsZone | null; failed: number; coordinator: boolean }
  hidden: number
  command_clash: HubCommandClash | null
  // Whether permission_relay runs for this tool, so the mod asks `relay next`.
  relay: boolean
  // How many tuner candidates wait, since when, and whether the mod's side call decides them.
  tuner: { pending: number; since: string; side_call: boolean }
  // The folder the mod writes each door ticket's hash to, before a change the user typed or clicked.
  doors: string
}

// A flag's value as the screen model prints it, such as a mode word, a number, a text or a list.
export type HubValue = string | number | boolean | string[]

// One flag's row in the screen model's families.
export type HubRow = {
  id: string
  name: string
  depth: number
  does: string
  value: HubValue
  effective: HubValue
  runs: boolean
  by: string
  scope: string
  relevant: boolean
  hidden_because: string | null
  cost: string
  next_session: boolean
  claude_code_only: boolean
  stage: string | null
}

// The fields `screen --json --all` adds to a row for the picker. They give its kind, its flag type,
// the values it takes, and what keeps it from running while it is on.
export type HubPickerRow = HubRow & { kind: string; type: string; values: string[]; waits_for: string[] }

// A part row, with whether hub offers it and has no answer yet.
export type HubPickerPart = HubPickerRow & { new: boolean; children: HubPickerRow[] }

export type HubFamily = { family: string; label?: string; parts_on: number; parts_total: number; parts: HubPickerPart[] }

export type HubPickerScreen = HubScreen & { families: HubFamily[]; everything: boolean }

// The picker pane's state. It holds the model it last read or why that read failed, the open
// families, the unfolded parts and instruction groups, the focused element, and the strip lines
// this visit made (newest last) for its undo, each with the row it changed.
export type HubPicker = {
  screen: HubPickerScreen | null
  failure: string | null
  families: string[]
  parts: string[]
  instructions: string[]
  focused: string | null
  // The first line the list draws, by its key, or null for the top. Only the ▲ and ▼ rows and an
  // undo that lands outside the drawn rows move it.
  top: string | null
  undo: { change: string; row: string }[]
}

// What `tuner ask` prints: the request the mod sends to the model, and the candidates in it.
export type HubTunerRequest = {
  model: string
  system: string
  prompt: string
  candidates: { flag: string; value: string; reason: string }[]
}

export type HubBand = { screen: HubScreen | null; failure: string | null }

// The name this session's slash command got (null when Claude Code refused every name),
// and each name it refused, with the reason.
export type HubCommand = { used: string | null; refused: Record<string, string> }

// One prompt that waits in another chat, as `relay next --json` prints it.
export type HubRelayDialog = { id: string; text: string; buttons: string[] }

export type HubRelayNext = { hub: false } | { hub: true; waiting: number; dialog: HubRelayDialog | null }

// Whether this chat is the coordinator that holds the relay, and how many prompts wait.
export type HubRelay = { hub: boolean; waiting: number }

declare module 'claude-code' {
  interface PluginState {
    hub: { band: HubBand; now: number; command: HubCommand; relay: HubRelay; tried: string; picker: HubPicker }
  }
}
