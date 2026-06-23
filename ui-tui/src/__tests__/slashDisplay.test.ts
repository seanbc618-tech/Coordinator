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

  it('formats project.goal confirm message', () => {
    const text = formatSlashResponse('project.goal', {
      message: 'goal 3 activated',
      status: 'active',
    })
    expect(text).toBe('goal 3 activated')
  })
})