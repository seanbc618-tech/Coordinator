import { describe, expect, it } from 'vitest'
import { formatSlashResponse } from '../slashDisplay.js'

describe('formatSlashResponse', () => {
  it('formats project.status with goal and counts', () => {
    const text = formatSlashResponse('project.status', {
      counts: { ready: 2 },
      paused: false,
      stopped: false,
      goal: { id: 1, status: 'active', title: 'Roadmap', progress_summary: '' },
    })
    expect(text).toContain('ready: 2')
    expect(text).toContain('goal 1 [active]')
  })

  it('formats empty project.tasks', () => {
    expect(formatSlashResponse('project.tasks', { tasks: [] })).toContain('none')
  })

  it('formats project.task detail', () => {
    const text = formatSlashResponse('project.task', {
      task: {
        id: 'task-1',
        title: 'Run baseline acceptance checks',
        state: 'failed',
        goal: 'Run verification commands',
        verification_commands: ['uv run pytest -q'],
        worktree_path: '/tmp/worktree',
      },
      latest_event: {
        old_state: 'running',
        new_state: 'failed',
        note: 'no changed files',
      },
      latest_attempt: {
        agent_id: 'claude_worker',
        exit_code: 0,
        result_class: 'interactive_blocked',
        log_path: '/tmp/agent.log',
      },
      artifacts: [{ kind: 'agent_log', path: '/tmp/agent.log' }],
    })
    expect(text).toContain('Run baseline acceptance checks')
    expect(text).toContain('uv run pytest -q')
    expect(text).toContain('no changed files')
    expect(text).toContain('agent.log')
  })

  it('formats project.task.log tail', () => {
    const text = formatSlashResponse('project.task.log', {
      task_id: 'task-1',
      content: 'worker line 1\nworker line 2\n',
    })
    expect(text).toContain('task-1 log')
    expect(text).toContain('worker line 1')
  })

  it('formats project.plan summary', () => {
    const text = formatSlashResponse('project.plan', {
      goal: { id: 1, status: 'active', title: 'Roadmap' },
      run: { status: 'running', last_decision: 'wait' },
      backlog: { ready: 2, blocked: 0 },
      tasks: { running: 1, failed: 0 },
      next: 'wait for running task',
    })
    expect(text).toContain('Plan')
    expect(text).toContain('goal 1 [active]')
    expect(text).toContain('failed=0')
    expect(text).toContain('wait for running task')
  })

  it('formats project.scan diagnostics', () => {
    const text = formatSlashResponse('project.scan', {
      git_root_exists: true,
      working_tree: { clean: true, changed_files: 0 },
      verify_commands: ['true'],
      failed_tasks: 1,
      active_run: null,
    })
    expect(text).toContain('verify')
    expect(text).toContain('failed tasks: 1')
  })

  it('formats project.jump hint without editor command', () => {
    const text = formatSlashResponse('project.jump', {
      target_type: 'task.log',
      path: '/tmp/agent.log',
      hint: 'Task log: /tmp/agent.log',
    })
    expect(text).toContain('/tmp/agent.log')
    expect(text.toLowerCase()).not.toContain('open ')
    expect(text.toLowerCase()).not.toContain('cursor ')
  })

  it('formats project.goal confirm message', () => {
    const text = formatSlashResponse('project.goal', {
      message: 'goal 3 activated',
      status: 'active',
    })
    expect(text).toBe('goal 3 activated')
  })
})