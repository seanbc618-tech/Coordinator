import { describe, expect, it, vi, afterEach } from 'vitest'
import React from 'react'
import { createServer, type Server } from 'node:net'
import { join } from 'node:path'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { render } from 'ink-testing-library'
import { encodeEnvelope, PROTOCOL_VERSION } from '../protocol.js'

const sampleDraft = {
  canonical_path: '/Users/dev/my-repo',
  repo_id: 'org/my-repo',
  default_branch: 'main',
  branch_prefix: 'coord/',
  verify_commands: ['npm test'],
  allow_push: false,
  merge_policy: 'no_push',
  review_policy: 'full_review',
  max_tasks_per_day: 24,
  max_task_runtime_seconds: 1800,
  registered: false,
  path_changed: false,
}

describe('App onboarding real SupervisorClient', () => {
  let tmpDir: string
  let server: Server
  let socketPath: string
  let unmountApp: (() => void) | undefined
  const recorded: Array<{ method: string; projectId: string | null }> = []
  let connections = 0

  afterEach(async () => {
    unmountApp?.()
    unmountApp = undefined
    if (server?.listening) {
      server.closeAllConnections?.()
      await Promise.race([
        new Promise<void>(resolve => server.close(() => resolve())),
        new Promise<void>(resolve => setTimeout(resolve, 500)),
      ])
    }
    if (tmpDir) {
      await rm(tmpDir, { recursive: true, force: true })
    }
    recorded.length = 0
    connections = 0
    vi.resetModules()
  })

  it('rebinds client after registration for snapshot and subscribe RPCs', async () => {
    tmpDir = await mkdtemp(join(tmpdir(), 'coord-onboard-client-'))
    socketPath = join(tmpDir, 'test.sock')
    const inspectResult = { ...sampleDraft, registered: false }

    await new Promise<void>(resolve => {
      server = createServer(sock => {
        connections += 1
        let buf = ''
        sock.on('error', () => {})
        sock.on('data', data => {
          buf += data.toString()
          let idx: number
          while ((idx = buf.indexOf('\n')) !== -1) {
            const line = buf.slice(0, idx)
            buf = buf.slice(idx + 1)
            if (!line.trim()) continue
            const parsed = JSON.parse(line)
            if (parsed.type !== 'request') continue
            recorded.push({ method: parsed.method, projectId: parsed.project_id ?? null })
            const result =
              parsed.method === 'project.inspect'
                ? inspectResult
                : parsed.method === 'project.register'
                  ? { project_id: 'proj-new' }
                  : parsed.method === 'events.subscribe'
                    ? { subscription_id: 'sub-1', replayed: [] }
                    : parsed.method === 'project.snapshot'
                      ? { cursor: 0 }
                      : {}
            sock.write(
              encodeEnvelope({
                type: 'response',
                protocol_version: PROTOCOL_VERSION,
                request_id: parsed.request_id,
                ok: true,
                result,
                error: null,
              }) + '\n',
            )
          }
        })
      })
      server.listen(socketPath, resolve)
    })

    const { App } = await import('../app.js')
    const { lastFrame, stdin, unmount } = render(
      <App socketPath={socketPath} projectId="__onboarding__" canonicalPath="/Users/dev/my-repo" />,
    )
    unmountApp = unmount

    await vi.waitFor(() => {
      expect(lastFrame()).toContain('org/my-repo')
    })
    await vi.waitFor(() => {
      expect(recorded.some(r => r.method === 'project.inspect')).toBe(true)
    })
    stdin.write('\r')
    await vi.waitFor(() => {
      expect(recorded.some(r => r.method === 'project.register')).toBe(true)
    })
    await vi.waitFor(() => {
      expect(lastFrame()).toContain('Tab')
    })

    await vi.waitFor(() => {
      expect(recorded.filter(r => r.method === 'project.register').length).toBeGreaterThan(0)
    })
    await new Promise(resolve => setTimeout(resolve, 300))
    const subscribes = recorded.filter(r => r.method === 'events.subscribe')
    expect(connections).toBe(2)
    expect(subscribes.filter(r => r.projectId === '__onboarding__')).toHaveLength(1)
    expect(subscribes.filter(r => r.projectId === 'proj-new')).toHaveLength(1)
    const snapshot = recorded.filter(r => r.method === 'project.snapshot').at(-1)
    const subscribe = subscribes.filter(r => r.projectId === 'proj-new').at(-1)
    expect(snapshot?.projectId).toBe('proj-new')
    expect(subscribe?.projectId).toBe('proj-new')
    expect(recorded.filter(r => r.method === 'project.inspect').every(r => r.projectId === '__onboarding__')).toBe(true)

    unmount()
    unmountApp = undefined
  })
})