import type { TranscriptItem } from './domain.js'
import { estimateActivityLines } from './components/ActivityBlock.js'
import { formatMessageLines } from './components/Message.js'

export interface RenderedTranscriptItem {
  item: TranscriptItem
  lines?: string[]
}

export function estimateTranscriptItemLines(item: TranscriptItem, columns: number): number {
  if (item.kind === 'message') {
    return formatMessageLines(item.role, item.text, columns).length
  }
  return estimateActivityLines(item.activity, columns)
}

export function selectTranscriptItems(
  items: TranscriptItem[],
  columns: number,
  height: number,
): RenderedTranscriptItem[] {
  const budget = Math.max(height, 1)
  const selected: RenderedTranscriptItem[] = []
  let used = 0

  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]!
    const lines = transcriptItemLines(item, columns)
    const lineCount = lines.length

    if (lineCount <= budget - used) {
      selected.unshift({ item, lines })
      used += lineCount
      continue
    }

    if (selected.length === 0 && lineCount > 0) {
      selected.unshift({ item, lines: lines.slice(-budget) })
    }
    break
  }

  return selected
}

function transcriptItemLines(item: TranscriptItem, columns: number): string[] {
  if (item.kind === 'message') {
    return formatMessageLines(item.role, item.text, columns)
  }
  const count = estimateActivityLines(item.activity, columns)
  return Array.from({ length: count }, (_, index) => `activity-${index}`)
}