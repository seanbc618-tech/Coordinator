/**
 * Unix-socket client for the Coordinator Supervisor.
 *
 * Handles connection, request/response correlation, event subscription,
 * reconnect with backoff, and cursor replay.
 */

import { connect, type Socket } from 'node:net'
import { EventEmitter } from 'node:events'
import {
  type EventEnvelope,
  type ResponseEnvelope,
  type RequestEnvelope,
  PROTOCOL_VERSION,
  encodeEnvelope,
  decodeEnvelope,
  nextRequestId,
} from './protocol.js'

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline'

interface PendingRequest {
  resolve: (response: ResponseEnvelope) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

export interface SupervisorClientOptions {
  socketPath: string
  projectId: string
  requestTimeoutMs?: number
  reconnectBaseMs?: number
  reconnectMaxMs?: number
}

export class SupervisorClient extends EventEmitter {
  private readonly socketPath: string
  private projectId: string
  private readonly requestTimeoutMs: number
  private readonly reconnectBaseMs: number
  private readonly reconnectMaxMs: number

  private socket: Socket | null = null
  private buffer = ''
  private pending = new Map<string, PendingRequest>()
  private state: ConnectionState = 'offline'
  private lastCursor = 0
  private reconnectAttempt = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private closed = false
  private eventHandler: ((event: EventEnvelope) => void) | null = null

  constructor(options: SupervisorClientOptions) {
    super()
    this.socketPath = options.socketPath
    this.projectId = options.projectId
    this.requestTimeoutMs = options.requestTimeoutMs ?? 30_000
    this.reconnectBaseMs = options.reconnectBaseMs ?? 500
    this.reconnectMaxMs = options.reconnectMaxMs ?? 30_000
  }

  getState(): ConnectionState {
    return this.state
  }

  getLastCursor(): number {
    return this.lastCursor
  }

  getProjectId(): string {
    return this.projectId
  }

  setProjectId(projectId: string): void {
    if (this.projectId === projectId) {
      return
    }
    this.projectId = projectId
    if (this.state === 'connected') {
      this.subscribe()
    }
  }

  /**
   * Close the onboarding connection and reconnect scoped to a registered project.
   */
  rebind(projectId: string): void {
    if (this.closed) {
      return
    }
    this.disconnect()
    this.projectId = projectId
    this.connect()
  }

  private disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    for (const [, req] of this.pending) {
      clearTimeout(req.timer)
      req.reject(new Error('client rebound'))
    }
    this.pending.clear()
    if (this.socket) {
      this.socket.destroy()
      this.socket = null
    }
    this.buffer = ''
    this.lastCursor = 0
    this.reconnectAttempt = 0
    this.setState('offline')
  }

  onEvent(handler: (event: EventEnvelope) => void): void {
    this.eventHandler = handler
  }

  connect(): void {
    if (this.closed) return
    this.setState('connecting')
    this.socket = connect(this.socketPath)

    this.socket.on('connect', () => {
      this.reconnectAttempt = 0
      this.setState('connected')
      this.subscribe()
    })

    this.socket.on('data', data => {
      this.buffer += data.toString()
      this.processBuffer()
    })

    this.socket.on('close', () => {
      if (!this.closed) {
        this.scheduleReconnect()
      }
    })

    this.socket.on('error', () => {
      // 'close' will follow
    })
  }

  async request(method: string, params: Record<string, unknown> = {}): Promise<ResponseEnvelope> {
    if (this.state !== 'connected' || !this.socket) {
      throw new Error(`not connected (state=${this.state})`)
    }

    const requestId = nextRequestId()
    const envelope: RequestEnvelope = {
      type: 'request',
      protocol_version: PROTOCOL_VERSION,
      request_id: requestId,
      project_id: this.projectId,
      method,
      params,
    }

    return new Promise<ResponseEnvelope>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        reject(new Error(`request ${requestId} timed out`))
      }, this.requestTimeoutMs)

      this.pending.set(requestId, { resolve, reject, timer })

      try {
        this.socket!.write(encodeEnvelope(envelope) + '\n')
      } catch (err) {
        clearTimeout(timer)
        this.pending.delete(requestId)
        reject(err)
      }
    })
  }

  close(): void {
    this.closed = true
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    for (const [, req] of this.pending) {
      clearTimeout(req.timer)
      req.reject(new Error('client closed'))
    }
    this.pending.clear()
    if (this.socket) {
      this.socket.destroy()
      this.socket = null
    }
    this.setState('offline')
  }

  private setState(state: ConnectionState): void {
    if (this.state !== state) {
      this.state = state
      this.emit('state', state)
    }
  }

  private subscribe(): void {
    void this.request('events.subscribe', { after_cursor: this.lastCursor }).catch(() => {
      // Subscribe failure is non-fatal; events may still arrive
    })
  }

  private processBuffer(): void {
    let newlineIdx: number
    while ((newlineIdx = this.buffer.indexOf('\n')) !== -1) {
      const line = this.buffer.slice(0, newlineIdx)
      this.buffer = this.buffer.slice(newlineIdx + 1)

      if (!line.trim()) continue

      try {
        const envelope = decodeEnvelope(line)
        if (envelope.type === 'response') {
          this.handleResponse(envelope)
        } else if (envelope.type === 'event') {
          this.handleEvent(envelope)
        }
      } catch {
        // Malformed line — skip
      }
    }
  }

  private handleResponse(response: ResponseEnvelope): void {
    const req = this.pending.get(response.request_id)
    if (req) {
      this.pending.delete(response.request_id)
      clearTimeout(req.timer)
      req.resolve(response)
    }
  }

  private handleEvent(event: EventEnvelope): void {
    if (event.project_id !== this.projectId) return
    if (event.cursor <= this.lastCursor) return // deduplicate
    this.lastCursor = event.cursor
    this.eventHandler?.(event)
    this.emit('event', event)
  }

  private scheduleReconnect(): void {
    this.setState('reconnecting')
    const delay = Math.min(
      this.reconnectBaseMs * 2 ** this.reconnectAttempt,
      this.reconnectMaxMs,
    )
    this.reconnectAttempt += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }
}
