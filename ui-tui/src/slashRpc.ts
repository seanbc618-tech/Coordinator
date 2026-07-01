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
    || commandName === '/inbox'
    || commandName === '/attention'
    || commandName === '/brain'
    || commandName === '/map'
  ) {
    return {
      ok: true,
      method,
      params: {},
      displayMethod: method,
    }
  }

  if (commandName === '/where') {
    const query = args.trim()
    if (!query) {
      return { ok: false, error: 'usage: /where <query>' }
    }
    return {
      ok: true,
      method,
      params: { query },
      displayMethod: method,
    }
  }

  if (commandName === '/why') {
    const target = args.trim().split(/\s+/)[0] ?? ''
    if (!target) {
      return { ok: false, error: 'usage: /why <task-id|path>' }
    }
    if (target.startsWith('task-')) {
      return {
        ok: true,
        method: 'operator.explain_failure',
        params: { task_id: target },
        displayMethod: 'operator.explain_failure',
      }
    }
    return {
      ok: true,
      method: 'project.why',
      params: { path: target },
      displayMethod: 'project.why',
    }
  }

  if (commandName === '/impact') {
    const pathArg = args.trim()
    if (!pathArg) {
      return { ok: false, error: 'usage: /impact <path>' }
    }
    return {
      ok: true,
      method,
      params: { path: pathArg },
      displayMethod: method,
    }
  }

  if (commandName === '/doctor') {
    return {
      ok: true,
      method: 'operator.doctor',
      params: { dry_run: true },
      displayMethod: 'operator.doctor',
    }
  }

  if (commandName === '/repair') {
    const apply = args.includes('--apply')
    return {
      ok: true,
      method: 'operator.repair',
      params: { dry_run: !apply, apply, confirmed: apply },
      displayMethod: 'operator.repair',
    }
  }

  if (commandName === '/health') {
    return {
      ok: true,
      method: 'operator.health',
      params: {},
      displayMethod: 'operator.health',
    }
  }

  if (commandName === '/morning') {
    return {
      ok: true,
      method: 'operator.morning',
      params: {},
      displayMethod: 'operator.morning',
    }
  }

  if (commandName === '/pause all') {
    return {
      ok: true,
      method: 'global.pause',
      params: { reason: args.trim() || 'operator pause' },
      displayMethod: 'global.pause',
    }
  }

  if (commandName === '/resume all') {
    return {
      ok: true,
      method: 'global.resume',
      params: {},
      displayMethod: 'global.resume',
    }
  }

  if (commandName === '/context') {
    const taskId = args.trim().split(/\s+/)[0] ?? ''
    return {
      ok: true,
      method,
      params: taskId ? { task_id: taskId } : {},
      displayMethod: method,
    }
  }

  if (commandName === '/summary') {
    const kind = args.trim().toLowerCase().includes('morning') ? 'morning' : 'current'
    return {
      ok: true,
      method,
      params: { kind, args: args.trim() },
      displayMethod: method,
    }
  }

  if (commandName === '/notify') {
    const dryRun = args.includes('--dry-run')
    return {
      ok: true,
      method,
      params: { dry_run: dryRun, args: args.trim() },
      displayMethod: method,
    }
  }

  if (commandName === '/decision' || commandName === '/dismiss') {
    const itemId = args.trim().split(/\s+/)[0] ?? ''
    if (!itemId) {
      return { ok: false, error: `usage: ${commandName} <item-id>` }
    }
    return {
      ok: true,
      method,
      params: { item_id: itemId, args: args.trim() },
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
      method: 'agent.list',
      params: {},
      displayMethod: 'agent.list',
    }
  }

  if (commandName === '/agent') {
    const agentId = args.trim().split(/\s+/)[0] ?? ''
    if (!agentId) {
      return { ok: false, error: 'usage: /agent <id>' }
    }
    return {
      ok: true,
      method: 'agent.detail',
      params: { agent_id: agentId },
      displayMethod: 'agent.detail',
    }
  }

  if (commandName === '/route') {
    const taskId = args.trim().split(/\s+/)[0] ?? ''
    if (!taskId) {
      return { ok: false, error: 'usage: /route <task-id>' }
    }
    return {
      ok: true,
      method: 'agent.route.preview',
      params: { task_id: taskId },
      displayMethod: 'agent.route.preview',
    }
  }

  if (commandName === '/benchmark agents') {
    return {
      ok: true,
      method: 'agent.benchmark',
      params: { scope: 'agents' },
      displayMethod: 'agent.benchmark',
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

  if (commandName === '/heal') {
    return {
      ok: true,
      method: 'project.pr.heal',
      params: { dry_run: true },
      displayMethod: 'project.pr.heal',
    }
  }

  if (commandName === '/stale') {
    return {
      ok: true,
      method: 'project.pr.health',
      params: { stale_only: true },
      displayMethod: 'project.pr.health',
    }
  }

  if (commandName === '/ci failures') {
    return {
      ok: true,
      method: 'project.pr.health',
      params: { ci_failed_only: true },
      displayMethod: 'project.pr.health',
    }
  }

  if (commandName === '/reviews') {
    return {
      ok: true,
      method: 'project.pr.reviews',
      params: {},
      displayMethod: 'project.pr.reviews',
    }
  }

  if (commandName === '/pr update') {
    const deliveryId = args.trim().split(/\s+/)[0]
    if (!deliveryId) {
      return { ok: false, error: 'usage: /pr update <delivery-id>' }
    }
    return {
      ok: true,
      method: 'project.pr.update_evidence',
      params: { delivery_id: deliveryId, dry_run: true },
      displayMethod: 'project.pr.update_evidence',
    }
  }

  if (commandName === '/rebase') {
    const apply = args.includes('--apply')
    const deliveryId = args.replace('--apply', '').trim().split(/\s+/)[0]
    if (!deliveryId) {
      return { ok: false, error: 'usage: /rebase <delivery-id> [--apply]' }
    }
    return {
      ok: true,
      method: 'project.pr.rebase',
      params: { delivery_id: deliveryId, dry_run: !apply, apply },
      displayMethod: 'project.pr.rebase',
    }
  }

  if (commandName === '/notify test') {
    return {
      ok: true,
      method: 'operator.notify',
      params: { dry_run: true, args: 'test' },
      displayMethod: 'operator.notify',
    }
  }

  if (commandName === '/approvals') {
    return {
      ok: true,
      method: 'operator.approvals',
      params: {},
      displayMethod: 'operator.approvals',
    }
  }

  if (commandName === '/channels') {
    return {
      ok: true,
      method: 'operator.channels',
      params: {},
      displayMethod: 'operator.channels',
    }
  }

  if (commandName === '/reject') {
    const token = args.trim().split(/\s+/)[0]
    if (!token || !token.startsWith('coord-appr-')) {
      return { ok: false, error: 'usage: /reject <approval-token>' }
    }
    return {
      ok: true,
      method: 'operator.approval.reject',
      params: { token },
      displayMethod: 'operator.approval.reject',
    }
  }

  if (commandName === '/approve') {
    const parts = args.trim().split(/\s+/).filter(Boolean)
    if (parts[0]?.toLowerCase() === 'token') {
      const token = parts[1]
      if (!token || !token.startsWith('coord-appr-')) {
        return { ok: false, error: 'usage: /approve token <approval-token>' }
      }
      return {
        ok: true,
        method: 'operator.approval.approve',
        params: { token, confirmed: true },
        displayMethod: 'operator.approval.approve',
      }
    }
    const taskId = args.trim()
    if (!taskId) {
      return { ok: false, error: 'usage: /approve <task-id> or /approve token <approval-token>' }
    }
    return {
      ok: true,
      method: 'project.task.approve',
      params: { task_id: taskId },
      displayMethod: 'project.task.approve',
    }
  }

  if (
    method === 'project.task.retry'
    || method === 'project.task.cancel'
  ) {
    const taskId = args.trim()
    if (!taskId) {
      return { ok: false, error: `usage: ${commandName} <task-id>` }
    }
    return { ok: true, method, params: { task_id: taskId }, displayMethod: method }
  }

  if (commandName === '/onboard') {
    const parts = args.trim().split(/\s+/).filter(Boolean)
    if (parts[0]?.toLowerCase() === 'apply') {
      const preset = parts[1] ?? 'observe'
      return {
        ok: true,
        method: 'project.onboard.apply',
        params: { path: '.', preset },
        displayMethod: 'project.onboard.apply',
      }
    }
    return {
      ok: true,
      method: 'project.onboard.plan',
      params: { path: '.', preset: 'observe' },
      displayMethod: 'project.onboard.plan',
    }
  }

  if (commandName === '/simulate' || commandName === '/what-if') {
    const parts = args.trim().split(/\s+/).filter(Boolean)
    if (parts[0]?.toLowerCase() === 'preset') {
      const preset = parts[1] ?? 'overnight'
      return {
        ok: true,
        method: 'project.onboard.simulate',
        params: { preset, path: '.' },
        displayMethod: 'project.onboard.simulate',
      }
    }
    if (parts[0]?.toLowerCase() === 'project') {
      const hours = parts[1] ? Number(parts[1]) : 8
      return {
        ok: true,
        method: 'simulation.run',
        params: { scope: 'project', horizon_hours: Number.isFinite(hours) ? hours : 8 },
        displayMethod: 'simulation.run',
      }
    }
    return {
      ok: true,
      method: 'simulation.run',
      params: { scope: 'global', horizon_hours: 8 },
      displayMethod: 'simulation.run',
    }
  }

  if (commandName === '/fleet') {
    const root = args.trim() || '.'
    return {
      ok: true,
      method: 'fleet.scan',
      params: { root },
      displayMethod: 'fleet.scan',
    }
  }

  if (commandName === '/rollback-onboard') {
    const snapshotId = args.trim().split(/\s+/)[0] ?? ''
    if (!snapshotId) {
      return { ok: false, error: 'usage: /rollback-onboard <snapshot-id>' }
    }
    return {
      ok: true,
      method: 'project.onboard.rollback',
      params: { snapshot_id: snapshotId },
      displayMethod: 'project.onboard.rollback',
    }
  }

  if (commandName === '/profile') {
    return {
      ok: true,
      method: 'project.profile',
      params: {},
      displayMethod: 'project.profile',
    }
  }

  if (commandName === '/preferences') {
    return {
      ok: true,
      method: 'preference.list',
      params: {},
      displayMethod: 'preference.list',
    }
  }

  if (commandName === '/learned') {
    return {
      ok: true,
      method: 'preference.list',
      params: { learned_only: true },
      displayMethod: 'preference.list',
    }
  }

  if (commandName === '/prefer') {
    const parts = args.trim().split(/\s+/)
    if (parts.length < 2) {
      return { ok: false, error: 'usage: /prefer <rule_type> <json-or-key=value>' }
    }
    const ruleType = parts[0]!
    const ruleText = parts.slice(1).join(' ')
    let rule: Record<string, unknown>
    try {
      if (ruleText.startsWith('{')) {
        rule = JSON.parse(ruleText) as Record<string, unknown>
      } else {
        const eq = ruleText.indexOf('=')
        if (eq < 0) {
          return { ok: false, error: 'rule body must be JSON or key=value' }
        }
        rule = { [ruleText.slice(0, eq).trim()]: ruleText.slice(eq + 1).trim() }
      }
    } catch {
      return { ok: false, error: 'rule body must be JSON or key=value' }
    }
    return {
      ok: true,
      method: 'preference.approve',
      params: { rule_type: ruleType, rule },
      displayMethod: 'preference.approve',
    }
  }

  if (commandName === '/forget') {
    const ruleId = args.trim().split(/\s+/)[0] ?? ''
    if (!ruleId) {
      return { ok: false, error: 'usage: /forget <rule-id>' }
    }
    return {
      ok: true,
      method: 'preference.delete',
      params: { rule_id: ruleId },
      displayMethod: 'preference.delete',
    }
  }

  return { ok: true, method, params: { args }, displayMethod: method }
}

export function isDestructiveRpc(method: string): boolean {
  return method === 'project.task.cancel' || method === 'project.onboard.rollback'
}