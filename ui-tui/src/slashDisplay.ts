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
        ...tasks.map(t => {
          const note = t.latest_note ? ` — ${t.latest_note}` : ''
          const goal = t.goal ? `\n  Goal: ${String(t.goal).slice(0, 120)}` : ''
          return `- ${t.id} [${t.state}] ${t.title}${note}${goal}`
        }),
      ].join('\n')
    }

    case 'project.task': {
      const task = result.task as Record<string, unknown> | undefined
      if (!task) {
        return '(task not found)'
      }
      const lines = [
        `Task ${task.id} [${task.state}] ${task.title}`,
        `Goal: ${task.goal}`,
      ]
      const policy = result.execution_policy as Record<string, unknown> | undefined
      if (policy && Object.keys(policy).length) {
        lines.push(`Policy: ${JSON.stringify(policy)}`)
      }
      if (result.failure_class) {
        lines.push(`Failure: ${result.failure_class} — ${String(result.failure_summary ?? '')}`)
      }
      if (result.human_review_required) {
        lines.push('Human review required')
      }
      const verify = (task.verification_commands as string[] | undefined) ?? []
      if (verify.length) {
        lines.push('Verify:')
        lines.push(...verify.map(cmd => `- ${cmd}`))
      }
      const latest = result.latest_event as Record<string, unknown> | null | undefined
      if (latest) {
        lines.push(
          `Last event: ${latest.old_state} -> ${latest.new_state}: ${latest.note}`,
        )
      }
      const attempt = result.latest_attempt as Record<string, unknown> | null | undefined
      if (attempt) {
        lines.push(
          `Latest attempt: ${attempt.agent_id} exit=${attempt.exit_code} ${attempt.result_class}`,
        )
        if (attempt.log_path) {
          lines.push(`Log: ${attempt.log_path}`)
        }
      }
      if (task.worktree_path) {
        lines.push(`Worktree: ${task.worktree_path}`)
      }
      return lines.join('\n')
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

    case 'supervisor.dashboard': {
      const projects = (result.projects as Array<Record<string, unknown>> | undefined) ?? []
      if (!projects.length) {
        return 'Dashboard — (no projects)'
      }
      return [
        'Dashboard:',
        ...projects.map(entry => {
          const counts = (entry.task_counts as Record<string, number> | undefined) ?? {}
          const countText = Object.keys(counts).length
            ? Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(', ')
            : 'none'
          return `- ${entry.project_id} goal=${entry.goal_status} workers=${entry.active_workers ?? 0} [${countText}]`
        }),
      ].join('\n')
    }

    case 'project.task.approve':
    case 'project.task.retry':
    case 'project.task.cancel': {
      return `Task ${result.task_id} -> ${result.state}`
    }

    case 'project.goal': {
      if (typeof result.message === 'string') {
        return result.message
      }
      if (result.status === 'no goal') {
        return 'Goal — none. Use /goal <objective> to create one.'
      }
      if (result.status === 'draft') {
        return `Goal draft ${result.goal_id}: ${result.progress_summary}`
      }
      const goal = result.goal as Record<string, unknown> | undefined
      if (goal) {
        return `Goal ${goal.id} [${goal.status}] ${goal.title}\n${goal.objective}`
      }
      if (result.goal == null) {
        return 'Goal — none. Use /goal <objective> to create one.'
      }
      return JSON.stringify(result, null, 2)
    }

    default:
      return JSON.stringify(result, null, 2)
  }
}