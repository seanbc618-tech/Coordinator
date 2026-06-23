/**
 * Pure decision logic for the destructive-command confirmation state machine.
 *
 * Extracted from App.handleSubmit so that production and tests invoke
 * exactly the same code path.
 */

import { parse } from './slash.js'

export type SubmitDecision =
  | { action: 'quit'; newPending: null }
  | { action: 'destructive-confirmed'; commandName: string; method: string; args: string; newPending: null }
  | { action: 'destructive-pending'; commandName: string; newPending: string }
  | { action: 'local-help'; newPending: null }
  | { action: 'send'; method: string; args: string; newPending: null }
  | { action: 'chat'; text: string; newPending: null }

/**
 * Decide what to do when the user submits text.
 *
 * @param text Raw input text.
 * @param pendingDestructive Current pending destructive command name, or null.
 * @returns A decision describing the action and the new pending state.
 */
export function decideSubmit(text: string, pendingDestructive: string | null): SubmitDecision {
  const parsed = parse(text)

  if (parsed.type === 'command') {
    if (parsed.command.name === '/quit') {
      return { action: 'quit', newPending: null }
    }

    if (parsed.command.name === '/help') {
      return { action: 'local-help', newPending: null }
    }

    if (parsed.command.destructive) {
      if (pendingDestructive === parsed.command.name) {
        return {
          action: 'destructive-confirmed',
          commandName: parsed.command.name,
          method: parsed.command.method,
          args: parsed.args,
          newPending: null,
        }
      }
      return {
        action: 'destructive-pending',
        commandName: parsed.command.name,
        newPending: parsed.command.name,
      }
    }

    // Non-destructive command — send immediately, clear any pending.
    return {
      action: 'send',
      method: parsed.command.method,
      args: parsed.args,
      newPending: null,
    }
  }

  // Plain message — send as chat, clear any pending.
  return { action: 'chat', text, newPending: null }
}
