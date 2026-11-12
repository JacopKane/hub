// The words the feature picker and the band's features button show. They live apart from the
// logic, so they can be reviewed on their own.

export const PICKER_TITLE = 'hub features'

export function onCount(on: number, total: number) {
  return `${on} of ${total} on`
}

export const LEGEND = '[x] on   [ ] off   [~] on, but waits for a parent or a need'

export const KEYS = 'Enter tick or open   Tab or arrows move   Esc close'

export const UNDO_LABEL = 'undo'

export const NEW_MARK = 'new'

export function waitsLine(name: string, waits: readonly string[]) {
  return `${name} is on, but waits for ${waits.join(' and ')}.`
}

export function moreLabel(count: number) {
  return `${count} more`
}

export const LESS_LABEL = 'less'

export function instructionsLabel(on: number, total: number) {
  return `instructions  ${on} of ${total} on`
}

export function setLine(command: string, id: string) {
  return `/${command} set ${id} <value>`
}

export function refusePick(command: string) {
  return `Only you can open the picker, by typing /${command} pick at the prompt.`
}

export const FEATURES_LABEL = 'features'

export function failedLine(reason: string) {
  return `✗ hub did not answer: ${reason}`
}
