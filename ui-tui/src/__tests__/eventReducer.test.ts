import { describe, expect, it } from 'vitest'
import { reduceEvent } from '../eventReducer.js'
import type { EventEnvelope } from '../protocol.js'
import type { TuiState } from '../domain.js'
import { PROTOCOL_VERSION } from '../protocol.js'

function freshState(): TuiState {
  return {
    connectionState: 'connected',
    transcript: [],
    activities: new Map(),
    lastCursor: 0,
  }
}

function makeEvent(
  cursor: number,
  eventType: string,
  payload: Record<string, unknown> = {},
  projectId = 'proj-a',
): EventEnvelope {
  return {
    type: 'event',
    protocol_version: PROTOCOL_VERSION,
    project_id: projectId,
    cursor,
    event_type: eventType,
    payload,
  }
}

describe('reduceEvent', () => {
  it('ignores duplicate cursor', () => {
    const state = freshState()
    const evt = makeEvent(0, 'chat.message', { text: 'hello' })
    const result = reduceEvent(state, evt)
    expect(result).toBe(state) // same reference
  })

  it('ignores out-of-order duplicate cursor', () => {
    const state = { ...freshState(), lastCursor: 5 }
    const evt = makeEvent(3, 'chat.message', { text: 'hello' })
    const result = reduceEvent(state, evt)
    expect(result).toBe(state)
  })

  it('handles chat.message', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'chat.message', { role: 'coordinator', text: 'Hello!' }))
    expect(result.transcript).toHaveLength(1)
    expect(result.transcript[0]).toMatchObject({ kind: 'message', role: 'coordinator', text: 'Hello!' })
  })

  it('handles user chat.message', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'chat.message', { role: 'user', text: 'do the thing' }))
    expect(result.transcript[0]).toMatchObject({ kind: 'message', role: 'user', text: 'do the thing' })
  })

  it('deduplicates duplicate user chat.message after local echo', () => {
    let state = freshState()
    state = {
      ...state,
      transcript: [
        { id: 'local-1', kind: 'message', role: 'user', text: 'hi' },
      ],
    }
    const result = reduceEvent(state, makeEvent(1, 'chat.message', { role: 'user', text: 'hi' }))
    const visibleUsers = result.transcript.filter(
      item => item.kind === 'message' && item.role === 'user' && item.text === 'hi',
    )
    expect(visibleUsers).toHaveLength(1)
  })

  it('handles chat.stream appending to last coordinator message', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'chat.message', { role: 'coordinator', text: 'Hello' }))
    state = reduceEvent(state, makeEvent(2, 'chat.stream', { text: ' world' }))
    expect(state.transcript).toHaveLength(1)
    expect(state.transcript[0]).toMatchObject({ text: 'Hello world' })
  })

  it('handles chat.stream creating new message if last is not coordinator', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'chat.message', { role: 'user', text: 'hi' }))
    state = reduceEvent(state, makeEvent(2, 'chat.stream', { text: 'streaming...' }))
    expect(state.transcript).toHaveLength(2)
    expect(state.transcript[1]).toMatchObject({ kind: 'message', role: 'coordinator', text: 'streaming...' })
  })

  it('handles task.created with goal and verification commands', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'task.created', {
      task_id: 't1',
      title: 'Run baseline acceptance checks',
      goal: 'Run verification commands without changing code',
      verification_commands: ['uv run pytest -q'],
      state: 'ready',
    }))
    expect(result.activities.get('t1')).toMatchObject({
      title: 'Run baseline acceptance checks',
      goal: 'Run verification commands without changing code',
      verificationCommands: ['uv run pytest -q'],
      state: 'ready',
    })
  })

  it('handles task.done failure reason', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1', title: 'Task 1' }))
    const result = reduceEvent(state, makeEvent(2, 'task.done', {
      task_id: 't1',
      result: 'failed',
      reason: 'agent command failed',
      next_action: 'inspect agent log and retry',
    }))
    expect(result.activities.get('t1')).toMatchObject({
      latestNote: 'agent command failed',
      nextAction: 'inspect agent log and retry',
    })
    expect(result.activities.get('t1')?.stage).toContain('failed')
    expect(result.activities.get('t1')?.stage).toContain('agent command failed')
  })

  it('handles task.created', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1', title: 'Fix bug', agent: 'worker' }))
    expect(result.activities.get('t1')).toMatchObject({
      taskId: 't1',
      title: 'Fix bug',
      agent: 'worker',
      stage: 'created',
    })
    expect(result.transcript).toHaveLength(1)
    expect(result.transcript[0]!.kind).toBe('activity')
  })

  it('handles task.stage transition', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1', title: 'Fix bug' }))
    state = reduceEvent(state, makeEvent(2, 'task.stage', { task_id: 't1', stage: 'running' }))
    expect(state.activities.get('t1')!.stage).toBe('running')
  })

  it('handles task.command', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.command', { task_id: 't1', command: 'npm test' }))
    expect(state.activities.get('t1')!.latestCommand).toBe('npm test')
  })

  it('handles task.output appending lines', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.output', { task_id: 't1', output: 'line1\nline2\n' }))
    state = reduceEvent(state, makeEvent(3, 'task.output', { task_id: 't1', output: 'line3\n' }))
    expect(state.activities.get('t1')!.output).toEqual(['line1', 'line2', 'line3'])
  })

  it('handles task.verification', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.verification', { task_id: 't1', result: 'passed' }))
    expect(state.activities.get('t1')!.stage).toBe('verification: passed')
  })

  it('handles task.review', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.review', { task_id: 't1', result: 'approved' }))
    expect(state.activities.get('t1')!.stage).toBe('review: approved')
  })

  it('handles task.git', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.git', { task_id: 't1', operation: 'commit' }))
    expect(state.activities.get('t1')!.stage).toBe('git: commit')
  })

  it('handles task.fallback', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.fallback', {
      task_id: 't1',
      from_agent: 'worker-a',
      to_agent: 'worker-b',
      used: 1,
      limit: 2,
    }))
    expect(state.activities.get('t1')!.fallback).toEqual({
      from: 'worker-a',
      to: 'worker-b',
      used: 1,
      limit: 2,
    })
  })

  it('handles task.done', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    state = reduceEvent(state, makeEvent(2, 'task.done', { task_id: 't1', result: 'completed' }))
    expect(state.activities.get('t1')!.stage).toBe('done: completed')
  })

  it('handles unknown event types gracefully', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'unknown.event', { foo: 'bar' }))
    expect(result.lastCursor).toBe(1)
    expect(result.transcript).toHaveLength(0)
  })

  it('updates lastCursor', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(42, 'tick_scheduled'))
    expect(result.lastCursor).toBe(42)
  })

  it('preserves activity expanded state across updates', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    // Toggle expanded
    const activities = new Map(state.activities)
    activities.set('t1', { ...activities.get('t1')!, expanded: true })
    state = { ...state, activities }
    // Update stage — should preserve expanded
    state = reduceEvent(state, makeEvent(2, 'task.stage', { task_id: 't1', stage: 'running' }))
    expect(state.activities.get('t1')!.expanded).toBe(true)
  })

  it('bounds output lines to MAX_OUTPUT_LINES', () => {
    let state = freshState()
    state = reduceEvent(state, makeEvent(1, 'task.created', { task_id: 't1' }))
    // Add 250 lines
    const lines = Array.from({ length: 250 }, (_, i) => `line-${i}`).join('\n')
    state = reduceEvent(state, makeEvent(2, 'task.output', { task_id: 't1', output: lines }))
    expect(state.activities.get('t1')!.output.length).toBeLessThanOrEqual(200)
  })

  it('rejects foreign-project event when projectId is set', () => {
    const state = freshState()
    const foreignEvent = makeEvent(1, 'chat.message', { role: 'coordinator', text: 'intrusion' }, 'proj-other')
    const result = reduceEvent(state, foreignEvent, 'proj-a')
    expect(result).toBe(state) // same reference — rejected
    expect(result.transcript).toHaveLength(0)
  })

  it('accepts matching-project event when projectId is set', () => {
    const state = freshState()
    const event = makeEvent(1, 'chat.message', { role: 'coordinator', text: 'hello' }, 'proj-a')
    const result = reduceEvent(state, event, 'proj-a')
    expect(result.transcript).toHaveLength(1)
    expect(result.transcript[0]).toMatchObject({ text: 'hello' })
  })

  it('handles commander.completed rejection diagnostics', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'commander.completed', {
      run_id: 42,
      rejection_reasons: ['Add helper: duplicate title for goal'],
      succeeded: true,
    }))
    const diagnostic = result.activities.get('commander-42')
    expect(diagnostic).toMatchObject({
      title: 'Commander diagnostics',
      stage: 'commander: diagnostics',
      output: ['Add helper: duplicate title for goal'],
    })
    expect(result.transcript).toHaveLength(1)
    expect(result.transcript[0]!.kind).toBe('activity')
  })

  it('ignores commander.completed without rejection reasons', () => {
    const state = freshState()
    const result = reduceEvent(state, makeEvent(1, 'commander.completed', {
      run_id: 42,
      rejection_reasons: [],
      succeeded: true,
    }))
    expect(result.activities.size).toBe(0)
    expect(result.transcript).toHaveLength(0)
  })

  it('rejects foreign-project activity event', () => {
    const state = freshState()
    const foreignEvent = makeEvent(1, 'task.created', { task_id: 't1', title: 'Hack' }, 'proj-other')
    const result = reduceEvent(state, foreignEvent, 'proj-a')
    expect(result).toBe(state)
    expect(result.activities.size).toBe(0)
  })
})
