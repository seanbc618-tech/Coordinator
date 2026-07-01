import { describe, expect, it } from 'vitest'
import { buildSlashRpc, parseTaskSlashArgs, isDestructiveRpc } from '../slashRpc.js'

describe('parseTaskSlashArgs', () => {
  it('parses task id only', () => {
    expect(parseTaskSlashArgs('task-abc')).toEqual({ taskId: 'task-abc', action: null })
  })

  it('parses task id and action', () => {
    expect(parseTaskSlashArgs('task-abc log')).toEqual({ taskId: 'task-abc', action: 'log' })
  })
})

describe('buildSlashRpc', () => {
  it('maps /task log to project.task.log', () => {
    const rpc = buildSlashRpc('/task', 'project.task', 'task-1 log')
    expect(rpc.ok).toBe(true)
    if (rpc.ok) {
      expect(rpc.method).toBe('project.task.log')
      expect(rpc.params).toEqual({ task_id: 'task-1' })
    }
  })

  it('maps /task cancel to project.task.cancel', () => {
    const rpc = buildSlashRpc('/task', 'project.task', 'task-1 cancel')
    expect(rpc.ok).toBe(true)
    if (rpc.ok) {
      expect(rpc.method).toBe('project.task.cancel')
      expect(isDestructiveRpc(rpc.method)).toBe(true)
    }
  })

  it('maps /approve to task_id param', () => {
    const rpc = buildSlashRpc('/approve', 'project.task.approve', 'task-9')
    expect(rpc.ok).toBe(true)
    if (rpc.ok) {
      expect(rpc.method).toBe('project.task.approve')
      expect(rpc.params).toEqual({ task_id: 'task-9' })
    }
  })

  it('maps /approve token to operator.approval.approve', () => {
    const rpc = buildSlashRpc(
      '/approve',
      'project.task.approve',
      'token coord-appr-abc123def456',
    )
    expect(rpc.ok).toBe(true)
    if (rpc.ok) {
      expect(rpc.method).toBe('operator.approval.approve')
      expect(rpc.params).toEqual({
        token: 'coord-appr-abc123def456',
        confirmed: true,
      })
    }
  })
})