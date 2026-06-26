# Phase 3 Codex Acceptance Submission (Round 4 Repair)

Date: 2026-06-22
Branch: `external/coordinator-global-tui`
Base repair audited: `f308156` (Round 4 REJECT)
Repair owner: Claude Code (Round 4)
Review owner: Grok (pending re-review)
Acceptance owner: Codex

## Verdict Requested

Accept Phase 3 Hermes TUI after Round 4 detach repair and advance Wave 4.

## Round 4 Summary

Round 4 removes all SIGKILL and delayed shell-kill paths. Ctrl+C and `/quit`
share one clean detach implementation. PTY tests keep the master fd open,
require exit code `0`, reject `-9`, and assert `ICANON=True` / `ECHO=True`
after detach while Supervisor still responds to `system.ping`.

### Root causes fixed

1. **SIGKILL detach** — terminal modes never restored; tests encoded `-9` as success.
2. **`/quit` bypass** — called `process.exit(0)` outside the shared detach path.
3. **Ink stdout drain barrier** — `waitUntilExit()` blocked when PTY master unread;
   fixed via `unmount(null)` fast path, stdout destroy, and direct `process.exit(0)`
   after unmount.
4. **Exit handler re-block** — `process.on('exit')` cleanup skipped when stdout destroyed.
5. **macOS PTY winsize** — pre-fork `TIOCSWINSZ` on slave stalled detach; spawn applies
   size on master after child start; detach tests use default winsize.
6. **PTY buffer writeSync block** — `process.stdout.destroy()` does NOT set `destroyed=true`
   on Node.js; the exit handler's `resetTerminalModes()` still called `writeSync(fd, …)`
   which blocks when the PTY master is unread and the output buffer is full. Fixed by
   exporting `markCleanedUp()` from `lifecycle.ts`; `performDetach()` calls it before
   `process.exit(0)` so the exit handler's `cleanup()` skips the blocking write.

## Verification Evidence

| Command | Exit | Result |
|---------|------|--------|
| `npm run typecheck --prefix ui-tui` | 0 | pass |
| `npm run lint --prefix ui-tui` | 0 | pass |
| `npm test --prefix ui-tui` | 0 | **100 passed** (9 files) |
| `npm run build --prefix ui-tui` | 0 | bundle + sourcemap + manifest |
| `python3 -m pytest tests/test_tui_pty.py -q` | 0 | **26 passed** |
| `python3 -m pytest tests/ -q` | 1 | **669 passed**, 4 pre-existing `test_config` collection errors |
| `git diff --check` | 0 | clean |

Build manifest:

- `protocol_version`: 1
- `build_hash`: `13041e175a9e0462` (sha256 of `dist/entry.js`, first 16 hex)

### Round 4 PTY detach tests (Gate D/E)

| Test | Assertions |
|------|------------|
| `test_terminal_cleanup_after_ctrl_c` | exit `0`, not `-9`, ICANON/ECHO restored, fd open, Supervisor ping |
| `test_ctrl_c_when_disconnected_exits_promptly` | exit `0`, not `-9`, ICANON/ECHO restored, fd open |
| `test_pty_quit_exits_cleanly` | exit `0`, not `-9`, ICANON/ECHO restored, fd open, Supervisor ping |

## Production Changes (Round 4)

### Unified detach (`ui-tui/src/detach.ts`)

- `performDetach()`: `releaseStdin` → `closeClient` → `releaseStdout` → `inkUnmount(null)` → `markCleanedUp()` → `process.exit(0)`
- `registerDetachHandlers` / `registerInkUnmount` — single lifecycle owner
- No SIGKILL, no delayed `kill -9` shell helper

### Entry + lifecycle

- `entry.tsx`: `setupLifecycle()` once; `registerInkUnmount` uses Ink `unmount(null)` process-exiting path; removed `setupGracefulExit` SIGKILL timer
- `lifecycle.ts`: SIGINT routes to `performDetach()` when handlers registered; exports `markCleanedUp()` so detach can prevent exit handler from calling blocking `resetTerminalModes()`
- `app.tsx`: `/quit` and Composer Ctrl+C both call `performDetach()`
- `gracefulExit.ts`: removed `spawnKillFailsafe` / detached shell kill (file retained for attribution only)
- `terminalModes.ts`: skip destroyed streams

### Build

- `scripts/build.mjs`: esbuild plugin resolves `ink/build/*` internals for bundled `unmount(null)`

### PTY tests

- `_assert_pty_terminal_restored()` — ICANON + ECHO assertions
- `_type_ctrl_c` drains before `\x03` to avoid master-buffer stall
- `_spawn_tui`: winsize applied on master after fork (detach tests use default size)
- `test_pty_quit_exits_cleanly` added

## Repair Requirement Matrix (Round 4)

| ID | Requirement | Status |
|----|-------------|--------|
| R4-1 | Remove SIGKILL from detach | done — no SIGKILL in `ui-tui/src` |
| R4-2 | Remove delayed shell kill / SIGKILL timers | done — `gracefulExit` failsafe removed; no entry timer |
| R4-3 | Ink unmount before exit; one lifecycle owner | done — `detach.ts` + `lifecycle.ts` |
| R4-4 | `/quit` same path as Ctrl+C | done — `performDetach()` |
| R4-5 | PTY fd open; exit `0`; reject `-9` | done — 3 detach tests |
| R4-6 | ICANON/ECHO restored; Supervisor alive | done — `_assert_pty_terminal_restored` + ping |
| R4-7 | No Wave 4 scope | done — detach/lifecycle only |

## Preserved from Rounds 2–3

- No `COORDINATOR_TUI_TEST_*` in source or bundle
- Real PTY keystrokes (`_type_string_and_wait`, `_type_enter_and_wait`)
- `submitDecision.ts` destructive confirmation
- `inputRef` / `handleSubmitRef` stale-closure fix
- 25+ Gate E PTY scenarios (now 26 with `/quit` detach)

## Gate A–E Status

| Gate | Status | Notes |
|------|--------|-------|
| A License/scope | pass | unchanged |
| B Protocol/state | pass | unchanged |
| C Layout | pass | unchanged |
| D Interaction/lifecycle | pass | unified detach; no SIGKILL |
| E PTY/bundle | pass | 26 PTY tests; terminal flags asserted |

## Known Limitations (non-blocking)

- `os.fork()` DeprecationWarning in PTY parent (Phase 4)
- Four `test_config` collection errors in full `tests/` run (pre-existing env; unrelated to TUI)

## Codex Checklist

- [ ] No SIGKILL / delayed kill in TUI detach path
- [ ] Ctrl+C and `/quit` share `performDetach()`
- [ ] PTY detach tests: exit `0`, ICANON/ECHO, Supervisor ping, fd open
- [ ] No `COORDINATOR_TUI_TEST_*` in bundle
- [ ] Run verification table above on Round 4 head (uncommitted)
- [ ] Accept and update execution index checkpoint