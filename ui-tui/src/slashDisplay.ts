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
          const strategic = (entry.strategic as Record<string, number> | undefined) ?? {}
          const strategicText = (
            `milestones=${strategic.active_milestones ?? 0} `
            + `recoveries=${strategic.pending_recoveries ?? 0} `
            + `overnight=${strategic.overnight_summaries ?? 0}`
          )
          return (
            `- ${entry.project_id} goal=${entry.goal_status} `
            + `workers=${entry.active_workers ?? 0} [${countText}] ${strategicText}`
          )
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

    case 'project.strategy': {
      const milestone = result.current_milestone as Record<string, unknown> | null | undefined
      if (!milestone) {
        return 'Strategy — no active milestone'
      }
      return (
        `Strategy — ${milestone.title} (priority ${milestone.priority ?? 0}); `
        + `active=${result.active_milestone_count ?? 0}`
      )
    }

    case 'project.evidence': {
      const evidence = (result.evidence as Array<Record<string, unknown>> | undefined) ?? []
      if (!evidence.length) {
        return `Evidence — ${result.task_id}: (none)`
      }
      return [
        `Evidence — ${result.task_id}:`,
        ...evidence.map(
          e => `- [${e.type}] ${e.status}: ${e.summary}`,
        ),
      ].join('\n')
    }

    case 'project.review': {
      return (
        `Review — ${result.task_id} [${result.state}] allowed=${result.completion_allowed} `
        + `risk=${result.risk_level ?? 'unknown'} human=${result.requires_human_review}`
      )
    }

    case 'project.risk': {
      const reasons = (result.reasons as string[] | undefined) ?? []
      const reasonText = reasons.length ? reasons.join('; ') : '(none)'
      return (
        `Risk — ${result.task_id}: ${result.risk_level ?? 'unknown'} `
        + `human=${result.requires_human_review}; ${reasonText}`
      )
    }

    case 'project.merge_ready': {
      const blockers = (result.blockers as string[] | undefined) ?? []
      const blockerText = blockers.length ? blockers.join('; ') : '(none)'
      return (
        `Merge-ready — ${result.task_id}: ${result.merge_ready} `
        + `human=${result.requires_human_review}; blockers: ${blockerText}`
      )
    }

    case 'project.deliver': {
      const blockers = (result.blockers as string[] | undefined) ?? []
      const blockerText = blockers.length ? blockers.join('; ') : '(none)'
      const delivery = result.delivery as Record<string, unknown> | null | undefined
      const pr = delivery?.pr_url ?? '(no pr)'
      return (
        `Deliver — ${result.task_id}: allowed=${result.allowed} `
        + `human=${result.requires_human_review}; blockers: ${blockerText}; pr=${pr}`
      )
    }

    case 'project.prs': {
      const prs = (result.prs as Array<Record<string, unknown>> | undefined) ?? []
      if (!prs.length) {
        return 'PRs — none'
      }
      return [
        'PRs:',
        ...prs.map(
          p => `- ${p.task_id ?? '?'} #${p.pr_number ?? '?'} [${p.status}] ${p.last_check_state ?? ''}`,
        ),
      ].join('\n')
    }

    case 'project.ci': {
      return `CI — ${result.task_id}: ${result.ci_state ?? 'unknown'}`
    }

    case 'project.delivery': {
      const delivery = result.delivery as Record<string, unknown> | null | undefined
      if (!delivery) {
        return `Delivery — ${result.task_id}: (none)`
      }
      return (
        `Delivery — ${result.task_id}: ${delivery.status} `
        + `pr=#${delivery.pr_number ?? '?'} ci=${delivery.last_check_state ?? 'unknown'}`
      )
    }

    case 'project.merge_policy': {
      const repos = (result.repos as Array<Record<string, unknown>> | undefined) ?? []
      if (!repos.length) {
        return 'Merge policy — (no repos)'
      }
      return [
        'Merge policy:',
        ...repos.map(
          r => (
            `- ${r.repo_id}: allow_push=${r.allow_push} `
            + `merge_policy=${r.merge_policy} review=${r.review_policy}`
          ),
        ),
      ].join('\n')
    }

    case 'operator.inbox':
    case 'operator.attention': {
      const items = (result.items as Array<Record<string, unknown>> | undefined) ?? []
      if (!items.length) {
        return 'Operator inbox — (empty)'
      }
      return [
        'Operator inbox:',
        ...items.map(
          i => `- [${i.severity}] ${i.title} (${i.source_type}/${i.source_id})`,
        ),
      ].join('\n')
    }

    case 'operator.summary': {
      const counts = (result.counts as Record<string, number> | undefined) ?? {}
      return (
        `Summary — ${result.summary_kind ?? 'current'}: `
        + `total=${counts.total ?? 0} critical=${counts.critical ?? 0}`
      )
    }

    case 'operator.notify': {
      const deliveries = (result.deliveries as Array<Record<string, unknown>> | undefined) ?? []
      const dry = result.dry_run ? ' (dry-run)' : ''
      return `Notify${dry}: ${deliveries.length} delivery record(s)`
    }

    case 'operator.decision': {
      if (result.requires_confirmation) {
        return `Decision — confirmation required for ${result.routed_method}`
      }
      return `Decision — routed to ${result.routed_method}`
    }

    case 'operator.dismiss': {
      return `Dismissed — ${result.item_id} -> ${result.status}`
    }

    case 'project.recoveries': {
      const proposals = (result.proposals as Array<Record<string, unknown>> | undefined) ?? []
      if (!proposals.length) {
        return 'Recoveries — none pending'
      }
      return [
        'Recoveries:',
        ...proposals.map(
          p => `- ${p.task_id} [${p.proposal_type}] ${p.title}`,
        ),
      ].join('\n')
    }

    case 'project.agents': {
      const agents = (result.agents as Array<Record<string, unknown>> | undefined) ?? []
      if (!agents.length) {
        return 'Agents — (none configured)'
      }
      return [
        'Agents:',
        ...agents.map(
          a => (
            `- ${a.agent_id} [${a.role}] ok=${a.successes ?? 0} `
            + `fail=${a.failures ?? 0} rank=${a.preferred_rank ?? '-'}`
          ),
        ),
      ].join('\n')
    }

    case 'project.overnight': {
      const latest = result.latest_summary as Record<string, unknown> | null | undefined
      const latestText = latest
        ? `; last tasks_completed=${latest.tasks_completed ?? 0}`
        : ''
      return (
        `Overnight — quiet ${result.quiet_start}-${result.quiet_end}${latestText}`
      )
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