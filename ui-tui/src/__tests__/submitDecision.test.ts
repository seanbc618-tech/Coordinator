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
})