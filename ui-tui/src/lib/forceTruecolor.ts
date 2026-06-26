// Adapted from Hermes Agent (MIT) by Nous Research.
// See THIRD_PARTY_NOTICES.md for attribution.

const TRUE_RE = /^(?:1|true|yes|on)$/i
const FALSE_RE = /^(?:0|false|no|off)$/i

export function shouldForceTruecolor(env: NodeJS.ProcessEnv = process.env): boolean {
  const override = (env.COORDINATOR_TUI_TRUECOLOR ?? '').trim()

  if (FALSE_RE.test(override) || 'NO_COLOR' in env) {
    return false
  }

  return TRUE_RE.test(override)
}

if (shouldForceTruecolor()) {
  if (!process.env.COLORTERM) {
    process.env.COLORTERM = 'truecolor'
  }
  process.env.FORCE_COLOR = '3'
}
