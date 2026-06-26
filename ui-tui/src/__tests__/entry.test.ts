import { describe, expect, it } from 'vitest'
import { createApp, parseEntryArgs } from '../entry.js'

describe('TUI entry', () => {
  it('exports an application factory that can be constructed without starting', async () => {
    const mod = await import('../entry.js')
    expect(typeof mod.createApp).toBe('function')
  })

  it('parseEntryArgs reads socket, projectId, and canonical path from argv', () => {
    const parsed = parseEntryArgs([
      'node',
      '/path/to/entry.js',
      '/tmp/coordinator.sock',
      '__onboarding__',
      '/Users/dev/my-repo',
    ])
    expect(parsed).toEqual({
      socketPath: '/tmp/coordinator.sock',
      projectId: '__onboarding__',
      canonicalPath: '/Users/dev/my-repo',
    })
  })

  it('parseEntryArgs omits canonical path when absent', () => {
    const parsed = parseEntryArgs([
      'node',
      '/path/to/entry.js',
      '/tmp/coordinator.sock',
      'proj-abc123',
    ])
    expect(parsed).toEqual({
      socketPath: '/tmp/coordinator.sock',
      projectId: 'proj-abc123',
      canonicalPath: undefined,
    })
  })

  it('parseEntryArgs rejects missing required arguments', () => {
    expect(parseEntryArgs(['node', '/path/to/entry.js'])).toBeNull()
    expect(parseEntryArgs(['node', '/path/to/entry.js', '/tmp/coordinator.sock'])).toBeNull()
  })

  it('createApp forwards canonicalPath to the app factory', () => {
    const app = createApp({
      socketPath: '/tmp/coordinator.sock',
      projectId: '__onboarding__',
      canonicalPath: '/Users/dev/my-repo',
    })
    expect(app.canonicalPath).toBe('/Users/dev/my-repo')
  })
})