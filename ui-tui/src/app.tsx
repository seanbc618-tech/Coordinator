/**
 * Coordinator TUI application.
 *
 * Wires the SupervisorClient, event reducer, store, layout, and
 * lifecycle together.
 */

import React, { useEffect, useState, useCallback, useRef } from 'react'
import { Box } from 'ink'
import { useStore } from '@nanostores/react'
import { SupervisorClient } from './supervisorClient.js'
import { reduceEvent } from './eventReducer.js'
import {
  connectionState as connStateAtom,
  transcript as transcriptAtom,
  activities as activitiesAtom,
  lastCursor as lastCursorAtom,
} from './store.js'
import { AppLayout } from './components/AppLayout.js'
import { Composer } from './components/Composer.js'
import { parse } from './slash.js'
import { setupLifecycle } from './lifecycle.js'
import type { TuiState } from './domain.js'
import type { EventEnvelope } from './protocol.js'

interface AppProps {
  socketPath: string
  projectId: string
}

export function App({ socketPath, projectId }: AppProps) {
  const conn = useStore(connStateAtom)
  const [client] = useState(() => new SupervisorClient({ socketPath, projectId }))
  const [tuiState, setTuiState] = useState<TuiState>({
    connectionState: 'connecting',
    transcript: [],
    activities: new Map(),
    lastCursor: 0,
  })

  // P0: Destructive command confirmation state machine.
  // Tracks which destructive command is awaiting confirmation.
  // null = no pending; string = command name awaiting re-entry.
  const pendingDestructiveRef = useRef<string | null>(null)

  // Set up lifecycle cleanup
  useEffect(() => {
    setupLifecycle({
      onCleanup: () => {
        client.close()
      },
    })
  }, [client])

  // Connect and subscribe
  useEffect(() => {
    client.on('state', (state: string) => {
      connStateAtom.set(state as 'connecting' | 'connected' | 'reconnecting' | 'offline')
      // Clear pending destructive on reconnect
      if (state === 'reconnecting' || state === 'offline') {
        pendingDestructiveRef.current = null
      }
    })

    client.on('event', (event: EventEnvelope) => {
      // P1 fix: use functional update only — no stale closure writes to atoms.
      // Atoms are synced via the useEffect below.
      setTuiState(prev => reduceEvent(prev, event))
    })

    client.connect()

    // Load initial snapshot
    client.request('project.snapshot').then(resp => {
      if (resp.ok && resp.result) {
        const snapshot = resp.result as { cursor?: number }
        if (snapshot.cursor) {
          lastCursorAtom.set(snapshot.cursor)
        }
      }
    }).catch(() => {
      // Snapshot failure is non-fatal on first connect
    })

    return () => {
      client.close()
    }
  }, [client])

  // P1 fix: single source of truth — sync tuiState → atoms here only.
  useEffect(() => {
    transcriptAtom.set(tuiState.transcript)
    activitiesAtom.set(tuiState.activities)
    lastCursorAtom.set(tuiState.lastCursor)
  }, [tuiState])

  const handleSubmit = useCallback((text: string) => {
    const parsed = parse(text)

    if (parsed.type === 'command') {
      if (parsed.command.name === '/quit') {
        client.close()
        process.exit(0)
        return
      }

      // P0: Destructive confirmation state machine.
      if (parsed.command.destructive) {
        const pending = pendingDestructiveRef.current

        if (pending === parsed.command.name) {
          // Second entry confirmed — execute.
          pendingDestructiveRef.current = null
          setTuiState(prev => ({
            ...prev,
            transcript: [
              ...prev.transcript,
              { id: `conf-${Date.now()}`, kind: 'message', role: 'system', text: `${parsed.command.name} confirmed.` },
            ],
          }))
          void client.request(parsed.command.method, { args: parsed.args }).catch(() => {})
        } else {
          // First entry — ask for confirmation, do NOT send RPC.
          pendingDestructiveRef.current = parsed.command.name
          setTuiState(prev => ({
            ...prev,
            transcript: [
              ...prev.transcript,
              { id: `pend-${Date.now()}`, kind: 'message', role: 'system', text: `Confirm: ${parsed.command.name}? Type ${parsed.command.name} again to proceed.` },
            ],
          }))
        }
        return
      }

      // Non-destructive command — send immediately.
      void client.request(parsed.command.method, { args: parsed.args }).catch(() => {})
    } else {
      // Plain message — send as chat.
      // Clear any pending destructive on freeform input.
      pendingDestructiveRef.current = null
      setTuiState(prev => ({
        ...prev,
        transcript: [
          ...prev.transcript,
          { id: `user-${Date.now()}`, kind: 'message', role: 'user', text },
        ],
      }))
      void client.request('chat.send', { text }).catch(() => {})
    }
  }, [client])

  const handleDetach = useCallback(() => {
    client.close()
    process.exit(0)
  }, [client])

  return (
    <Box flexDirection="column" width="100%" height="100%">
      <AppLayout projectId={projectId} />
      <Composer onSubmit={handleSubmit} onDetach={handleDetach} disabled={conn !== 'connected'} />
    </Box>
  )
}
