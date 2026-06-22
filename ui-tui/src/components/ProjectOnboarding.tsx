/**
 * One-time project registration confirmation shown before chat.
 */

import React from 'react'
import { Box, Text, useInput } from 'ink'

export interface ProjectInspectDraft {
  canonical_path: string
  repo_id: string
  default_branch: string
  branch_prefix: string
  verify_commands: string[]
  allow_push: boolean
  merge_policy: string
  review_policy: string
  max_tasks_per_day: number
  max_task_runtime_seconds: number
  registered: boolean
  path_changed: boolean
  project_id?: string
  stored_canonical_path?: string
}

interface ProjectOnboardingProps {
  draft: ProjectInspectDraft
  onAccept: () => void
  onReject: () => void
}

function formatVerifyCommands(commands: string[]): string {
  if (commands.length === 0) {
    return '(none detected)'
  }
  return commands.join(', ')
}

export function ProjectOnboarding({ draft, onAccept, onReject }: ProjectOnboardingProps) {
  useInput((_input, key) => {
    if (key.escape) {
      onReject()
      return
    }
    if (key.return) {
      onAccept()
    }
  })

  return (
    <Box flexDirection="column" width="100%" paddingX={1}>
      <Text bold>Register this project?</Text>
      <Text> </Text>
      {draft.path_changed && draft.stored_canonical_path ? (
        <Text color="yellow">
          Repository moved from {draft.stored_canonical_path}. Confirm the new location.
        </Text>
      ) : null}
      {draft.path_changed && draft.stored_canonical_path ? <Text> </Text> : null}
      <Text>
        <Text dimColor>Canonical path: </Text>
        <Text>{draft.canonical_path}</Text>
      </Text>
      <Text>
        <Text dimColor>Repo id: </Text>
        <Text>{draft.repo_id}</Text>
      </Text>
      <Text>
        <Text dimColor>Default branch: </Text>
        <Text>{draft.default_branch}</Text>
      </Text>
      <Text>
        <Text dimColor>Branch prefix: </Text>
        <Text>{draft.branch_prefix}</Text>
      </Text>
      <Text>
        <Text dimColor>Verify commands: </Text>
        <Text>{formatVerifyCommands(draft.verify_commands)}</Text>
      </Text>
      <Text> </Text>
      <Text bold>Policies</Text>
      <Text>
        <Text dimColor>Push: </Text>
        <Text>{draft.allow_push ? 'allow_push' : 'no push'}</Text>
      </Text>
      <Text>
        <Text dimColor>Merge: </Text>
        <Text>{draft.merge_policy}</Text>
      </Text>
      <Text>
        <Text dimColor>Review: </Text>
        <Text>{draft.review_policy}</Text>
      </Text>
      <Text> </Text>
      <Text bold>Budget defaults</Text>
      <Text>
        <Text dimColor>Max tasks per day: </Text>
        <Text>{draft.max_tasks_per_day}</Text>
      </Text>
      <Text>
        <Text dimColor>Max task runtime (seconds): </Text>
        <Text>{draft.max_task_runtime_seconds}</Text>
      </Text>
      <Text> </Text>
      <Text dimColor>Enter accept · Esc reject and exit</Text>
    </Box>
  )
}