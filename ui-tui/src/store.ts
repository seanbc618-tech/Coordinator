/**
 * Coordinator TUI state store using nanostores.
 */

import { atom } from 'nanostores'
import type { ConnectionState, TranscriptItem, Activity } from './domain.js'

export const connectionState = atom<ConnectionState>('offline')
export const transcript = atom<TranscriptItem[]>([])
export const activities = atom<Map<string, Activity>>(new Map())
export const lastCursor = atom(0)

export function resetStore(): void {
  connectionState.set('offline')
  transcript.set([])
  activities.set(new Map())
  lastCursor.set(0)
}
