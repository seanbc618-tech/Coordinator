/**
 * Slash command registry and parsing for the Coordinator TUI.
 */

export interface SlashCommand {
  name: string
  description: string
  method: string
  destructive?: boolean
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: '/status', description: 'Show project status', method: 'project.status' },
  { name: '/goal', description: 'Set or view the current goal', method: 'project.goal' },
  { name: '/tasks', description: 'List project tasks', method: 'project.tasks' },
  { name: '/task', description: 'Show one task in detail', method: 'project.task' },
  { name: '/approve', description: 'Approve a human-gated task', method: 'project.task.approve' },
  { name: '/retry', description: 'Retry a failed task', method: 'project.task.retry' },
  { name: '/cancel', description: 'Cancel a running task', method: 'project.task.cancel', destructive: true },
  { name: '/dashboard', description: 'Show multi-project dashboard', method: 'supervisor.dashboard' },
  { name: '/strategy', description: 'Show current milestone objective', method: 'project.strategy' },
  { name: '/evidence', description: 'Show durable task evidence', method: 'project.evidence' },
  { name: '/review', description: 'Show evidence review summary', method: 'project.review' },
  { name: '/risk', description: 'Show task risk assessment', method: 'project.risk' },
  { name: '/merge-ready', description: 'Check merge readiness under repo policy', method: 'project.merge_ready' },
  { name: '/deliver', description: 'Deliver task branch to GitHub under policy', method: 'project.deliver' },
  { name: '/prs', description: 'List project delivery PR records', method: 'project.prs' },
  { name: '/heal', description: 'Run bounded PR self-healing cycle (dry-run)', method: 'project.pr.heal' },
  { name: '/stale', description: 'List stale delivery PRs', method: 'project.pr.health' },
  { name: '/ci failures', description: 'List PRs with failed CI', method: 'project.pr.health' },
  { name: '/reviews', description: 'Ingest unresolved PR review comments', method: 'project.pr.reviews' },
  { name: '/pr update', description: 'Refresh PR evidence section (dry-run)', method: 'project.pr.update_evidence' },
  { name: '/rebase', description: 'Dry-run rebase for a delivery PR', method: 'project.pr.rebase' },
  { name: '/ci', description: 'Poll GitHub CI for a task delivery', method: 'project.ci' },
  { name: '/delivery', description: 'Show delivery record for a task', method: 'project.delivery' },
  { name: '/merge-policy', description: 'Show repo merge and push policy', method: 'project.merge_policy' },
  { name: '/brain', description: 'Show project brain snapshot', method: 'project.brain' },
  { name: '/map', description: 'Show project structure map', method: 'project.map' },
  { name: '/where', description: 'Find where to make a change', method: 'project.where' },
  { name: '/why', description: 'Explain a file path', method: 'project.why' },
  { name: '/impact', description: 'Show impact of changing a file', method: 'project.impact' },
  { name: '/context', description: 'Show task context packet', method: 'project.context' },
  { name: '/inbox', description: 'Show operator inbox for this project', method: 'operator.inbox' },
  { name: '/attention', description: 'Show items needing human attention', method: 'operator.attention' },
  { name: '/summary', description: 'Show operator summary', method: 'operator.summary' },
  { name: '/notify', description: 'Dispatch notifications (use --dry-run)', method: 'operator.notify' },
  { name: '/notify test', description: 'Dry-run notification delivery test', method: 'operator.notify' },
  { name: '/approvals', description: 'List pending external approval requests', method: 'operator.approvals' },
  { name: '/channels', description: 'Show external approval channel configs', method: 'operator.channels' },
  { name: '/reject', description: 'Reject an external approval token', method: 'operator.approval.reject' },
  { name: '/decision', description: 'Route safe action for an inbox item', method: 'operator.decision' },
  { name: '/dismiss', description: 'Dismiss an operator inbox item', method: 'operator.dismiss' },
  { name: '/recoveries', description: 'List pending recovery proposals', method: 'project.recoveries' },
  { name: '/overnight', description: 'Overnight window and latest summary', method: 'project.overnight' },
  { name: '/loop', description: 'Autonomous loop status and run controls', method: 'project.loop.status' },
  { name: '/plan', description: 'Show autonomous plan and next action', method: 'project.plan' },
  { name: '/scan', description: 'Read-only project diagnostics', method: 'project.scan' },
  { name: '/jump', description: 'Resolve task, log, or worktree target', method: 'project.jump' },
  { name: '/open', description: 'Alias of /jump', method: 'project.jump' },
  { name: '/logs', description: 'Show recent logs', method: 'project.logs' },
  { name: '/agents', description: 'Show agent scorecards and routing hints', method: 'project.agents' },
  { name: '/pause', description: 'Pause project scheduling', method: 'project.pause' },
  { name: '/resume', description: 'Resume project scheduling', method: 'project.resume' },
  { name: '/stop', description: 'Stop project at safe boundary', method: 'project.stop', destructive: true },
  { name: '/shutdown', description: 'Shut down the Supervisor', method: 'system.shutdown', destructive: true },
  { name: '/new', description: 'Start a new conversation', method: 'chat.new' },
  { name: '/project', description: 'Switch project context', method: 'project.switch' },
  { name: '/help', description: 'Show available commands', method: 'local.help' },
  { name: '/quit', description: 'Detach the TUI', method: 'system.quit' },
]

export interface ParsedSlash {
  type: 'command'
  command: SlashCommand
  args: string
}

export interface ParsedMessage {
  type: 'message'
  text: string
}

export interface ParsedUnknownCommand {
  type: 'unknown-command'
  command: string
}

export type Parsed = ParsedSlash | ParsedMessage | ParsedUnknownCommand

const MULTI_WORD_COMMANDS = ['/ci failures', '/pr update', '/notify test'] as const

export function parse(input: string): Parsed {
  const trimmed = input.trim()

  if (!trimmed.startsWith('/')) {
    return { type: 'message', text: trimmed }
  }

  const lowered = trimmed.toLowerCase()
  for (const multi of MULTI_WORD_COMMANDS) {
    if (lowered.startsWith(multi)) {
      const command = SLASH_COMMANDS.find(c => c.name === multi)
      if (command) {
        return { type: 'command', command, args: trimmed.slice(multi.length).trim() }
      }
    }
  }

  const spaceIdx = trimmed.indexOf(' ')
  const cmdPart = spaceIdx >= 0 ? trimmed.slice(0, spaceIdx) : trimmed
  const args = spaceIdx >= 0 ? trimmed.slice(spaceIdx + 1).trim() : ''
  const cmdName = cmdPart.toLowerCase()

  const command = SLASH_COMMANDS.find(c => c.name === cmdName)
  if (command) {
    return { type: 'command', command, args }
  }

  return { type: 'unknown-command', command: cmdPart }
}

const HELP_COMMAND_NAMES = new Set([
  '/status',
  '/goal',
  '/tasks',
  '/task',
  '/approve',
  '/retry',
  '/dashboard',
  '/strategy',
  '/evidence',
  '/review',
  '/risk',
  '/merge-ready',
  '/deliver',
  '/prs',
  '/heal',
  '/stale',
  '/ci failures',
  '/reviews',
  '/pr update',
  '/rebase',
  '/ci',
  '/delivery',
  '/merge-policy',
  '/brain',
  '/map',
  '/where',
  '/why',
  '/impact',
  '/context',
  '/inbox',
  '/attention',
  '/summary',
  '/notify',
  '/notify test',
  '/approvals',
  '/channels',
  '/reject',
  '/decision',
  '/dismiss',
  '/recoveries',
  '/agents',
  '/overnight',
  '/loop',
  '/plan',
  '/scan',
  '/jump',
  '/open',
  '/cancel',
  '/logs',
  '/quit',
])

export function formatHelpText(): string {
  const lines = ['Commands:']
  for (const cmd of SLASH_COMMANDS) {
    if (!HELP_COMMAND_NAMES.has(cmd.name)) {
      continue
    }
    if (cmd.name === '/goal') {
      lines.push('/goal <objective> - Create a draft goal')
      lines.push('/goal confirm - Activate the draft goal')
      continue
    }
    if (cmd.name === '/task') {
      lines.push('/task <id> - Show one task in detail')
      lines.push('/task <id> log - Tail worker log (live + poll)')
      lines.push('/task <id> cancel - Cancel task (confirm twice)')
      continue
    }
    if (cmd.name === '/approve') {
      lines.push('/approve <task-id> - Unblock awaiting_human task')
      continue
    }
    if (cmd.name === '/retry') {
      lines.push('/retry <task-id> - Retry a failed task')
      continue
    }
    if (cmd.name === '/cancel') {
      lines.push('/cancel <task-id> - Cancel a running task (confirm twice)')
      continue
    }
    if (cmd.name === '/dashboard') {
      lines.push('/dashboard - Multi-project task counts (no titles)')
      continue
    }
    if (cmd.name === '/strategy') {
      lines.push('/strategy - Show current milestone objective')
      continue
    }
    if (cmd.name === '/evidence') {
      lines.push('/evidence <task-id> - Show durable task evidence')
      continue
    }
    if (cmd.name === '/review') {
      lines.push('/review <task-id> - Show evidence review summary')
      continue
    }
    if (cmd.name === '/risk') {
      lines.push('/risk <task-id> - Show task risk assessment')
      continue
    }
    if (cmd.name === '/merge-ready') {
      lines.push('/merge-ready <task-id> - Check merge readiness under repo policy')
      continue
    }
    if (cmd.name === '/deliver') {
      lines.push('/deliver <task-id> - Deliver task branch to GitHub under policy')
      continue
    }
    if (cmd.name === '/prs') {
      lines.push('/prs - List project delivery PR records')
      continue
    }
    if (cmd.name === '/heal') {
      lines.push('/heal - Run bounded PR self-healing cycle (dry-run)')
      continue
    }
    if (cmd.name === '/stale') {
      lines.push('/stale - List stale delivery PRs')
      continue
    }
    if (cmd.name === '/ci failures') {
      lines.push('/ci failures - List PRs with failed CI')
      continue
    }
    if (cmd.name === '/reviews') {
      lines.push('/reviews - Ingest unresolved PR review comments')
      continue
    }
    if (cmd.name === '/pr update') {
      lines.push('/pr update <delivery-id> - Refresh PR evidence (dry-run)')
      continue
    }
    if (cmd.name === '/rebase') {
      lines.push('/rebase <delivery-id> [--apply] - Dry-run or apply safe rebase')
      continue
    }
    if (cmd.name === '/ci') {
      lines.push('/ci <task-id> - Poll GitHub CI for a task delivery')
      continue
    }
    if (cmd.name === '/delivery') {
      lines.push('/delivery <task-id> - Show delivery record for a task')
      continue
    }
    if (cmd.name === '/merge-policy') {
      lines.push('/merge-policy - Show repo merge and push policy')
      continue
    }
    if (cmd.name === '/brain') {
      lines.push('/brain - Show project brain snapshot')
      continue
    }
    if (cmd.name === '/map') {
      lines.push('/map - Show project structure map')
      continue
    }
    if (cmd.name === '/where') {
      lines.push('/where <query> - Find where to make a change')
      continue
    }
    if (cmd.name === '/why') {
      lines.push('/why <path> - Explain a file path')
      continue
    }
    if (cmd.name === '/impact') {
      lines.push('/impact <path> - Show impact of changing a file')
      continue
    }
    if (cmd.name === '/context') {
      lines.push('/context <task-id> - Show task context packet')
      continue
    }
    if (cmd.name === '/inbox') {
      lines.push('/inbox - Show operator inbox for this project')
      continue
    }
    if (cmd.name === '/attention') {
      lines.push('/attention - Show items needing human attention')
      continue
    }
    if (cmd.name === '/summary') {
      lines.push('/summary - Show operator summary')
      lines.push('/summary morning - Morning summary from durable events')
      continue
    }
    if (cmd.name === '/notify') {
      lines.push('/notify --dry-run - Preview notification delivery')
      continue
    }
    if (cmd.name === '/notify test') {
      lines.push('/notify test - Dry-run notification delivery test')
      continue
    }
    if (cmd.name === '/approvals') {
      lines.push('/approvals - List pending external approval requests')
      continue
    }
    if (cmd.name === '/channels') {
      lines.push('/channels - Show external approval channel configs')
      continue
    }
    if (cmd.name === '/reject') {
      lines.push('/reject <token> - Reject an external approval token')
      continue
    }
    if (cmd.name === '/decision') {
      lines.push('/decision <item-id> - Route safe action for an inbox item')
      continue
    }
    if (cmd.name === '/dismiss') {
      lines.push('/dismiss <item-id> - Dismiss an operator inbox item')
      continue
    }
    if (cmd.name === '/recoveries') {
      lines.push('/recoveries - List pending recovery proposals')
      continue
    }
    if (cmd.name === '/agents') {
      lines.push('/agents - Show agent scorecards and routing hints')
      continue
    }
    if (cmd.name === '/overnight') {
      lines.push('/overnight - Overnight window and latest summary')
      lines.push('/overnight start --until 08:00 - Configure overnight run window')
      continue
    }
    if (cmd.name === '/plan') {
      lines.push('/plan - Show autonomous plan and next action')
      continue
    }
    if (cmd.name === '/scan') {
      lines.push('/scan - Read-only project diagnostics')
      continue
    }
    if (cmd.name === '/jump') {
      lines.push('/jump <task-id|goal|log|worktree> - Resolve a path or hint')
      lines.push('/open <target> - Alias of /jump')
      continue
    }
    if (cmd.name === '/open') {
      continue
    }
    if (cmd.name === '/loop') {
      lines.push('/loop - Loop status')
      lines.push('/loop step - Run one bounded iteration')
      lines.push('/loop start - Start unattended autonomous run')
      lines.push('/loop stop - Stop active autonomous run')
      lines.push('/loop pause - Pause active autonomous run')
      lines.push('/loop resume - Resume paused autonomous run')
      lines.push('/loop run - Show active run status')
      continue
    }
    lines.push(`${cmd.name} - ${cmd.description}`)
  }
  return lines.join('\n')
}

export function completePartial(partial: string): string[] {
  if (!partial.startsWith('/')) return []
  const lower = partial.toLowerCase()
  return SLASH_COMMANDS
    .filter(c => c.name.startsWith(lower))
    .map(c => c.name)
}
