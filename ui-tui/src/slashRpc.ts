/**
 * Map slash command input to Supervisor RPC method + params.
 */

export interface TaskSlashParts {
  taskId: string
  action: string | null
}

export function parseTaskSlashArgs(args: string): TaskSlashParts {
  const parts = args.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) {
    return { taskId: '', action: null }
  }
  return {
    taskId: parts[0]!,
    action: parts[1]?.toLowerCase() ?? null,
  }
}

export type SlashRpcRequest =
  | { ok: true; method: string; params: Record<string, unknown>; displayMethod: string }
  | { ok: false; error: string }

export function buildSlashRpc(
  commandName: string,
  method: string,
  args: string,
): SlashRpcRequest {
  if (commandName === '/task') {
    const { taskId, action } = parseTaskSlashArgs(args)
    if (!taskId) {
      return { ok: false, error: 'usage: /task <id> [log|cancel|approve|retry]' }
    }
    if (action === 'log') {
      return {
        ok: true,
        method: 'project.task.log',
        params: { task_id: taskId },
        displayMethod: 'project.task.log',
      }
    }
    if (action === 'cancel') {
      return {
        ok: true,
        method: 'project.task.cancel',
        params: { task_id: taskId },
        displayMethod: 'project.task.cancel',
      }
    }
    if (action === 'approve') {
      return {
        ok: true,
        method: 'project.task.approve',
        params: { task_id: taskId },
        displayMethod: 'project.task.approve',
      }
    }
    if (action === 'retry') {
      return {
        ok: true,
        method: 'project.task.retry',
        params: { task_id: taskId },
        displayMethod: 'project.task.retry',
      }
    }
    if (action) {
      return { ok: false, error: `unknown task action: ${action}` }
    }
    return {
      ok: true,
      method: 'project.task',
      params: { args: taskId },
      displayMethod: 'project.task',
    }
  }

  if (
    method === 'project.task.approve'
    || method === 'project.task.retry'
    || method === 'project.task.cancel'
  ) {
    const taskId = args.trim()
    if (!taskId) {
      return { ok: false, error: `usage: ${commandName} <task-id>` }
    }
    return { ok: true, method, params: { task_id: taskId }, displayMethod: method }
  }

  return { ok: true, method, params: { args }, displayMethod: method }
}

export function isDestructiveRpc(method: string): boolean {
  return method === 'project.task.cancel'
}