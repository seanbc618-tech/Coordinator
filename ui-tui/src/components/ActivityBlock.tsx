import React from 'react'
import { Box, Text } from 'ink'
import type { Activity } from '../domain.js'
import { estimateWrappedLineCount } from '../textLayout.js'

interface ActivityBlockProps {
  activity: Activity
  columns: number
}

export function estimateActivityLines(activity: Activity, columns: number): number {
  const compact = columns < 60
  const isDiagnostic = activity.stage.startsWith('commander:')
  const verifySummary = activity.verificationCommands?.length
    ? activity.verificationCommands.join('; ')
    : null
  const failureNote = activity.stage.startsWith('failed') && activity.latestNote
    ? activity.latestNote
    : null

  if (!activity.expanded || compact) {
    let lines = 1
    if (activity.goal && activity.stage === 'created') {
      lines += estimateWrappedLineCount(
        `Goal: ${activity.goal}`,
        Math.max(columns - 8, 24),
      )
    }
    if (verifySummary && activity.stage === 'created') {
      lines += estimateWrappedLineCount(
        `Verify: ${verifySummary}`,
        Math.max(columns - 10, 24),
      )
    }
    if (failureNote) {
      lines += estimateWrappedLineCount(
        `Reason: ${failureNote}`,
        Math.max(columns - 10, 24),
      )
    }
    if (isDiagnostic && activity.output.length > 0) {
      lines += 1
    }
    return lines
  }

  let lines = 1
  if (activity.latestCommand) {
    lines += 1
  }
  if (activity.fallback) {
    lines += 1
  }
  if (isDiagnostic && activity.output.length > 0) {
    lines += 1
  }
  lines += Math.min(activity.output.length, 10)
  return lines
}

function formatElapsed(startedAt: number | null): string {
  if (!startedAt) return ''
  const seconds = Math.floor((Date.now() - startedAt) / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m${seconds % 60}s`
}

export function ActivityBlock({ activity, columns }: ActivityBlockProps) {
  const compact = columns < 60

  // Compact: single line
  // Expanded: show output
  const isDiagnostic = activity.stage.startsWith('commander:')

  const statusIcon = isDiagnostic
    ? '⚠'
    : activity.stage.startsWith('done')
      ? '✓'
      : activity.stage.startsWith('verification')
        ? '⟐'
        : activity.stage.startsWith('review')
          ? '◉'
          : activity.stage.startsWith('git')
            ? '⎇'
            : '●'

  const statusColor = isDiagnostic
    ? 'yellow'
    : activity.stage.startsWith('done')
      ? 'green'
      : activity.stage.startsWith('verification: passed')
        ? 'green'
        : activity.stage.startsWith('verification: failed')
          ? 'red'
          : 'yellow'

  const verifySummary = activity.verificationCommands?.length
    ? activity.verificationCommands.join('; ')
    : null
  const failureNote = activity.stage.startsWith('failed') && activity.latestNote
    ? activity.latestNote
    : null

  if (!activity.expanded || compact) {
    return (
      <Box paddingX={1} flexDirection="column">
        <Box flexDirection="row">
          <Text color={statusColor}>{statusIcon} </Text>
          <Text bold>{activity.title}</Text>
          {activity.agent && <Text dimColor> [{activity.agent}]</Text>}
          <Text dimColor> {activity.stage}</Text>
          {activity.startedAt && <Text dimColor> {formatElapsed(activity.startedAt)}</Text>}
          {activity.fallback && (
            <Text color="yellow"> ⚠ {activity.fallback.from}→{activity.fallback.to}</Text>
          )}
        </Box>
        {activity.goal && activity.stage === 'created' && (
          <Text dimColor>  Goal: {activity.goal.slice(0, Math.max(columns - 8, 24))}</Text>
        )}
        {verifySummary && activity.stage === 'created' && (
          <Text dimColor>  Verify: {verifySummary.slice(0, Math.max(columns - 10, 24))}</Text>
        )}
        {failureNote && (
          <Text color="red">  Reason: {failureNote}</Text>
        )}
        {isDiagnostic && !activity.expanded && activity.output.length > 0 && (
          <Text dimColor>  {activity.output.length} diagnostic note(s) — expand for details</Text>
        )}
      </Box>
    )
  }

  const liveTail = !activity.stage.startsWith('done:')
    && !activity.stage.startsWith('failed:')
    && activity.stage !== 'created'
  const outputLines = activity.output.slice(liveTail ? -20 : -10)
  return (
    <Box flexDirection="column" paddingX={1} borderStyle="round" borderColor="gray">
      <Text>
        <Text color={statusColor}>{statusIcon} </Text>
        <Text bold>{activity.title}</Text>
        {activity.agent && <Text dimColor> [{activity.agent}]</Text>}
        <Text dimColor> {activity.stage}</Text>
        {activity.startedAt && <Text dimColor> {formatElapsed(activity.startedAt)}</Text>}
      </Text>
      {activity.latestCommand && (
        <Text dimColor>  $ {activity.latestCommand}</Text>
      )}
      {activity.fallback && (
        <Text color="yellow">  ⚠ fallback: {activity.fallback.from} → {activity.fallback.to} ({activity.fallback.used}/{activity.fallback.limit})</Text>
      )}
      {isDiagnostic && activity.output.length > 0 && (
        <Text dimColor>  Diagnostics:</Text>
      )}
      {outputLines.map((line, i) => (
        <Text key={i} dimColor>  {line.slice(0, Math.max(columns - 4, 20))}</Text>
      ))}
    </Box>
  )
}
