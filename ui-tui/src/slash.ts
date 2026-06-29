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

export function parse(input: string): Parsed {
  const trimmed = input.trim()

  if (!trimmed.startsWith('/')) {
    return { type: 'message', text: trimmed }
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
