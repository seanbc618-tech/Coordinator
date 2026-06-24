import React from 'react'
import { Box, Text } from 'ink'
import type { Activity } from '../domain.js'

interface ActivityBlockProps {
  activity: Activity
  columns: number
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

  // Expanded view
  const outputLines = activity.output.slice(-10)
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
