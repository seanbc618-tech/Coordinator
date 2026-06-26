import { describe, expect, it } from 'vitest'
import { appendTaskOutputLines, shouldPollTaskLog } from '../logTailPoller.js'
import type { Activity } from '../domain.js'

function activity(stage: string, startedAt: number | null = Date.now()): Activity {
  return {
    taskId: 't1',
    title: 'Test',
    agent: null,
    stage,
    startedAt,
    fallback: null,
    latestCommand: null,
    output: [],
    expanded: false,
  }
}

describe('shouldPollTaskLog', () => {
  it('polls running tasks', () => {
    expect(shouldPollTaskLog(activity('running'))).toBe(true)
  })

  it('skips terminal tasks', () => {
    expect(shouldPollTaskLog(activity('done: completed'))).toBe(false)
    expect(shouldPollTaskLog(activity('failed: timeout'))).toBe(false)
  })

  it('skips created tasks', () => {
    expect(shouldPollTaskLog(activity('created', null))).toBe(false)
  })
})

describe('appendTaskOutputLines', () => {
  it('appends chunked log lines', () => {
    const next = appendTaskOutputLines(['line1'], 'line2\nline3\n')
    expect(next).toEqual(['line1', 'line2', 'line3'])
  })
})