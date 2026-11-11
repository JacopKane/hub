// hub's feature picker is a pane that lists every part by family, with its flags under it. Each
// press saves at once through hub's step, with a door ticket of its own, and `u` takes back the
// changes this visit made, newest first. register.tsx opens it for `/hub pick` and the band's
// features button.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On } from 'claude-code'

import {
  FEATURES_LABEL,
  KEYS,
  LEGEND,
  LESS_LABEL,
  NEW_MARK,
  PICKER_TITLE,
  UNDO_LABEL,
  failedLine,
  instructionsLabel,
  moreLabel,
  onCount,
  setLine,
  waitsLine,
} from './picker-texts'
import { COMMAND, SCREEN_TIMEOUT, change, refresh, runStep } from './register'
import type {
  HubCommand,
  HubFamily,
  HubPicker,
  HubPickerPart,
  HubPickerRow,
  HubPickerScreen,
  HubStripLine,
} from './types'

export { FEATURES_LABEL }

export const PICKER = 'hub-picker'

type Answer = { exitCode: number; stdout: string; stderr: string }


const CLOSED: HubPicker = {
  screen: null,
  failure: null,
  families: [],
  parts: [],
  instructions: [],
  focused: null,
  top: null,
  undo: [],
}

const picker = atom({ plugin: 'hub', key: 'picker' } as const, CLOSED)
// The name the slash command got, as register.tsx keeps it, for the line that says how to set a value.
const command = atom({ plugin: 'hub', key: 'command' } as const, { used: null, refused: {} } as HubCommand)

// Below this width a part row leaves out what it does, so every name fits.
const DOES_COLUMNS = 60

const UNDO_KEY = 'u'

// The pane draws the undo button as `u: undo`. The keys line leaves room for it beside the first
// line all the time, so the footer keeps its height when the button comes and goes.
const UNDO_COLUMNS = `${UNDO_KEY}: ${UNDO_LABEL}`.length + 2

// Rows kept under the keys for what the focused row waits for and, on a narrow pane, what it does.
const NOTE_ROWS = 2

// The list keeps at least a ▲ row, one line and a ▼ row, however short the pane is.
const LIST_ROWS = 3

type Mark = '[x]' | '[~]' | '[ ]'

// One row of the list. `ring` is the key of the control on it, or null for a value line, which
// the focus ring skips. `id` names the line, so the window can keep its first line by key.
type ListLine =
  | { id: string; ring: string; draw: 'family'; group: HubFamily; open: boolean }
  | { id: string; ring: string; draw: 'part'; part: HubPickerPart }
  | { id: string; ring: string; draw: 'more'; part: HubPickerPart; open: boolean }
  | { id: string; ring: string; draw: 'instructions'; part: HubPickerPart; open: boolean; on: number; total: number }
  | { id: string; ring: string | null; draw: 'row'; row: HubPickerRow }

function isOn(row: HubPickerRow) {
  return row.effective !== 'off' && row.effective !== false
}

function mark(row: HubPickerRow): Mark {
  if (!isOn(row)) {
    return '[ ]'
  }
  return row.runs ? '[x]' : '[~]'
}

// A switch takes a press. A mode, a choice and a switch under a mode pick one of their values.
function picks(row: HubPickerRow) {
  return row.type === 'mode' || row.type === 'choice' || row.values.length > 2
}

function ticks(row: HubPickerRow) {
  return row.type === 'switch' && !picks(row)
}

function firstLine(text: string) {
  return text.trim().split('\n')[0] ?? ''
}

function shortName(row: HubPickerRow) {
  return row.name.split('.').pop() ?? row.name
}

function indent(row: HubPickerRow) {
  return '  '.repeat(row.depth)
}

function valueText(value: HubPickerRow['effective']) {
  return Array.isArray(value) ? value.join(', ') : String(value)
}

// A part's instructions sit behind one fold. It holds the instruction rows and each group that
// holds only instructions.
function isInstruction(row: HubPickerRow, part: HubPickerPart) {
  if (row.kind === 'instruction') {
    return true
  }
  const inside = part.children.filter(child => child.id.startsWith(`${row.id}.`))
  return row.kind === 'group' && inside.length > 0 && inside.every(child => child.kind === 'instruction')
}

function startFamilies(screen: HubPickerScreen) {
  const found = screen.families.find(family => family.parts.some(part => part.new || mark(part) === '[~]'))
  return found === undefined ? [] : [found.family]
}

// The state outlives a hot reload of the mod. A pane left open can still hold the old single
// `family` value, which reads as one open family.
function openFamilies(state: HubPicker): string[] {
  const stored = state as Partial<HubPicker> & { family?: string | null }
  if (Array.isArray(stored.families)) {
    return stored.families
  }
  return typeof stored.family === 'string' ? [stored.family] : []
}

// Opens or closes one family and leaves the others alone, so a press changes only what is drawn
// below the row.
function foldFamily(state: HubPicker, family: string): HubPicker {
  const { family: _earlier, ...rest } = state as HubPicker & { family?: string | null }
  return { ...rest, families: toggle(openFamilies(state), family) }
}

function rowsOf(screen: HubPickerScreen | null) {
  const found = new Map<string, HubPickerRow>()
  for (const family of screen?.families ?? []) {
    for (const part of family.parts) {
      found.set(part.id, part)
      for (const child of part.children) {
        found.set(child.id, child)
      }
    }
  }
  return found
}

function newestChange(screen: HubPickerScreen | null): HubStripLine | undefined {
  return screen?.strip.find(line => line.kind === 'change')
}

async function readModel($: EngineInterface) {
  try {
    const done = await runStep($, ['screen', '--json', '--all'], undefined, SCREEN_TIMEOUT)
    if (done.exitCode === 0) {
      return { screen: JSON.parse(done.stdout) as HubPickerScreen, failure: null }
    }
    return { screen: null, failure: firstLine(done.stderr || done.stdout) || `exit code ${done.exitCode}` }
  } catch (error) {
    return { screen: null, failure: error instanceof Error ? error.message : String(error) }
  }
}

// Starts a new visit, with the model read fresh, the folds as at the start and an empty undo.
export async function openPicker($: EngineInterface) {
  const { screen, failure } = await readModel($)
  await update($, picker, () => ({ ...CLOSED, screen, failure, families: screen === null ? [] : startFamilies(screen) }))
  await $.ui.open({ id: PICKER, title: PICKER_TITLE, focus: true, closeOnEscape: true, rows: 30 })
}

// Runs one change, reads the model again and, when the step made a strip line, keeps it for undo.
async function save($: EngineInterface, args: readonly string[], undoing = false, landing?: string, room?: number) {
  let done: Answer
  try {
    done = await change($, args)
  } catch (error) {
    $.ui.toast(error instanceof Error ? error.message : String(error))
    return
  }
  if (done.exitCode !== 0) {
    $.ui.toast(firstLine(done.stderr || done.stdout))
    return
  }
  const now = await read($, picker)
  const before = newestChange(now.screen)
  const { screen, failure } = await readModel($)
  const made = newestChange(screen)
  const moves = landing === undefined ? undefined : ringMoves(landing)
  const top = landing === undefined || room === undefined ? undefined : landingTop(now, landing, room)
  const saved = (state: HubPicker): HubPicker => {
    const undo = undoing ? undoEntries(state).slice(0, -1) : undoEntries(state)
    const added = !undoing && made !== undefined && made.id !== before?.id ? [{ change: made.id, row: args[1] ?? '' }] : []
    // The pane only reports focus it moved itself, so the landing is stored here for the does line.
    const focused = landing === undefined ? state.focused : landing
    return {
      ...state,
      screen: screen ?? state.screen,
      failure,
      focused,
      top: top === undefined ? state.top ?? null : top,
      undo: [...undo, ...added],
    }
  }
  // The ring goes first to the control now drawn where the landing row will be, so the new
  // drawing, which keeps the ring at its place, puts it on that row. The undo button leaving the
  // end of the list cannot shift it. If the ring does not move, the undo still counts.
  const place = landing === undefined || room === undefined ? undefined : placeFor(now, saved(now), room, landing)
  if (place !== undefined) {
    await moveRing($, place)
  }
  await update($, picker, saved)
  await refresh($)
  if (moves !== undefined) {
    try {
      await $.ui.scroll(moves.scroll)
    } catch {}
  }
}

// The window's new first line when an undo lands on `key` outside the drawn rows, or undefined
// when the list draws it already.
export function landingTop(state: HubPicker, key: string, room: number) {
  const lines = listLines(state, state.screen)
  const index = lines.findIndex(line => line.ring === key)
  const top = index < 0 ? undefined : topFor(lines, room, startOf(lines, state.top), index)
  return top === undefined ? undefined : lines[top].id
}

// What the pane is asked to do so the ring lands on `key`: focus it, then scroll it into view.
export function ringMoves(key: string) {
  return { focus: { requestId: PICKER, key }, scroll: { in: PICKER, to: { key } } }
}

// Where the ring goes when the next undo runs. Only the last undo moves it, to the drawn key
// nearest the row it takes back. While more undos are left, the ring stays where it is.
export function undoLanding(state: HubPicker) {
  const entries = undoEntries(state)
  const last = entries.at(-1)
  return entries.length === 1 && last !== undefined && last.row !== '' ? drawnKey(state, state.screen, last.row) : undefined
}

// The undo stack of the state. After a hot reload it can still hold the old plain strings, which
// name the change but not its row.
function undoEntries(state: HubPicker) {
  return (state.undo as (HubPicker['undo'][number] | string)[]).map(entry =>
    typeof entry === 'string' ? { change: entry, row: '' } : entry,
  )
}

// The nearest key the pane draws for a row, so the ring never goes to a key that is not there. A
// row inside a folded part falls back to the part, then to the family.
function drawnKey(state: HubPicker, screen: HubPickerScreen | null, id: string) {
  for (const group of screen?.families ?? []) {
    const familyKey = `family:${group.family}`
    for (const item of group.parts) {
      const inside = item.id === id ? undefined : item.children.find(child => child.id === id)
      if (item.id !== id && inside === undefined) {
        continue
      }
      if (!openFamilies(state).includes(group.family)) {
        return familyKey
      }
      if (inside === undefined) {
        return `row:${item.id}`
      }
      if (!state.parts.includes(item.id)) {
        return `row:${item.id}`
      }
      if (!isInstruction(inside, item)) {
        return `row:${inside.id}`
      }
      return state.instructions.includes(item.id) ? `row:${inside.id}` : `instructions:${item.id}`
    }
  }
  return undefined
}

async function press($: EngineInterface, row: HubPickerRow) {
  await save($, [mark(row) === '[ ]' ? 'on' : 'off', row.id])
}

async function undoLast($: EngineInterface, room: number) {
  const state = await read($, picker)
  const last = undoEntries(state).at(-1)
  if (last !== undefined) {
    await save($, ['undo', last.change], true, undoLanding(state), room)
  }
}

function toggle(list: readonly string[], id: string) {
  return list.includes(id) ? list.filter(one => one !== id) : [...list, id]
}

// Every row the list would draw with nothing hidden, in order. The render hook draws a window of
// them, and the focus hook reads them to find the row before or after the drawn ones.
function listLines(state: HubPicker, screen: HubPickerScreen | null) {
  const lines: ListLine[] = []
  const row = (item: HubPickerRow) => {
    const ring = picks(item) || ticks(item) ? `row:${item.id}` : null
    lines.push({ id: ring ?? `line:${item.id}`, ring, draw: 'row', row: item })
  }
  const opened = openFamilies(state)
  for (const group of screen?.families ?? []) {
    const familyOpen = opened.includes(group.family)
    lines.push({ id: `family:${group.family}`, ring: `family:${group.family}`, draw: 'family', group, open: familyOpen })
    if (!familyOpen) {
      continue
    }
    for (const part of group.parts) {
      const open = state.parts.includes(part.id)
      lines.push({ id: `row:${part.id}`, ring: `row:${part.id}`, draw: 'part', part })
      if (part.children.length > 0) {
        lines.push({ id: `more:${part.id}`, ring: `more:${part.id}`, draw: 'more', part, open })
      }
      if (!open) {
        continue
      }
      const instructions = part.children.filter(child => isInstruction(child, part))
      part.children.filter(child => !isInstruction(child, part)).forEach(row)
      if (instructions.length === 0) {
        continue
      }
      const instructionsOpen = state.instructions.includes(part.id)
      const counted = instructions.filter(child => child.kind === 'instruction')
      lines.push({
        id: `instructions:${part.id}`,
        ring: `instructions:${part.id}`,
        draw: 'instructions',
        part,
        open: instructionsOpen,
        on: counted.filter(child => mark(child) !== '[ ]').length,
        total: counted.length,
      })
      if (instructionsOpen) {
        instructions.forEach(row)
      }
    }
  }
  return lines
}

// The lines a list of `total` draws in `room` rows from line `start`. A ▲ row shows while lines
// are hidden above. The last row is kept for ▼ even while nothing is hidden below, so a fold that
// opens under the last drawn line never pushes that line out.
export function windowOf(total: number, room: number, start: number) {
  const first = Math.min(Math.max(0, start), Math.max(0, total - 1))
  const end = Math.min(total, first + Math.max(1, room - 1 - (first > 0 ? 1 : 0)))
  return { start: first, end, above: first > 0, below: end < total }
}

function startOf(lines: readonly ListLine[], top: string | null | undefined) {
  return Math.max(0, lines.findIndex(line => line.id === top))
}

// The first line of a window that draws line `index`, or undefined when the window from `start`
// draws it already. A line above goes to the top. A line below goes to the bottom, or as low as
// the lines after it allow when no control follows it, so those lines show too.
function topFor(lines: readonly ListLine[], room: number, start: number, index: number) {
  const shown = windowOf(lines.length, room, start)
  if (index >= shown.start && index < shown.end) {
    return undefined
  }
  if (index < shown.start) {
    return index
  }
  const last = lines.slice(index + 1).some(line => line.ring !== null) ? index + 1 : lines.length
  return Math.min(index, Math.max(shown.start + 1, last - Math.max(1, room - 2)))
}

// What the focus hook does when the person's Tab, arrow or click lands on ▲ or ▼. From a list
// row, the window moves one control up or down and the ring goes to that control (`to`, with the
// window's new first line in `top`). The ring wraps at the ends, so an Up on the first control
// lands on ▼ and a Down on the last lands on ▲; then the ring goes to the far end of the list, as
// it does when the whole list is drawn. From anywhere else, such as undo going up, the ring lands
// on the nearest drawn list row instead (`land`).
export function markStep(state: HubPicker, way: 'up' | 'down', room: number) {
  const lines = listLines(state, state.screen)
  const shown = windowOf(lines.length, room, startOf(lines, state.top))
  const drawn = lines.slice(shown.start, shown.end).filter(line => line.ring !== null)
  const keys = ringKeys(state, room)
  const at = keys.indexOf(state.focused ?? '')
  if (at >= 0 && (way === 'down' ? at === 0 : at === keys.length - 1 && at > 1)) {
    const index = way === 'down' ? lines.findLastIndex(line => line.ring !== null) : 0
    const top = way === 'down' ? topFor(lines, room, shown.start, index) : 0
    return { to: lines[index]?.ring ?? undefined, top: top === undefined ? undefined : lines[top]?.id }
  }
  const edge = way === 'down' ? drawn.at(-1) : drawn[0]
  if (!drawn.some(line => line.ring === state.focused)) {
    return { land: edge?.ring ?? undefined }
  }
  const index =
    way === 'down'
      ? lines.findIndex((line, at) => at >= shown.end && line.ring !== null)
      : lines.findLastIndex((line, at) => at < shown.start && line.ring !== null)
  if (index < 0) {
    return {}
  }
  const top = topFor(lines, room, shown.start, index)
  return { to: lines[index].ring ?? undefined, top: top === undefined ? undefined : lines[top].id }
}

// Splits a text at spaces into lines no wider than `width`, so the pane knows how many rows it
// draws. A word longer than the width keeps its own line, and the pane cuts it.
function wrapLines(text: string, width: number) {
  const lines: string[] = []
  let line = ''
  for (const word of text.split(' ')) {
    if (line !== '' && line.length + 1 + word.length > width) {
      lines.push(line.trimEnd())
      line = word
    } else {
      line = line === '' ? word : `${line} ${word}`
    }
  }
  lines.push(line)
  return lines
}

// The rows the list gets in a pane this big: what the legend, the keys and the notes leave.
function listRoom(bodyColumns: number, bodyRows: number) {
  const footer = wrapLines(LEGEND, bodyColumns).length + wrapLines(KEYS, bodyColumns - UNDO_COLUMNS).length
  return Math.max(LIST_ROWS, bodyRows - footer - NOTE_ROWS)
}

// The keys of the controls the pane draws, in the order the ring walks them: ▲, the list rows, ▼
// and undo.
export function ringKeys(state: HubPicker, room: number) {
  const lines = listLines(state, state.screen)
  const view = windowOf(lines.length, room, startOf(lines, state.top))
  return [
    ...(view.above ? [`above:${room}`] : []),
    ...lines.slice(view.start, view.end).flatMap(line => (line.ring === null ? [] : [line.ring])),
    ...(view.below ? [`below:${room}`] : []),
    ...(state.undo.length > 0 ? ['undo'] : []),
  ]
}

// The pane keeps the ring at its place among the drawn controls when it draws again, not on its
// key. So to land on `key` in the drawing of `after`, the ring first goes to the control that sits
// at that place in the drawing of `before`. Undefined when either drawing has no such place.
export function placeFor(before: HubPicker, after: HubPicker, room: number, key: string) {
  const index = ringKeys(after, room).indexOf(key)
  return index < 0 ? undefined : ringKeys(before, room)[index]
}

// Moves the ring by key, to a control the pane draws now. If the ring does not move, nothing
// else depends on it.
async function moveRing($: EngineInterface, key: string) {
  try {
    await $.ui.focus({ requestId: PICKER, key })
  } catch {}
}

// The ▲ and ▼ rows are controls, so the ring can land on them. Their keys carry the list's room,
// which the focus hook needs to move the window.
const MARK_KEY = /^(above|below):(\d+)$/

export function registerPicker(on: On) {
  on('ui.focus', async ($, e, next) => {
    if (e.component !== 'Pane' || e.requestId !== PICKER) {
      return next(e)
    }
    const found = e.origin.kind === 'person' ? MARK_KEY.exec(e.element ?? '') : null
    if (found !== null) {
      const before = await read($, picker)
      const room = Number(found[2])
      const step = markStep(before, found[1] === 'below' ? 'down' : 'up', room)
      if (step.land !== undefined) {
        const result = await next({ ...e, element: step.land })
        if (result.deny === undefined) {
          await update($, picker, state => ({ ...state, focused: step.land ?? null }))
        }
        return result
      }
      if (step.to === undefined) {
        return {}
      }
      // The ring first goes to the control now drawn where the new row will be, then the window
      // moves and the new drawing puts that row in the same place. When that place is the
      // ring's own, it stays.
      const to = step.to
      const after = { ...before, focused: to, top: step.top ?? before.top ?? null }
      const place = placeFor(before, after, room, to)
      if (place === undefined) {
        return {}
      }
      const result = place === before.focused ? undefined : await next({ ...e, element: place })
      if (result?.deny === undefined) {
        await update($, picker, state => ({ ...state, focused: to, top: after.top }))
      }
      return result ?? {}
    }
    const result = await next(e)
    if (result.deny === undefined) {
      await update($, picker, state => ({ ...state, focused: e.element ?? null }))
    }
    return result
  })

  on('ui.render', { component: 'Pane', requestId: PICKER }, async ($, e) => {
    const { Box, Button, Select, Text } = $.ui.resolve(e)
    const state = await read($, picker)
    const columns = e.props.bodyColumns
    const wide = columns >= DOES_COLUMNS
    const typed = (await read($, command)).used ?? COMMAND
    if (state.screen === null) {
      return (
        <Box flexDirection="column">
          <Text color="red">{failedLine(state.failure ?? '')}</Text>
        </Box>
      )
    }
    const shown = state.screen
    const room = listRoom(columns, e.props.scroll.bodyRows)

    // Every line is one row on screen, so the list and the footer always fit the pane and the
    // pane never scrolls. While it fits, Up and Down move the ring.
    const draw = (line: ListLine) => {
      if (line.draw === 'family') {
        const group = line.group
        return (
          <Box key={`group:${group.family}`} flexDirection="row" columnGap={2}>
            <Button
              plain
              key={line.ring}
              label={`${line.open ? '▾' : '▸'} ${group.label ?? group.family}`}
              onPress={() => update($, picker, now => foldFamily(now, group.family))}
            />
            <Text dimColor wrap="truncate-end">
              {onCount(group.parts_on, group.parts_total)}
            </Text>
            {group.parts.some(item => item.new) && <Text color="green">{NEW_MARK}</Text>}
          </Box>
        )
      }
      if (line.draw === 'part') {
        const item = line.part
        const label = `${mark(item)} ${item.name}`
        return (
          <Box key={`part:${item.id}`} flexDirection="row" columnGap={2}>
            {picks(item) ? (
              <Select
                key={line.ring}
                label={label}
                options={item.values.map(value => ({ value }))}
                value={valueText(item.effective)}
                onSelect={value => save($, ['set', item.id, value])}
              />
            ) : (
              <Button plain key={line.ring} label={label} onPress={() => press($, item)} />
            )}
            {item.new && <Text color="green">{NEW_MARK}</Text>}
            {wide && (
              <Box flexShrink={1}>
                <Text dimColor wrap="truncate-end">
                  {item.does}
                </Text>
              </Box>
            )}
          </Box>
        )
      }
      if (line.draw === 'more') {
        const item = line.part
        return (
          <Button
            plain
            dimColor
            key={line.ring}
            label={line.open ? `▾ ${LESS_LABEL}` : `▸ ${moreLabel(item.children.length)}`}
            onPress={() => update($, picker, now => ({ ...now, parts: toggle(now.parts, item.id) }))}
          />
        )
      }
      if (line.draw === 'instructions') {
        const item = line.part
        return (
          <Button
            plain
            dimColor
            key={line.ring}
            label={`${line.open ? '▾' : '▸'} ${instructionsLabel(line.on, line.total)}`}
            onPress={() => update($, picker, now => ({ ...now, instructions: toggle(now.instructions, item.id) }))}
          />
        )
      }
      const item = line.row
      if (picks(item)) {
        return (
          <Box key={`line:${item.id}`} flexDirection="row">
            <Select
              key={`row:${item.id}`}
              label={`${indent(item)}${mark(item)} ${shortName(item)}`}
              options={item.values.map(value => ({ value }))}
              value={valueText(item.effective)}
              onSelect={value => save($, ['set', item.id, value])}
            />
          </Box>
        )
      }
      if (ticks(item)) {
        return (
          <Box key={`line:${item.id}`} flexDirection="row">
            <Button
              plain
              key={`row:${item.id}`}
              label={`${indent(item)}${mark(item)} ${shortName(item)}`}
              onPress={() => press($, item)}
            />
          </Box>
        )
      }
      return (
        <Box key={`line:${item.id}`} flexDirection="row" columnGap={2}>
          <Text wrap="truncate-end">{`${indent(item)}${shortName(item)}  ${valueText(item.effective)}`}</Text>
          {wide && (
            <Text dimColor wrap="truncate-end">
              {setLine(typed, item.id)}
            </Text>
          )}
        </Box>
      )
    }

    const lines = listLines(state, shown)
    const view = windowOf(lines.length, room, startOf(lines, state.top))

    const focused = state.focused?.startsWith('row:') ? rowsOf(shown).get(state.focused.slice('row:'.length)) : undefined
    const waits = focused !== undefined && focused.waits_for.length > 0 ? focused : undefined
    const notes = [
      ...(waits === undefined ? [] : wrapLines(waitsLine(waits.id, waits.waits_for), columns)).map(text => ({ text, waits: true })),
      ...(!wide && focused !== undefined ? wrapLines(focused.does, columns) : []).map(text => ({ text, waits: false })),
    ].slice(0, NOTE_ROWS)
    const keys = wrapLines(KEYS, columns - UNDO_COLUMNS)

    // A redraw keeps the focus ring at its place among the controls, not on its key. The undo
    // button always comes after the list, so it never moves the ring when it comes or goes.
    return (
      <Box flexDirection="column">
        {view.above && <Button plain dimColor key={`above:${room}`} label="▲" onPress={() => undefined} />}
        {lines.slice(view.start, view.end).map(draw)}
        {view.below && <Button plain dimColor key={`below:${room}`} label="▼" onPress={() => undefined} />}
        {wrapLines(LEGEND, columns).map((text, index) => (
          <Text key={`legend:${index}`} dimColor wrap="truncate-end">
            {text}
          </Text>
        ))}
        <Box key="keys" flexDirection="row" columnGap={2}>
          <Text dimColor wrap="truncate-end">
            {keys[0]}
          </Text>
          {state.undo.length > 0 && (
            <Button plain key="undo" label={UNDO_LABEL} hotkey={UNDO_KEY} onPress={() => undoLast($, room)} />
          )}
        </Box>
        {keys.slice(1).map((text, index) => (
          <Text key={`keys:${index}`} dimColor wrap="truncate-end">
            {text}
          </Text>
        ))}
        {notes.map((note, index) => (
          <Text key={`note:${index}`} color={note.waits ? 'yellow' : undefined} dimColor={!note.waits} wrap="truncate-end">
            {note.text}
          </Text>
        ))}
      </Box>
    )
  })
}
