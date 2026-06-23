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

  it('formats project.goal confirm message', () => {
    const text = formatSlashResponse('project.goal', {
      message: 'goal 3 activated',
      status: 'active',
    })
    expect(text).toBe('goal 3 activated')
  })
})