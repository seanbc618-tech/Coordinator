/**
 * Unified detach path for Ctrl+C and /quit.
 *
 * Ink must unmount before process.exit(); release stdin first so Ink's
 * readable listener does not block while the PTY master fd stays open.
 */

import { markCleanedUp } from './lifecycle.js'

interface DetachHandlers {
  closeClient: () => void
}

let handlers: DetachHandlers | null = null
let inkUnmount: (() => void) | null = null
let detaching = false

export function registerDetachHandlers(next: DetachHandlers | null): void {
  handlers = next
}

export function registerInkUnmount(next: (() => void) | null): void {
  inkUnmount = next
}

export function hasDetachHandlers(): boolean {
  return handlers !== null
}

function releaseStdinForDetach(): void {
  const stdin = process.stdin
  if (!stdin.isTTY) {
    return
  }
  try {
    stdin.setRawMode?.(false)
  } catch {
    // Best-effort when stdin is already torn down.
  }
  stdin.removeAllListeners('readable')
  stdin.removeAllListeners('data')
  stdin.pause()
  stdin.unref?.()
}

/**
 * Ink waits for stdout to drain before resolving exit(). When the PTY master
 * fd stays open but unread, that barrier write never completes. Destroy stdout
 * so unmount uses the process-exiting fast path instead.
 */
function releaseStdoutForDetach(): void {
  const stdout = process.stdout
  if (stdout.destroyed) {
    return
  }
  try {
    stdout.destroy()
  } catch {
    // Best-effort when stdout is already torn down.
  }
}

/**
 * Detach the TUI without stopping Supervisor work.
 * Idempotent — safe to call from Ctrl+C, /quit, or SIGINT.
 */
export function performDetach(): void {
  if (detaching || !handlers) {
    return
  }
  detaching = true

  // Restore canonical mode before any PTY output; setRawMode(false) is what
  // Gate E asserts (ICANON/ECHO) — it must not follow a blocking stdout write.
  releaseStdinForDetach()
  handlers.closeClient()
  releaseStdoutForDetach()
  inkUnmount?.()
  // Mark lifecycle cleanup as done so the exit handler does NOT call
  // resetTerminalModes() — its writeSync(fd, …) blocks on a full PTY
  // buffer when the master fd is unread.
  markCleanedUp()
  process.exit(0)
}

/** Reset for unit tests. */
export function _resetDetachForTesting(): void {
  detaching = false
  handlers = null
  inkUnmount = null
}