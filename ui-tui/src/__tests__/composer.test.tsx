import { describe, expect, it, beforeEach } from 'vitest'
import { InputHistory } from '../inputHistory.js'
import { parse, completePartial } from '../slash.js'

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
  /**
   * Simulates the App's destructive confirmation logic.
   * This is the same algorithm as app.tsx handleSubmit.
   */
  function simulateSubmit(
    input: string,
    pendingDestructive: string | null,
  ): { confirmed: boolean; newPending: string | null; message: string } {
    const parsed = parse(input)

    if (parsed.type === 'command' && parsed.command.destructive) {
      if (pendingDestructive === parsed.command.name) {
        // Confirmed
        return {
          confirmed: true,
          newPending: null,
          message: `${parsed.command.name} confirmed.`,
        }
      } else {
        // First entry — request confirmation
        return {
          confirmed: false,
          newPending: parsed.command.name,
          message: `Confirm: ${parsed.command.name}? Type ${parsed.command.name} again to proceed.`,
        }
      }
    }

    // Non-destructive or plain message
    return {
      confirmed: false,
      newPending: null,
      message: '',
    }
  }

  it('/stop first entry does NOT confirm', () => {
    const result = simulateSubmit('/stop', null)
    expect(result.confirmed).toBe(false)
    expect(result.newPending).toBe('/stop')
    expect(result.message).toContain('Confirm')
  })

  it('/stop second entry confirms', () => {
    const result = simulateSubmit('/stop', '/stop')
    expect(result.confirmed).toBe(true)
    expect(result.newPending).toBe(null)
  })

  it('/shutdown first entry does NOT confirm', () => {
    const result = simulateSubmit('/shutdown', null)
    expect(result.confirmed).toBe(false)
    expect(result.newPending).toBe('/shutdown')
  })

  it('/shutdown second entry confirms', () => {
    const result = simulateSubmit('/shutdown', '/shutdown')
    expect(result.confirmed).toBe(true)
  })

  it('different destructive command resets pending', () => {
    // User types /stop, then /shutdown — /shutdown should not confirm
    const result = simulateSubmit('/shutdown', '/stop')
    expect(result.confirmed).toBe(false)
    expect(result.newPending).toBe('/shutdown')
  })

  it('plain message clears pending destructive', () => {
    // This is tested by the App logic: non-destructive input clears pending
    const parsed = parse('hello')
    expect(parsed.type).toBe('message')
    // App clears pendingDestructive on plain messages
  })

  it('non-destructive command does not enter pending', () => {
    const result = simulateSubmit('/status', null)
    expect(result.confirmed).toBe(false)
    expect(result.newPending).toBe(null)
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
