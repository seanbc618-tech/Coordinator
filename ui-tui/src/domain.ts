/**
 * Coordinator TUI domain types.
 */

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline'

export interface Activity {
  taskId: string
  title: string
  agent: string | null
  stage: string
  startedAt: number | null
  fallback: { from: string; to: string; used: number; limit: number } | null
  latestCommand: string | null
  output: string[]
  expanded: boolean
  goal?: string | null
  acceptanceCriteria?: string | null
  verificationCommands?: string[]
  state?: string | null
  latestNote?: string | null
  nextAction?: string | null
}

export type TranscriptItem =
  | { id: string; kind: 'message'; role: 'user' | 'coordinator' | 'system'; text: string }
  | { id: string; kind: 'activity'; activity: Activity }

export interface TuiState {
  connectionState: ConnectionState
  transcript: TranscriptItem[]
  activities: Map<string, Activity>
  lastCursor: number
}

/** Maximum live output lines kept per activity in memory. */
export const MAX_OUTPUT_LINES = 200
