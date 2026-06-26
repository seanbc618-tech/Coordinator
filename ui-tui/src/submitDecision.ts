/**
 * Pure decision logic for the destructive-command confirmation state machine.
 *
 * Extracted from App.handleSubmit so that production and tests invoke
 * exactly the same code path.
 */

import { parse } from './slash.js'
import { buildSlashRpc, isDestructiveRpc } from './slashRpc.js'

export type SubmitDecision =
  | { action: 'quit'; newPending: null }
  | {
    action: 'destructive-confirmed'
    commandName: string
    method: string
    params: Record<string, unknown>
    displayMethod: string
    newPending: null
  }
  | { action: 'destructive-pending'; commandName: string; newPending: string }
  | { action: 'local-help'; newPending: null }
  | { action: 'local-error'; text: string; newPending: null }
  | {
    action: 'send'
    method: string
    params: Record<string, unknown>
    displayMethod: string
    newPending: null
  }
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

    const rpc = buildSlashRpc(parsed.command.name, parsed.command.method, parsed.args)
    if (!rpc.ok) {
      return { action: 'local-error', text: rpc.error, newPending: null }
    }

    const destructive = parsed.command.destructive || isDestructiveRpc(rpc.method)
    const confirmName = destructive && rpc.method === 'project.task.cancel'
      ? `/task ${String(rpc.params.task_id ?? '')} cancel`
      : parsed.command.name

    if (destructive) {
      if (pendingDestructive === confirmName) {
        return {
          action: 'destructive-confirmed',
          commandName: confirmName,
          method: rpc.method,
          params: rpc.params,
          displayMethod: rpc.displayMethod,
          newPending: null,
        }
      }
      return {
        action: 'destructive-pending',
        commandName: confirmName,
        newPending: confirmName,
      }
    }

    return {
      action: 'send',
      method: rpc.method,
      params: rpc.params,
      displayMethod: rpc.displayMethod,
      newPending: null,
    }
  }

  if (parsed.type === 'unknown-command') {
    return {
      action: 'local-error',
      text: `Unknown command: ${parsed.command}. Use /help.`,
      newPending: null,
    }
  }

  // Plain message — send as chat, clear any pending.
  return { action: 'chat', text: parsed.text, newPending: null }
}
