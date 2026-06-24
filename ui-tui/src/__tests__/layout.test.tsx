import { describe, expect, it } from 'vitest'
import React from 'react'
import { render } from 'ink-testing-library'
import { Header } from '../components/Header.js'
import { Message } from '../components/Message.js'
import { ActivityBlock } from '../components/ActivityBlock.js'
import { Footer } from '../components/Footer.js'
import { Transcript } from '../components/Transcript.js'
import type { Activity, TranscriptItem } from '../domain.js'

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

// Mixed Chinese/English content for layout regression tests.
const MIXED_HELP_TEXT =
  '使用 /help 查看可用命令。Commands: /status 显示项目状态, /tasks 列出任务, ' +
  '/task <id> 查看任务详情, /goal 设置目标, /quit 退出。Use Tab to expand activities.'

const MIXED_TASK_TEXT =
  '任务：添加辅助模块 (Add helper module)。需要创建一个可复用的辅助函数，' +
  '支持中英文混合字符串处理、Unicode 宽度计算、以及行尾自动换行。' +
  'Expected files: 3, expected minutes: 15. Acceptance criteria: helper exists, ' +
  'tests pass, no regressions in existing CJK layout.'

const MIXED_COMMANDER_TEXT =
  '你好！我来帮你规划下一步工作。根据当前项目状态，建议先完成辅助模块的开发，' +
  '然后再处理布局问题。Progress: 2/5 tasks done. Next slice: implement text wrapping ' +
  'utility that handles CJK characters correctly. 收到你的请求后我会创建相应的任务。'

describe('Header', () => {
  it('renders project id at 120 columns', () => {
    const { lastFrame } = render(<Header projectId="proj-a" connectionState="connected" columns={120} />)
    expect(lastFrame()).toContain('proj-a')
    expect(lastFrame()).toContain('connected')
  })

  it('renders at 80 columns', () => {
    const { lastFrame } = render(<Header projectId="proj-a" connectionState="connected" columns={80} />)
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

// ---------------------------------------------------------------------------
// Phase 5.2 layout regression: mixed CJK/English content at 40/80/120 columns
// ---------------------------------------------------------------------------

function makeTranscriptItems(): TranscriptItem[] {
  return [
    { id: 'm1', kind: 'message', role: 'user', text: '你好，帮我看看项目状态' },
    { id: 'm2', kind: 'message', role: 'coordinator', text: MIXED_COMMANDER_TEXT },
    { id: 'a1', kind: 'activity', activity: makeActivity({ title: 'Add helper module — 辅助模块' }) },
    { id: 'm3', kind: 'message', role: 'coordinator', text: MIXED_TASK_TEXT },
    { id: 'm4', kind: 'message', role: 'system', text: MIXED_HELP_TEXT },
  ]
}

/** Count how many times `marker` appears as a standalone token in the frame. */
function countMarker(frame: string, marker: string): number {
  return frame.split('\n').filter(line => line.includes(marker)).length
}

describe('Layout at 40 columns', () => {
  it('renders mixed CJK/English messages without exceeding height', () => {
    const terminalHeight = 24
    const { lastFrame } = render(
      <Transcript items={makeTranscriptItems()} columns={40} height={terminalHeight} />,
    )
    const frame = lastFrame()!
    const lineCount = frame.split('\n').length
    expect(lineCount).toBeLessThanOrEqual(terminalHeight)
  })

  it('footer markers appear exactly once at 40 columns', () => {
    const { lastFrame } = render(<Footer connectionState="connected" columns={40} />)
    const frame = lastFrame()!
    expect(countMarker(frame, 'connected')).toBe(1)
  })
})

describe('Layout at 80 columns', () => {
  it('renders mixed CJK/English messages without exceeding height', () => {
    const terminalHeight = 24
    const { lastFrame } = render(
      <Transcript items={makeTranscriptItems()} columns={80} height={terminalHeight} />,
    )
    const frame = lastFrame()!
    const lineCount = frame.split('\n').length
    expect(lineCount).toBeLessThanOrEqual(terminalHeight)
  })

  it('footer markers appear exactly once at 80 columns', () => {
    const { lastFrame } = render(<Footer connectionState="connected" columns={80} />)
    const frame = lastFrame()!
    expect(countMarker(frame, 'Tab')).toBe(1)
  })
})

describe('Layout at 120 columns', () => {
  it('renders mixed CJK/English messages without exceeding height', () => {
    const terminalHeight = 24
    const { lastFrame } = render(
      <Transcript items={makeTranscriptItems()} columns={120} height={terminalHeight} />,
    )
    const frame = lastFrame()!
    const lineCount = frame.split('\n').length
    expect(lineCount).toBeLessThanOrEqual(terminalHeight)
  })

  it('footer markers appear exactly once at 120 columns', () => {
    const { lastFrame } = render(<Footer connectionState="connected" columns={120} />)
    const frame = lastFrame()!
    expect(countMarker(frame, 'Tab')).toBe(1)
    expect(countMarker(frame, '/help')).toBe(1)
  })
})
