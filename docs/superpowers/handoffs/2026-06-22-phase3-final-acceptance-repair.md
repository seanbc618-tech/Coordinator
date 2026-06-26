# Phase 3 Final Acceptance Repair

## Verdict

Codex rejects Phase 3 at commit `bbdab7e`. TypeScript, build, PTY, and Python
commands are green, but Gate D/E behavior is not sufficiently asserted and one
destructive-confirmation state remains unsafe.

Claude Code owns the repair. Grok performs adversarial re-review only. Keep the
repair on `external/coordinator-global-tui` as one focused commit.

## P1-1: Make Destructive Confirmation Consecutive

Current behavior in `ui-tui/src/app.tsx` clears pending confirmation for a plain
message, reconnect, or another destructive command, but not for a
non-destructive slash command. `/shutdown`, `/status`, `/shutdown` can therefore
execute a stale shutdown confirmation.

Required behavior:

- Only two consecutive submissions of the same destructive command confirm it.
- Any intervening input clears the previous pending confirmation.
- A different destructive command starts a new confirmation and does not execute.
- Reconnect and offline transitions clear pending confirmation.
- Exactly one RPC is sent after valid confirmation; the first entry sends none.

Do not test a copied `simulateSubmit()` algorithm. Extract the production
submission decision into a pure helper or drive the real `App` with an injected
client. Tests must invoke the same code used by production.

Required cases:

```text
/stop, /stop                 -> one project.stop RPC
/stop, /status, /stop        -> zero project.stop RPCs
/shutdown, hello, /shutdown  -> zero system.shutdown RPCs
/stop, /shutdown             -> zero destructive RPCs
/shutdown, reconnect, /shutdown -> zero system.shutdown RPCs
```

## P1-2: Replace Vacuous PTY Assertions

`tests/test_tui_pty.py::test_tui_sigterm_exits_cleanly` ends with
`assertTrue(True)` and does not prove the child exited. The 120/80/50 tests only
assert non-empty bytes, which can pass on an error message.

Required changes:

- Add a condition-based child wait helper that returns the real exit status.
- Assert SIGTERM exits within the deadline with the documented exit code.
- Fail and force-kill during cleanup if the child remains alive.
- Assert rendered output contains `Coordinator`, the project ID, and the expected
  connected/activity content at 120, 80, and 50 columns.
- Keep output normalization local to tests; do not weaken production rendering.

## P1-3: Exercise Real Composer and Detach Behavior

Use the real PTY and type keys individually so Ink does not treat the command as
a single paste chunk. Extend `FakeSupervisor` with a thread-safe method log.

Automate these flows:

1. Type `hello` and Enter; assert one `chat.send` request and visible echo.
2. Send Ctrl+C; assert TUI exits and a subsequent `system.ping` to the fake
   Supervisor succeeds.
3. Type `/shutdown` once; assert no `system.shutdown` request.
4. Type `/shutdown` twice consecutively; assert exactly one request.
5. Type `/shutdown`, `/status`, `/shutdown`; assert no shutdown request.

## P1-4: Complete Explicit Gate E Scenarios

Automate the acceptance scenarios already required by the Phase 3 plan:

- Resize a running PTY from 120 to 50 columns with `TIOCSWINSZ` and `SIGWINCH`;
  assert a valid narrow render after resize.
- Drop the fake Supervisor connection, allow reconnect, replay missed cursors,
  and assert each event appears once.
- Model fake work independently of the client connection. Terminate the TUI
  during active work and assert the fake work counter/state continues advancing.
- Assert terminal cleanup after Ctrl+C, SIGTERM, and forced application error.

The fake server must expose synchronized observations rather than relying on
arbitrary sleeps or private counters read during mutation.

## P2: Wire Project Defense in Depth

Change the production event path to call:

```typescript
reduceEvent(prev, event, projectId)
```

Keep the existing client-side project filter. Add a production-path test proving
a foreign-project event cannot enter transcript or activity state.

## Deferred P2

The `os.fork()` deprecation warning may be replaced with `pty.spawn` or a
subprocess-based PTY helper in Phase 4. It does not block this repair provided
the new PTY tests are deterministic and always reap child processes.

## Required Verification

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
git diff --check
```

The repair report must include test counts, exact commands, process exit codes,
and a matrix mapping every requirement above to an automated test. Do not
declare Phase 3 complete; only Codex may accept it.
