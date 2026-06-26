import React from 'react'
import { Box, Text } from 'ink'
import { wrapText } from '../textLayout.js'

interface MessageProps {
  role: 'user' | 'coordinator' | 'system'
  text: string
  columns: number
  lines?: string[]
}

export function messagePrefix(role: 'user' | 'coordinator' | 'system'): string {
  return role === 'user' ? '> ' : role === 'system' ? '! ' : ''
}

export function messageMaxWidth(columns: number): number {
  return Math.max(columns - 4, 20)
}

export function formatMessageLines(
  role: 'user' | 'coordinator' | 'system',
  text: string,
  columns: number,
): string[] {
  return wrapText(messagePrefix(role) + text, messageMaxWidth(columns))
}

export function estimateMessageLines(
  role: 'user' | 'coordinator' | 'system',
  text: string,
  columns: number,
): number {
  return formatMessageLines(role, text, columns).length
}

export function Message({ role, text, columns, lines }: MessageProps) {
  const color = role === 'user' ? 'cyan' : role === 'system' ? 'yellow' : undefined
  const rendered = lines ?? formatMessageLines(role, text, columns)

  return (
    <Box flexDirection="column" paddingX={1}>
      {rendered.map((line, index) => (
        <Text key={index} color={color}>{line}</Text>
      ))}
    </Box>
  )
}