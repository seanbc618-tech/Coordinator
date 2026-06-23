/**
 * Format Supervisor slash-command RPC results for the TUI transcript.
 */

export function formatSlashResponse(
  method: string,
  result: Record<string, unknown> | null | undefined,
): string {
  if (!result) {
    return '(empty response)'
  }

  switch (method) {
    case 'project.status': {
      const counts = (result.counts as Record<string, number> | undefined) ?? {}
      const countText = Object.keys(counts).length
        ? Object.entries(counts).map(([k, v]) => `${k}: ${v}`).join(', ')
        : 'no tasks'
      const goal = result.goal as Record<string, unknown> | null | undefined
      const goalText = goal
        ? `goal ${goal.id} [${goal.status}] ${goal.title}`
        : 'no goal'
      const flags = [
        result.paused ? 'paused' : null,
        result.stopped ? 'stopped' : null,
      ].filter(Boolean).join(', ')
      return `Status — tasks: ${countText}; ${goalText}${flags ? `; ${flags}` : ''}`
    }

    case 'project.tasks': {
      const tasks = (result.tasks as Array<Record<string, unknown>> | undefined) ?? []
      if (!tasks.length) {
        return 'Tasks — (none)'
      }
      return [
        'Tasks:',
        ...tasks.map(t => `- ${t.id} [${t.state}] ${t.title}`),
      ].join('\n')
    }

    case 'project.logs': {
      const tail = String(result.log_tail ?? '').trim()
      const run = result.commander_run as Record<string, unknown> | null | undefined
      const runText = run
        ? `Latest Commander run: ${run.status} (${run.trigger})`
        : 'Latest Commander run: (none)'
      const logText = tail || '(supervisor log empty)'
      return `${runText}\n--- supervisor log ---\n${logText}`
    }

    case 'project.goal': {
      if (result.status === 'no goal' || result.goal == null) {
        return 'Goal — none. Use /goal <objective> to create one.'
      }
      if (typeof result.message === 'string') {
        return result.message
      }
      const goal = result.goal as Record<string, unknown> | undefined
      if (goal) {
        return `Goal ${goal.id} [${goal.status}] ${goal.title}\n${goal.objective}`
      }
      if (result.status === 'draft') {
        return `Goal draft ${result.goal_id}: ${result.progress_summary}`
      }
      return JSON.stringify(result, null, 2)
    }

    default:
      return JSON.stringify(result, null, 2)
  }
}