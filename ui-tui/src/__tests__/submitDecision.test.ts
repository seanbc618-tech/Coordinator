import { describe, expect, it } from 'vitest'
import { decideSubmit } from '../submitDecision.js'

describe('decideSubmit', () => {
  it('returns local-help for /help without RPC', () => {
    expect(decideSubmit('/help', null)).toEqual({
      action: 'local-help',
      newPending: null,
    })
  })

  it('returns send for /task with args', () => {
    expect(decideSubmit('/task task-abc', null)).toEqual({
      action: 'send',
      method: 'project.task',
      params: { args: 'task-abc' },
      displayMethod: 'project.task',
      newPending: null,
    })
  })

  it('returns send for /task log', () => {
    expect(decideSubmit('/task task-abc log', null)).toEqual({
      action: 'send',
      method: 'project.task.log',
      params: { task_id: 'task-abc' },
      displayMethod: 'project.task.log',
      newPending: null,
    })
  })

  it('requires confirmation for /task cancel', () => {
    expect(decideSubmit('/task task-abc cancel', null)).toEqual({
      action: 'destructive-pending',
      commandName: '/task task-abc cancel',
      newPending: '/task task-abc cancel',
    })
  })

  // --- Phase 5.2: unknown slash commands stay local ---

  it('returns local error for unknown slash /taskz — no chat.send', () => {
    const decision = decideSubmit('/taskz', null)
    expect(decision.action).not.toBe('chat')
    expect(decision).toEqual({
      action: 'local-error',
      text: 'Unknown command: /taskz. Use /help.',
      newPending: null,
    })
  })

  it('returns local error for unknown slash /nota command', () => {
    const decision = decideSubmit('/nota command', null)
    expect(decision.action).not.toBe('chat')
    expect(decision).toEqual({
      action: 'local-error',
      text: 'Unknown command: /nota. Use /help.',
      newPending: null,
    })
  })
})