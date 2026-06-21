import { describe, expect, it } from 'vitest'

describe('TUI entry', () => {
  it('exports an application factory that can be constructed without starting', async () => {
    const mod = await import('../entry.js')
    expect(typeof mod.createApp).toBe('function')
  })
})
