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

// CLI invocation: node dist/entry.js <socketPath> <projectId>
if (process.argv[1] && !process.env.VITEST) {
  const socketPath = process.argv[2]
  const projectId = process.argv[3]

  if (!socketPath || !projectId) {
    console.error('Usage: coordinator-tui <socketPath> <projectId>')
    process.exit(1)
  }

  // TTY guard
  if (!process.stdin.isTTY) {
    console.log('coordinator-tui: no TTY')
    process.exit(0)
  }

  // Terminal cleanup
  resetTerminalModes()
  process.stdout.write('\x1b[2J\x1b[H\x1b[3J')

  const app = createApp({ socketPath, projectId })
  void app.start()
}