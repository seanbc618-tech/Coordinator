import React from 'react'
import { Box, Text } from 'ink'

interface FooterProps {
  connectionState: string
  columns: number
}

export function Footer({ connectionState, columns }: FooterProps) {
  const statusColor = connectionState === 'connected' ? 'green' : connectionState === 'reconnecting' ? 'yellow' : 'red'

  if (columns < 60) {
    return (
      <Box borderStyle="single" borderColor="gray" paddingX={1}>
        <Text color={statusColor}>{connectionState}</Text>
        <Text dimColor> | Tab:expand /help</Text>
      </Box>
    )
  }

  return (
    <Box borderStyle="single" borderColor="gray" paddingX={1}>
      <Text>
        <Text color={statusColor}>● {connectionState}</Text>
        <Text dimColor> | </Text>
        <Text dimColor>Tab: expand activity | /help: commands | Ctrl+C: detach</Text>
      </Text>
    </Box>
  )
}
