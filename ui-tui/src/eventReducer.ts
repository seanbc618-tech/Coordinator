/**
 * Pure event reducer for Coordinator TUI state.
 *
 * Takes the current state and an event, returns the new state.
 * Returns unchanged state for duplicate or foreign events.
 * This function is pure: no module-level mutable state.
 */

import type { EventEnvelope } from './protocol.js'
import type { TuiState, TranscriptItem, Activity } from './domain.js'
import { MAX_OUTPUT_LINES } from './domain.js'

function nextItemId(state: TuiState): string {
  // Derive ID from transcript length + cursor — pure, no module state.
  return `item-${state.transcript.length}-${state.lastCursor}`
}

/**
 * Reduce an event into TUI state.
 *
 * @param state Current TUI state.
 * @param event Supervisor event envelope.
 * @param projectId The project this TUI is scoped to. If set, foreign
 *   events (different project_id) are rejected.
 */
export function reduceEvent(state: TuiState, event: EventEnvelope, projectId?: string): TuiState {
  // Reject foreign events when project scope is known.
  if (projectId && event.project_id !== projectId) {
    return state
  }

  // Deduplicate by cursor
  if (event.cursor <= state.lastCursor) {
    return state
  }

  const newState = { ...state, lastCursor: event.cursor }

  switch (event.event_type) {
    case 'chat.message':
      return reduceChatMessage(newState, event.payload)
    case 'chat.stream':
      return reduceChatStream(newState, event.payload)
    case 'task.created':
      return reduceTaskCreated(newState, event.payload)
    case 'task.stage':
      return reduceTaskStage(newState, event.payload)
    case 'task.command':
      return reduceTaskCommand(newState, event.payload)
    case 'task.output':
      return reduceTaskOutput(newState, event.payload)
    case 'task.verification':
      return reduceTaskVerification(newState, event.payload)
    case 'task.review':
      return reduceTaskReview(newState, event.payload)
    case 'task.git':
      return reduceTaskGit(newState, event.payload)
    case 'task.fallback':
      return reduceTaskFallback(newState, event.payload)
    case 'task.done':
      return reduceTaskDone(newState, event.payload)
    case 'tick_scheduled':
    case 'cycle_complete':
      // Internal supervisor events — no TUI state change
      return newState
    default:
      return newState
  }
}

function addMessage(state: TuiState, role: 'user' | 'coordinator' | 'system', text: string): TuiState {
  const item: TranscriptItem = { id: nextItemId(state), kind: 'message', role, text }
  return { ...state, transcript: [...state.transcript, item] }
}

function upsertActivity(state: TuiState, taskId: string, update: Partial<Activity>): TuiState {
  const existing = state.activities.get(taskId)
  const activity: Activity = {
    taskId,
    title: update.title ?? existing?.title ?? taskId,
    agent: update.agent ?? existing?.agent ?? null,
    stage: update.stage ?? existing?.stage ?? 'pending',
    startedAt: update.startedAt ?? existing?.startedAt ?? null,
    fallback: update.fallback ?? existing?.fallback ?? null,
    latestCommand: update.latestCommand ?? existing?.latestCommand ?? null,
    output: update.output ?? existing?.output ?? [],
    expanded: update.expanded ?? existing?.expanded ?? false,
    goal: update.goal ?? existing?.goal ?? null,
    acceptanceCriteria: update.acceptanceCriteria ?? existing?.acceptanceCriteria ?? null,
    verificationCommands: update.verificationCommands ?? existing?.verificationCommands ?? [],
    state: update.state ?? existing?.state ?? null,
    latestNote: update.latestNote ?? existing?.latestNote ?? null,
    nextAction: update.nextAction ?? existing?.nextAction ?? null,
  }

  const newActivities = new Map(state.activities)
  newActivities.set(taskId, activity)

  // Update or add activity block in transcript
  const existingIdx = state.transcript.findIndex(
    item => item.kind === 'activity' && item.activity.taskId === taskId,
  )
  const newTranscript = [...state.transcript]
  const activityItem: TranscriptItem = { id: nextItemId(state), kind: 'activity', activity }

  if (existingIdx >= 0) {
    newTranscript[existingIdx] = activityItem
  } else {
    newTranscript.push(activityItem)
  }

  return { ...state, activities: newActivities, transcript: newTranscript }
}

function isDuplicateRecentUserMessage(state: TuiState, text: string): boolean {
  const last = state.transcript[state.transcript.length - 1]
  return last?.kind === 'message' && last.role === 'user' && last.text === text
}

function reduceChatMessage(state: TuiState, payload: Record<string, unknown>): TuiState {
  const role = String(payload.role ?? 'coordinator')
  const text = String(payload.text ?? '')
  if (!text) return state
  if (role === 'user' && isDuplicateRecentUserMessage(state, text)) {
    return state
  }
  return addMessage(state, role as 'user' | 'coordinator' | 'system', text)
}

function reduceChatStream(state: TuiState, payload: Record<string, unknown>): TuiState {
  const text = String(payload.text ?? '')
  if (!text) return state
  // Streaming appends to the last coordinator message or creates one
  const last = state.transcript[state.transcript.length - 1]
  if (last?.kind === 'message' && last.role === 'coordinator') {
    const newTranscript = [...state.transcript]
    newTranscript[newTranscript.length - 1] = { ...last, text: last.text + text }
    return { ...state, transcript: newTranscript }
  }
  return addMessage(state, 'coordinator', text)
}

function reduceTaskCreated(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  const verificationCommands = Array.isArray(payload.verification_commands)
    ? payload.verification_commands.map(String)
    : []
  return upsertActivity(state, taskId, {
    title: String(payload.title ?? taskId),
    agent: payload.agent ? String(payload.agent) : null,
    stage: 'created',
    state: payload.state ? String(payload.state) : 'ready',
    startedAt: Date.now(),
    goal: payload.goal ? String(payload.goal) : null,
    acceptanceCriteria: payload.acceptance_criteria
      ? String(payload.acceptance_criteria)
      : null,
    verificationCommands,
  })
}

function reduceTaskStage(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  return upsertActivity(state, taskId, {
    stage: String(payload.stage ?? 'unknown'),
  })
}

function reduceTaskCommand(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  return upsertActivity(state, taskId, {
    latestCommand: payload.command ? String(payload.command) : null,
  })
}

function reduceTaskOutput(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  const existing = state.activities.get(taskId)
  const newLines = String(payload.output ?? '').split('\n').filter(Boolean)
  const output = [...(existing?.output ?? []), ...newLines].slice(-MAX_OUTPUT_LINES)
  return upsertActivity(state, taskId, { output })
}

function reduceTaskVerification(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  return upsertActivity(state, taskId, {
    stage: `verification: ${String(payload.result ?? 'pending')}`,
  })
}

function reduceTaskReview(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  return upsertActivity(state, taskId, {
    stage: `review: ${String(payload.result ?? 'pending')}`,
  })
}

function reduceTaskGit(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  return upsertActivity(state, taskId, {
    stage: `git: ${String(payload.operation ?? 'unknown')}`,
  })
}

function reduceTaskFallback(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  return upsertActivity(state, taskId, {
    fallback: {
      from: String(payload.from_agent ?? ''),
      to: String(payload.to_agent ?? ''),
      used: Number(payload.used ?? 0),
      limit: Number(payload.limit ?? 1),
    },
  })
}

function reduceTaskDone(state: TuiState, payload: Record<string, unknown>): TuiState {
  const taskId = String(payload.task_id ?? '')
  if (!taskId) return state
  const result = String(payload.result ?? 'completed')
  const reason = payload.reason ? String(payload.reason) : null
  const nextAction = payload.next_action ? String(payload.next_action) : null
  const terminalDone = result === 'done' || result === 'completed'
  return upsertActivity(state, taskId, {
    stage: terminalDone ? 'done: completed' : `failed: ${reason ?? result}`,
    state: result,
    latestNote: reason,
    nextAction,
  })
}
