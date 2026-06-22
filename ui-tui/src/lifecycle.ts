/**
 * Terminal lifecycle management for the Coordinator TUI.
 *
 * Single owner for signal and error cleanup. Interactive detach routes
 * through performDetach(); signals use the same path when registered.
 */

import { resetTerminalModes } from './lib/terminalModes.js'
import { hasDetachHandlers, performDetach } from './detach.js'

interface LifecycleOptions {
  onCleanup?: () => void | Promise<void>
}

let wired = false
let cleaned = false

export function setupLifecycle(options: LifecycleOptions = {}): void {
  if (wired) return
  wired = true

  const cleanup = () => {
    if (cleaned) return
    cleaned = true
    if (!process.stdout.destroyed) {
      resetTerminalModes()
    }
    try {
      options.onCleanup?.()
    } catch {
      // Cleanup errors are non-fatal
    }
  }

  const shutdown = (code: number) => {
    cleanup()
    process.exit(code)
  }

  process.on('exit', cleanup)

  process.on('SIGINT', () => {
    if (hasDetachHandlers()) {
      performDetach()
      return
    }
    shutdown(130)
  })

  process.on('SIGTERM', () => shutdown(143))
  process.on('SIGHUP', () => shutdown(129))

  process.on('uncaughtException', err => {
    process.stderr.write(`coordinator-tui: uncaught: ${String(err)}\n`)
    shutdown(1)
  })

  process.on('unhandledRejection', reason => {
    process.stderr.write(`coordinator-tui: unhandled: ${String(reason)}\n`)
    shutdown(1)
  })
}

export function isCleanedUp(): boolean {
  return cleaned
}

/**
 * Mark cleanup as already done. Used by performDetach() to prevent the
 * exit handler from calling resetTerminalModes(), which blocks on a full
 * PTY buffer (writeSync to fd 1 hangs when the master is unread).
 */
export function markCleanedUp(): void {
  cleaned = true
}

/** Reset for testing. */
export function _resetLifecycleForTesting(): void {
  cleaned = false
  wired = false
}