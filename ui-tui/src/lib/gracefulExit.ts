// Adapted from Hermes Agent (MIT) by Nous Research.
// See THIRD_PARTY_NOTICES.md for attribution.

import { spawn } from 'node:child_process'

interface SetupOptions {
  cleanups?: (() => Promise<void> | void)[]
  failsafeMs?: number
  onError?: (scope: 'uncaughtException' | 'unhandledRejection', err: unknown) => void
  onSignal?: (signal: NodeJS.Signals) => void
}

const SIGNAL_EXIT_CODE: Record<'SIGHUP' | 'SIGINT' | 'SIGTERM', number> = {
  SIGHUP: 129,
  SIGINT: 130,
  SIGTERM: 143,
}

let wired = false

/**
 * Spawn a detached process that sends SIGKILL to us after a delay.
 * This is the only reliable failsafe when process.exit() blocks the
 * event loop (e.g. Ink's exit handler hanging on stdin read).
 */
function spawnKillFailsafe(delayMs: number): void {
  try {
    const child = spawn(
      'sh',
      ['-c', `sleep ${Math.ceil(delayMs / 1000)} && kill -9 ${process.pid} 2>/dev/null`],
      { detached: true, stdio: 'ignore' },
    )
    child.unref()
  } catch {
    // If spawn fails, we have no failsafe — but try our best.
  }
}

export function setupGracefulExit({
  cleanups = [],
  failsafeMs = 4000,
  onError,
  onSignal,
}: SetupOptions = {}) {
  if (wired) {
    return
  }

  wired = true

  let shuttingDown = false

  const exit = (code: number, signal?: NodeJS.Signals) => {
    if (shuttingDown) {
      return
    }

    shuttingDown = true

    if (signal) {
      onSignal?.(signal)
    }

    // Spawn a detached failsafe process. If process.exit() blocks the
    // event loop (Ink's exit handler on stdin), the child process
    // independently sends SIGKILL after the delay.
    spawnKillFailsafe(failsafeMs + 1000)

    setTimeout(() => process.exit(code), failsafeMs).unref?.()

    void Promise.allSettled(cleanups.map(fn => Promise.resolve().then(fn))).finally(() =>
      process.exit(code)
    )
  }

  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
    process.on(sig, () => exit(SIGNAL_EXIT_CODE[sig], sig))
  }

  process.on('uncaughtException', err => onError?.('uncaughtException', err))
  process.on('unhandledRejection', reason => onError?.('unhandledRejection', reason))
}
