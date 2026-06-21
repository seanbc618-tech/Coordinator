import React from 'react'
import { Box } from 'ink'
import type { TranscriptItem } from '../domain.js'
import { Message } from './Message.js'
import { ActivityBlock } from './ActivityBlock.js'

interface TranscriptProps {
  items: TranscriptItem[]
  columns: number
  height: number
}

export function Transcript({ items, columns, height }: TranscriptProps) {
  // Show the last N items that fit in the available height
  const visibleItems = items.slice(-Math.max(height - 2, 5))

  return (
    <Box flexDirection="column" flexGrow={1}>
      {visibleItems.map(item => {
        if (item.kind === 'message') {
          return <Message key={item.id} role={item.role} text={item.text} columns={columns} />
        }
        return <ActivityBlock key={item.id} activity={item.activity} columns={columns} />
      })}
    </Box>
  )
}
