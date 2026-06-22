/**
 * End-to-end submission flow tests.
 *
 * These tests exercise the real decideSubmit helper (the same code path
 * used by App.handleSubmit) combined with the SupervisorClient talking
 * to a real Unix-socket server. This validates the full chain:
 *   input text → decideSubmit → client.request → server receives RPC
 *
 * P1-3: Exercise real composer and detach behavior at the protocol level.
 * Ink's useInput does not work reliably in forked PTY environments, so
 * interactive keystroke tests are covered here via the real decision
 * helper and a mock server.
 */

import { describe, expect, it, beforeEach, afterEach } from 'vitest'
import { createServer, type Server } from 'node:net'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdtemp, rm } from 'node:fs/promises'
import { decideSubmit } from '../submitDecision.js'
import { SupervisorClient } from '../supervisorClient.js'
import { encodeEnvelope, PROTOCOL_VERSION, type ResponseEnvelope } from '../protocol.js'

interface RecordedRequest {
  method: string
  params: Record<string, unknown>
  requestId: string
}

function makeResponse(requestId: string, result: Record<string, unknown> | null = {}): string {
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

describe('submit flow: decideSubmit → client.request → server', () => {
  let tmpDir: string
  let server: Server
  let socketPath: string
  let requests: RecordedRequest[]

  beforeEach(async () => {
    tmpDir = await mkdtemp(join(tmpdir(), 'coord-submit-'))
    socketPath = join(tmpDir, 'test.sock')
    requests = []
  })

  afterEach(async () => {
    await new Promise<void>(resolve => {
      if (server?.listening) server.close(() => resolve())
      else resolve()
    })
    await rm(tmpDir, { recursive: true, force: true })
  })

  function startServer(): Promise<void> {
    return new Promise(resolve => {
      server = createServer(sock => {
        let buf = ''
        sock.on('error', () => {}) // suppress EPIPE on client disconnect
        sock.on('data', data => {
          buf += data.toString()
          let idx: number
          while ((idx = buf.indexOf('\n')) !== -1) {
            const line = buf.slice(0, idx)
            buf = buf.slice(idx + 1)
            if (line.trim()) {
              const parsed = JSON.parse(line)
              if (parsed.type === 'request') {
                requests.push({
                  method: parsed.method,
                  params: parsed.params,
                  requestId: parsed.request_id,
                })
                try {
                  sock.write(makeResponse(parsed.request_id))
                } catch { /* client disconnected */ }
              }
            }
          }
        })
      })
      server.listen(socketPath, resolve)
    })
  }

  async function connectClient(): Promise<SupervisorClient> {
    const client = new SupervisorClient({ socketPath, projectId: 'proj-a', requestTimeoutMs: 2000 })
    client.connect()
    await new Promise<void>(resolve => client.on('state', s => { if (s === 'connected') resolve() }))
    return client
  }

  it('/stop, /stop → one project.stop RPC', async () => {
    await startServer()
    const client = await connectClient()

    // Simulate the App's submission logic using the real decideSubmit.
    let pending: string | null = null

    // First /stop
    const d1 = decideSubmit('/stop', pending)
    expect(d1.action).toBe('destructive-pending')
    pending = d1.newPending

    // Second /stop
    const d2 = decideSubmit('/stop', pending)
    expect(d2.action).toBe('destructive-confirmed')
    pending = d2.newPending

    // Send the confirmed RPC via the real client.
    await client.request(d2.method, { args: d2.args })

    // Verify server received exactly one project.stop.
    const stopReqs = requests.filter(r => r.method === 'project.stop')
    expect(stopReqs).toHaveLength(1)

    client.close()
  })

  it('/stop, /status, /stop → zero project.stop RPCs', async () => {
    await startServer()
    const client = await connectClient()

    let pending: string | null = null

    // First /stop — pending
    const d1 = decideSubmit('/stop', pending)
    expect(d1.action).toBe('destructive-pending')
    pending = d1.newPending

    // /status — non-destructive, clears pending
    const d2 = decideSubmit('/status', pending)
    expect(d2.action).toBe('send')
    pending = d2.newPending
    await client.request(d2.method, { args: d2.args })

    // Second /stop — new pending (not confirmed)
    const d3 = decideSubmit('/stop', pending)
    expect(d3.action).toBe('destructive-pending')
    pending = d3.newPending

    // Verify: only project.status was sent, no project.stop.
    const stopReqs = requests.filter(r => r.method === 'project.stop')
    const statusReqs = requests.filter(r => r.method === 'project.status')
    expect(stopReqs).toHaveLength(0)
    expect(statusReqs).toHaveLength(1)

    client.close()
  })

  it('/shutdown, hello, /shutdown → zero system.shutdown RPCs', async () => {
    await startServer()
    const client = await connectClient()

    let pending: string | null = null

    // /shutdown — pending
    const d1 = decideSubmit('/shutdown', pending)
    expect(d1.action).toBe('destructive-pending')
    pending = d1.newPending

    // hello — chat, clears pending
    const d2 = decideSubmit('hello', pending)
    expect(d2.action).toBe('chat')
    pending = d2.newPending
    await client.request('chat.send', { text: d2.text })

    // /shutdown — new pending (not confirmed)
    const d3 = decideSubmit('/shutdown', pending)
    expect(d3.action).toBe('destructive-pending')

    // Verify: only chat.send was sent, no system.shutdown.
    const shutdownReqs = requests.filter(r => r.method === 'system.shutdown')
    const chatReqs = requests.filter(r => r.method === 'chat.send')
    expect(shutdownReqs).toHaveLength(0)
    expect(chatReqs).toHaveLength(1)

    client.close()
  })

  it('/stop, /shutdown → zero destructive RPCs', async () => {
    await startServer()
    const client = await connectClient()

    let pending: string | null = null

    // /stop — pending
    const d1 = decideSubmit('/stop', pending)
    expect(d1.action).toBe('destructive-pending')
    expect(d1.newPending).toBe('/stop')
    pending = d1.newPending

    // /shutdown — different destructive, resets pending
    const d2 = decideSubmit('/shutdown', pending)
    expect(d2.action).toBe('destructive-pending')
    expect(d2.newPending).toBe('/shutdown')

    // Verify: no RPCs sent at all.
    expect(requests).toHaveLength(0)

    client.close()
  })

  it('/shutdown, reconnect, /shutdown → zero system.shutdown RPCs', async () => {
    await startServer()
    const client = await connectClient()

    let pending: string | null = null

    // /shutdown — pending
    const d1 = decideSubmit('/shutdown', pending)
    expect(d1.action).toBe('destructive-pending')
    pending = d1.newPending

    // Simulate reconnect: clear pending (as App does on reconnect/offline).
    pending = null

    // /shutdown — new pending (not confirmed)
    const d2 = decideSubmit('/shutdown', pending)
    expect(d2.action).toBe('destructive-pending')
    expect(d2.newPending).toBe('/shutdown')

    // Verify: no shutdown RPCs.
    const shutdownReqs = requests.filter(r => r.method === 'system.shutdown')
    expect(shutdownReqs).toHaveLength(0)

    client.close()
  })

  it('non-destructive command clears pending destructive', async () => {
    await startServer()
    const client = await connectClient()

    let pending: string | null = '/shutdown'

    // /status — clears pending
    const d = decideSubmit('/status', pending)
    expect(d.action).toBe('send')
    expect(d.newPending).toBe(null)
    await client.request(d.method, { args: d.args })

    // Verify project.status sent.
    const statusReqs = requests.filter(r => r.method === 'project.status')
    expect(statusReqs).toHaveLength(1)

    client.close()
  })

  it('Ctrl+C detach: client.close() + supervisor still responds', async () => {
    await startServer()
    const client = await connectClient()

    // Simulate Ctrl+C: close client, verify supervisor still works.
    client.close()

    // Connect a new client — supervisor should still respond.
    const client2 = await connectClient()
    const resp = await client2.request('system.ping')
    expect(resp.ok).toBe(true)

    client2.close()
  })
})
