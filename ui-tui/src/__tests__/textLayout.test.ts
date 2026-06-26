import { describe, expect, it } from 'vitest'
import { estimateWrappedLineCount, stringDisplayWidth, wrapText } from '../textLayout.js'

describe('textLayout', () => {
  it('counts CJK characters as width 2', () => {
    expect(stringDisplayWidth('你好')).toBe(4)
    expect(stringDisplayWidth('ab你好')).toBe(6)
  })

  it('wraps explicit newlines', () => {
    expect(wrapText('line1\nline2', 40)).toEqual(['line1', 'line2'])
  })

  it('wraps long mixed text into multiple lines', () => {
    const text = '你好'.repeat(20) + ' English tail'
    const lines = wrapText(text, 20)
    expect(lines.length).toBeGreaterThan(1)
    expect(estimateWrappedLineCount(text, 20)).toBe(lines.length)
  })
})