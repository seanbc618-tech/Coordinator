# Hermes-Based Coordinator TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt the MIT-licensed Hermes React Ink shell into Coordinator's hybrid chat and live activity TUI.

**Architecture:** Vendor only the terminal renderer and UI utilities required by Coordinator, replace Hermes gateway and domain state with the versioned Supervisor protocol, and test the TUI against a fake Unix-socket Supervisor before integration.

**Tech Stack:** TypeScript, React 19, Ink 6, Node.js, Vitest, esbuild, pseudo-terminal tests.

---

## Ownership and Order

- Claude Code: Tasks 1 through 7 implementation, one focused commit per task.
- Grok: adversarial review after each wave; no production rewrite unless Codex
  returns a defect to the responsible task branch.
- Codex: integration, license/scope review after Task 1, wave gates, and UI/PTY
  review after Task 7.
- Start after Phase 2 protocol and event contracts are frozen.

### Task 1: Create the Licensed TUI Package

**Files:**
- Create: `ui-tui/package.json`
- Create: `ui-tui/tsconfig.json`
- Create: `ui-tui/vitest.config.ts`
- Create: `ui-tui/eslint.config.mjs`
- Create: `ui-tui/scripts/build.mjs`
- Create: `ui-tui/src/entry.tsx`
- Create: `ui-tui/src/theme.ts`
- Create: `THIRD_PARTY_NOTICES.md`
- Modify: `.gitignore`

- [ ] **Step 1: Add a failing smoke test**

Create `ui-tui/src/__tests__/entry.test.ts` that imports the application factory
and asserts it can be constructed with a fake client without starting a process.

- [ ] **Step 2: Run the test and confirm missing package failure**

Run: `npm test --prefix ui-tui -- --run`
Expected: FAIL before package scaffolding is complete.

- [ ] **Step 3: Scaffold the package with pinned dependencies**

Use React, Ink, ink-text-input, nanostores, TypeScript, Vitest, ESLint, and
esbuild versions compatible with the locally installed Hermes TUI. Do not copy
Hermes node_modules or lockfiles containing unrelated workspace packages.

- [ ] **Step 4: Adapt terminal startup and theme**

Adapt the required portions of Hermes `entry.tsx` and `theme.ts`. Branding must
say Coordinator. Retain alternate-screen cleanup, TTY guard, signal cleanup,
resize handling, and truecolor fallback. Remove Hermes gateway spawning, model
configuration, telemetry, heap dumps, desktop sidecars, and Hermes-specific
environment variables.

- [ ] **Step 5: Add attribution**

THIRD_PARTY_NOTICES.md must include the Nous Research copyright, full MIT license,
the upstream source path/repository identity, and a list of adapted component
families. Add generated TUI bundles and local brainstorm files to .gitignore.

- [ ] **Step 6: Run checks**

~~~bash
npm install --prefix ui-tui
npm run typecheck --prefix ui-tui
npm test --prefix ui-tui -- --run
~~~

Expected: PASS.

- [ ] **Step 7: Commit**

~~~bash
git add ui-tui THIRD_PARTY_NOTICES.md .gitignore
git commit -m "feat: scaffold licensed Coordinator TUI"
~~~

### Task 2: Implement the Unix-Socket Client

**Files:**
- Create: `ui-tui/src/protocol.ts`
- Create: `ui-tui/src/supervisorClient.ts`
- Create: `ui-tui/src/__tests__/supervisorClient.test.ts`

- [ ] **Step 1: Write failing protocol tests**

Cover request correlation, one-line framing, protocol mismatch, timeout, socket
close, reconnect backoff, project ID propagation, cursor replay, event ordering,
and rejection of messages larger than 1 MiB.

- [ ] **Step 2: Run tests and confirm missing client**

Run: `npm test --prefix ui-tui -- supervisorClient.test.ts --run`
Expected: FAIL.

- [ ] **Step 3: Implement typed envelopes**

~~~typescript
export const PROTOCOL_VERSION = 1

export interface RequestEnvelope {
  protocol_version: 1
  type: 'request'
  request_id: string
  project_id: string | null
  method: string
  params: Record<string, unknown>
}

export interface EventEnvelope {
  protocol_version: 1
  type: 'event'
  project_id: string
  cursor: number
  event_type: string
  payload: Record<string, unknown>
}
~~~

`SupervisorClient` accepts socket path and project ID, exposes `request`,
`subscribe`, `connect`, and `close`, and emits connection-state changes.
Reconnect sends the last committed cursor and deduplicates replayed events.

- [ ] **Step 4: Run tests**

Run: `npm test --prefix ui-tui -- supervisorClient.test.ts --run`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add ui-tui/src/protocol.ts ui-tui/src/supervisorClient.ts ui-tui/src/__tests__/supervisorClient.test.ts
git commit -m "feat: connect TUI to local Supervisor"
~~~

### Task 3: Model Coordinator Transcript and Activity State

**Files:**
- Create: `ui-tui/src/domain.ts`
- Create: `ui-tui/src/store.ts`
- Create: `ui-tui/src/eventReducer.ts`
- Create: `ui-tui/src/__tests__/eventReducer.test.ts`

- [ ] **Step 1: Write failing reducer tests**

Cover user messages, Commander streaming text, task creation, stage transitions,
latest command/output, verification, review, Git operations, fallback agent,
completion, out-of-order duplicate cursor, and project mismatch.

- [ ] **Step 2: Run tests and confirm missing reducer**

Run: `npm test --prefix ui-tui -- eventReducer.test.ts --run`
Expected: FAIL.

- [ ] **Step 3: Define minimal domain types**

~~~typescript
export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline'

export interface Activity {
  taskId: string
  title: string
  agent: string | null
  stage: string
  startedAt: number | null
  fallback: { from: string; to: string; used: number; limit: number } | null
  latestCommand: string | null
  output: string[]
  expanded: boolean
}

export type TranscriptItem =
  | { id: string; kind: 'message'; role: 'user' | 'coordinator' | 'system'; text: string }
  | { id: string; kind: 'activity'; activity: Activity }
~~~

The reducer is pure and returns unchanged state for duplicate or foreign events.
Keep only a bounded live output tail in memory; full logs remain server-side.

- [ ] **Step 4: Run tests**

Run: `npm test --prefix ui-tui -- eventReducer.test.ts --run`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add ui-tui/src/domain.ts ui-tui/src/store.ts ui-tui/src/eventReducer.ts ui-tui/src/__tests__/eventReducer.test.ts
git commit -m "feat: model Coordinator TUI events"
~~~

### Task 4: Build Hybrid Chat and Activity Rendering

**Files:**
- Create: `ui-tui/src/components/AppLayout.tsx`
- Create: `ui-tui/src/components/Header.tsx`
- Create: `ui-tui/src/components/Transcript.tsx`
- Create: `ui-tui/src/components/Message.tsx`
- Create: `ui-tui/src/components/ActivityBlock.tsx`
- Create: `ui-tui/src/components/Footer.tsx`
- Create: `ui-tui/src/__tests__/layout.test.tsx`

- [ ] **Step 1: Write failing render tests**

Use Ink's test renderer. Assert header priority at 120, 80, and 50 columns;
compact/expanded activity forms; long unbroken commands wrapping; no overlapping
footer; fallback visibility; and deterministic elapsed-time injection.

- [ ] **Step 2: Run tests and confirm missing components**

Run: `npm test --prefix ui-tui -- layout.test.tsx --run`
Expected: FAIL.

- [ ] **Step 3: Adapt only required Hermes rendering utilities**

Adapt Markdown, streaming Markdown, text width, viewport, and terminal-safe link
helpers as focused files under `ui-tui/src/lib/`. Keep upstream attribution
comments. Do not import Hermes message, model, usage, subagent, todo, plugin,
skill, voice, image, or approval types.

- [ ] **Step 4: Implement the chosen layout**

Header keeps project and connection state at narrow widths, then adds goal,
counts, and budget as room permits. Transcript is the primary full-width region.
Activity blocks have stable borders and dimensions; Tab toggles only the active
block. Footer reserves fixed rows for hints and connection status.

- [ ] **Step 5: Run tests**

Run: `npm test --prefix ui-tui -- layout.test.tsx --run`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add ui-tui/src/components ui-tui/src/lib ui-tui/src/__tests__/layout.test.tsx
git commit -m "feat: render chat with live activity blocks"
~~~

### Task 5: Add Composer, History, and Slash Commands

**Files:**
- Create: `ui-tui/src/components/Composer.tsx`
- Create: `ui-tui/src/inputHistory.ts`
- Create: `ui-tui/src/slash.ts`
- Create: `ui-tui/src/__tests__/composer.test.tsx`
- Create: `ui-tui/src/__tests__/slash.test.ts`

- [ ] **Step 1: Write failing interaction tests**

Cover multiline editing, submit, history up/down, command completion, empty
submission, busy-state submission, Tab activity expansion versus completion,
Ctrl+C detach, and every command in the approved catalog.

- [ ] **Step 2: Run tests and confirm missing behavior**

Run: `npm test --prefix ui-tui -- composer.test.tsx slash.test.ts --run`
Expected: FAIL.

- [ ] **Step 3: Adapt Hermes input primitives**

Adapt only text input, editor, prompt, history, fuzzy completion, clipboard, and
bracketed paste helpers. Remove image attachment, voice, model picker, external
editor, prompt queue steering, and Hermes session commands unless required by an
approved Coordinator behavior.

- [ ] **Step 4: Map messages and commands to Supervisor methods**

Plain text calls `chat.send`. Commands map explicitly to project/status/task/log/
agent/pause/resume/stop/shutdown/new/goal/project/help/quit methods. Destructive
shutdown requires a second in-TUI confirmation.

- [ ] **Step 5: Run tests**

Run: `npm test --prefix ui-tui -- composer.test.tsx slash.test.ts --run`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add ui-tui/src/components/Composer.tsx ui-tui/src/inputHistory.ts ui-tui/src/slash.ts ui-tui/src/__tests__/composer.test.tsx ui-tui/src/__tests__/slash.test.ts
git commit -m "feat: add Coordinator chat composer"
~~~

### Task 6: Handle Reconnect and Terminal Lifecycle

**Files:**
- Create: `ui-tui/src/app.tsx`
- Create: `ui-tui/src/lifecycle.ts`
- Create: `ui-tui/src/__tests__/reconnect.test.tsx`
- Create: `ui-tui/src/__tests__/terminalLifecycle.test.ts`

- [ ] **Step 1: Write failing lifecycle tests**

Verify initial snapshot then subscription, offline banner, reconnect backoff,
cursor replay, duplicate suppression, Ctrl+C detach, slash quit, forced exception,
SIGTERM, and terminal mode reset.

- [ ] **Step 2: Run tests and confirm failure**

Run: `npm test --prefix ui-tui -- reconnect.test.tsx terminalLifecycle.test.ts --run`
Expected: FAIL.

- [ ] **Step 3: Compose client, store, and layout**

App receives socket path and project ID from command-line arguments. It loads
`project.snapshot`, subscribes from the returned cursor, reduces events, and
renders connection transitions without unmounting transcript state.

- [ ] **Step 4: Adapt graceful cleanup**

Adapt Hermes terminal reset and graceful exit helpers. Cleanup must be idempotent
and run on normal detach, signal, uncaught exception, and process exit. It closes
only the client connection and never sends project stop.

- [ ] **Step 5: Run tests**

Run: `npm test --prefix ui-tui -- reconnect.test.tsx terminalLifecycle.test.ts --run`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add ui-tui/src/app.tsx ui-tui/src/lifecycle.ts ui-tui/src/__tests__/reconnect.test.tsx ui-tui/src/__tests__/terminalLifecycle.test.ts
git commit -m "feat: reconnect and restore TUI safely"
~~~

### Task 7: Verify the TUI Against a Fake Supervisor

**Files:**
- Create: `tests/fixtures/fake_supervisor.py`
- Create: `tests/test_tui_pty.py`
- Modify: `ui-tui/scripts/build.mjs`
- Modify: `README.md`

- [ ] **Step 1: Write failing PTY tests**

Spawn the built TUI in a real pseudo-terminal. Verify initial render, chat send,
streaming response, task activity, command output, fallback, Tab expansion,
resize to 50 columns, reconnect replay, Ctrl+C detach, and clean terminal output.

- [ ] **Step 2: Run the PTY test and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v`
Expected: FAIL until the production bundle and fake server interoperate.

- [ ] **Step 3: Produce a deterministic production bundle**

Build one ESM entry artifact plus source map and a manifest containing protocol
version and build hash. Exclude test code and all Hermes gateway/runtime modules.

- [ ] **Step 4: Document development commands**

Document install, typecheck, test, build, fake-server launch, and PTY test. State
that this phase still uses an explicit socket/project invocation and Phase 4 adds
the global no-argument launcher.

- [ ] **Step 5: Run phase verification**

~~~bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v
git diff --check
~~~

Expected: all checks PASS.

- [ ] **Step 6: Commit**

~~~bash
git add ui-tui tests/fixtures/fake_supervisor.py tests/test_tui_pty.py README.md
git commit -m "feat: complete Coordinator TUI client"
~~~

## Phase 3 Acceptance

- Compare vendored files against Hermes and verify every copied substantial
  portion is attributed.
- Search ui-tui for Hermes agent, model, MCP, memory, skill, voice, and provider
  imports; none may remain.
- Run all TypeScript, build, and PTY checks.
- Inspect 120-, 80-, and 50-column snapshots.
- Kill the TUI during an active fake task and confirm the fake task continues.
- Codex verifies no runtime dependency on /Users/xiafan/.hermes.
