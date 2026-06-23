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
  { name: '/logs', description: 'Show recent logs', method: 'project.logs' },
  { name: '/agents', description: 'List active agents', method: 'project.agents' },
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

export type Parsed = ParsedSlash | ParsedMessage

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

  // Unknown slash command — treat as message
  return { type: 'message', text: trimmed }
}

const HELP_COMMAND_NAMES = new Set([
  '/status',
  '/goal',
  '/tasks',
  '/task',
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
