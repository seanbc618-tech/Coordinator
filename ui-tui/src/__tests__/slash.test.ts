import { describe, expect, it } from 'vitest'
import { parse, completePartial, SLASH_COMMANDS } from '../slash.js'

describe('parse', () => {
  it('parses plain message', () => {
    const result = parse('hello world')
    expect(result).toEqual({ type: 'message', text: 'hello world' })
  })

  it('parses /status command', () => {
    const result = parse('/status')
    expect(result.type).toBe('command')
    if (result.type === 'command') {
      expect(result.command.name).toBe('/status')
      expect(result.command.method).toBe('project.status')
      expect(result.args).toBe('')
    }
  })

  it('parses command with args', () => {
    const result = parse('/stop proj-a')
    expect(result.type).toBe('command')
    if (result.type === 'command') {
      expect(result.command.name).toBe('/stop')
      expect(result.args).toBe('proj-a')
    }
  })

  it('treats unknown slash as message', () => {
    const result = parse('/unknown')
    expect(result).toEqual({ type: 'message', text: '/unknown' })
  })

  it('handles empty input', () => {
    const result = parse('')
    expect(result).toEqual({ type: 'message', text: '' })
  })

  it('handles whitespace-only input', () => {
    const result = parse('   ')
    expect(result).toEqual({ type: 'message', text: '' })
  })

  it('marks /shutdown as destructive', () => {
    const result = parse('/shutdown')
    expect(result.type).toBe('command')
    if (result.type === 'command') {
      expect(result.command.destructive).toBe(true)
    }
  })

  it('marks /stop as destructive', () => {
    const result = parse('/stop')
    expect(result.type).toBe('command')
    if (result.type === 'command') {
      expect(result.command.destructive).toBe(true)
    }
  })

  it('parses all commands in the catalog', () => {
    for (const cmd of SLASH_COMMANDS) {
      const result = parse(cmd.name)
      expect(result.type).toBe('command')
      if (result.type === 'command') {
        expect(result.command.name).toBe(cmd.name)
      }
    }
  })
})

describe('completePartial', () => {
  it('completes /st to matching commands', () => {
    const results = completePartial('/st')
    expect(results).toContain('/status')
    expect(results).toContain('/stop')
  })

  it('completes /h to /help', () => {
    const results = completePartial('/h')
    expect(results).toEqual(['/help'])
  })

  it('returns empty for non-slash input', () => {
    expect(completePartial('hello')).toEqual([])
  })

  it('returns empty for no matches', () => {
    expect(completePartial('/zzz')).toEqual([])
  })

  it('completes exact match', () => {
    const results = completePartial('/quit')
    expect(results).toEqual(['/quit'])
  })
})
