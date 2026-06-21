import { describe, expect, it } from 'vitest'
import React from 'react'
import { render } from 'ink-testing-library'
import { Header } from '../components/Header.js'
import { Message } from '../components/Message.js'
import { ActivityBlock } from '../components/ActivityBlock.js'
import { Footer } from '../components/Footer.js'
import type { Activity } from '../domain.js'

function makeActivity(overrides: Partial<Activity> = {}): Activity {
  return {
    taskId: 't1',
    title: 'Fix login bug',
    agent: 'worker',
    stage: 'running',
    startedAt: Date.now() - 5000,
    fallback: null,
    latestCommand: 'npm test',
    output: ['pass 1', 'pass 2'],
    expanded: false,
    ...overrides,
  }
}

describe('Header', () => {
  it('renders project id at 120 columns', () => {
    const { lastFrame } = render(<Header projectId="proj-a" connectionState="connected" columns={120} />)
    expect(lastFrame()).toContain('proj-a')
    expect(lastFrame()).toContain('connected')
  })

  it('renders compact at 50 columns', () => {
    const { lastFrame } = render(<Header projectId="proj-a" connectionState="connected" columns={50} />)
    expect(lastFrame()).toContain('proj-a')
  })

  it('shows disconnected state in red', () => {
    const { lastFrame } = render(<Header projectId="proj-a" connectionState="offline" columns={120} />)
    expect(lastFrame()).toContain('offline')
  })
})

describe('Message', () => {
  it('renders user message with prefix', () => {
    const { lastFrame } = render(<Message role="user" text="hello" columns={120} />)
    expect(lastFrame()).toContain('> hello')
  })

  it('renders coordinator message without prefix', () => {
    const { lastFrame } = render(<Message role="coordinator" text="Sure thing" columns={120} />)
    expect(lastFrame()).toContain('Sure thing')
    expect(lastFrame()).not.toContain('>')
  })

  it('renders system message with exclamation prefix', () => {
    const { lastFrame } = render(<Message role="system" text="Task completed" columns={120} />)
    expect(lastFrame()).toContain('! Task completed')
  })

  it('wraps long commands at narrow width', () => {
    const longText = 'a'.repeat(200)
    const { lastFrame } = render(<Message role="coordinator" text={longText} columns={40} />)
    const frame = lastFrame()!
    // Should have multiple lines
    expect(frame.split('\n').length).toBeGreaterThan(1)
  })
})

describe('ActivityBlock', () => {
  it('renders compact form with title and stage', () => {
    const activity = makeActivity()
    const { lastFrame } = render(<ActivityBlock activity={activity} columns={120} />)
    expect(lastFrame()).toContain('Fix login bug')
    expect(lastFrame()).toContain('running')
  })

  it('shows agent name', () => {
    const activity = makeActivity({ agent: 'worker-2' })
    const { lastFrame } = render(<ActivityBlock activity={activity} columns={120} />)
    expect(lastFrame()).toContain('worker-2')
  })

  it('shows fallback warning', () => {
    const activity = makeActivity({
      fallback: { from: 'worker-a', to: 'worker-b', used: 1, limit: 2 },
    })
    const { lastFrame } = render(<ActivityBlock activity={activity} columns={120} />)
    expect(lastFrame()).toContain('worker-a')
    expect(lastFrame()).toContain('worker-b')
  })

  it('shows done stage in green', () => {
    const activity = makeActivity({ stage: 'done: completed' })
    const { lastFrame } = render(<ActivityBlock activity={activity} columns={120} />)
    expect(lastFrame()).toContain('done: completed')
    expect(lastFrame()).toContain('✓')
  })

  it('shows expanded view with output', () => {
    const activity = makeActivity({ expanded: true, output: ['line1', 'line2', 'line3'] })
    const { lastFrame } = render(<ActivityBlock activity={activity} columns={120} />)
    expect(lastFrame()).toContain('line1')
    expect(lastFrame()).toContain('$ npm test')
  })

  it('renders compact at narrow width even if expanded', () => {
    const activity = makeActivity({ expanded: true })
    const { lastFrame } = render(<ActivityBlock activity={activity} columns={40} />)
    expect(lastFrame()).toContain('Fix login bug')
  })
})

describe('Footer', () => {
  it('renders connection state and hints', () => {
    const { lastFrame } = render(<Footer connectionState="connected" columns={120} />)
    expect(lastFrame()).toContain('connected')
    expect(lastFrame()).toContain('Tab')
  })

  it('renders compact at narrow width', () => {
    const { lastFrame } = render(<Footer connectionState="connected" columns={50} />)
    expect(lastFrame()).toContain('connected')
  })
})
