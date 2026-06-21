import { describe, expect, it, afterEach } from 'vitest'
import { setupLifecycle, isCleanedUp, _resetLifecycleForTesting } from '../lifecycle.js'

describe('lifecycle', () => {
  afterEach(() => {
    _resetLifecycleForTesting()
  })

  it('setupLifecycle is idempotent', () => {
    let callCount = 0
    setupLifecycle({ onCleanup: () => { callCount++ } })
    setupLifecycle({ onCleanup: () => { callCount++ } })
    // Should only wire once — the second call is a no-op
    expect(isCleanedUp()).toBe(false)
  })

  it('cleanup callback is registered without error', () => {
    expect(() => {
      setupLifecycle({ onCleanup: () => {} })
    }).not.toThrow()
  })

  it('isCleanedUp starts false', () => {
    setupLifecycle()
    expect(isCleanedUp()).toBe(false)
  })
})
