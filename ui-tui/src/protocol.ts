/**
 * Coordinator Supervisor protocol types.
 * Compatible with supervisor_protocol.py version 1.
 */

export const PROTOCOL_VERSION = 1 as const
export const MAX_MESSAGE_BYTES = 1024 * 1024

export interface RequestEnvelope {
  type: 'request'
  protocol_version: typeof PROTOCOL_VERSION
  request_id: string
  project_id: string | null
  method: string
  params: Record<string, unknown>
}

export interface ResponseEnvelope {
  type: 'response'
  protocol_version: typeof PROTOCOL_VERSION
  request_id: string
  ok: boolean
  result: Record<string, unknown> | null
  error: string | null
}

export interface EventEnvelope {
  type: 'event'
  protocol_version: typeof PROTOCOL_VERSION
  project_id: string
  cursor: number
  event_type: string
  payload: Record<string, unknown>
}

export type Envelope = RequestEnvelope | ResponseEnvelope | EventEnvelope

let requestCounter = 0

export function nextRequestId(): string {
  requestCounter += 1
  return `tui-${requestCounter}-${Date.now().toString(36)}`
}

export function encodeEnvelope(envelope: Envelope): string {
  const encoded = JSON.stringify(envelope)
  if (new TextEncoder().encode(encoded).length > MAX_MESSAGE_BYTES) {
    throw new Error('message too large')
  }
  return encoded
}

export function decodeEnvelope(raw: string): Envelope {
  if (new TextEncoder().encode(raw).length > MAX_MESSAGE_BYTES) {
    throw new Error('message too large')
  }

  const data: unknown = JSON.parse(raw)

  if (typeof data !== 'object' || data === null) {
    throw new Error('envelope must be an object')
  }

  const obj = data as Record<string, unknown>
  const envelopeType = obj.type

  if (envelopeType === 'response') {
    return {
      type: 'response',
      protocol_version: validateProtocolVersion(obj.protocol_version),
      request_id: validateNonBlankString(obj.request_id, 'request_id'),
      ok: typeof obj.ok === 'boolean' ? obj.ok : false,
      result: (obj.result as Record<string, unknown>) ?? null,
      error: typeof obj.error === 'string' ? obj.error : null,
    }
  }

  if (envelopeType === 'event') {
    return {
      type: 'event',
      protocol_version: validateProtocolVersion(obj.protocol_version),
      project_id: validateNonBlankString(obj.project_id, 'project_id'),
      cursor: validateCursor(obj.cursor),
      event_type: validateNonBlankString(obj.event_type, 'event_type'),
      payload: (obj.payload as Record<string, unknown>) ?? {},
    }
  }

  throw new Error(`unsupported envelope type: ${String(envelopeType)}`)
}

function validateProtocolVersion(value: unknown): typeof PROTOCOL_VERSION {
  if (value !== PROTOCOL_VERSION) {
    throw new Error(`unsupported protocol_version: ${String(value)}`)
  }
  return PROTOCOL_VERSION
}

function validateNonBlankString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${field} must be a non-blank string`)
  }
  return value.trim()
}

function validateCursor(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new Error('cursor must be a non-negative integer')
  }
  return value
}
