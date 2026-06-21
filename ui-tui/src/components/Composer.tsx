import React, { useState, useCallback } from 'react'
import { Box, Text, useInput, useApp } from 'ink'
import { completePartial } from '../slash.js'
import { InputHistory } from '../inputHistory.js'

interface ComposerProps {
  onSubmit: (text: string) => void
  onDetach: () => void
  disabled?: boolean
}

export function Composer({ onSubmit, onDetach, disabled = false }: ComposerProps) {
  const [input, setInput] = useState('')
  const [completions, setCompletions] = useState<string[]>([])
  const [history] = useState(() => new InputHistory())
  const { exit } = useApp()

  const handleSubmit = useCallback(() => {
    const text = input.trim()
    if (!text) return
    history.push(text)
    onSubmit(text)
    setInput('')
    setCompletions([])
  }, [input, onSubmit, history])

  useInput((inputChar, key) => {
    if (disabled) return

    // Ctrl+C — detach (ctrl flag is set, inputChar is 'c')
    if (key.ctrl && inputChar === 'c') {
      onDetach()
      exit()
      return
    }

    // Enter — submit
    if (key.return) {
      handleSubmit()
      return
    }

    // Tab — completion
    if (key.tab) {
      if (completions.length > 0) {
        setInput(completions[0]!)
        setCompletions([])
      } else if (input.startsWith('/')) {
        const matches = completePartial(input)
        if (matches.length === 1) {
          setInput(matches[0]!)
          setCompletions([])
        } else {
          setCompletions(matches)
        }
      }
      return
    }

    // Up arrow — history
    if (key.upArrow) {
      setInput(history.up(input))
      return
    }

    // Down arrow — history
    if (key.downArrow) {
      setInput(history.down(input))
      return
    }

    // Backspace
    if (key.backspace || key.delete) {
      setInput(prev => prev.slice(0, -1))
      setCompletions([])
      return
    }

    // Regular character
    if (inputChar && !key.ctrl && !key.meta) {
      setInput(prev => prev + inputChar)
      setCompletions([])
    }
  })

  const promptColor = disabled ? 'gray' : 'cyan'

  return (
    <Box flexDirection="column" borderStyle="single" borderColor="gray" paddingX={1}>
      <Box>
        <Text color={promptColor}>❯ </Text>
        <Text>{input}</Text>
        <Text dimColor>█</Text>
      </Box>
      {completions.length > 0 && (
        <Box>
          <Text dimColor>  {completions.join('  ')}</Text>
        </Box>
      )}
      {disabled && (
        <Text dimColor>  (paused — type to queue)</Text>
      )}
    </Box>
  )
}
