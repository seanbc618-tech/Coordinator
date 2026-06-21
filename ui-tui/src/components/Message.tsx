import React from 'react'
import { Box, Text } from 'ink'

interface MessageProps {
  role: 'user' | 'coordinator' | 'system'
  text: string
  columns: number
}

export function Message({ role, text, columns }: MessageProps) {
  const prefix = role === 'user' ? '> ' : role === 'system' ? '! ' : ''
  const color = role === 'user' ? 'cyan' : role === 'system' ? 'yellow' : undefined

  // Wrap long lines
  const maxWidth = Math.max(columns - 4, 20)
  const lines = wrapText(prefix + text, maxWidth)

  return (
    <Box flexDirection="column" paddingX={1}>
      {lines.map((line, i) => (
        <Text key={i} color={color}>{line}</Text>
      ))}
    </Box>
  )
}

function wrapText(text: string, maxWidth: number): string[] {
  if (text.length <= maxWidth) return [text]
  const lines: string[] = []
  let remaining = text
  while (remaining.length > maxWidth) {
    // Find last space within maxWidth
    let breakAt = remaining.lastIndexOf(' ', maxWidth)
    if (breakAt <= 0) breakAt = maxWidth
    lines.push(remaining.slice(0, breakAt))
    remaining = remaining.slice(breakAt).trimStart()
  }
  if (remaining) lines.push(remaining)
  return lines
}
