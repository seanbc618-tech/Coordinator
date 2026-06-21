import { describe, expect, it } from 'vitest'
import { InputHistory } from '../inputHistory.js'

describe('InputHistory', () => {
  it('pushes and retrieves entries via up/down', () => {
    const history = new InputHistory()
    history.push('first')
    history.push('second')
    history.push('third')

    expect(history.up('')).toBe('third')
    expect(history.up('')).toBe('second')
    expect(history.up('')).toBe('first')
    expect(history.up('')).toBe('first') // stays at 0
  })

  it('down returns to pending', () => {
    const history = new InputHistory()
    history.push('first')
    history.push('second')

    history.up('current') // -> 'second', pending = 'current'
    history.up('')        // -> 'first'
    expect(history.down('')).toBe('second')
    expect(history.down('')).toBe('current') // back to pending
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
    expect(history.up('new')).toBe('first') // starts from end again
  })
})
