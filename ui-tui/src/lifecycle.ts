/**
 * Terminal lifecycle management for the Coordinator TUI.
 *
 * Handles graceful cleanup on detach, signal, uncaught exception,
 * and process exit. Idempotent — safe to call multiple times.
 */

import { resetTerminalModes } from './lib/terminalModes.js'

interface LifecycleOptions {
  onCleanup?: () => void | Promise<void>
}

let cleaned = false

export function setupLifecycle(options: LifecycleOptions = {}): void {
  if (cleaned) return

  const cleanup = () => {
    if (cleaned) return
    cleaned = true
    resetTerminalModes()
    try {
      options.onCleanup?.()
    } catch {
      // Cleanup errors are non-fatal
    }
  }

  process.on('exit', cleanup)
  process.on('SIGINT', () => { cleanup(); process.exit(130) })
  process.on('SIGTERM', () => { cleanup(); process.exit(143) })
  process.on('SIGHUP', () => { cleanup(); process.exit(129) })
  process.on('uncaughtException', err => {
    process.stderr.write(`coordinator-tui: uncaught: ${String(err)}\n`)
    cleanup()
    process.exit(1)
  })
  process.on('unhandledRejection', reason => {
    process.stderr.write(`coordinator-tui: unhandled: ${String(reason)}\n`)
    cleanup()
    process.exit(1)
  })
}

export function isCleanedUp(): boolean {
  return cleaned
}

/** Reset for testing. */
export function _resetLifecycleForTesting(): void {
  cleaned = false
}
