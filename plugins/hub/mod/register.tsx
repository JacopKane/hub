// hub's mod for Claude Code: the band above the prompt, the /hub command, the tuner's side call and,
// in the coordinator chat, the permission relay's dialogs. All of them run hubctl as a process, so
// every door shares one Python library: the band draws `hubctl screen --json`, and each button,
// each /hub call and each relay dialog is a hubctl call.
import { atom, read, update } from 'claude-code'
import type { CatchHandler, CommandRunInput, EngineInterface, Hook, PromptOrigin, Register, Timer } from 'claude-code'

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

const COMMAND = 'hub'
// Claude Code names the hubctl skill's command hub:hubctl, whether the user types /hubctl or
// /hub:hubctl. The skill may run hubctl without a permission question, so this command gets the
// same origin check as /hub.
const SKILL = 'hub:hubctl'
const SKILL_TYPED = 'hubctl'
// The name the door takes when Claude Code gives /hub to something else, such as the
// user's own skill named hub. The band and hubctl doctor both name the clash.
const FALLBACK = 'hub-flags'
const SPEC = {
  description: "Show hub's parts and flags, or change one, without a model turn",
  argumentHint: '[show | on <flag> | off <flag> | set <flag> <value> | undo [id] | doctor]',
}

const HOUR = 60 * 60 * 1000
// How often the band checks the age of its lines, so an old line leaves without a turn.
const TICK = 10 * 60 * 1000
const SEPARATOR = ' │ '
const READ_VERBS = new Set(['show', 'screen', 'doctor', 'offers', 'board', '-h', '--help'])
// The user types at the prompt or writes through the Remote Control bridge. Any other origin,
// such as a peer chat, a schedule or another plugin, can read a switch but not change it.
const USER_ORIGINS = new Set<PromptOrigin['kind']>(['composer', 'bridge'])

// A button either makes a change and redraws the band, or prints hubctl's answer in the
// transcript, which the model does not read.
type ShownButton = { key: string; label: string; hotkey: string; args: string[]; prints?: boolean }

type Shown = {
  label: string
  waiting: string
  text: string | null
  buttons: ShownButton[]
  zone: { text: string; width: number } | null
  failed: string | null
}

// A change may take a while to apply, but reading the screen should be quick. The band keeps
// its last state until a read ends. So a stuck hubctl never holds up a session start or a turn.
const CHANGE_TIMEOUT = 30000
const SCREEN_TIMEOUT = 5000

async function hubctl($: EngineInterface, args: readonly string[], stdin?: string, timeoutMs = CHANGE_TIMEOUT) {
  return $.process.run(['python3', '-I', `${$.plugin.root}/bin/hubctl`, ...args], { timeoutMs, stdin })
}

function hex(bytes: Uint8Array) {
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
}

// A change the user typed or clicked comes through the mod, the one door that may lower a
// protected flag. The mod proves it with a single-use ticket: 32 random bytes, sent as the first
// line of hubctl's stdin, and a file named by their sha256 in hub's doors folder. hubctl removes
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

async function change($: EngineInterface, args: readonly string[]) {
  return hubctl($, args, await ticket($))
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

// hubctl keeps the clash for hubctl doctor. A session that gets /hub again clears it.
async function tellHubctl($: EngineInterface, got: HubCommand, screen: HubScreen | null) {
  const clashed = Object.keys(got.refused).length > 0
  if (!clashed && (screen === null || screen.command_clash === null)) {
    return
  }
  try {
    await hubctl($, ['mod-command', '--json'], JSON.stringify(got))
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
    const done = await hubctl($, ['screen', '--json', '--band'], undefined, SCREEN_TIMEOUT)
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
// model only picks among the candidates the rules found, and `hubctl tuner decide` makes only
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
      await hubctl($, ['tuner', 'give-back', '--since', since])
    } catch {
      // hubctl did not run. The chat gets the list once the mod has gone two weeks without asking.
    }
  }
}

// Returns true when nothing is left to decide, because the pick landed or no candidate waits.
async function decide($: EngineInterface) {
  const asked = await hubctl($, ['tuner', 'ask'])
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
  const done = await hubctl($, ['tuner', 'decide', '--answer', '-'], pick)
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
// hubctl decides which chat is the coordinator, and a failed read leaves the timer as it was.
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

function refresh($: EngineInterface): Promise<void> {
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

// The band looks like "1 hub │ 2 prompts wait │ chats │ notice [2 undo] │ ✗2". Only the
// coordinator chat shows the count of prompts that wait in other chats. The notice is, in the
// order surfaces.md gives, the question of a chat that needs the user, the newest change with a
// button, an offer, or hub's state. The count, the chats zone and the failed checks count also
// show when there is no notice.
function clashText(got: HubCommand) {
  const taken = `✗ /${COMMAND} is taken (${got.refused[COMMAND]})`
  return got.used !== null ? `${taken}, so hub uses /${got.used}` : `${taken}, and so is /${FALLBACK}. Rename the other /${COMMAND}, then use /${COMMAND} in Claude Code`
}

// The chats zone takes at most this share of the band's width.
const ZONE_SHARE = 0.4
const MOST_FAILED = 99

type Zone = { text: string; width: number }

// Use the full zone when it fits its share of the line. Otherwise use the folded one, cut to that share.
function zone(chats: HubChatsZone, columns: number): Zone {
  const share = Math.floor(ZONE_SHARE * columns)
  if (chats.full_cells <= share) {
    return { text: chats.full, width: chats.full_cells }
  }
  return { text: chats.folded, width: Math.min(chats.folded_cells, share) }
}

function failedText(count: number) {
  return count > MOST_FAILED ? `✗${MOST_FAILED}+` : `✗${count}`
}

function notice(screen: HubScreen, time: number, got: HubCommand): Pick<Shown, 'text' | 'buttons'> | null {
  const flags = screen.band
  const digit = flags.first_digit
  const question = flags.chats ? screen.board?.chats?.question ?? null : null
  if (question !== null) {
    return {
      text: question.line,
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
      buttons: change.buttons.slice(0, 2).map((button, place) => {
        const name = button.label.toLowerCase()
        return { key: `${change.id}:${name}`, label: name, hotkey: String(digit + 1 + place), args: button.args }
      }),
    }
  }
  if (got.refused[COMMAND] !== undefined) {
    return { text: clashText(got), buttons: [] }
  }
  if (flags.state) {
    const header = screen.header
    return { text: `${header.parts_on} on · start text ${thousands(header.start_text)}`, buttons: [] }
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
    return { label: '1 hub', waiting: '', text: `✗ hubctl did not answer: ${failure}`, buttons: [], zone: null, failed: null }
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
  return {
    label: `${flags.first_digit} hub`,
    waiting: prompts,
    text: line?.text ?? null,
    buttons: line?.buttons ?? [],
    zone: chats === null ? null : zone(chats, columns),
    failed: count > 0 ? failedText(count) : null,
  }
}

async function press($: EngineInterface, button: ShownButton) {
  const done = button.prints ? await hubctl($, button.args) : await change($, button.args)
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
  await tellHubctl($, got, (await read($, band)).screen)
  await askRelay($)
}

// The permission relay runs in the coordinator chat only. hubctl keeps the prompts that wait in
// other chats and answers none of them. The mod asks it at a session start, at a main turn end,
// and every RELAY_TICK while hubctl says this chat is the coordinator. It opens one dialog at a
// time, only while the chat is idle. The buttons only open that chat's tab or put the prompt off.
const RELAY_TICK = 2000
// Each prompt the user dismissed or answered with typed text, passed over until /hub relay.
const skipped = new Set<string>()
let relayTimer: Timer | null = null
let asking = false
let dialogOpen = false
let questionsOpen = 0

// Asks hubctl what waits, when the last screen read says the relay runs here.
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
    const done = await hubctl($, args, undefined, SCREEN_TIMEOUT)
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
      const done = await hubctl($, ['relay', 'pick', dialog.id, label, '--session', session])
      if (done.exitCode !== 0) {
        $.ui.toast(firstLine(done.stderr || done.stdout))
      }
    } catch {
      // hubctl did not run, so nothing was picked. The next tick shows the prompt again.
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
  const done = await hubctl($, ['relay', 'again', '--session', await $.session.id()])
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

// Runs the words after a command through hubctl and answers with what it printed. A change
// runs only when the user typed the command or sent it through the bridge.
async function run($: EngineInterface, e: CommandRunInput, typed: string): Promise<{ text: string }> {
  const args = words(e.args)
  const asked = args.length > 0 ? args : ['show']
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
    const done = changing ? await change($, asked) : await hubctl($, asked)
    text = (done.exitCode === 0 ? done.stdout : done.stderr || done.stdout).trimEnd()
  } catch (error) {
    text = `hubctl did not run: ${error instanceof Error ? error.message : String(error)}`
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

// /hubctl from the user runs the skill. From anywhere else the mod answers in its place, so the
// skill's text and its Bash call never reach the model. A read runs hubctl, and a change is refused.
const guard: Hook<'command.run'> = async ($, e, next) => {
  if (fromUser(e.origin)) {
    return next(e)
  }
  return run($, e, SKILL_TYPED)
}

// Claude Code skips a guard that fails and runs the command anyway. So when the guard fails
// before the skill starts, this refuses the command.
const guardFailed: CatchHandler<Hook<'command.run'>> = ($, e, next) =>
  next.called ? next(e) : { text: 'Nothing ran, because hub could not check where this /hubctl came from.' }

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

  on('command.run', { command: COMMAND }, answer)
  on('command.run', { command: FALLBACK }, answer)
  on('command.run', { command: SKILL }, guard).catch(guardFailed)

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
    const separator = <Text dimColor>{SEPARATOR}</Text>

    return (
      <Box flexDirection="row" width={e.props.bodyColumns}>
        <Text bold>{line.label}</Text>
        {line.waiting !== '' && separator}
        {line.waiting !== '' && <Text key="waiting">{line.waiting}</Text>}
        {line.zone !== null && separator}
        {line.zone !== null && (
          <Box key="chats" width={line.zone.width} flexShrink={0}>
            <Text wrap="truncate-end">{line.zone.text}</Text>
          </Box>
        )}
        {line.text !== null && separator}
        {line.text !== null && (
          <Box flexGrow={1} flexShrink={1}>
            <Text wrap="truncate-end">{line.text}</Text>
          </Box>
        )}
        {line.buttons.map(button => (
          <Button key={button.key} label={button.label} hotkey={button.hotkey} onPress={() => press($, button)} />
        ))}
        {line.failed !== null && separator}
        {line.failed !== null && (
          <Text key="failed" color="red">
            {line.failed}
          </Text>
        )}
      </Box>
    )
  })
}
