// hub's mod for Claude Code: the band above the prompt, the /hub command, the tuner's side call and,
// in the coordinator chat, the permission relay's dialogs. All of them run hub's step as a process,
// so every door shares one Python library: the band draws `screen --json`, and each button, each
// /hub call and each relay dialog is a call to the step.
import { atom, read, update } from 'claude-code'
import type { CommandRunInput, EngineInterface, Hook, PromptOrigin, Register, Timer } from 'claude-code'

import { FEATURES_LABEL, openPicker, registerPicker } from './picker'
import { refusePick } from './picker-texts'
import type {
  HubBand,
  HubChatsZone,
  HubCommand,
  HubRelay,
  HubRelayDialog,
  HubRelayNext,
  HubScreen,
  HubStripLine,
  HubTunerRequest,
} from './types'

const band = atom({ plugin: 'hub', key: 'band' } as const, { screen: null, failure: null } as HubBand)
const now = atom({ plugin: 'hub', key: 'now' } as const, 0)
const command = atom({ plugin: 'hub', key: 'command' } as const, { used: null, refused: {} } as HubCommand)
const relay = atom({ plugin: 'hub', key: 'relay' } as const, { hub: false, waiting: 0 } as HubRelay)
// The time of the last tuner list the side call took, so each list gets one try in a session.
const tried = atom({ plugin: 'hub', key: 'tried' } as const, '')

export const COMMAND = 'hub'
// The name the door takes when Claude Code gives /hub to something else, such as the
// user's own skill named hub. The band and the doctor both name the clash.
const FALLBACK = 'hub-flags'
const SPEC = {
  description: "Show hub's parts and flags, or change one, without a model turn",
  argumentHint: '[show | on <flag> | off <flag> | set <flag> <value> | undo [id] | doctor]',
}

const HOUR = 60 * 60 * 1000
// How often the band checks the age of its lines, so an old line leaves without a turn.
const TICK = 10 * 60 * 1000
const READ_VERBS = new Set(['show', 'screen', 'doctor', 'offers', 'board', 'tasks', '-h', '--help'])
// The user types at the prompt or writes through the Remote Control bridge. Any other origin,
// such as a peer chat, a schedule or another plugin, can read a switch but not change it.
const USER_ORIGINS = new Set<PromptOrigin['kind']>(['composer', 'bridge'])

// A button either makes a change and redraws the band, or prints the step's answer in the
// transcript, which the model does not read.
type ShownButton = { key: string; label: string; hotkey: string; args: string[]; prints?: boolean }

// A part of a row with the cells it takes.
type Zone = { text: string; width: number }

// The top row as it is drawn at one width: each part, or null (or '') where the row dropped it.
type Top = {
  label: Zone | null
  waiting: string
  zone: Zone | null
  rule: string
  failed: string | null
  features: boolean
}

// The notice's width is in cells, as the step counts them where the screen model sends a count.
type Shown = {
  top: Top
  text: string | null
  textCells: number
  buttons: ShownButton[]
}

type Notice = { text: string; cells: number | null; buttons: ShownButton[] }

// A change may take a while to apply, but reading the screen should be quick. The band keeps
// its last state until a read ends. So a stuck step never holds up a session start or a turn.
const CHANGE_TIMEOUT = 30000
export const SCREEN_TIMEOUT = 5000

export async function runStep($: EngineInterface, args: readonly string[], stdin?: string, timeoutMs = CHANGE_TIMEOUT) {
  return $.process.run(['python3', '-I', `${$.plugin.root}/lib/hub_switch.py`, ...args], { timeoutMs, stdin })
}

function hex(bytes: Uint8Array) {
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}

// A change the user typed or clicked comes through the mod, the one door that may lower a
// protected flag. The mod proves it with a single-use ticket: 32 random bytes, sent as the first
// line of the step's stdin, and a file named by their sha256 in hub's doors folder. The step removes
// the file when it takes the ticket, and takes none older than a minute. With no doors folder
// known yet, the change goes without a ticket, so it can not lower a protected flag.
async function ticket($: EngineInterface): Promise<string | undefined> {
  const folder = (await read($, band)).screen?.doors
  if (folder === undefined) {
    return undefined
  }
  const text = hex(crypto.getRandomValues(new Uint8Array(32)))
  const name = hex(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))))
  try {
    await $.fs.write(`${folder}/${name}`, '{}')
  } catch {
    return undefined
  }
  return `${text}\n`
}

export async function change($: EngineInterface, args: readonly string[]) {
  return runStep($, args, await ticket($))
}

// Claude Code's refusal reads '"/hub" refused: it is the user's /hub'; the band shows the reason.
function refusal(error: unknown) {
  const text = error instanceof Error ? error.message : String(error)
  const at = text.indexOf('refused: ')
  return at === -1 ? text : text.slice(at + 'refused: '.length)
}

async function registerCommand($: EngineInterface): Promise<HubCommand> {
  const refused: Record<string, string> = {}
  for (const name of [COMMAND, FALLBACK]) {
    try {
      const done = await $.command.register({ name, ...SPEC })
      return { used: done.command, refused }
    } catch (error) {
      refused[name] = refusal(error)
    }
  }
  return { used: null, refused }
}

// The step keeps the clash for the doctor. A session that gets /hub again clears it.
async function tellStep($: EngineInterface, got: HubCommand, screen: HubScreen | null) {
  const clashed = Object.keys(got.refused).length > 0
  if (!clashed && (screen === null || screen.command_clash === null)) {
    return
  }
  try {
    await runStep($, ['mod-command', '--json'], JSON.stringify(got))
  } catch {
    // The band already says what clashed; the doctor line waits for the next start.
  }
}

function firstLine(text: string) {
  return text.trim().split('\n')[0] ?? ''
}

async function readScreen($: EngineInterface) {
  let next: HubBand
  try {
    // --band notes the line the band shows, so no start text repeats it. It also hides a line a chat told.
    const done = await runStep($, ['screen', '--json', '--band'], undefined, SCREEN_TIMEOUT)
    next =
      done.exitCode === 0
        ? { screen: JSON.parse(done.stdout) as HubScreen, failure: null }
        : { screen: null, failure: firstLine(done.stderr || done.stdout) || `exit code ${done.exitCode}` }
  } catch (error) {
    next = { screen: null, failure: error instanceof Error ? error.message : String(error) }
  }
  const time = await $.clock.now()
  await update($, band, () => next)
  await update($, now, () => time)
  followBoard($, next)
  const waiting = next.screen?.tuner
  if (waiting !== undefined && waiting.side_call && waiting.pending > 0 && waiting.since !== (await read($, tried))) {
    await update($, tried, () => waiting.since)
    // Started apart from the read, so the band, the session start and the turn never wait for the model.
    $.clock.after(0, () => {
      void sideCall($, waiting.since)
    })
  }
}

// The tuner's model call, made here beside the chat so the chat's context stays clean. The
// model only picks among the candidates the rules found, and `tuner decide` makes only
// those changes. When the call fails or the answer is no use, the list goes back to the chat.
const MODEL_TIMEOUT = 20000
const MODEL_TOKENS = 300

async function sideCall($: EngineInterface, since: string) {
  let decided = false
  try {
    decided = await decide($)
  } catch {
    decided = false
  }
  if (!decided) {
    try {
      await runStep($, ['tuner', 'give-back', '--since', since])
    } catch {
      // The step did not run. The chat gets the list once the mod has gone two weeks without asking.
    }
  }
}

// Returns true when nothing is left to decide, because the pick landed or no candidate waits.
async function decide($: EngineInterface) {
  const asked = await runStep($, ['tuner', 'ask'])
  if (asked.exitCode !== 0) {
    return false
  }
  const request = JSON.parse(asked.stdout) as HubTunerRequest
  if (request.candidates.length === 0) {
    return true
  }
  const reply = await $.model.complete({
    model: request.model,
    system: request.system,
    prompt: request.prompt,
    maxTokens: MODEL_TOKENS,
    effort: 'low',
    timeoutMs: MODEL_TIMEOUT,
  })
  const pick = reply.isAnswered ? firstObject(reply.text) : null
  if (pick === null) {
    return false
  }
  const done = await runStep($, ['tuner', 'decide', '--answer', '-'], pick)
  if (done.exitCode !== 0) {
    return false
  }
  // The band shows the tuner's line with its undo.
  await refresh($)
  return true
}

// The first {...} in the model's reply, when it is JSON. A model may wrap it in a sentence.
function firstObject(text: string) {
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start === -1 || end < start) {
    return null
  }
  const found = text.slice(start, end + 1)
  try {
    JSON.parse(found)
    return found
  } catch {
    return null
  }
}

// In the coordinator chat the band rereads the board every BOARD_TICK while no main turn runs, so
// the user sees other chats change while this one waits. Other chats reread after each turn.
// The step decides which chat is the coordinator, and a failed read leaves the timer as it was.
const BOARD_TICK = 2000
let boardTimer: Timer | null = null
let mainTurn = false

function followBoard($: EngineInterface, latest: HubBand) {
  if (latest.screen === null) {
    return
  }
  if (latest.screen.board?.coordinator === true) {
    boardTimer ??= $.clock.every(BOARD_TICK, () => {
      if (!mainTurn) {
        void refresh($)
      }
    })
  } else {
    boardTimer?.cancel()
    boardTimer = null
  }
}

// Only one read runs at a time. A call during a read waits for one more read, and later calls
// share that one. So the band always ends on a read that started after the newest call.
let reading: Promise<void> | null = null
let queued: Promise<void> | null = null

export function refresh($: EngineInterface): Promise<void> {
  if (reading === null) {
    reading = readScreen($).finally(() => {
      reading = null
    })
    return reading
  }
  if (queued === null) {
    queued = reading.then(() => {
      queued = null
      return refresh($)
    })
  }
  return queued
}

async function tick($: EngineInterface) {
  const time = await $.clock.now()
  await update($, now, () => time)
}

// Splits the words after /hub as a shell would for plain quotes: "two words" stays one.
function words(text: string) {
  const found: string[] = []
  const pattern = /"([^"]*)"|'([^']*)'|(\S+)/g
  for (const match of text.matchAll(pattern)) {
    found.push(match[1] ?? match[2] ?? match[3] ?? '')
  }
  return found
}

function thousands(count: number) {
  return count < 1000 ? String(count) : `${(count / 1000).toFixed(1)}k`
}

function fresh(line: HubStripLine, time: number, keepHours: number) {
  return time === 0 || Date.parse(line.time) >= time - keepHours * HOUR
}

// The band has two rows. The top row is "1 hub ── 2 prompts wait  chats ──── ✗2 [ features ]",
// and the event row below it is "notice  [ undo ]". Only the coordinator chat shows the count of
// prompts that wait in other chats. The notice is, in the order surfaces.md gives, the question of
// a chat that needs the user, the newest change with a button, an offer, or hub's state. With no
// notice the event row is not drawn, and the top row still shows.
function clashText(got: HubCommand) {
  const taken = `✗ /${COMMAND} is taken (${got.refused[COMMAND]})`
  return got.used !== null ? `${taken}, so hub uses /${got.used}` : `${taken}, and so is /${FALLBACK}. Rename the other /${COMMAND}, then use /${COMMAND} in Claude Code`
}

const MOST_FAILED = 99
// The dim mark after the label, the gap between parts of one row, and the dim rule that fills the
// top row, which keeps at least this many dashes.
const LEAD = ' ── '
const GAP = '  '
const LEAST_RULE = 3
// The event's text stays beside its buttons while it has at least this many cells there.
const LEAST_BESIDE = 40

// A guess at the cells a terminal draws, only for text the screen model sends no count for, such
// as a change line or a button label. It counts the East Asian wide and full width blocks and two
// emoji blocks as two cells, and every mark as none. hub's Python counts by each letter's East
// Asian width, which also makes emoji such as ✅ ❌ ⚡ 🚀 two cells and U+FE0F one, so the two can
// differ. The mod takes the step's count wherever the screen model has one.
const WIDE = /[\u{1100}-\u{115F}\u{2E80}-\u{303E}\u{3041}-\u{33FF}\u{3400}-\u{4DBF}\u{4E00}-\u{9FFF}\u{A000}-\u{A4CF}\u{AC00}-\u{D7A3}\u{F900}-\u{FAFF}\u{FE30}-\u{FE4F}\u{FF00}-\u{FF60}\u{FFE0}-\u{FFE6}\u{1F300}-\u{1F64F}\u{1F900}-\u{1F9FF}\u{20000}-\u{3FFFD}]/u
const MARK = /\p{M}/u

function characterCells(character: string) {
  if (MARK.test(character)) {
    return 0
  }
  return WIDE.test(character) ? 2 : 1
}

function cellWidth(text: string) {
  return [...text].reduce((total, character) => total + characterCells(character), 0)
}

// A terminal draws a button as "[ label ]".
function buttonCells(label: string) {
  return cellWidth(label) + 4
}

const FEATURES_CELLS = buttonCells(FEATURES_LABEL)

// The cells the top row takes whatever the chats are: the label, the rule's two spaces, the
// failed checks count with its space, and the features button.
function fixedCells(label: string, failed: string | null) {
  const failedCells = failed === null ? 0 : cellWidth(failed) + 1
  return cellWidth(label) + 2 + failedCells + buttonCells(FEATURES_LABEL)
}

// The waiting count with the gap after it, when chats follow it.
function waitingCells(waiting: string, withChats: boolean) {
  if (waiting === '') {
    return 0
  }
  return cellWidth(waiting) + (withChats ? GAP.length : 0)
}

// The first names, as many as fit, then "+N" for the rest. Every chat that asks keeps its name
// (the step sends those first), so +N only ever hides chats that do not ask. Null when not even
// those names fit.
function namesThatFit(entries: HubChatsZone['entries'], room: number): Zone | null {
  const lastAsking = entries.reduce((last, entry, index) => (entry.asks ? index : last), -1)
  const tries = entries.slice(0, -1).map((_, index) => {
    const kept = entries.slice(0, index + 1)
    const more = `+${entries.length - kept.length}`
    const keptCells = kept.reduce((total, entry) => total + entry.cells + GAP.length, 0)
    return { text: [...kept.map(entry => entry.text), more].join(GAP), width: keptCells + cellWidth(more), last: index }
  })
  const fitting = tries.filter(attempt => attempt.last >= lastAsking && attempt.width <= room).at(-1)
  return fitting === undefined ? null : { text: fitting.text, width: fitting.width }
}

// Use the full zone when it fits the room the top row leaves. Otherwise the names that fit and
// +N, and otherwise the folded counters, which keep every chat that asks, cut to the room.
function zone(chats: HubChatsZone, room: number): Zone {
  if (chats.full_cells <= room) {
    return { text: chats.full, width: chats.full_cells }
  }
  return namesThatFit(chats.entries, room) ?? { text: chats.folded, width: Math.max(0, Math.min(chats.folded_cells, room)) }
}

// The room for the chats: the row less its fixed parts, the lead, the waiting count and the least rule.
function chatsRoom(columns: number, label: string, waiting: string, failed: string | null) {
  return columns - fixedCells(label, failed) - LEAD.length - waitingCells(waiting, true) - LEAST_RULE
}

// The cells the top row's parts take, all but the rule.
function partsCells(parts: Omit<Top, 'rule'>) {
  const lead = parts.waiting !== '' || parts.zone !== null ? LEAD.length : 0
  const failedCells = parts.failed === null ? 0 : cellWidth(parts.failed) + 1
  const features = parts.features ? FEATURES_CELLS : 0
  return (parts.label?.width ?? 0) + lead + waitingCells(parts.waiting, parts.zone !== null) + (parts.zone?.width ?? 0) + failedCells + features
}

// The dim rule fills the cells the parts leave, with a space at each end, or only spaces when
// fewer than 3 cells are left.
function ruleText(cells: number) {
  return cells >= 3 ? ` ${'─'.repeat(cells - 2)} ` : ' '.repeat(Math.max(0, cells))
}

// The top row at a width. The full row keeps a rule of at least 3 dashes, and its chats fold or
// are cut to the room it leaves. When even that does not fit, parts go in the order of loss: the
// chats, then the rule down to one space, the failed checks count, the waiting count, the label,
// and last the features button. A chat that asks still shows in the event row's question.
function topRow(columns: number, label: string, waiting: string, failed: string | null, chats: HubChatsZone | null): Top {
  const room = chatsRoom(columns, label, waiting, failed)
  const labelZone = { text: label, width: cellWidth(label) }
  const full = { label: labelZone, waiting, zone: chats !== null && room >= 1 ? zone(chats, room) : null, failed, features: true }
  const bare = { ...full, zone: null }
  const tries = [
    { parts: full, leastRule: LEAST_RULE + 2 },
    { parts: bare, leastRule: 1 },
    { parts: { ...bare, failed: null }, leastRule: 1 },
    { parts: { ...bare, failed: null, waiting: '' }, leastRule: 1 },
    { parts: { ...bare, failed: null, waiting: '', label: null }, leastRule: 0 },
  ]
  const fitting = tries.find(attempt => partsCells(attempt.parts) + attempt.leastRule <= columns)?.parts ?? {
    label: { text: label, width: Math.max(0, columns) },
    waiting: '',
    zone: null,
    failed: null,
    features: false,
  }
  return { ...fitting, rule: ruleText(columns - partsCells(fitting)) }
}

function failedText(count: number) {
  return count > MOST_FAILED ? `✗${MOST_FAILED}+` : `✗${count}`
}

// Only the question's line comes with its cells from the step; the mod counts the other notices.
function notice(screen: HubScreen, time: number, got: HubCommand): Notice | null {
  const flags = screen.band
  const digit = flags.first_digit
  const question = flags.chats ? screen.board?.chats?.question ?? null : null
  if (question !== null) {
    return {
      text: question.line,
      cells: question.cells,
      buttons: [
        { key: `open:${question.session}`, label: 'open', hotkey: String(digit + 1), args: ['board', 'show', question.session], prints: true },
      ],
    }
  }
  const lines = screen.strip.filter(line => line.buttons.length > 0 && fresh(line, time, flags.keep_hours))
  const change =
    (flags.notices && lines.find(line => line.kind === 'change' || line.kind === 'update')) ||
    (flags.offers && lines.find(line => line.kind === 'offer')) ||
    undefined
  if (change !== undefined) {
    const reason = change.reason ? ` · ${change.reason}` : ''
    return {
      text: `${change.mark} ${change.text}${reason}`,
      cells: null,
      buttons: change.buttons.slice(0, 2).map((button, place) => {
        const name = button.label.toLowerCase()
        return { key: `${change.id}:${name}`, label: name, hotkey: String(digit + 1 + place), args: button.args }
      }),
    }
  }
  if (got.refused[COMMAND] !== undefined) {
    return { text: clashText(got), cells: null, buttons: [] }
  }
  if (flags.state) {
    const header = screen.header
    return { text: `${header.parts_on} on · start text ${thousands(header.start_text)}`, cells: null, buttons: [] }
  }
  return null
}

// The coordinator's count of prompts that wait in other chats, beside the band's label.
function waitingText(waiting: HubRelay) {
  if (!waiting.hub || waiting.waiting === 0) {
    return ''
  }
  return waiting.waiting === 1 ? '1 prompt waits' : `${waiting.waiting} prompts wait`
}

function shown(state: HubBand, time: number, got: HubCommand, columns: number, waiting: HubRelay): Shown | null {
  const { screen, failure } = state
  if (failure !== null) {
    const text = `✗ hub did not answer: ${failure}`
    return { top: topRow(columns, '1 hub', '', null, null), text, textCells: cellWidth(text), buttons: [] }
  }
  if (screen === null || !screen.band.runs) {
    return null
  }
  const flags = screen.band
  const chats = flags.chats ? screen.board?.chats ?? null : null
  const count = flags.problems ? screen.board?.failed ?? 0 : 0
  const line = notice(screen, time, got)
  const prompts = waitingText(waiting)
  if (line === null && chats === null && count <= 0 && prompts === '') {
    return null
  }
  const label = `${flags.first_digit} hub`
  const failed = count > 0 ? failedText(count) : null
  return {
    top: topRow(columns, label, prompts, failed, chats),
    text: line?.text ?? null,
    textCells: line === null ? 0 : line.cells ?? cellWidth(line.text),
    buttons: line?.buttons ?? [],
  }
}

async function press($: EngineInterface, button: ShownButton) {
  const done = button.prints ? await runStep($, button.args) : await change($, button.args)
  if (done.exitCode !== 0) {
    $.ui.toast(firstLine(done.stderr || done.stdout))
  } else if (button.prints) {
    $.ui.log(done.stdout.trimEnd())
  }
  if (!button.prints) {
    await refresh($)
  }
}

function origin(from: PromptOrigin | undefined) {
  if (from === undefined) {
    return 'a place Claude Code did not name'
  }
  if (from.kind === 'plugin') {
    return `plugin ${from.name}${from.asUser ? ' as the user' : ''}`
  }
  return from.kind
}

async function startRead($: EngineInterface, got: HubCommand) {
  await refresh($)
  await tellStep($, got, (await read($, band)).screen)
  await askRelay($)
}

// The permission relay runs in the coordinator chat only. The step keeps the prompts that wait in
// other chats and answers none of them. The mod asks it at a session start, at a main turn end,
// and every RELAY_TICK while the step says this chat is the coordinator. It opens one dialog at a
// time, only while the chat is idle. The buttons only open that chat's tab or put the prompt off.
const RELAY_TICK = 2000
// Each prompt the user dismissed or answered with typed text, passed over until /hub relay.
const skipped = new Set<string>()
let relayTimer: Timer | null = null
let asking = false
let dialogOpen = false
let questionsOpen = 0

// Asks the step what waits, when the last screen read says the relay runs here.
async function askRelay($: EngineInterface) {
  const screen = (await read($, band)).screen
  if (screen === null || !screen.relay) {
    stopRelay($)
    return
  }
  await pollRelay($)
}

function stopRelay($: EngineInterface) {
  relayTimer?.cancel()
  relayTimer = null
  void update($, relay, () => ({ hub: false, waiting: 0 }))
}

async function pollRelay($: EngineInterface) {
  if (asking) {
    return
  }
  asking = true
  try {
    let session: string
    try {
      session = await $.session.id()
    } catch {
      stopRelay($)
      return
    }
    const answer = await relayNext($, session)
    if (!answer.hub) {
      stopRelay($)
      return
    }
    await update($, relay, () => ({ hub: true, waiting: answer.waiting }))
    relayTimer ??= $.clock.every(RELAY_TICK, () => {
      void pollRelay($)
    })
    if (answer.dialog !== null) {
      await offer($, session, answer.dialog)
    }
  } finally {
    asking = false
  }
}

// A relay that fails or prints something else counts as "not the coordinator", so a chat
// where the part is off or broken shows nothing and runs no timer.
async function relayNext($: EngineInterface, session: string): Promise<HubRelayNext> {
  const args = ['relay', 'next', '--session', session, '--json', ...[...skipped].flatMap(id => ['--skip', id])]
  try {
    const done = await runStep($, args, undefined, SCREEN_TIMEOUT)
    if (done.exitCode !== 0) {
      return { hub: false }
    }
    const answer = JSON.parse(done.stdout) as HubRelayNext
    return answer.hub === true ? answer : { hub: false }
  } catch {
    return { hub: false }
  }
}

async function idle($: EngineInterface) {
  return !mainTurn && questionsOpen === 0 && !dialogOpen && (await $.prompt.read()).text === ''
}

async function offer($: EngineInterface, session: string, dialog: HubRelayDialog) {
  if (!(await idle($))) {
    return
  }
  if (dialog.buttons.length < 2) {
    // Claude Code pads a dialog with fewer than two labels with Yes and No, so a prompt with
    // only Later gets no dialog. It is passed over, so the next prompt can show.
    skipped.add(dialog.id)
    return
  }
  // The dialog counts as open until its pick has run, so a tick in between can not show the
  // same prompt again.
  dialogOpen = true
  void (async () => {
    try {
      const label = await $.ui.ask(dialog.text, dialog.buttons).catch(() => null)
      if (label === null || !dialog.buttons.includes(label)) {
        skipped.add(dialog.id)
        return
      }
      const done = await runStep($, ['relay', 'pick', dialog.id, label, '--session', session])
      if (done.exitCode !== 0) {
        $.ui.toast(firstLine(done.stderr || done.stdout))
      }
    } catch {
      // The step did not run, so nothing was picked. The next tick shows the prompt again.
    } finally {
      dialogOpen = false
    }
  })()
}

// /hub relay shows again what the user put off, with Later or in this session. It answers with
// only a count, because Claude Code may show the command's text to the model.
async function relayAgain($: EngineInterface): Promise<{ text: string }> {
  const { screen } = await read($, band)
  if (screen !== null && !screen.relay) {
    return { text: 'The permission relay is off. Switch it on with /hub on permission_relay.' }
  }
  skipped.clear()
  const done = await runStep($, ['relay', 'again', '--session', await $.session.id()])
  if (done.exitCode !== 0) {
    return { text: firstLine(done.stderr || done.stdout) }
  }
  const count = Number.parseInt(done.stdout.trim(), 10)
  $.clock.after(0, () => {
    void pollRelay($)
  })
  if (count === 0) {
    return { text: 'No prompts wait in other chats.' }
  }
  const waits = count === 1 ? '1 prompt waits' : `${count} prompts wait`
  return { text: `${waits} in other chats. The ones you put off show again.` }
}

// Runs the words after a command through hub's step and answers with what it printed. A change
// runs only when the user typed the command or sent it through the bridge, and so does `pick`,
// which opens the picker pane.
async function run($: EngineInterface, e: CommandRunInput, typed: string): Promise<{ text?: string }> {
  const args = words(e.args)
  const asked = args.length > 0 ? args : ['show']
  if (asked[0] === 'pick') {
    if (!fromUser(e.origin)) {
      return { text: refusePick(typed) }
    }
    await openPicker($)
    return {}
  }
  const changing = changes(asked)
  if (changing && !fromUser(e.origin)) {
    return {
      text: `Nothing changed. Only the user can change a switch, by typing /${typed} at the prompt. This request came from ${origin(e.origin)}.`,
    }
  }
  if (asked[0] === 'relay') {
    return relayAgain($)
  }
  let text: string
  try {
    const done = changing ? await change($, asked) : await runStep($, asked)
    text = (done.exitCode === 0 ? done.stdout : done.stderr || done.stdout).trimEnd()
  } catch (error) {
    text = `hub did not run: ${error instanceof Error ? error.message : String(error)}`
  }
  if (changing) {
    await refresh($)
  }
  return { text }
}

// `board task` writes a chat's task list, so it is a change. Every other board view is a read.
// Options can come before it, as in `board --json task add x`, so any word "task" counts.
function changes(asked: string[]) {
  const verb = asked[0] ?? 'show'
  return !READ_VERBS.has(verb) || (verb === 'board' && asked.includes('task'))
}

function fromUser(from: PromptOrigin | undefined) {
  return from !== undefined && USER_ORIGINS.has(from.kind)
}

// /hub, or /hub-flags when /hub is taken, answered here without a model turn.
const answer: Hook<'command.run'> = async ($, e, next) => {
  if ((await read($, command)).used !== e.command) {
    // Not hub's: the user's own /hub, which Claude Code runs as it would without hub.
    return next(e)
  }
  return run($, e, e.command)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const got = await registerCommand($)
    await update($, command, () => got)
    $.clock.after(0, () => {
      void startRead($, got)
    })
    $.clock.every(TICK, () => {
      void tick($)
    })
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      mainTurn = true
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    const main = e.agentId === undefined
    if (main) {
      mainTurn = false
    }
    $.clock.after(0, () => {
      void refresh($).then(() => (main ? askRelay($) : undefined))
    })
    return result
  })

  // A question the model or another plugin asks keeps the relay's dialog away until it is answered.
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    questionsOpen += 1
    try {
      return await next(e)
    } finally {
      questionsOpen -= 1
    }
  })

  registerPicker(on)

  on('command.run', { command: COMMAND }, answer)
  on('command.run', { command: FALLBACK }, answer)

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) {
      return next(e)
    }
    const line = shown(
      await read($, band),
      await read($, now),
      await read($, command),
      e.props.bodyColumns,
      await read($, relay),
    )
    if (line === null) {
      return next(e)
    }
    const { Box, Button, Text } = $.ui.resolve(e)
    const columns = e.props.bodyColumns
    const parts = line.top
    const hasLead = parts.waiting !== '' || parts.zone !== null
    const buttons = line.buttons.map(button => (
      <Button key={button.key} label={button.label} hotkey={button.hotkey} onPress={() => press($, button)} />
    ))
    // The label is cut only when the band is narrower than the features button.
    const label =
      parts.label === null ? null : parts.label.width < cellWidth(parts.label.text) ? (
        <Box key="label" width={parts.label.width} flexShrink={0}>
          <Text bold wrap="truncate-end">
            {parts.label.text}
          </Text>
        </Box>
      ) : (
        <Text bold>{parts.label.text}</Text>
      )

    const top = (
      <Box key="top" flexDirection="row" width={columns}>
        {label}
        {hasLead && <Text dimColor>{LEAD}</Text>}
        {parts.waiting !== '' && (
          <Text key="waiting" wrap="truncate-end">
            {parts.waiting}
          </Text>
        )}
        {parts.waiting !== '' && parts.zone !== null && <Text dimColor>{GAP}</Text>}
        {parts.zone !== null && (
          <Box key="chats" width={parts.zone.width} flexShrink={0}>
            <Text wrap="truncate-end">{parts.zone.text}</Text>
          </Box>
        )}
        {parts.rule !== '' && (
          <Box key="rule" flexGrow={1}>
            <Text dimColor>{parts.rule}</Text>
          </Box>
        )}
        {parts.failed !== null && (
          <Text key="failed" color="red">
            {parts.failed}
          </Text>
        )}
        {parts.failed !== null && <Text dimColor> </Text>}
        {parts.features && <Button key="features" label={FEATURES_LABEL} hotkey="f" onPress={() => openPicker($)} />}
      </Box>
    )
    if (line.text === null) {
      return <Box flexDirection="column" width={columns}>{top}</Box>
    }

    // The event row keeps the text beside its buttons when it fits there or 40 cells are left
    // there. Otherwise the text wraps over the whole width with the buttons below it, 3 lines at
    // most in all.
    const buttonsCells = line.buttons.reduce((total, button) => total + buttonCells(button.label), 0)
    const beside = buttons.length > 0 ? columns - buttonsCells - GAP.length : columns
    const textCells = line.textCells
    const event =
      textCells <= beside || beside >= LEAST_BESIDE ? (
        <Box key="event" flexDirection="row" width={columns}>
          <Box key="notice" width={Math.min(textCells, beside)} flexShrink={0}>
            <Text wrap="truncate-end" maxLines={1}>
              {line.text}
            </Text>
          </Box>
          {buttons.length > 0 && <Text dimColor>{GAP}</Text>}
          {buttons}
        </Box>
      ) : buttons.length > 0 ? (
        <Box key="event" flexDirection="column" width={columns}>
          <Box key="notice" width={columns}>
            <Text wrap="wrap" maxLines={2}>
              {line.text}
            </Text>
          </Box>
          <Box key="buttons" flexDirection="row">
            {buttons}
          </Box>
        </Box>
      ) : (
        <Box key="event" flexDirection="column" width={columns}>
          <Box key="notice" width={columns}>
            <Text wrap="wrap" maxLines={3}>
              {line.text}
            </Text>
          </Box>
        </Box>
      )

    return (
      <Box flexDirection="column" width={columns}>
        {top}
        {event}
      </Box>
    )
  })
}
