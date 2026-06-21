import React from 'react'
import { Box, useStdout } from 'ink'
import { useStore } from '@nanostores/react'
import { connectionState, transcript } from '../store.js'
import { Header } from './Header.js'
import { Transcript } from './Transcript.js'
import { Footer } from './Footer.js'

interface AppLayoutProps {
  projectId: string
}

export function AppLayout({ projectId }: AppLayoutProps) {
  const conn = useStore(connectionState)
  const items = useStore(transcript)
  const { stdout } = useStdout()
  const columns = stdout?.columns ?? 80
  const rows = stdout?.rows ?? 24

  return (
    <Box flexDirection="column" width={columns} height={rows}>
      <Header projectId={projectId} connectionState={conn} columns={columns} />
      <Transcript items={items} columns={columns} height={rows - 4} />
      <Footer connectionState={conn} columns={columns} />
    </Box>
  )
}
