import React from 'react'
import { Box, Text } from 'ink'

interface HeaderProps {
  projectId: string
  connectionState: string
  columns: number
}

export function Header({ projectId, connectionState, columns }: HeaderProps) {
  const statusColor = connectionState === 'connected' ? 'green' : connectionState === 'reconnecting' ? 'yellow' : 'red'

  if (columns < 60) {
    return (
      <Box flexDirection="column" borderStyle="single" borderColor="gray" paddingX={1}>
        <Text>
          <Text bold color="blue">◆ {projectId}</Text>
          <Text> </Text>
          <Text color={statusColor}>[{connectionState}]</Text>
        </Text>
      </Box>
    )
  }

  return (
    <Box flexDirection="column" borderStyle="single" borderColor="gray" paddingX={1}>
      <Text>
        <Text bold color="blue">◆ Coordinator</Text>
        <Text> — </Text>
        <Text bold>{projectId}</Text>
        <Text> | </Text>
        <Text color={statusColor}>{connectionState}</Text>
      </Text>
    </Box>
  )
}
