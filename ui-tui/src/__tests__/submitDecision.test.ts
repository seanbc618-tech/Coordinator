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
      args: 'task-abc',
      newPending: null,
    })
  })

  // --- Phase 5.2: unknown slash commands stay local ---

  it('returns local error for unknown slash /taskz — no chat.send', () => {
    const decision = decideSubmit('/taskz', null)
    // Must NOT be 'chat' (which would send to the backend).
    expect(decision.action).not.toBe('chat')
    // Must be a local action that does not trigger RPC.
    expect(decision.action).toBe('local-help')
  })

  it('returns local error for unknown slash /nota command', () => {
    const decision = decideSubmit('/nota command', null)
    expect(decision.action).not.toBe('chat')
    expect(decision.action).toBe('local-help')
  })
})