// The feature picker pane, run by `claude plugin test plugins/hub`.
// As in hub.test.tsx, the hooks registered here stand for the engine, and process.run answers
// as hubctl would, with the full day 9 model from picker-day9.ts.
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, PromptOrigin } from 'claude-code'

import type { HubPicker, HubPickerRow, HubPickerScreen, HubStripLine } from '../types'
import { KEYS, LEGEND } from '../picker-texts'
import { DAY9, DAY9_NOW } from './day9'
import { PICKER_DAY9 } from './picker-day9'
import { landingTop, markStep, placeFor, ringKeys, ringMoves, undoLanding, windowOf } from '../picker'

const PICKER = 'hub-picker'

function pane(bodyColumns = 100, bodyRows = 30) {
  return {
    plugin: 'hub',
    component: 'Pane',
    requestId: PICKER,
    surface: 'terminal',
    props: {
      title: 'hub features',
      isFocused: true,
      bodyColumns,
      placement: 'inline',
      scroll: { offset: 0, bodyRows },
      view: {},
    },
  } as const
}

const TYPED: PromptOrigin = { kind: 'composer' }

// A body taller than the whole list with every fold open, so the list is drawn whole.
const TALL = 400

// A 40-column terminal gives the pane 36 columns and 13 rows of body, as the pane checks saw.
const NARROW = { columns: 36, rows: 13 }

type Answer = { exitCode: number; stdout: string; stderr: string }

function ok(stdout: string): Answer {
  return { exitCode: 0, stdout, stderr: '' }
}

function copy(): HubPickerScreen {
  return JSON.parse(JSON.stringify(PICKER_DAY9)) as HubPickerScreen
}

function rowsOf(model: HubPickerScreen) {
  const found = new Map<string, HubPickerRow>()
  for (const family of model.families) {
    for (const part of family.parts) {
      found.set(part.id, part)
      for (const child of part.children) {
        found.set(child.id, child)
      }
    }
  }
  return found
}

// A hubctl that keeps the full model. A change sets the row and adds its strip line, newest
// first, and an undo adds an undo line. The band reads the day 9 model throughout.
function hubctl() {
  const model = copy()
  const calls: { args: string; stdin: string | undefined }[] = []
  let made = 0
  const answer = (args: string[], stdin: string | undefined): Answer => {
    calls.push({ args: args.join(' '), stdin })
    if (args[0] === 'screen') {
      return ok(JSON.stringify(args.includes('--all') ? model : DAY9))
    }
    if (args[0] === 'on' || args[0] === 'off' || args[0] === 'set' || args[0] === 'undo') {
      made += 1
      const row = rowsOf(model).get(args[1])
      if (row !== undefined) {
        row.effective = args[0] === 'set' ? args[2] : args[0]
        row.runs = row.effective !== 'off' && row.waits_for.length === 0
      }
      const line: HubStripLine = {
        id: `pick${made}`,
        kind: args[0] === 'undo' ? 'undo' : 'change',
        mark: args[0] === 'off' ? '-' : '+',
        time: '2026-11-09T12:00:00Z',
        text: `you set ${args[1]}`,
        reason: '',
        buttons: [],
      }
      model.strip = [line, ...model.strip]
      return ok(`${args[1]}  ${args[0]}\n`)
    }
    return ok('')
  }
  return { model, calls, answer }
}

// Starts a session whose hubctl is `fake`, opens the picker as the user would, and mounts its pane.
async function open($: Engine, on: On, fake = hubctl(), bodyColumns = 100, bodyRows = 30) {
  const written: string[] = []
  const landed: string[] = []
  const toasts: string[] = []
  const opened: unknown[] = []
  let stored: HubPicker | undefined
  const clock = mock.clock(on, { now: DAY9_NOW })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('fs.write', (_, e) => {
    written.push(e.path)
    return { value: undefined }
  })
  on('ui.open', (_, e) => {
    opened.push(e)
    return { value: { isPlaced: true } }
  })
  // The mod's own $.ui.focus and $.ui.scroll have no stand-in in the test kit, so the landing is
  // checked through the pure functions and the state the mod stored.
  on('ui.focus', (_, e) => {
    landed.push(e.element ?? '')
    return {}
  })
  on('state.set', { plugin: 'hub', key: 'picker' }, (_, e, next) => {
    stored = e.value as HubPicker
    return next(e)
  })
  on('ui.toast', (_, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('process.run', (_, e) => ({ value: fake.answer(e.argv.slice(3), e.init?.stdin) }))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await clock.settle()
  const result = await $.command.run({ command: 'hub', args: 'pick', origin: TYPED })
  const ui = await $.ui.mount(pane(bodyColumns, bodyRows))
  return { ui, fake, written, toasts, opened, result, landed, stored: () => stored }
}

// The person moving the pane's focus ring with Tab or the arrows.
async function focus($: Engine, element: string) {
  await $.ui.focus({ component: 'Pane', requestId: PICKER, plugin: 'hub', element, origin: { kind: 'person' } })
}

type Mounted = Awaited<ReturnType<typeof open>>['ui']

async function labels(ui: Mounted) {
  const controls = [...(await ui.findAll({ type: 'Button' })), ...(await ui.findAll({ type: 'Select' }))]
  return controls.map(control => String(control.props.label))
}

async function partLabels(ui: Mounted) {
  return (await ui.findAll({ type: 'Button' }))
    .filter(button => button.key?.startsWith('row:'))
    .map(button => String(button.props.label))
}

function changes(fake: ReturnType<typeof hubctl>) {
  return fake.calls.filter(call => !call.args.startsWith('screen ')).map(call => call.args)
}

test('/hub pick opens the picker pane with the keyboard, and it reads the full model', async ($, on) => {
  const { result, opened, fake, ui } = await open($, on)

  expect(result.text).toBeUndefined()
  expect(opened).toEqual([{ id: PICKER, title: 'hub features', focus: true, closeOnEscape: true, rows: 30 }])
  expect(fake.calls.map(call => call.args)).toContain('screen --json --all')
  await ui.unmount()
})

test("the band's features button opens the picker too", async ($, on) => {
  const { ui, opened } = await open($, on)
  await ui.unmount()
  opened.length = 0

  const band = await $.ui.mount({
    plugin: 'hub',
    component: 'AbovePrompt',
    surface: 'terminal',
    props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} },
  })
  const features = await band.find({ type: 'Button', key: 'features' })
  expect([features?.props.label, features?.props.hotkey]).toEqual(['features', 'f'])
  await band.press({ key: 'features' })
  expect(opened).toEqual([{ id: PICKER, title: 'hub features', focus: true, closeOnEscape: true, rows: 30 }])
  await band.unmount()
})

test('the picker rows equal the model, and only the first family with a new or waiting part starts open', async ($, on) => {
  const { ui } = await open($, on)

  const families = (await ui.findAll({ type: 'Button' })).filter(button => button.key?.startsWith('family:'))
  expect(families.map(button => button.props.label)).toEqual(
    PICKER_DAY9.families.map(family => `${family.family === 'tiers' ? '▾' : '▸'} ${family.label ?? family.family}`),
  )
  const tiers = PICKER_DAY9.families.find(family => family.family === 'tiers')
  expect(await partLabels(ui)).toEqual(
    tiers?.parts.map(part => `${part.runs ? '[x]' : '[ ]'} ${part.name}`),
  )
  expect((await ui.find({ type: 'Text', text: /^3 of 11 on$/ }))?.text).toBe('3 of 11 on')

  const tierRows = await partLabels(ui)
  await ui.press({ key: 'family:chats' })
  expect(await partLabels(ui)).toEqual([
    ...tierRows,
    '[ ] chat_board',
    '[ ] chat_names',
    '[ ] coordinator',
    '[ ] permission_relay',
    '[ ] queue',
    '[ ] restart',
  ])
  expect((await ui.find({ type: 'Button', key: 'family:tiers' }))?.props.label).toBe('▾ tiers')
  await ui.unmount()
})

test('with nothing new, the first family with a part that is on but waits starts open', async ($, on) => {
  const fake = hubctl()
  const rows = rowsOf(fake.model)
  for (const family of fake.model.families) {
    for (const part of family.parts) {
      part.new = false
    }
  }
  const relay = rows.get('permission_relay')
  if (relay !== undefined) {
    Object.assign(relay, { effective: 'on', runs: false, waits_for: ['coordinator'] })
  }
  const { ui } = await open($, on, fake)

  expect((await ui.find({ type: 'Button', key: 'family:chats' }))?.props.label).toBe('▾ chats')
  expect((await ui.find({ type: 'Button', key: 'row:permission_relay' }))?.props.label).toBe('[~] permission_relay')
  expect((await ui.find({ type: 'Button', key: 'family:tiers' }))?.props.label).toBe('▸ tiers')
  await ui.unmount()
})

test('a new part shows new, and so does its family', async ($, on) => {
  const { ui } = await open($, on)

  const shown = await ui.findAll({ type: 'Text', text: /^new$/ })
  expect(shown).toHaveLength(2)
  await ui.press({ key: 'family:tiers' })
  expect(await ui.findAll({ type: 'Text', text: /^new$/ })).toHaveLength(1)
  await ui.unmount()
})

test('instructions sit behind one row per part, and value lines are not buttons', async ($, on) => {
  // A body tall enough for every row, so the folds it opens are all drawn.
  const { ui } = await open($, on, hubctl(), 100, TALL)

  expect((await ui.find({ type: 'Button', key: 'more:builder' }))?.props.label).toBe('▸ 5 more')
  await ui.press({ key: 'more:builder' })
  expect((await ui.find({ type: 'Button', key: 'more:builder' }))?.props.label).toBe('▾ less')
  const model = await ui.find({ type: 'Select', key: 'row:builder.model' })
  expect(model?.props.value).toBe('opus')
  expect((model?.props.options as { value: string }[]).map(option => option.value)).toEqual(['haiku', 'sonnet', 'opus'])
  expect((await ui.find({ type: 'Button', key: 'instructions:builder' }))?.props.label).toBe('▸ instructions  2 of 2 on')
  expect(await ui.find({ key: 'row:builder.instructions.read_first' })).toBeUndefined()

  await ui.press({ key: 'instructions:builder' })
  expect((await ui.find({ type: 'Button', key: 'row:builder.instructions' }))?.props.label).toBe('  [x] instructions')
  expect((await ui.find({ type: 'Button', key: 'row:builder.instructions.read_first' }))?.props.label).toBe(
    '    [x] read_first',
  )

  await ui.press({ key: 'family:chats' })
  await ui.press({ key: 'more:coordinator' })
  const name = await ui.find({ type: 'Text', text: /^ {2}name {2}HUB$/ })
  expect(name?.text).toBe('  name  HUB')
  expect((await ui.find({ type: 'Text', text: /set coordinator\.name/ }))?.text).toBe('/hub set coordinator.name <value>')
  expect(await ui.find({ key: 'row:coordinator.name' })).toBeUndefined()
  expect(await ui.find({ key: 'row:coordinator.work_guard.allowed' })).toBeUndefined()
  await ui.unmount()
})

test('a press on an off row runs on with a fresh ticket, and a press on an on row runs off', async ($, on) => {
  const { ui, fake, written } = await open($, on)

  await ui.press({ key: 'row:researcher' })
  await ui.press({ key: 'row:scout' })

  expect(changes(fake)).toEqual(['on researcher', 'off scout'])
  const tickets = fake.calls.filter(call => !call.args.startsWith('screen ')).map(call => call.stdin ?? '')
  expect(tickets.every(line => /^[0-9a-f]{64}\n$/.test(line))).toBe(true)
  expect(tickets[0]).not.toBe(tickets[1])
  expect(written.filter(path => path.startsWith(`${DAY9.doors}/`))).toHaveLength(2)
  expect((await ui.find({ type: 'Button', key: 'row:researcher' }))?.props.label).toBe('[x] researcher')
  expect((await ui.find({ type: 'Button', key: 'row:scout' }))?.props.label).toBe('[ ] scout')
  // The band reads the model again after each change.
  expect(fake.calls.filter(call => call.args === 'screen --json --band')).toHaveLength(3)
  await ui.unmount()
})

test('a protected row lowers only through a press, which carries a ticket', async ($, on) => {
  const { ui, fake } = await open($, on)

  await ui.press({ key: 'family:gates' })
  await ui.press({ key: 'more:self_protection' })
  await ui.press({ key: 'family:tiers' })
  await ui.press({ key: 'family:gates' })
  await ui.press({ key: 'family:gates' })
  expect(changes(fake)).toEqual([])

  await ui.press({ key: 'row:self_protection' })
  const lowered = fake.calls.filter(call => call.args === 'off self_protection')
  expect(lowered).toHaveLength(1)
  expect(lowered[0].stdin).toMatch(/^[0-9a-f]{64}\n$/)
  await ui.unmount()
})

test('a select runs set with the picked mode', async ($, on) => {
  const { ui, fake } = await open($, on)

  await ui.press({ key: 'family:gates' })
  const gate = await ui.find({ type: 'Select', key: 'row:public_text_gate' })
  expect(gate?.props.label).toBe('[x] public_text_gate')
  expect(gate?.props.value).toBe('warn')
  expect((gate?.props.options as { value: string }[]).map(option => option.value)).toEqual(['off', 'warn', 'block'])

  await ui.select({ key: 'row:public_text_gate', value: 'block' })
  expect(changes(fake)).toEqual(['set public_text_gate block'])
  expect(fake.calls.find(call => call.args === 'set public_text_gate block')?.stdin).toMatch(/^[0-9a-f]{64}\n$/)
  await ui.unmount()
})

test('a child that is on under an off part shows [~] and says what it waits for', async ($, on) => {
  const { ui } = await open($, on)

  await ui.press({ key: 'family:chats' })
  await ui.press({ key: 'more:coordinator' })
  expect((await ui.find({ type: 'Button', key: 'row:coordinator.start_nudge' }))?.props.label).toBe('  [~] start_nudge')
  expect(await ui.find({ type: 'Text', text: /is on, but waits for/ })).toBeUndefined()

  await focus($, 'row:coordinator.start_nudge')
  expect((await ui.find({ type: 'Text', text: /is on, but waits for/ }))?.text).toBe(
    'coordinator.start_nudge is on, but waits for coordinator.',
  )
  await focus($, 'row:coordinator')
  expect(await ui.find({ type: 'Text', text: /is on, but waits for/ })).toBeUndefined()
  await ui.unmount()
})

test('u takes back the changes this visit made, newest first, and shows only while there is one', async ($, on) => {
  const { ui, fake } = await open($, on)

  expect(await ui.find({ key: 'undo' })).toBeUndefined()
  await ui.press({ key: 'row:researcher' })
  await ui.press({ key: 'row:scout' })
  const undo = await ui.find({ type: 'Button', key: 'undo' })
  expect([undo?.props.label, undo?.props.hotkey]).toEqual(['undo', 'u'])

  await ui.press({ key: 'undo' })
  await ui.press({ key: 'undo' })
  expect(changes(fake)).toEqual(['on researcher', 'off scout', 'undo pick2', 'undo pick1'])
  expect(fake.calls.find(call => call.args === 'undo pick2')?.stdin).toMatch(/^[0-9a-f]{64}\n$/)
  expect(await ui.find({ key: 'undo' })).toBeUndefined()
  await ui.unmount()
})

test('a change hubctl refuses says why in a toast, and the row keeps its mark', async ($, on) => {
  const fake = hubctl()
  const answer = fake.answer
  fake.answer = (args, stdin) =>
    args[0] === 'on' ? { exitCode: 1, stdout: '', stderr: 'researcher needs a model\nmore\n' } : answer(args, stdin)
  const { ui, toasts } = await open($, on, fake)

  await ui.press({ key: 'row:researcher' })
  expect(toasts).toEqual(['researcher needs a model'])
  expect((await ui.find({ type: 'Button', key: 'row:researcher' }))?.props.label).toBe('[ ] researcher')
  expect(await ui.find({ key: 'undo' })).toBeUndefined()
  await ui.unmount()
})

test('when hubctl does not answer, the pane says so in one line', async ($, on) => {
  const fake = hubctl()
  const answer = fake.answer
  fake.answer = (args, stdin) =>
    args.includes('--all') ? { exitCode: 2, stdout: '', stderr: 'no state folder\n' } : answer(args, stdin)
  const { ui } = await open($, on, fake)

  expect((await ui.find({ type: 'Text', text: /did not answer/ }))?.text).toBe('✗ hub did not answer: no state folder')
  expect(await ui.findAll({ type: 'Button' })).toHaveLength(0)
  await ui.unmount()
})

for (const from of [
  { kind: 'plugin', name: 'other' },
  { kind: 'peer' },
  { kind: 'sdk' },
] as PromptOrigin[]) {
  test(`a pick from ${from.kind} opens nothing and says only the user can open it`, async ($, on) => {
    const opened: unknown[] = []
    const clock = mock.clock(on, { now: DAY9_NOW })
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    on('command.register', (_, e) => ({ value: { command: e.name } }))
    on('ui.open', (_, e) => {
      opened.push(e)
      return { value: { isPlaced: true } }
    })
    on('process.run', () => ({ value: ok(JSON.stringify(DAY9)) }))
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    await clock.settle()

    const result = await $.command.run({ command: 'hub', args: 'pick', origin: from })

    expect(result.text).toBe('Only you can open the picker, by typing /hub pick at the prompt.')
    expect(opened).toEqual([])
  })
}

test('at 40 columns every name fits, and no does text is drawn', async ($, on) => {
  // A body tall enough for every row, so the whole list is drawn at once and each label is read.
  const { ui } = await open($, on, hubctl(), 40, TALL)

  for (const family of PICKER_DAY9.families) {
    if ((await ui.find({ key: `family:${family.family}` }))?.props.label !== `▾ ${family.label ?? family.family}`) {
      await ui.press({ key: `family:${family.family}` })
    }
    for (const part of family.parts) {
      if (part.children.length > 0) {
        await ui.press({ key: `more:${part.id}` })
      }
      if (await ui.find({ key: `instructions:${part.id}` })) {
        await ui.press({ key: `instructions:${part.id}` })
      }
    }
    const shown = await labels(ui)
    for (const label of shown) {
      expect(label.length).toBeLessThanOrEqual(40)
    }
    for (const part of family.parts) {
      expect(shown).toContain(`${part.effective === 'off' ? '[ ]' : part.runs ? '[x]' : '[~]'} ${part.name}`)
      expect(await ui.find({ type: 'Text', text: part.does })).toBeUndefined()
    }
  }
  await ui.unmount()
})

async function keysComeFirst(ui: Mounted) {
  const order = await ui.findAll({})
  const keys = order.findIndex(node => node.type === 'Text' && node.text === KEYS)
  const list = order.findIndex(node => node.key?.startsWith('family:'))
  return keys < list
}

type Drawn = { type: string; key?: string; props: Record<string, unknown>; children?: unknown[] }

function textOf(element: Drawn) {
  return (element.children ?? []).filter(child => typeof child === 'string').join('')
}

function children(element: Drawn) {
  return (element.children ?? []).filter((child): child is Drawn => typeof child === 'object' && child !== null)
}

// The columns an element takes on one line. A plain Button with a hotkey draws as `u: undo`.
function lineWidth(element: Drawn): number {
  if (element.type === 'Button' || element.type === 'Select') {
    return String(element.props.label).length + (element.props.hotkey === undefined ? 0 : 3)
  }
  if (element.type === 'Text') {
    return textOf(element).length
  }
  const inside = children(element)
  const gap = element.props.flexDirection === 'row' ? Number(element.props.columnGap ?? 0) * Math.max(0, inside.length - 1) : 0
  return inside.reduce((sum, child) => sum + lineWidth(child), gap)
}

// The rows a text takes when the terminal wraps it at spaces.
function wrapped(text: string, width: number) {
  let count = 1
  let used = 0
  for (const word of text.split(' ')) {
    if (used > 0 && used + 1 + word.length > width) {
      count += 1
      used = word.length
    } else {
      used += (used > 0 ? 1 : 0) + word.length
    }
  }
  return count
}

// The rows a drawn element takes in a body this wide: a column sums its children, a row takes its
// tallest child, and a Text in a row gets the width its siblings leave.
function height(element: Drawn, width: number): number {
  if (element.type === 'Button' || element.type === 'Select') {
    return 1
  }
  if (element.type === 'Text') {
    return element.props.wrap === 'truncate-end' ? 1 : wrapped(textOf(element), width)
  }
  const inside = children(element)
  if (element.props.flexDirection === 'row') {
    const gap = Number(element.props.columnGap ?? 0) * Math.max(0, inside.length - 1)
    return Math.max(
      0,
      ...inside.map(child => {
        const others = inside.filter(other => other !== child).reduce((sum, other) => sum + lineWidth(other), 0)
        return height(child, Math.max(1, width - others - gap))
      }),
    )
  }
  return inside.reduce((sum, child) => sum + height(child, width), 0)
}

async function tallness(ui: Mounted) {
  return height((await ui.drawn()) as unknown as Drawn, NARROW.columns)
}

// The keys of the drawn controls, in the order the ring walks them.
async function ringOrder(ui: Mounted) {
  return (await controls(ui)).map(node => String(node.key))
}

// The engine's end of the pane's focus ring, for the person's Tab into the pane and the arrows.
// While the drawn tree is taller than the body, an arrow scrolls it and the ring stays. While it
// fits, the ring goes to the next or the previous control, round from the last to the first, and
// the mod's focus hook keeps that move, denies it or sends it to another control. When the mod
// draws again, the ring keeps its place among the controls, not its key.
function personRing($: Engine, ui: Mounted, landed: string[]) {
  let place = 0
  const key = async () => (await ringOrder(ui))[place]
  const move = async (order: string[], to: number) => {
    landed.length = 0
    await focus($, order[to])
    if (landed.length > 0) {
      place = order.indexOf(landed.at(-1) ?? '')
    }
    return key()
  }
  return {
    key,
    enter: async () => move(await ringOrder(ui), 0),
    arrow: async (way: 'up' | 'down') => {
      if ((await tallness(ui)) > NARROW.rows) {
        return key()
      }
      const order = await ringOrder(ui)
      return move(order, (place + (way === 'down' ? 1 : order.length - 1)) % order.length)
    },
  }
}

type Ring = ReturnType<typeof personRing>

// Arrows the ring until it is on `key`. False when it never gets there.
async function walkTo(ring: Ring, key: string, way: 'up' | 'down' = 'down') {
  for (let step = 0; step < 100; step += 1) {
    if ((await ring.key()) === key) {
      return true
    }
    await ring.arrow(way)
  }
  return false
}

async function marks(ui: Mounted) {
  const order = await ringOrder(ui)
  return { above: order.some(key => key.startsWith('above:')), below: order.some(key => key.startsWith('below:')) }
}

// The list's rows, as the ▲ or ▼ key carries them.
async function roomOf(ui: Mounted) {
  const mark = (await ringOrder(ui)).find(key => /^(above|below):\d+$/.test(key))
  return Number(mark?.split(':')[1])
}

// Opens a family and every fold in it.
async function unfold(ui: Mounted, family: string) {
  if ((await ui.find({ key: `family:${family}` }))?.props.label !== `▾ ${family}`) {
    await ui.press({ key: `family:${family}` })
  }
  for (const part of PICKER_DAY9.families.find(group => group.family === family)?.parts ?? []) {
    if (part.children.length > 0) {
      await ui.press({ key: `more:${part.id}` })
    }
    if (await ui.find({ key: `instructions:${part.id}` })) {
      await ui.press({ key: `instructions:${part.id}` })
    }
  }
}

const LIST_KEY = /^(family|row|more|instructions|above|below):/

function words(text: string) {
  return text.replace(/\s+/g, ' ').trim()
}

// What a person sees of the footer: the legend, the keys and undo all after the last list row,
// the keys line no wider than the body, and everything within the body's rows.
async function footer(ui: Mounted) {
  const drawn = await ui.findAll({})
  const list = drawn.findLastIndex(node => LIST_KEY.test(node.key ?? ''))
  const after = words(
    drawn
      .slice(list + 1)
      .filter(node => node.type === 'Text')
      .map(node => node.text ?? '')
      .join(' '),
  )
  const keys = await ui.find({ key: 'keys' })
  return {
    legend: after.includes(words(LEGEND)),
    keys: after.includes(words(KEYS)),
    undo: drawn.findIndex(node => node.key === 'undo') > list,
    keysFit: keys !== undefined && lineWidth(keys as unknown as Drawn) <= NARROW.columns,
    fits: (await tallness(ui)) <= NARROW.rows,
  }
}

test('at 40 columns the legend, the keys and undo come after the list and stay in view wherever the ring is', { timeoutMs: 20000 }, async ($, on) => {
  const { ui, landed } = await open($, on, hubctl(), NARROW.columns, NARROW.rows)
  const ring = personRing($, ui, landed)
  await ring.enter()

  expect(await walkTo(ring, 'row:researcher')).toBe(true)
  await ui.press({ key: 'row:researcher' })
  const inView = { legend: true, keys: true, undo: true, keysFit: true, fits: true }
  expect(await footer(ui)).toEqual(inView)

  expect(await walkTo(ring, `family:${PICKER_DAY9.families.at(-1)?.family}`)).toBe(true)
  expect(await footer(ui)).toEqual(inView)
  await ui.unmount()
})

test('at 40 columns Up and Down walk the ring one control at a time through the whole list, past ▼ and ▲, and round at both ends', { timeoutMs: 30000 }, async ($, on) => {
  // A second value line under coordinator, so two value lines sit between two controls.
  const fake = hubctl()
  const coordinator = rowsOf(fake.model).get('coordinator') as HubPickerScreen['families'][number]['parts'][number]
  const name = coordinator.children.find(child => child.id === 'coordinator.name')
  expect(name).toBeDefined()
  coordinator.children.splice(1, 0, { ...name!, id: 'coordinator.motto', name: 'motto' })
  const { ui: whole, landed } = await open($, on, fake, NARROW.columns, TALL)
  await unfold(whole, 'tiers')
  await unfold(whole, 'chats')
  const order = await ringOrder(whole)
  await whole.unmount()
  expect(order).toContain('row:builder.model')
  expect(order).toContain('row:coordinator.start_nudge')

  const ui = await $.ui.mount(pane(NARROW.columns, NARROW.rows))
  const ring = personRing($, ui, landed)
  expect(await ring.enter()).toBe(order[0])
  expect(await marks(ui)).toEqual({ above: false, below: true })
  for (const next of order.slice(1)) {
    expect(await ring.arrow('down')).toBe(next)
    expect(await tallness(ui)).toBeLessThanOrEqual(NARROW.rows)
  }
  expect(await marks(ui)).toEqual({ above: true, below: false })

  // Down on the last row goes round to the first, and Up on the first goes round to the last.
  expect(await ring.arrow('down')).toBe(order[0])
  expect(await marks(ui)).toEqual({ above: false, below: true })
  expect(await ring.arrow('up')).toBe(order.at(-1))
  expect(await marks(ui)).toEqual({ above: true, below: false })

  for (const previous of order.slice(0, -1).reverse()) {
    expect(await ring.arrow('up')).toBe(previous)
    expect(await tallness(ui)).toBeLessThanOrEqual(NARROW.rows)
  }
  expect(await marks(ui)).toEqual({ above: false, below: true })
  await ui.unmount()
})

test('the list window keeps its last row for ▼, and a row for ▲ while lines are hidden above', async () => {
  expect(windowOf(10, 5, 0)).toEqual({ start: 0, end: 4, above: false, below: true })
  expect(windowOf(10, 5, 3)).toEqual({ start: 3, end: 6, above: true, below: true })
  expect(windowOf(10, 5, 8)).toEqual({ start: 8, end: 10, above: true, below: false })
  expect(windowOf(3, 5, 0)).toEqual({ start: 0, end: 3, above: false, below: false })
  expect(windowOf(10, 5, 40)).toEqual({ start: 9, end: 10, above: true, below: false })
})

test('at 140 columns the keys line is drawn after the list', async ($, on) => {
  const { ui } = await open($, on, hubctl(), 140)

  expect(await keysComeFirst(ui)).toBe(false)
  await ui.unmount()
})

// The controls in drawn order. The pane's focus ring keeps its place in this order across a
// redraw, not its key, so a control added above the ring moves the ring onto another row.
async function controls(ui: Mounted) {
  return (await ui.findAll({})).filter(node => node.type === 'Button' || node.type === 'Select')
}

// Enter on whatever control holds the ring's place.
async function pressAt(ui: Mounted, place: number) {
  await ui.press({ key: (await controls(ui))[place].key })
}

for (const bodyColumns of [40, 140]) {
  test(`at ${bodyColumns} columns no control comes before the first family row, also once undo shows`, async ($, on) => {
    const { ui } = await open($, on, hubctl(), bodyColumns)

    await ui.press({ key: 'row:researcher' })
    expect(await ui.find({ type: 'Button', key: 'undo' })).toBeDefined()
    const drawn = await ui.findAll({})
    const first = drawn.findIndex(node => node.key?.startsWith('family:'))
    expect(first).toBeGreaterThan(0)
    expect(drawn.slice(0, first).filter(node => node.type === 'Button' || node.type === 'Select')).toEqual([])
    await ui.unmount()
  })
}

test('at 40 columns a second Enter on the same place takes the same row back', async ($, on) => {
  const { ui, fake } = await open($, on, hubctl(), 40)
  const place = (await controls(ui)).findIndex(node => node.key === 'row:researcher')

  await pressAt(ui, place)
  await pressAt(ui, place)

  expect(changes(fake)).toEqual(['on researcher', 'off researcher'])
  await ui.unmount()
})

for (const bodyColumns of [40, 140]) {
  test(`at ${bodyColumns} columns the last undo lands on the row it took back`, async ($, on) => {
    const { ui, fake, stored } = await open($, on, hubctl(), bodyColumns)
    await ui.press({ key: 'row:researcher' })
    await ui.press({ key: 'row:scout' })

    // With two undos left the ring stays on the undo button.
    expect(undoLanding(stored()!)).toBeUndefined()
    await ui.press({ key: 'undo' })
    expect(undoLanding(stored()!)).toBe('row:researcher')
    expect(ringMoves('row:researcher')).toEqual({
      focus: { requestId: PICKER, key: 'row:researcher' },
      scroll: { in: PICKER, to: { key: 'row:researcher' } },
    })
    await ui.press({ key: 'undo' })
    expect(changes(fake)).toEqual(['on researcher', 'off scout', 'undo pick2', 'undo pick1'])
    expect(await ui.find({ key: 'undo' })).toBeUndefined()
    await ui.unmount()
  })
}

// At 40x13: the ring walks to restart in chats, a press changes it, and the ring walks back up to
// the top, so the list no longer draws restart.
async function changeRestartThenGoTop($: Engine, on: On) {
  const opened = await open($, on, hubctl(), NARROW.columns, NARROW.rows)
  const ring = personRing($, opened.ui, opened.landed)
  await ring.enter()
  expect(await walkTo(ring, 'family:chats')).toBe(true)
  await opened.ui.press({ key: 'family:chats' })
  expect(await walkTo(ring, 'row:restart')).toBe(true)
  await opened.ui.press({ key: 'row:restart' })
  expect(await walkTo(ring, 'family:setup', 'up')).toBe(true)
  expect(await opened.ui.find({ key: 'row:restart' })).toBeUndefined()
  return opened
}

test('at 40x13 the last undo of a row the list moved past brings the list back and lands the ring on that row', { timeoutMs: 20000 }, async ($, on) => {
  const { ui, stored } = await changeRestartThenGoTop($, on)
  const room = await roomOf(ui)
  const before = stored()!
  const drawnBefore = await ringOrder(ui)
  expect(ringKeys(before, room)).toEqual(drawnBefore)

  const landing = undoLanding(before)
  expect(landing).toBe('row:restart')
  expect(ringMoves(landing!).scroll).toEqual({ in: 'hub-picker', to: { key: 'row:restart' } })
  expect(landingTop(before, landing!, room)).toBeDefined()

  await ui.press({ key: 'undo' })
  const after = stored()!
  const drawnAfter = await ringOrder(ui)
  expect(ringKeys(after, room)).toEqual(drawnAfter)
  // The ring goes to this control before the new drawing, and keeps its place there.
  const place = placeFor(before, after, room, 'row:restart')
  expect(drawnBefore).toContain(place)
  expect(drawnAfter[drawnBefore.indexOf(place!)]).toBe('row:restart')
  expect(await ui.find({ key: 'undo' })).toBeUndefined()
  await ui.unmount()
})

test('a move onto ▲ or ▼ from a control the list does not draw lands on the last or the first drawn list row, whichever side the arrow came from', { timeoutMs: 20000 }, async ($, on) => {
  const { ui, stored } = await changeRestartThenGoTop($, on)
  const room = await roomOf(ui)
  const state = { ...stored()!, focused: 'row:restart' }
  const rows = ringKeys(state, room).filter(key => LIST_KEY.test(key) && !/^(above|below):/.test(key))
  expect(rows.length).toBeGreaterThan(2)
  expect(markStep(state, 'down', room)).toEqual({ land: rows.at(-1) })
  expect(markStep(state, 'up', room)).toEqual({ land: rows[0] })
  await ui.unmount()
})

test('after the last undo the stored focus is the landing row, so the narrow does line follows the ring', { timeoutMs: 20000 }, async ($, on) => {
  const { ui, stored } = await changeRestartThenGoTop($, on)
  expect(stored()!.focused).not.toBe('row:restart')

  await ui.press({ key: 'undo' })
  expect(stored()!.undo).toEqual([])
  expect(stored()!.focused).toBe('row:restart')
  const restart = rowsOf(PICKER_DAY9).get('restart')
  // The last rows of the footer are the notes, here the start of what restart does.
  const notes = words(
    (await ui.findAll({ type: 'Text' }))
      .slice(-2)
      .map(node => node.text ?? '')
      .join(' '),
  )
  expect(notes).toContain('Restarts the running chats')
  expect(words(restart?.does ?? '').startsWith(notes)).toBe(true)
  await ui.unmount()
})

test('a last undo of a row in a folded family lands on the family line', async ($, on) => {
  const { ui, stored } = await open($, on, hubctl(), 140)
  await ui.press({ key: 'family:chats' })
  await ui.press({ key: 'row:restart' })
  await ui.press({ key: 'family:chats' })

  expect(undoLanding(stored()!)).toBe('family:chats')
  await ui.press({ key: 'undo' })
  expect(await ui.find({ key: 'undo' })).toBeUndefined()
  await ui.unmount()
})

test('a last undo of a row in a closed part lands on the part row', async ($, on) => {
  const { ui, stored } = await open($, on, hubctl(), 140)
  await ui.press({ key: 'more:builder' })
  await ui.press({ key: 'instructions:builder' })
  await ui.press({ key: 'row:builder.instructions.read_first' })
  expect(undoLanding(stored()!)).toBe('row:builder.instructions.read_first')
  await ui.press({ key: 'more:builder' })

  expect(undoLanding(stored()!)).toBe('row:builder')
  await ui.unmount()
})

test('a last undo of an instruction row lands on the instructions fold while it is folded', async ($, on) => {
  const { ui, stored } = await open($, on, hubctl(), 140)
  await ui.press({ key: 'more:builder' })
  await ui.press({ key: 'instructions:builder' })
  await ui.press({ key: 'row:builder.instructions.read_first' })
  await ui.press({ key: 'instructions:builder' })

  expect(undoLanding(stored()!)).toBe('instructions:builder')
  await ui.unmount()
})

test('an undo stack stored as plain strings before rows were kept still undoes, and lands nowhere', async ($, on) => {
  // A value outlives a hot reload of the mod. A pane left open can still hold the old strings.
  on('state.set', { plugin: 'hub', key: 'picker' }, (_, e, next) => {
    const value = e.value as HubPicker
    const old = value.undo.map(entry => entry.change) as unknown as HubPicker['undo']
    return next({ ...e, value: { ...value, undo: old } })
  })
  const { ui, fake, stored } = await open($, on)

  await ui.press({ key: 'row:researcher' })
  expect(undoLanding(stored()!)).toBeUndefined()
  await ui.press({ key: 'undo' })
  expect(changes(fake)).toEqual(['on researcher', 'undo pick1'])
  await ui.unmount()
})

test('opening a family keeps the open family above it open, and the ring stays on its header', async ($, on) => {
  const { ui, fake } = await open($, on, hubctl(), 40)
  const tierRows = await partLabels(ui)
  const place = (await controls(ui)).findIndex(node => node.key === 'family:chats')

  await pressAt(ui, place)
  expect((await controls(ui))[place].key).toBe('family:chats')
  expect((await ui.find({ type: 'Button', key: 'family:tiers' }))?.props.label).toBe('▾ tiers')
  expect((await ui.find({ type: 'Button', key: 'family:chats' }))?.props.label).toBe('▾ chats')
  expect((await partLabels(ui)).slice(0, tierRows.length)).toEqual(tierRows)

  await pressAt(ui, place)
  expect((await ui.find({ type: 'Button', key: 'family:chats' }))?.props.label).toBe('▸ chats')
  expect(changes(fake)).toEqual([])
  await ui.unmount()
})

const FOLDS = ['family:', 'more:', 'instructions:']

// Presses every Button the picker draws and picks another option on every Select, in drawn order.
// A press that closes a fold is pressed again, so the walk reaches the rows inside it too. About
// 100 actions need more than the default 5 seconds.
for (const bodyColumns of [40, 100, 140]) {
  test(`at ${bodyColumns} columns a press or a pick on any control leaves every control above it as it was`, { timeoutMs: 20000 }, async ($, on) => {
    // A body tall enough for every row, so the walk reaches every control without moving the list.
    const { ui } = await open($, on, hubctl(), bodyColumns, TALL)
    const pressed: string[] = []
    const picked: string[] = []

    const act = async (before: Awaited<ReturnType<typeof controls>>, place: number) => {
      const key = String(before[place].key)
      if (before[place].type === 'Select') {
        const options = (before[place].props.options as { value: string }[]).map(option => option.value)
        const value = options.find(option => option !== before[place].props.value)
        expect(value).toBeDefined()
        await ui.select({ key, value: String(value) })
        picked.push(key)
      } else {
        await ui.press({ key })
        pressed.push(key)
      }
      const after = await controls(ui)
      expect({ key, above: after.slice(0, place).map(node => node.key) }).toEqual({
        key,
        above: before.slice(0, place).map(node => node.key),
      })
      if (key !== 'undo') {
        expect({ key, ring: after[place]?.key }).toEqual({ key, ring: key })
      }
      return after
    }

    let drawn = await controls(ui)
    for (let place = 0; place < drawn.length; place += 1) {
      drawn = await act(drawn, place)
      if (FOLDS.some(fold => drawn[place]?.key?.startsWith(fold)) && String(drawn[place].props.label).startsWith('▸')) {
        drawn = await act(drawn, place)
      }
    }

    const buttons = drawn.filter(node => node.type === 'Button').map(node => String(node.key))
    expect(buttons.filter(key => !pressed.includes(key))).toEqual([])
    expect(drawn.filter(node => node.type === 'Select').map(node => String(node.key)).filter(key => !picked.includes(key))).toEqual([])
    expect(picked).toHaveLength(9)
    expect(PICKER_DAY9.families.map(family => `family:${family.family}`).filter(key => !pressed.includes(key))).toEqual([])
    expect(pressed).toContain('undo')
    await ui.unmount()
  })
}

test('a picker state stored before families opened on their own still loads, with its one family open', async ($, on) => {
  // A value outlives a hot reload of the mod. A pane left open can still hold the old single
  // `family` value, which reads as one open family.
  let stored = false
  on('state.set', { plugin: 'hub', key: 'picker' }, (_, e, next) => {
    if (stored) {
      return next(e)
    }
    stored = true
    const { families: _open, ...earlier } = e.value as HubPicker
    return next({ ...e, value: { ...earlier, family: 'chats' } as unknown as HubPicker })
  })
  const { ui } = await open($, on)

  expect((await ui.find({ type: 'Button', key: 'family:chats' }))?.props.label).toBe('▾ chats')
  expect((await ui.find({ type: 'Button', key: 'family:tiers' }))?.props.label).toBe('▸ tiers')
  await ui.press({ key: 'family:gates' })
  expect((await ui.find({ type: 'Button', key: 'family:chats' }))?.props.label).toBe('▾ chats')
  expect((await ui.find({ type: 'Button', key: 'family:gates' }))?.props.label).toBe('▾ gates')
  await ui.press({ key: 'family:chats' })
  expect((await ui.find({ type: 'Button', key: 'family:chats' }))?.props.label).toBe('▸ chats')
  expect((await ui.find({ type: 'Button', key: 'family:gates' }))?.props.label).toBe('▾ gates')
  await ui.unmount()
})

test('at 100 columns a part row shows what it does', async ($, on) => {
  const { ui } = await open($, on)

  const builder = PICKER_DAY9.families[1].parts[0]
  expect((await ui.find({ type: 'Text', text: builder.does }))?.text).toBe(builder.does)
  await ui.unmount()
})

