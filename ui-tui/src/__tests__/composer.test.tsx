import { describe, expect, it } from 'vitest'
import { InputHistory } from '../inputHistory.js'
import { completePartial } from '../slash.js'
import { decideSubmit } from '../submitDecision.js'

/**
 * Composer interaction tests.
 *
 * Since Ink's useInput hook requires a real terminal, we test the
 * logic through the underlying modules (InputHistory, slash.parse)
 * and the App's destructive confirmation state machine.
 */

describe('Composer: history', () => {
  it('pushes and retrieves entries via up/down', () => {
    const history = new InputHistory()
    history.push('first')
    history.push('second')
    history.push('third')

    expect(history.up('')).toBe('third')
    expect(history.up('')).toBe('second')
    expect(history.up('')).toBe('first')
    expect(history.up('')).toBe('first')
  })

  it('down returns to pending', () => {
    const history = new InputHistory()
    history.push('first')
    history.push('second')

    history.up('current')
    history.up('')
    expect(history.down('')).toBe('second')
    expect(history.down('')).toBe('current')
  })

  it('returns current input when empty', () => {
    const history = new InputHistory()
    expect(history.up('current')).toBe('current')
  })

  it('does not duplicate consecutive entries', () => {
    const history = new InputHistory()
    history.push('same')
    history.push('same')
    expect(history.length).toBe(1)
  })

  it('ignores empty entries', () => {
    const history = new InputHistory()
    history.push('')
    history.push('  ')
    expect(history.length).toBe(0)
  })

  it('reset clears navigation state', () => {
    const history = new InputHistory()
    history.push('first')
    history.up('pending')
    history.reset()
    expect(history.up('new')).toBe('first')
  })
})

describe('Composer: destructive confirmation state machine', () => {
  // Tests exercise the real decideSubmit production helper.

  it('/stop first entry does NOT confirm', () => {
    const d = decideSubmit('/stop', null)
    expect(d.action).toBe('destructive-pending')
    expect(d.newPending).toBe('/stop')
  })

  it('/stop second entry confirms', () => {
    const d = decideSubmit('/stop', '/stop')
    expect(d.action).toBe('destructive-confirmed')
    expect(d.newPending).toBe(null)
  })

  it('/shutdown first entry does NOT confirm', () => {
    const d = decideSubmit('/shutdown', null)
    expect(d.action).toBe('destructive-pending')
    expect(d.newPending).toBe('/shutdown')
  })

  it('/shutdown second entry confirms', () => {
    const d = decideSubmit('/shutdown', '/shutdown')
    expect(d.action).toBe('destructive-confirmed')
  })

  it('different destructive command resets pending', () => {
    const d = decideSubmit('/shutdown', '/stop')
    expect(d.action).toBe('destructive-pending')
    expect(d.newPending).toBe('/shutdown')
  })

  it('/stop, /stop → one project.stop RPC', () => {
    let d = decideSubmit('/stop', null)
    expect(d.action).toBe('destructive-pending')
    d = decideSubmit('/stop', d.newPending)
    expect(d.action).toBe('destructive-confirmed')
    expect(d.method).toBe('project.stop')
  })

  it('/stop, /status, /stop → zero project.stop RPCs', () => {
    let d = decideSubmit('/stop', null)
    expect(d.action).toBe('destructive-pending')
    d = decideSubmit('/status', d.newPending)
    expect(d.action).toBe('send')
    expect(d.newPending).toBe(null) // cleared by non-destructive
    d = decideSubmit('/stop', d.newPending)
    expect(d.action).toBe('destructive-pending') // new confirmation cycle
    expect(d.newPending).toBe('/stop')
  })

  it('/shutdown, hello, /shutdown → zero system.shutdown RPCs', () => {
    let d = decideSubmit('/shutdown', null)
    expect(d.action).toBe('destructive-pending')
    d = decideSubmit('hello', d.newPending)
    expect(d.action).toBe('chat')
    expect(d.newPending).toBe(null) // cleared by plain message
    d = decideSubmit('/shutdown', d.newPending)
    expect(d.action).toBe('destructive-pending') // new cycle, no confirmation
  })

  it('/stop, /shutdown → zero destructive RPCs', () => {
    let d = decideSubmit('/stop', null)
    expect(d.action).toBe('destructive-pending')
    expect(d.newPending).toBe('/stop')
    d = decideSubmit('/shutdown', d.newPending)
    expect(d.action).toBe('destructive-pending')
    expect(d.newPending).toBe('/shutdown') // resets to new command
  })

  it('/shutdown, reconnect, /shutdown → zero system.shutdown RPCs', () => {
    let d = decideSubmit('/shutdown', null)
    expect(d.action).toBe('destructive-pending')
    // Simulate reconnect clearing pending
    d = decideSubmit('/shutdown', null) // reconnect cleared it
    expect(d.action).toBe('destructive-pending')
    expect(d.newPending).toBe('/shutdown')
  })

  it('plain message clears pending destructive', () => {
    const d = decideSubmit('hello', '/stop')
    expect(d.action).toBe('chat')
    expect(d.newPending).toBe(null)
  })

  it('non-destructive command does not enter pending', () => {
    const d = decideSubmit('/status', null)
    expect(d.action).toBe('send')
    expect(d.newPending).toBe(null)
  })

  it('non-destructive command clears pending destructive', () => {
    const d = decideSubmit('/status', '/shutdown')
    expect(d.action).toBe('send')
    expect(d.newPending).toBe(null)
  })
})

describe('Composer: slash completion', () => {
  it('completes /st to matching commands', () => {
    const results = completePartial('/st')
    expect(results).toContain('/status')
    expect(results).toContain('/stop')
  })

  it('completes /h to /help', () => {
    const results = completePartial('/h')
    expect(results).toEqual(['/help'])
  })

  it('returns empty for non-slash input', () => {
    expect(completePartial('hello')).toEqual([])
  })
})
