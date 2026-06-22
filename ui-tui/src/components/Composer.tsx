import React, { useState, useCallback, useRef } from 'react'
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

  // Ref mirrors state so useInput callbacks always read the latest input,
  // avoiding stale closures when React 18 batches rapid setInput calls.
  const inputRef = useRef(input)
  inputRef.current = input

  const handleSubmit = useCallback(() => {
    const text = inputRef.current.trim()
    if (!text) return
    history.push(text)
    onSubmit(text)
    setInput('')
    setCompletions([])
  }, [onSubmit, history])

  // Ref for handleSubmit so useInput always calls the latest version,
  // avoiding stale closure when React 18 batches rapid input events.
  const handleSubmitRef = useRef(handleSubmit)
  handleSubmitRef.current = handleSubmit

  useInput((inputChar, key) => {
    // Ctrl+C — detach (always handled, even when disconnected)
    if (key.ctrl && inputChar === 'c') {
      onDetach()
      exit()
      return
    }

    if (disabled) return

    // Enter — submit
    if (key.return) {
      handleSubmitRef.current()
      return
    }

    // Tab — completion
    if (key.tab) {
      if (completions.length > 0) {
        setInput(completions[0]!)
        setCompletions([])
      } else if (inputRef.current.startsWith('/')) {
        const matches = completePartial(inputRef.current)
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
      setInput(history.up(inputRef.current))
      return
    }

    // Down arrow — history
    if (key.downArrow) {
      setInput(history.down(inputRef.current))
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
