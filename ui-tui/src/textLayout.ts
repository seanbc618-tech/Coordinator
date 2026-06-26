/**
 * Shared text wrapping and line-budget utilities for transcript rendering.
 */

export function charDisplayWidth(char: string): number {
  const code = char.codePointAt(0) ?? 0
  if (code <= 0x7f) {
    return 1
  }
  if (
    (code >= 0x1100 && code <= 0x115f)
    || (code >= 0x2e80 && code <= 0xa4cf)
    || (code >= 0xac00 && code <= 0xd7a3)
    || (code >= 0xf900 && code <= 0xfaff)
    || (code >= 0xfe10 && code <= 0xfe19)
    || (code >= 0xfe30 && code <= 0xfe6f)
    || (code >= 0xff00 && code <= 0xff60)
    || (code >= 0xffe0 && code <= 0xffe6)
    || (code >= 0x20000 && code <= 0x2ffff)
  ) {
    return 2
  }
  return 1
}

export function stringDisplayWidth(text: string): number {
  let width = 0
  for (const char of text) {
    width += charDisplayWidth(char)
  }
  return width
}

export function wrapText(text: string, maxWidth: number): string[] {
  if (maxWidth <= 0) {
    return [text]
  }

  const paragraphs = text.split('\n')
  const lines: string[] = []

  for (const paragraph of paragraphs) {
    if (!paragraph) {
      lines.push('')
      continue
    }

    let remaining = paragraph
    while (remaining.length > 0) {
      if (stringDisplayWidth(remaining) <= maxWidth) {
        lines.push(remaining)
        break
      }

      let breakIndex = -1
      let width = 0
      let lastSpace = -1
      for (let index = 0; index < remaining.length; index++) {
        const char = remaining[index]!
        const charWidth = charDisplayWidth(char)
        if (char === ' ') {
          lastSpace = index
        }
        if (width + charWidth > maxWidth) {
          breakIndex = lastSpace > 0 ? lastSpace : index
          if (breakIndex <= 0) {
            breakIndex = index || 1
          }
          break
        }
        width += charWidth
      }

      if (breakIndex < 0) {
        breakIndex = remaining.length
      }

      const chunk = remaining.slice(0, breakIndex).trimEnd()
      lines.push(chunk.length > 0 ? chunk : remaining.slice(0, 1))
      remaining = remaining.slice(breakIndex).trimStart()
    }
  }

  return lines.length > 0 ? lines : ['']
}

export function estimateWrappedLineCount(text: string, maxWidth: number): number {
  return wrapText(text, maxWidth).length
}