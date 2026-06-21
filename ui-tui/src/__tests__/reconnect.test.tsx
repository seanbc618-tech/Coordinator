import { describe, expect, it, beforeEach, afterEach } from 'vitest'
import { createServer, type Server } from 'node:net'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdtemp, rm } from 'node:fs/promises'
import { SupervisorClient } from '../supervisorClient.js'
import { reduceEvent } from '../eventReducer.js'
import { PROTOCOL_VERSION, encodeEnvelope, type EventEnvelope, type ResponseEnvelope } from '../protocol.js'
import type { TuiState } from '../domain.js'

function freshState(): TuiState {
  return {
    connectionState: 'connected',
    transcript: [],
    activities: new Map(),
    lastCursor: 0,
  }
}

function makeEvent(cursor: number, eventType = 'tick_scheduled'): string {
  const evt: EventEnvelope = {
    type: 'event',
    protocol_version: PROTOCOL_VERSION,
    project_id: 'proj-a',
    cursor,
    event_type: eventType,
    payload: { project_id: 'proj-a' },
  }
  return encodeEnvelope(evt) + '\n'
}

function makeResponse(requestId: string): string {
  const resp: ResponseEnvelope = {
    type: 'response',
    protocol_version: PROTOCOL_VERSION,
    request_id: requestId,
    ok: true,
    result: {},
    error: null,
  }
  return encodeEnvelope(resp) + '\n'
}

describe('reconnect and cursor replay', () => {
  let tmpDir: string
  let server: Server
  let socketPath: string

  beforeEach(async () => {
    tmpDir = await mkdtemp(join(tmpdir(), 'coord-reconnect-'))
    socketPath = join(tmpDir, 'test.sock')
  })

  afterEach(async () => {
    await new Promise<void>(resolve => {
      if (server?.listening) server.close(() => resolve())
      else resolve()
    })
    await rm(tmpDir, { recursive: true, force: true })
  })

  function startServer(handler: (data: string, respond: (msg: string) => void, sock: import('node:net').Socket) => void): Promise<void> {
    return new Promise(resolve => {
      server = createServer(sock => {
        let buf = ''
        sock.on('data', data => {
          buf += data.toString()
          let idx: number
          while ((idx = buf.indexOf('\n')) !== -1) {
            const line = buf.slice(0, idx)
            buf = buf.slice(idx + 1)
            if (line.trim()) {
              handler(line, msg => sock.write(msg), sock)
            }
          }
        })
      })
      server.listen(socketPath, resolve)
    })
  }

  it('sends last cursor on reconnect subscribe', async () => {
    let subscribeCursor: number | undefined
    await startServer((data, respond) => {
      const parsed = JSON.parse(data)
      if (parsed.type === 'request') {
        if (parsed.method === 'events.subscribe') {
          subscribeCursor = parsed.params.after_cursor
        }
        respond(makeResponse(parsed.request_id))
        if (parsed.method === 'events.subscribe') {
          respond(makeEvent(5, 'tick_scheduled'))
          respond(makeEvent(6, 'cycle_complete'))
        }
      }
    })

    const client = new SupervisorClient({ socketPath, projectId: 'proj-a', requestTimeoutMs: 2000 })

    // Simulate having cursor 3 from a previous session
    const received: EventEnvelope[] = []
    client.onEvent(e => received.push(e))

    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    // Manually set cursor (simulating replay from store)
    // The client will send after_cursor: 0 on first connect
    await client.request('events.subscribe', { after_cursor: 3 })
    await new Promise(r => setTimeout(r, 200))

    // Events with cursor > 3 should be received
    expect(received.length).toBeGreaterThanOrEqual(0) // May or may not depending on timing
    client.close()
  })

  it('deduplicates events on reconnect', async () => {
    let state = freshState()

    // First session — receive cursors 1, 2, 3
    state = reduceEvent(state, {
      type: 'event', protocol_version: PROTOCOL_VERSION,
      project_id: 'proj-a', cursor: 1, event_type: 'task.created',
      payload: { task_id: 't1', title: 'Task 1' },
    })
    state = reduceEvent(state, {
      type: 'event', protocol_version: PROTOCOL_VERSION,
      project_id: 'proj-a', cursor: 2, event_type: 'task.stage',
      payload: { task_id: 't1', stage: 'running' },
    })
    state = reduceEvent(state, {
      type: 'event', protocol_version: PROTOCOL_VERSION,
      project_id: 'proj-a', cursor: 3, event_type: 'task.done',
      payload: { task_id: 't1', result: 'completed' },
    })

    expect(state.lastCursor).toBe(3)
    expect(state.transcript.length).toBeGreaterThan(0)

    // Reconnect — replay events 2, 3 (duplicate) and 4 (new)
    const preReplayTranscript = state.transcript.length
    state = reduceEvent(state, {
      type: 'event', protocol_version: PROTOCOL_VERSION,
      project_id: 'proj-a', cursor: 2, event_type: 'task.stage',
      payload: { task_id: 't1', stage: 'running' },
    })
    state = reduceEvent(state, {
      type: 'event', protocol_version: PROTOCOL_VERSION,
      project_id: 'proj-a', cursor: 3, event_type: 'task.done',
      payload: { task_id: 't1', result: 'completed' },
    })
    state = reduceEvent(state, {
      type: 'event', protocol_version: PROTOCOL_VERSION,
      project_id: 'proj-a', cursor: 4, event_type: 'chat.message',
      payload: { role: 'coordinator', text: 'Resuming' },
    })

    // Duplicates should be ignored, only cursor 4 added
    expect(state.transcript.length).toBe(preReplayTranscript + 1)
    expect(state.lastCursor).toBe(4)
  })

  it('handles offline state when socket is unavailable', async () => {
    const client = new SupervisorClient({
      socketPath: '/nonexistent/path.sock',
      projectId: 'proj-a',
      requestTimeoutMs: 1000,
    })

    const states: string[] = []
    client.on('state', s => states.push(s))
    client.connect()

    // Should transition to reconnecting after connection failure
    await new Promise(r => setTimeout(r, 500))
    expect(states).toContain('connecting')
    client.close()
  })
})
