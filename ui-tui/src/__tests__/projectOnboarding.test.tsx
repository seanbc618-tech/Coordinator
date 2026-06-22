import { describe, expect, it, vi, beforeEach } from 'vitest'
import React from 'react'
import { EventEmitter } from 'node:events'
import { render } from 'ink-testing-library'
import { ProjectOnboarding, type ProjectInspectDraft } from '../components/ProjectOnboarding.js'
import { registerDetachHandlers, _resetDetachForTesting } from '../detach.js'

const sampleDraft: ProjectInspectDraft = {
  canonical_path: '/Users/dev/my-repo',
  repo_id: 'org/my-repo',
  default_branch: 'main',
  branch_prefix: 'coord/',
  verify_commands: ['npm test', 'npm run lint'],
  allow_push: false,
  merge_policy: 'no_push',
  review_policy: 'full_review',
  max_tasks_per_day: 24,
  max_task_runtime_seconds: 1800,
  registered: false,
  path_changed: false,
}

describe('ProjectOnboarding', () => {
  beforeEach(() => {
    _resetDetachForTesting()
  })

  it('renders canonical path, repo id, branch, verify commands, and policies', () => {
    const { lastFrame } = render(
      <ProjectOnboarding draft={sampleDraft} onAccept={() => {}} onReject={() => {}} />,
    )
    const frame = lastFrame()!
    expect(frame).toContain('/Users/dev/my-repo')
    expect(frame).toContain('org/my-repo')
    expect(frame).toContain('main')
    expect(frame).toContain('coord/')
    expect(frame).toContain('npm test')
    expect(frame).toContain('no_push')
    expect(frame).toContain('full_review')
    expect(frame).toContain('24')
    expect(frame).toContain('1800')
    expect(frame).not.toContain('always trust')
    expect(frame).not.toContain('parent directory')
  })

  it('shows path movement warning when path_changed is true', () => {
    const { lastFrame } = render(
      <ProjectOnboarding
        draft={{ ...sampleDraft, path_changed: true, stored_canonical_path: '/old/path' }}
        onAccept={() => {}}
        onReject={() => {}}
      />,
    )
    expect(lastFrame()).toContain('/old/path')
    expect(lastFrame()).toContain('moved')
  })

  it('Enter accepts and calls onAccept', () => {
    const onAccept = vi.fn()
    const { stdin } = render(
      <ProjectOnboarding draft={sampleDraft} onAccept={onAccept} onReject={() => {}} />,
    )
    stdin.write('\r')
    expect(onAccept).toHaveBeenCalledOnce()
  })

  it('Esc rejects and calls onReject', async () => {
    const onReject = vi.fn()
    const { stdin } = render(
      <ProjectOnboarding draft={sampleDraft} onAccept={() => {}} onReject={onReject} />,
    )
    stdin.write('\x1b')
    await vi.waitFor(() => {
      expect(onReject).toHaveBeenCalledOnce()
    })
  })
})

describe('App onboarding integration', () => {
  beforeEach(() => {
    _resetDetachForTesting()
    vi.resetModules()
  })

  it('shows onboarding before chat when inspect requires confirmation', async () => {
    const inspectResult = { ...sampleDraft, registered: false }
    const request = vi.fn(async (method: string) => {
      if (method === 'project.inspect') {
        return { ok: true, result: inspectResult }
      }
      return { ok: true, result: {} }
    })

    vi.doMock('../supervisorClient.js', () => ({
      SupervisorClient: class extends EventEmitter {
        connect() {
          queueMicrotask(() => this.emit('state', 'connected'))
        }
        close() {}
        request = request
      },
    }))

    const { App } = await import('../app.js')
    const { lastFrame } = render(
      <App socketPath="/tmp/test.sock" projectId="__onboarding__" canonicalPath="/Users/dev/my-repo" />,
    )
    await vi.waitFor(() => {
      expect(lastFrame()).toContain('org/my-repo')
    })
    expect(lastFrame()).not.toContain('Tab')
    expect(request).toHaveBeenCalledWith('project.inspect', { path: '/Users/dev/my-repo' })
  })

  it('registers on accept and then shows chat', async () => {
    const inspectResult = { ...sampleDraft, registered: false }
    const request = vi.fn(async (method: string, params?: Record<string, unknown>) => {
      if (method === 'project.inspect') {
        return { ok: true, result: inspectResult }
      }
      if (method === 'project.register') {
        expect(params?.confirmed).toBe(true)
        return { ok: true, result: { project_id: 'proj-new' } }
      }
      return { ok: true, result: {} }
    })

    vi.doMock('../supervisorClient.js', () => ({
      SupervisorClient: class extends EventEmitter {
        connect() {
          queueMicrotask(() => this.emit('state', 'connected'))
        }
        close() {}
        request = request
      },
    }))

    const { App } = await import('../app.js')
    const { lastFrame, stdin } = render(
      <App socketPath="/tmp/test.sock" projectId="__onboarding__" canonicalPath="/Users/dev/my-repo" />,
    )
    await vi.waitFor(() => {
      expect(lastFrame()).toContain('org/my-repo')
    })
    stdin.write('\r')
    await vi.waitFor(() => {
      expect(request).toHaveBeenCalledWith('project.register', expect.objectContaining({ confirmed: true }))
    })
    await vi.waitFor(() => {
      expect(lastFrame()).toContain('Tab')
    })
  })

  it('reject exits without registering', async () => {
    const inspectResult = { ...sampleDraft, registered: false }
    const request = vi.fn(async (method: string) => {
      if (method === 'project.inspect') {
        return { ok: true, result: inspectResult }
      }
      return { ok: true, result: {} }
    })
    const exitSpy = vi.spyOn(process, 'exit').mockImplementation((() => {}) as never)

    registerDetachHandlers({ closeClient: () => {} })

    vi.doMock('../supervisorClient.js', () => ({
      SupervisorClient: class extends EventEmitter {
        connect() {
          queueMicrotask(() => this.emit('state', 'connected'))
        }
        close() {}
        request = request
      },
    }))

    const { App } = await import('../app.js')
    const { stdin } = render(
      <App socketPath="/tmp/test.sock" projectId="__onboarding__" canonicalPath="/Users/dev/my-repo" />,
    )
    await vi.waitFor(() => {
      expect(request).toHaveBeenCalledWith('project.inspect', { path: '/Users/dev/my-repo' })
    })
    stdin.write('\x1b')
    await vi.waitFor(() => {
      expect(exitSpy).toHaveBeenCalled()
    })
    expect(request).not.toHaveBeenCalledWith('project.register', expect.anything())
    exitSpy.mockRestore()
  })

  it('skips onboarding when project is already registered at path', async () => {
    const inspectResult = {
      ...sampleDraft,
      registered: true,
      project_id: 'proj-existing',
      path_changed: false,
    }
    const request = vi.fn(async (method: string) => {
      if (method === 'project.inspect') {
        return { ok: true, result: inspectResult }
      }
      return { ok: true, result: {} }
    })

    vi.doMock('../supervisorClient.js', () => ({
      SupervisorClient: class extends EventEmitter {
        connect() {
          queueMicrotask(() => this.emit('state', 'connected'))
        }
        close() {}
        request = request
      },
    }))

    const { App } = await import('../app.js')
    const { lastFrame } = render(
      <App socketPath="/tmp/test.sock" projectId="proj-existing" canonicalPath="/Users/dev/my-repo" />,
    )
    await vi.waitFor(() => {
      expect(lastFrame()).toContain('Tab')
    })
    expect(lastFrame()).not.toContain('Register this project')
    expect(request).not.toHaveBeenCalledWith('project.register', expect.anything())
  })
})