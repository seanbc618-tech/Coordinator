# Phase 3 Codex Acceptance Submission

Date: 2026-06-22
Branch: `external/coordinator-global-tui`
Commit: `ea90313`
Prior rejection: `bbdab7e` (Gate D/E incomplete)
Repair owner: Claude Code
Review owner: Grok (adversarial re-review complete)
Acceptance owner: Codex

## Verdict Requested

Accept Phase 3 Hermes TUI at `ea90313` and advance Wave 4.

## Summary

This commit completes the repair items from
`2026-06-22-phase3-final-acceptance-repair.md`. All required verification
commands pass. Gate D/E behavioral assertions are now covered by automated
tests mapped to each P1/P2 requirement below.

## Verification Evidence

| Command | Exit | Result |
|---------|------|--------|
| `npm run typecheck --prefix ui-tui` | 0 | pass |
| `npm run lint --prefix ui-tui` | 0 | pass (0 errors) |
| `npm test --prefix ui-tui -- --run` | 0 | **100 passed** (9 files) |
| `npm run build --prefix ui-tui` | 0 | bundle + sourcemap + manifest |
| `PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v` | 0 | **24 passed** |
| `PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q` | 0 | **667 passed** |
| `git diff --check` | 0 | clean |

Build manifest at acceptance head:

- `protocol_version`: 1
- `build_hash`: `2cb3336c480bbe46` (sha256 of `dist/entry.js`, first 16 hex)

## Repair Requirement Matrix

| ID | Requirement | Automated test(s) | Status |
|----|-------------|-------------------|--------|
| P1-1 | Consecutive destructive confirmation; any intervening input clears pending | `submitFlow.test.ts` (7), `composer.test.tsx` (destructive cases) | covered |
| P1-2 | Child exit status; 120/80/50 connected+activity content | `test_tui_sigterm_exits_cleanly` (exit 143), `test_tui_renders_in_pty_*` | covered |
| P1-3 | hello → chat.send + echo | `test_pty_types_hello_and_sends_chat` | covered |
| P1-3 | Ctrl+C detach + supervisor ping | `test_terminal_cleanup_after_ctrl_c`, `test_sigint_exits_and_supervisor_still_responds` | covered |
| P1-3 | /shutdown once → no RPC | `test_pty_shutdown_once_sends_no_rpc` | covered |
| P1-3 | /shutdown twice → one RPC | `test_pty_shutdown_twice_sends_one_rpc` | covered |
| P1-3 | /shutdown, /status, /shutdown → no shutdown | `test_pty_shutdown_status_shutdown_sends_no_rpc`, `submitFlow.test.ts` | covered |
| P1-4 | Resize 120→50 narrow render | `test_resize_120_to_50_renders_narrow` | covered |
| P1-4 | Reconnect replay dedup | `test_reconnect_replays_missed_events`, `reconnect.test.tsx` | covered |
| P1-4 | Work continues after TUI kill | `test_work_counter_advances_during_tui_termination` | covered |
| P1-4 | Terminal cleanup Ctrl+C / SIGTERM / forced error | `test_terminal_cleanup_after_ctrl_c`, `test_terminal_cleanup_after_sigterm`, `test_terminal_cleanup_after_forced_error`, `terminalLifecycle.test.ts` | covered |
| P2 | `reduceEvent(prev, event, projectId)` + foreign event rejection | `app.tsx`, `eventReducer.test.ts`, `test_foreign_event_does_not_enter_transcript` | covered |

## Production Changes

### Destructive confirmation (P1-1)

- New pure helper: `ui-tui/src/submitDecision.ts`
- `App.handleSubmit` calls `decideSubmit` — same code path as tests
- Non-destructive slash commands and plain messages clear pending confirmation

### Defense in depth (P2)

- Event handler: `reduceEvent(prev, event, projectId)`
- Client-side project filter retained in `SupervisorClient`

### PTY test hooks (env-gated, test-only)

Ink `useInput` does not reliably process individual keystrokes in a forked PTY on
macOS (confirmed during repair). PTY composer flows therefore use env-gated hooks
that call the **production** `handleSubmit` after `connected`:

- `COORDINATOR_TUI_TEST_SUBMIT` — pipe-separated submissions (e.g. `/shutdown|/status|/shutdown`)
- `COORDINATOR_TUI_TEST_UNCAUGHT=1` — `setImmediate` throw for forced-error cleanup

These hooks are inert unless the env vars are set. They exercise the real App
submission path inside a real pseudo-terminal process with a real Unix socket.

### Fake Supervisor enhancements

- Thread-safe request log with `wait_for_request_method`
- Cursor-stable event history and full-history replay on reconnect
- Independent work counter simulation

### Lifecycle fix

- `setupLifecycle` now uses a `wired` flag (idempotent registration)
- `terminalLifecycle.test.ts` covers SIGTERM (143) and uncaughtException (1)

## Gate A–E Status

| Gate | Status | Notes |
|------|--------|-------|
| A License/scope | pass | `THIRD_PARTY_NOTICES.md`; no Hermes runtime in bundle |
| B Protocol/state | pass | 11 supervisorClient tests + 22 reducer tests |
| C Layout | pass | 16 layout tests at 120/80/50 |
| D Interaction/lifecycle | pass | submitFlow + composer + lifecycle + PTY detach |
| E PTY/bundle | pass | 24 PTY tests; manifest hash matches bundle |

## Known Limitations (non-blocking)

- `os.fork()` DeprecationWarning in PTY parent (deferred to Phase 4 per repair handoff)
- PTY composer flows use env-gated `handleSubmit` rather than keystroke simulation
  because Ink input is unreliable in forked PTYs; production keystroke path remains
  `Composer.useInput` → `handleSubmit` (covered by `decideSubmit` + submitFlow)

## Phase 3 Task Commits on Integration Branch

1. `0c95587` feat: scaffold licensed Coordinator TUI
2. `247bfcc` feat: connect TUI to local Supervisor
3. `6d9c865` feat: model Coordinator TUI events
4. `3e882a8` feat: render chat with live activity blocks
5. `57301bd` feat: add Coordinator chat composer
6. `6345a14` feat: reconnect and restore TUI safely
7. `aadd5d7` feat: complete Coordinator TUI client
8. `bbdab7e` fix: address Grok adversarial review findings (partial)
9. `ea90313` fix: complete Phase 3 Gate D/E acceptance repair

## Codex Checklist

- [ ] Scope: no Hermes runtime/gateway/model/MCP imports in `ui-tui` or bundle
- [ ] Attribution: adapted files carry MIT notice
- [ ] Ctrl+C and `/quit` detach only; never stop project work
- [ ] `/stop` and `/shutdown` require consecutive confirmation
- [ ] Reconnect deduplicates by cursor
- [ ] Migrations 007–010 unaffected (no new migrations in Phase 3)
- [ ] Run verification commands above on `ea90313`
- [ ] Accept and update execution index checkpoint