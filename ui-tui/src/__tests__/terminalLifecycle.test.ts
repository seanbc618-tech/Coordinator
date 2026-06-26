import { describe, expect, it, afterEach, vi } from 'vitest'
import { resetTerminalModes } from '../lib/terminalModes.js'
import { setupLifecycle, isCleanedUp, _resetLifecycleForTesting } from '../lifecycle.js'

const SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP', 'exit', 'uncaughtException', 'unhandledRejection'] as const

describe('lifecycle', () => {
  afterEach(() => {
    for (const sig of SIGNALS) {
      process.removeAllListeners(sig)
    }
    _resetLifecycleForTesting()
    vi.restoreAllMocks()
  })

  it('setupLifecycle is idempotent', () => {
    let cleanupCalls = 0
    setupLifecycle({ onCleanup: () => { cleanupCalls++ } })
    setupLifecycle({ onCleanup: () => { cleanupCalls++ } })
    expect(isCleanedUp()).toBe(false)
    expect(cleanupCalls).toBe(0)
  })

  it('SIGTERM triggers cleanup and exits 143', () => {
    const onCleanup = vi.fn()
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation((() => undefined) as typeof process.exit)
    setupLifecycle({ onCleanup })
    process.emit('SIGTERM')
    expect(onCleanup).toHaveBeenCalledTimes(1)
    expect(isCleanedUp()).toBe(true)
    expect(exitSpy).toHaveBeenCalledWith(143)
  })

  it('uncaughtException triggers cleanup and exits 1', () => {
    const onCleanup = vi.fn()
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation((() => undefined) as typeof process.exit)
    setupLifecycle({ onCleanup })
    process.emit('uncaughtException', new Error('test failure'))
    expect(onCleanup).toHaveBeenCalledTimes(1)
    expect(isCleanedUp()).toBe(true)
    expect(exitSpy).toHaveBeenCalledWith(1)
  })

  it('resetTerminalModes returns false for non-TTY streams', () => {
    const fakeStream = { isTTY: false, write: () => true }
    expect(resetTerminalModes(fakeStream)).toBe(false)
  })
})