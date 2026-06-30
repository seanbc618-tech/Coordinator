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

  if (commandName === '/jump' || commandName === '/open') {
    const target = args.trim()
    if (!target) {
      return { ok: false, error: 'usage: /jump <task-id|goal|log|worktree>' }
    }
    const params: Record<string, unknown> = { target }
    if (commandName === '/open') {
      params.alias = 'open'
    }
    return {
      ok: true,
      method: 'project.jump',
      params,
      displayMethod: 'project.jump',
    }
  }

  if (
    commandName === '/plan'
    || commandName === '/scan'
    || commandName === '/strategy'
    || commandName === '/prs'
    || commandName === '/merge-policy'
  ) {
    return {
      ok: true,
      method,
      params: {},
      displayMethod: method,
    }
  }

  if (
    commandName === '/evidence'
    || commandName === '/review'
    || commandName === '/risk'
    || commandName === '/merge-ready'
    || commandName === '/deliver'
    || commandName === '/ci'
    || commandName === '/delivery'
  ) {
    const taskId = args.trim().split(/\s+/)[0] ?? ''
    if (!taskId) {
      return { ok: false, error: `usage: ${commandName} <task-id>` }
    }
    return {
      ok: true,
      method,
      params: { task_id: taskId },
      displayMethod: method,
    }
  }

  if (commandName === '/recoveries') {
    return {
      ok: true,
      method: 'project.recoveries',
      params: { status: 'pending' },
      displayMethod: 'project.recoveries',
    }
  }

  if (commandName === '/agents') {
    return {
      ok: true,
      method: 'project.agents',
      params: {},
      displayMethod: 'project.agents',
    }
  }

  if (commandName === '/overnight') {
    return {
      ok: true,
      method: 'project.overnight',
      params: { args: args.trim() },
      displayMethod: 'project.overnight',
    }
  }

  if (commandName === '/loop') {
    const sub = args.trim().split(/\s+/)[0]?.toLowerCase() ?? ''
    if (sub === 'step') {
      return {
        ok: true,
        method: 'project.loop.step',
        params: { force: true },
        displayMethod: 'project.loop.step',
      }
    }
    if (sub === 'start') {
      return {
        ok: true,
        method: 'project.loop.start',
        params: {},
        displayMethod: 'project.loop.start',
      }
    }
    if (sub === 'stop') {
      return {
        ok: true,
        method: 'project.loop.stop',
        params: { reason: 'operator stop' },
        displayMethod: 'project.loop.stop',
      }
    }
    if (sub === 'pause') {
      return {
        ok: true,
        method: 'project.loop.pause',
        params: {},
        displayMethod: 'project.loop.pause',
      }
    }
    if (sub === 'resume') {
      return {
        ok: true,
        method: 'project.loop.resume',
        params: {},
        displayMethod: 'project.loop.resume',
      }
    }
    if (sub === 'run') {
      return {
        ok: true,
        method: 'project.loop.run.status',
        params: {},
        displayMethod: 'project.loop.run.status',
      }
    }
    return {
      ok: true,
      method: 'project.loop.status',
      params: {},
      displayMethod: 'project.loop.status',
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