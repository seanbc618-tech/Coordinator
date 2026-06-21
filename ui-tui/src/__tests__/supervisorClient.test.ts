import { describe, expect, it, beforeEach, afterEach } from 'vitest'
import { createServer, type Server } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { mkdtemp, rm } from 'node:fs/promises'
import { SupervisorClient } from '../supervisorClient.js'
import { PROTOCOL_VERSION, encodeEnvelope, type EventEnvelope, type ResponseEnvelope } from '../protocol.js'

function tmpSocket(): string {
  return join(tmpdir(), `coord-test-${process.pid}-${Math.random().toString(36).slice(2)}.sock`)
}

function makeResponse(requestId: string, result: Record<string, unknown> = {}): string {
  const resp: ResponseEnvelope = {
    type: 'response',
    protocol_version: PROTOCOL_VERSION,
    request_id: requestId,
    ok: true,
    result,
    error: null,
  }
  return encodeEnvelope(resp) + '\n'
}

function makeEvent(cursor: number, eventType = 'tick_scheduled', projectId = 'proj-a'): string {
  const evt: EventEnvelope = {
    type: 'event',
    protocol_version: PROTOCOL_VERSION,
    project_id: projectId,
    cursor,
    event_type: eventType,
    payload: { project_id: projectId },
  }
  return encodeEnvelope(evt) + '\n'
}

describe('SupervisorClient', () => {
  let tmpDir: string
  let server: Server
  let socketPath: string
  let clients: SupervisorClient[]

  beforeEach(async () => {
    tmpDir = await mkdtemp(join(tmpdir(), 'coord-test-'))
    socketPath = join(tmpDir, 'test.sock')
    clients = []
  })

  afterEach(async () => {
    for (const c of clients) c.close()
    await new Promise<void>(resolve => {
      if (server?.listening) server.close(() => resolve())
      else resolve()
    })
    await rm(tmpDir, { recursive: true, force: true })
  })

  function makeClient(projectId = 'proj-a'): SupervisorClient {
    const c = new SupervisorClient({ socketPath, projectId, requestTimeoutMs: 2000 })
    clients.push(c)
    return c
  }

  function startServer(handler: (data: string, respond: (msg: string) => void) => void): Promise<void> {
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
              handler(line, msg => sock.write(msg))
            }
          }
        })
      })
      server.listen(socketPath, resolve)
    })
  }

  it('connects and sends a request receiving a correlated response', async () => {
    await startServer((data, respond) => {
      const parsed = JSON.parse(data)
      if (parsed.type === 'request') {
        respond(makeResponse(parsed.request_id, { pong: true }))
      }
    })

    const client = makeClient()
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    const resp = await client.request('system.ping')
    expect(resp.ok).toBe(true)
    expect(resp.result).toEqual({ pong: true })
  })

  it('times out when no response arrives', async () => {
    await startServer(() => {
      // Never respond
    })

    const client = new SupervisorClient({
      socketPath,
      projectId: 'proj-a',
      requestTimeoutMs: 100,
    })
    clients.push(client)
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    await expect(client.request('system.ping')).rejects.toThrow('timed out')
  })

  it('rejects messages larger than 1 MiB', async () => {
    const { encodeEnvelope: encode, MAX_MESSAGE_BYTES } = await import('../protocol.js')
    const big: ResponseEnvelope = {
      type: 'response',
      protocol_version: PROTOCOL_VERSION,
      request_id: 'x',
      ok: true,
      result: { data: 'x'.repeat(MAX_MESSAGE_BYTES) },
      error: null,
    }
    expect(() => encode(big)).toThrow('message too large')
  })

  it('deduplicates events with duplicate cursors', async () => {
    const received: EventEnvelope[] = []
    await startServer((data, respond) => {
      const parsed = JSON.parse(data)
      if (parsed.type === 'request') {
        respond(makeResponse(parsed.request_id))
        // Send events with duplicate cursor
        respond(makeEvent(1, 'tick_scheduled'))
        respond(makeEvent(1, 'tick_scheduled')) // duplicate
        respond(makeEvent(2, 'cycle_complete'))
      }
    })

    const client = makeClient()
    client.onEvent(e => received.push(e))
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    await client.request('events.subscribe')
    await new Promise(r => setTimeout(r, 100))

    expect(received).toHaveLength(2)
    expect(received[0]!.cursor).toBe(1)
    expect(received[1]!.cursor).toBe(2)
  })

  it('ignores events for other projects', async () => {
    const received: EventEnvelope[] = []
    await startServer((data, respond) => {
      const parsed = JSON.parse(data)
      if (parsed.type === 'request') {
        respond(makeResponse(parsed.request_id))
        respond(makeEvent(1, 'tick_scheduled', 'proj-a'))
        respond(makeEvent(1, 'tick_scheduled', 'proj-b'))
      }
    })

    const client = makeClient('proj-a')
    client.onEvent(e => received.push(e))
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    await client.request('events.subscribe')
    await new Promise(r => setTimeout(r, 100))

    expect(received).toHaveLength(1)
    expect(received[0]!.project_id).toBe('proj-a')
  })

  it('emits connection state changes', async () => {
    const states: string[] = []
    await startServer(() => {})

    const client = makeClient()
    client.on('state', s => states.push(s))
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    expect(states).toContain('connecting')
    expect(states).toContain('connected')
  })

  it('rejects protocol mismatch', async () => {
    const { decodeEnvelope } = await import('../protocol.js')
    const bad = JSON.stringify({
      type: 'response',
      protocol_version: 99,
      request_id: 'x',
      ok: true,
      result: {},
      error: null,
    })
    expect(() => decodeEnvelope(bad)).toThrow('unsupported protocol_version')
  })

  it('sends project_id in requests', async () => {
    let receivedProjectId: string | null = null
    await startServer((data, respond) => {
      const parsed = JSON.parse(data)
      receivedProjectId = parsed.project_id
      if (parsed.type === 'request') {
        respond(makeResponse(parsed.request_id))
      }
    })

    const client = makeClient('my-project')
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    await client.request('system.ping')
    expect(receivedProjectId).toBe('my-project')
  })

  it('tracks last cursor from events', async () => {
    await startServer((data, respond) => {
      const parsed = JSON.parse(data)
      if (parsed.type === 'request') {
        respond(makeResponse(parsed.request_id))
        respond(makeEvent(5, 'tick_scheduled'))
        respond(makeEvent(10, 'cycle_complete'))
      }
    })

    const client = makeClient()
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    await client.request('events.subscribe')
    await new Promise(r => setTimeout(r, 100))

    expect(client.getLastCursor()).toBe(10)
  })

  it('rejects request when not connected', async () => {
    const client = makeClient()
    await expect(client.request('system.ping')).rejects.toThrow('not connected')
  })

  it('close() rejects pending requests', async () => {
    await startServer(() => {
      // Never respond
    })

    const client = makeClient()
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))

    const promise = client.request('system.ping')
    client.close()

    await expect(promise).rejects.toThrow('client closed')
  })
})
