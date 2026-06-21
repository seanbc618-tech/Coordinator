// Coordinator TUI entry point.
// Adapted from Hermes Agent (MIT) by Nous Research.
// See THIRD_PARTY_NOTICES.md for attribution.

import { setupGracefulExit } from './lib/gracefulExit.js'
import { resetTerminalModes } from './lib/terminalModes.js'

export interface AppOptions {
  socketPath: string
  projectId: string
}

/**
 * Application factory. Creates the TUI app without starting it.
 * Tests can call this to verify construction without spawning a process.
 */
export function createApp(options: AppOptions) {
  const { socketPath, projectId } = options

  return {
    socketPath,
    projectId,
    async start() {
      const { render } = await import('ink')
      const { App } = await import('./app.js')

      setupGracefulExit({
        cleanups: [
          () => {
            resetTerminalModes()
          },
        ],
        onError: (_scope, err) => {
          const message = err instanceof Error ? `${err.name}: ${err.message}` : String(err)
          process.stderr.write(`coordinator-tui: ${message}\n`)
        },
        onSignal: signal => {
          resetTerminalModes()
          process.stderr.write(`coordinator-tui: received ${signal}\n`)
        },
      })

      render(<App socketPath={socketPath} projectId={projectId} />, {
        exitOnCtrlC: false,
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
  process.on('exit', () => { resetTerminalModes() })
  process.stdout.write('\x1b[2J\x1b[H\x1b[3J')

  const app = createApp({ socketPath, projectId })
  void app.start()
}
