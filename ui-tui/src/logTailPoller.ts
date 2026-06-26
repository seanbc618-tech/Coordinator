/**
 * Poll project.task.log for live activities when push events may lag.
 */

import type { SupervisorClient } from './supervisorClient.js'
import type { Activity } from './domain.js'
import { MAX_OUTPUT_LINES } from './domain.js'

const INITIAL_INTERVAL_MS = 500
const MAX_INTERVAL_MS = 2000

export function shouldPollTaskLog(activity: Activity): boolean {
  if (activity.stage.startsWith('done:') || activity.stage.startsWith('failed:')) {
    return false
  }
  if (activity.stage === 'created' || activity.stage.startsWith('commander:')) {
    return false
  }
  return activity.startedAt !== null
}

interface PollState {
  offset: number
  intervalMs: number
  timer: ReturnType<typeof setTimeout> | null
}

export class LogTailPoller {
  private readonly client: SupervisorClient
  private readonly states = new Map<string, PollState>()
  private readonly onAppend: (taskId: string, text: string) => void
  private stopped = false

  constructor(
    client: SupervisorClient,
    onAppend: (taskId: string, text: string) => void,
  ) {
    this.client = client
    this.onAppend = onAppend
  }

  sync(activities: Map<string, Activity>): void {
    if (this.stopped) {
      return
    }

    const liveIds = new Set<string>()
    for (const [taskId, activity] of activities) {
      if (!shouldPollTaskLog(activity)) {
        this.stopTask(taskId)
        continue
      }
      liveIds.add(taskId)
      if (!this.states.has(taskId)) {
        this.states.set(taskId, {
          offset: 0,
          intervalMs: INITIAL_INTERVAL_MS,
          timer: null,
        })
        this.schedule(taskId)
      }
    }

    for (const taskId of this.states.keys()) {
      if (!liveIds.has(taskId)) {
        this.stopTask(taskId)
      }
    }
  }

  stop(): void {
    this.stopped = true
    for (const taskId of [...this.states.keys()]) {
      this.stopTask(taskId)
    }
  }

  private stopTask(taskId: string): void {
    const state = this.states.get(taskId)
    if (!state) {
      return
    }
    if (state.timer) {
      clearTimeout(state.timer)
    }
    this.states.delete(taskId)
  }

  private schedule(taskId: string): void {
    const state = this.states.get(taskId)
    if (!state || this.stopped) {
      return
    }
    state.timer = setTimeout(() => {
      void this.pollOnce(taskId)
    }, state.intervalMs)
  }

  private async pollOnce(taskId: string): Promise<void> {
    const state = this.states.get(taskId)
    if (!state || this.stopped) {
      return
    }
    state.timer = null

    try {
      const resp = await this.client.request('project.task.log', {
        task_id: taskId,
        offset: state.offset,
        max_bytes: 65536,
      })
      if (!resp.ok || !resp.result) {
        state.intervalMs = Math.min(state.intervalMs * 2, MAX_INTERVAL_MS)
        this.schedule(taskId)
        return
      }

      const result = resp.result as {
        content?: string
        next_offset?: number
        eof?: boolean
      }
      const content = String(result.content ?? '')
      const nextOffset = Number(result.next_offset ?? state.offset)
      if (content) {
        this.onAppend(taskId, content)
        state.offset = nextOffset
        state.intervalMs = INITIAL_INTERVAL_MS
      } else {
        state.intervalMs = Math.min(state.intervalMs + 250, MAX_INTERVAL_MS)
      }
      if (!result.eof) {
        state.offset = nextOffset
      }
    } catch {
      state.intervalMs = Math.min(state.intervalMs * 2, MAX_INTERVAL_MS)
    }

    if (this.states.has(taskId) && !this.stopped) {
      this.schedule(taskId)
    }
  }
}

export function appendTaskOutputLines(
  output: string[],
  chunk: string,
): string[] {
  if (!chunk) {
    return output
  }
  const lines = chunk.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n')
  const merged = [...output]
  for (const line of lines) {
    if (line === '' && merged.length === 0) {
      continue
    }
    merged.push(line)
  }
  while (merged.length > 0 && merged[merged.length - 1] === '') {
    merged.pop()
  }
  return merged.slice(-MAX_OUTPUT_LINES)
}