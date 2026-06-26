import React from 'react'
import { Box } from 'ink'
import type { TranscriptItem } from '../domain.js'
import { selectTranscriptItems } from '../transcriptBudget.js'
import { Message } from './Message.js'
import { ActivityBlock } from './ActivityBlock.js'

interface TranscriptProps {
  items: TranscriptItem[]
  columns: number
  height: number
}

export function Transcript({ items, columns, height }: TranscriptProps) {
  const visibleItems = selectTranscriptItems(items, columns, height)

  return (
    <Box
      flexDirection="column"
      flexGrow={1}
      height={height}
      overflow="hidden"
    >
      {visibleItems.map(({ item, lines }) => {
        if (item.kind === 'message') {
          return (
            <Message
              key={item.id}
              role={item.role}
              text={item.text}
              columns={columns}
              lines={lines}
            />
          )
        }
        return <ActivityBlock key={item.id} activity={item.activity} columns={columns} />
      })}
    </Box>
  )
}