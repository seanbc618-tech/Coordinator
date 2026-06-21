// Adapted from Hermes Agent (MIT) by Nous Research.
// See THIRD_PARTY_NOTICES.md for attribution.

export interface ThemeColors {
  primary: string
  accent: string
  border: string
  text: string
  muted: string
  completionBg: string
  completionCurrentBg: string

  label: string
  ok: string
  error: string
  warn: string

  prompt: string
  statusBg: string
  statusFg: string
  statusGood: string
  statusWarn: string
  statusBad: string
  statusCritical: string
  selectionBg: string

  diffAdded: string
  diffRemoved: string
  diffAddedWord: string
  diffRemovedWord: string
}

export interface ThemeBrand {
  name: string
  icon: string
  prompt: string
  welcome: string
  goodbye: string
  helpHeader: string
}

export interface Theme {
  color: ThemeColors
  brand: ThemeBrand
}

// ── Color math ───────────────────────────────────────────────────────

function parseHex(h: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(h)
  if (!m) return null
  const n = parseInt(m[1]!, 16)
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]
}

function mix(a: string, b: string, t: number) {
  const pa = parseHex(a)
  const pb = parseHex(b)
  if (!pa || !pb) return a
  const lerp = (i: 0 | 1 | 2) => Math.round(pa[i] + (pb[i] - pa[i]) * t)
  return '#' + ((1 << 24) | (lerp(0) << 16) | (lerp(1) << 8) | lerp(2)).toString(16).slice(1)
}

// ── Defaults ─────────────────────────────────────────────────────────

const BRAND: ThemeBrand = {
  name: 'Coordinator',
  icon: '◆',
  prompt: '❯',
  welcome: 'Type your message or /help for commands.',
  goodbye: 'Goodbye!',
  helpHeader: 'Commands',
}

export const DARK_THEME: Theme = {
  color: {
    primary: '#60A5FA',
    accent: '#3B82F6',
    border: '#475569',
    text: '#F1F5F9',
    muted: '#94A3B8',
    completionBg: '#1E293B',
    completionCurrentBg: '#334155',

    label: '#93C5FD',
    ok: '#4ADE80',
    error: '#F87171',
    warn: '#FBBF24',

    prompt: '#F1F5F9',
    statusBg: '#1E293B',
    statusFg: '#CBD5E1',
    statusGood: '#4ADE80',
    statusWarn: '#FBBF24',
    statusBad: '#F97316',
    statusCritical: '#EF4444',
    selectionBg: '#334155',

    diffAdded: 'rgb(134,239,172)',
    diffRemoved: 'rgb(252,165,165)',
    diffAddedWord: 'rgb(34,197,94)',
    diffRemovedWord: 'rgb(239,68,68)',
  },
  brand: BRAND,
}

export const LIGHT_THEME: Theme = {
  color: {
    primary: '#2563EB',
    accent: '#1D4ED8',
    border: '#CBD5E1',
    text: '#1E293B',
    muted: '#64748B',
    completionBg: '#F8FAFC',
    completionCurrentBg: mix('#F8FAFC', '#2563EB', 0.15),

    label: '#1E40AF',
    ok: '#16A34A',
    error: '#DC2626',
    warn: '#D97706',

    prompt: '#1E293B',
    statusBg: '#F1F5F9',
    statusFg: '#475569',
    statusGood: '#16A34A',
    statusWarn: '#D97706',
    statusBad: '#EA580C',
    statusCritical: '#B91C1C',
    selectionBg: '#DBEAFE',

    diffAdded: 'rgb(187,247,208)',
    diffRemoved: 'rgb(254,202,202)',
    diffAddedWord: 'rgb(22,163,74)',
    diffRemovedWord: 'rgb(220,38,38)',
  },
  brand: BRAND,
}

// ── Light/dark detection ─────────────────────────────────────────────

const TRUE_RE = /^(?:1|true|yes|on)$/
const FALSE_RE = /^(?:0|false|no|off)$/

export function detectLightMode(env: NodeJS.ProcessEnv = process.env): boolean {
  const lightFlag = (env.COORDINATOR_TUI_LIGHT ?? '').trim().toLowerCase()
  if (TRUE_RE.test(lightFlag)) return true
  if (FALSE_RE.test(lightFlag)) return false

  const colorfgbg = (env.COLORFGBG ?? '').trim()
  if (colorfgbg) {
    const lastField = colorfgbg.split(';').at(-1) ?? ''
    if (/^\d+$/.test(lastField)) {
      const bg = Number(lastField)
      if (bg === 7 || bg === 15) return true
      if (bg >= 0 && bg < 16) return false
    }
  }

  return false
}

export function getDefaultTheme(): Theme {
  return detectLightMode() ? LIGHT_THEME : DARK_THEME
}
