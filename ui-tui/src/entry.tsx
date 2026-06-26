// Coordinator TUI entry point.
// Adapted from Hermes Agent (MIT) by Nous Research.
// See THIRD_PARTY_NOTICES.md for attribution.

import { registerInkUnmount } from './detach.js'
import { setupLifecycle } from './lifecycle.js'
import { resetTerminalModes } from './lib/terminalModes.js'

export interface AppOptions {
  socketPath: string
  projectId: string
  canonicalPath?: string
}

export interface ParsedEntryArgs {
  socketPath: string
  projectId: string
  canonicalPath?: string
}

/**
 * Parse launcher argv: node entry.js <socketPath> <projectId> [canonicalPath]
 */
export function parseEntryArgs(argv: string[]): ParsedEntryArgs | null {
  const socketPath = argv[2]
  const projectId = argv[3]
  const canonicalPath = argv[4]

  if (!socketPath || !projectId) {
    return null
  }

  return {
    socketPath,
    projectId,
    canonicalPath: canonicalPath?.trim() ? canonicalPath : undefined,
  }
}

/**
 * Application factory. Creates the TUI app without starting it.
 * Tests can call this to verify construction without spawning a process.
 */
export function createApp(options: AppOptions) {
  const { socketPath, projectId, canonicalPath } = options

  return {
    socketPath,
    projectId,
    canonicalPath,
    async start() {
      const { render } = await import('ink')
      const { App } = await import('./app.js')

      setupLifecycle()

      const instance = render(
        <App socketPath={socketPath} projectId={projectId} canonicalPath={canonicalPath} />,
        {
        exitOnCtrlC: false,
      })

      registerInkUnmount(() => {
        const instances = require('ink/build/instances.js').default as WeakMap<
          NodeJS.WriteStream,
          { isUnmounted: boolean; unmount: (error?: unknown) => void }
        >
        const ink = instances.get(process.stdout)
        if (ink && !ink.isUnmounted) {
          ink.unmount(null)
        }
      })

      void instance.waitUntilExit().then(() => {
        if (!process.stdout.destroyed) {
          resetTerminalModes()
        }
        process.exit(0)
      }).catch(err => {
        process.stderr.write(`coordinator-tui: exit error: ${String(err)}\n`)
        if (!process.stdout.destroyed) {
          resetTerminalModes()
        }
        process.exit(1)
      })
    },
  }
}

// CLI invocation: node dist/entry.js <socketPath> <projectId> [canonicalPath]
if (process.argv[1] && !process.env.VITEST) {
  const parsed = parseEntryArgs(process.argv)
  if (!parsed) {
    console.error('Usage: coordinator-tui <socketPath> <projectId> [canonicalPath]')
    process.exit(1)
  }
  const { socketPath, projectId, canonicalPath } = parsed

  // TTY guard
  if (!process.stdin.isTTY) {
    console.log('coordinator-tui: no TTY')
    process.exit(0)
  }

  // Terminal cleanup
  resetTerminalModes()
  process.stdout.write('\x1b[2J\x1b[H\x1b[3J')

  const app = createApp({
    socketPath,
    projectId,
    canonicalPath: canonicalPath || undefined,
  })
  void app.start()
}