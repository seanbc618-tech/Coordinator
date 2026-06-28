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

    case 'project.loop.status':
    case 'project.loop.run.status': {
      const run = result.run as Record<string, unknown> | null | undefined
      if (!run) {
        return `Loop [${result.project_id}] — run: none`
      }
      return (
        `Loop [${result.project_id}] — run: ${run.status} ${run.id}, `
        + `iterations=${run.iteration_count ?? 0}, idle=${run.idle_iteration_count ?? 0}`
      )
    }

    case 'project.loop.step': {
      return `Loop step — ${result.decision}: ${result.reason}`
    }

    case 'project.loop.start':
    case 'project.loop.stop':
    case 'project.loop.pause':
    case 'project.loop.resume': {
      const run = result.run as Record<string, unknown> | null | undefined
      if (!run) {
        return `Loop run [${result.project_id}] — run: none`
      }
      return (
        `Loop run [${result.project_id}] — ${run.status} ${run.id}, `
        + `iterations=${run.iteration_count ?? 0}, idle=${run.idle_iteration_count ?? 0}`
      )
    }

    case 'supervisor.dashboard': {
      const projects = (result.projects as Array<Record<string, unknown>> | undefined) ?? []
      const runs = result.autonomous_runs as Record<string, number> | undefined
      if (!projects.length) {
        return 'Dashboard — (no projects)'
      }
      const lines = ['Dashboard:']
      if (runs) {
        lines.push(
          `autonomous_runs: running=${runs.running ?? 0} `
          + `paused=${runs.paused ?? 0} stopped=${runs.stopped ?? 0}`,
        )
      }
      return [
        ...lines,
        ...projects.map(entry => {
          const counts = (entry.task_counts as Record<string, number> | undefined) ?? {}
          const countText = Object.keys(counts).length
            ? Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(', ')
            : 'none'
          return `- ${entry.project_id} goal=${entry.goal_status} workers=${entry.active_workers ?? 0} [${countText}]`
        }),
      ].join('\n')
    }

    case 'project.task.log': {
      const content = String(result.content ?? '').trim()
      if (!content) {
        return `Task ${result.task_id} log: (empty)`
      }
      const tail = content.length > 4000 ? content.slice(-4000) : content
      return `Task ${result.task_id} log:\n${tail}`
    }

    case 'project.task.approve':
    case 'project.task.retry':
    case 'project.task.cancel': {
      const terminated = result.worker_terminated === true ? ' (worker stopped)' : ''
      return `Task ${result.task_id} -> ${result.state}${terminated}`
    }

    case 'project.plan': {
      const goal = result.goal as Record<string, unknown> | null | undefined
      const run = result.run as Record<string, unknown> | null | undefined
      const backlog = (result.backlog as Record<string, number> | undefined) ?? {}
      const tasks = (result.tasks as Record<string, number> | undefined) ?? {}
      const goalText = goal
        ? `goal ${goal.id} [${goal.status}] ${goal.title}`
        : 'no goal'
      const runText = run
        ? `run ${run.status} (${run.last_decision ?? 'wait'})`
        : 'run none'
      return (
        `Plan — ${goalText}; ${runText}; `
        + `backlog ready=${backlog.ready ?? 0} blocked=${backlog.blocked ?? 0}; `
        + `tasks running=${tasks.running ?? 0} failed=${tasks.failed ?? 0}; `
        + `next: ${String(result.next ?? 'wait')}`
      )
    }

    case 'project.scan': {
      const worktree = (result.working_tree as Record<string, unknown> | undefined) ?? {}
      const cleanText = worktree.clean ? 'clean' : 'dirty'
      const verify = (result.verify_commands as string[] | undefined) ?? []
      const verifyText = verify.length ? verify.join(', ') : '(none)'
      const activeRun = result.active_run as Record<string, unknown> | null | undefined
      const runText = activeRun ? `${activeRun.status} (${activeRun.id})` : 'none'
      return (
        `Scan — git ${result.git_root_exists ? 'ok' : 'missing'}, `
        + `tree ${cleanText}, verify: ${verifyText}, `
        + `failed tasks: ${result.failed_tasks ?? 0}, run: ${runText}`
      )
    }

    case 'project.jump': {
      return String(result.hint ?? result.path ?? '(no target)')
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