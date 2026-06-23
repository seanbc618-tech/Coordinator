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
import { ProjectOnboarding, type ProjectInspectDraft } from './components/ProjectOnboarding.js'
import { decideSubmit } from './submitDecision.js'
import { formatSlashResponse } from './slashDisplay.js'
import { performDetach, registerDetachHandlers } from './detach.js'
import type { TuiState } from './domain.js'
import type { EventEnvelope } from './protocol.js'

interface AppProps {
  socketPath: string
  projectId: string
  canonicalPath?: string
}

type OnboardingPhase = 'pending' | 'confirm' | 'ready'

export function needsProjectOnboarding(draft: ProjectInspectDraft): boolean {
  return !draft.registered || draft.path_changed
}

export function App({ socketPath, projectId, canonicalPath }: AppProps) {
  const conn = useStore(connStateAtom)
  const [client] = useState(() => new SupervisorClient({ socketPath, projectId }))
  const [activeProjectId, setActiveProjectId] = useState(projectId)
  const activeProjectIdRef = useRef(activeProjectId)
  activeProjectIdRef.current = activeProjectId
  const [onboardingPhase, setOnboardingPhase] = useState<OnboardingPhase>(
    canonicalPath ? 'pending' : 'ready',
  )
  const [onboardingDraft, setOnboardingDraft] = useState<ProjectInspectDraft | null>(null)
  const [tuiState, setTuiState] = useState<TuiState>({
    connectionState: 'connecting',
    transcript: [],
    activities: new Map(),
    lastCursor: 0,
  })

  const resetProjectScopedState = useCallback(() => {
    const empty: TuiState = {
      connectionState: 'connecting',
      transcript: [],
      activities: new Map(),
      lastCursor: 0,
    }
    setTuiState(empty)
    transcriptAtom.set(empty.transcript)
    activitiesAtom.set(empty.activities)
    lastCursorAtom.set(empty.lastCursor)
  }, [])

  // P0: Destructive command confirmation state machine.
  // Tracks which destructive command is awaiting confirmation.
  // null = no pending; string = command name awaiting re-entry.
  const pendingDestructiveRef = useRef<string | null>(null)

  useEffect(() => {
    registerDetachHandlers({
      closeClient: () => client.close(),
    })
    return () => registerDetachHandlers(null)
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
      // P2: pass projectId for defense-in-depth foreign event rejection.
      setTuiState(prev => reduceEvent(prev, event, activeProjectIdRef.current))
    })

    client.connect()

    return () => {
      client.close()
    }
  }, [client])

  useEffect(() => {
    if (!canonicalPath || onboardingPhase !== 'pending' || conn !== 'connected') {
      return
    }

    let cancelled = false
    void client.request('project.inspect', { path: canonicalPath }).then(resp => {
      if (cancelled) return
      if (!resp.ok || !resp.result) {
        setOnboardingPhase('ready')
        return
      }

      const draft = resp.result as unknown as ProjectInspectDraft
      if (needsProjectOnboarding(draft)) {
        setOnboardingDraft(draft)
        setOnboardingPhase('confirm')
        return
      }

      if (draft.project_id) {
        setActiveProjectId(draft.project_id)
        client.rebind(draft.project_id)
        resetProjectScopedState()
      }
      setOnboardingPhase('ready')
    }).catch(() => {
      if (!cancelled) {
        setOnboardingPhase('ready')
      }
    })

    return () => {
      cancelled = true
    }
  }, [canonicalPath, client, conn, onboardingPhase, resetProjectScopedState])

  // Load initial snapshot once chat is available
  useEffect(() => {
    if (onboardingPhase !== 'ready' || conn !== 'connected') {
      return
    }

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
  }, [client, conn, onboardingPhase])

  // P1 fix: single source of truth — sync tuiState → atoms here only.
  useEffect(() => {
    transcriptAtom.set(tuiState.transcript)
    activitiesAtom.set(tuiState.activities)
    lastCursorAtom.set(tuiState.lastCursor)
  }, [tuiState])

  const handleOnboardingAccept = useCallback(() => {
    if (!onboardingDraft || !canonicalPath) {
      return
    }

    void client.request('project.register', {
      confirmed: true,
      path: canonicalPath,
      canonical_path: onboardingDraft.canonical_path,
      repo_id: onboardingDraft.repo_id,
      default_branch: onboardingDraft.default_branch,
      branch_prefix: onboardingDraft.branch_prefix,
      verify_commands: onboardingDraft.verify_commands,
    }).then(resp => {
      if (!resp.ok || !resp.result) {
        return
      }
      const result = resp.result as { project_id?: string }
      if (result.project_id) {
        setActiveProjectId(result.project_id)
        client.rebind(result.project_id)
        resetProjectScopedState()
      }
      setOnboardingDraft(null)
      setOnboardingPhase('ready')
    }).catch(() => {})
  }, [canonicalPath, client, onboardingDraft, resetProjectScopedState])

  const handleOnboardingReject = useCallback(() => {
    performDetach()
  }, [])

  const handleSubmit = useCallback((text: string) => {
    const decision = decideSubmit(text, pendingDestructiveRef.current)
    pendingDestructiveRef.current = decision.newPending

    switch (decision.action) {
      case 'quit':
        performDetach()
        return

      case 'destructive-confirmed':
        setTuiState(prev => ({
          ...prev,
          transcript: [
            ...prev.transcript,
            { id: `conf-${Date.now()}`, kind: 'message', role: 'system', text: `${decision.commandName} confirmed.` },
          ],
        }))
        void client.request(decision.method, { args: decision.args }).catch(() => {})
        return

      case 'destructive-pending':
        setTuiState(prev => ({
          ...prev,
          transcript: [
            ...prev.transcript,
            { id: `pend-${Date.now()}`, kind: 'message', role: 'system', text: `Confirm: ${decision.commandName}? Type ${decision.commandName} again to proceed.` },
          ],
        }))
        return

      case 'send':
        void client.request(decision.method, { args: decision.args }).then(resp => {
          const text = resp.ok
            ? formatSlashResponse(decision.method, resp.result as Record<string, unknown>)
            : (resp.error ?? 'request failed')
          setTuiState(prev => ({
            ...prev,
            transcript: [
              ...prev.transcript,
              {
                id: `slash-${Date.now()}`,
                kind: 'message',
                role: 'system',
                text,
              },
            ],
          }))
        }).catch(() => {})
        return

      case 'chat':
        setTuiState(prev => ({
          ...prev,
          transcript: [
            ...prev.transcript,
            { id: `user-${Date.now()}`, kind: 'message', role: 'user', text: decision.text },
          ],
        }))
        void client.request('chat.send', { text: decision.text }).catch(() => {})
        return
    }
  }, [client])

  const handleDetach = useCallback(() => {
    performDetach()
  }, [])

  if (onboardingPhase === 'confirm' && onboardingDraft) {
    return (
      <Box flexDirection="column" width="100%" height="100%">
        <ProjectOnboarding
          draft={onboardingDraft}
          onAccept={handleOnboardingAccept}
          onReject={handleOnboardingReject}
        />
      </Box>
    )
  }

  return (
    <Box flexDirection="column" width="100%" height="100%">
      <AppLayout projectId={activeProjectId} />
      <Composer onSubmit={handleSubmit} onDetach={handleDetach} disabled={conn !== 'connected'} />
    </Box>
  )
}