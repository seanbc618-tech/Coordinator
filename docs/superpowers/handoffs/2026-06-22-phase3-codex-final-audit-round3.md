# Phase 3 Codex Final Audit, Round 3

Date: 2026-06-22
Branch: `external/coordinator-global-tui`
Audited repair: `3e8333e`
Verdict: **REJECT - one P1 blocker remains**

## Closed Round 2 Findings

- Production `COORDINATOR_TUI_TEST_*` hooks were removed from `app.tsx` and
  are absent from `dist/entry.js`.
- The four composer scenarios now use real, paced PTY keystrokes.
- TypeScript, build, PTY, and full Python suites pass.

## Remaining P1: Ctrl+C Tests Produce a False Positive

`Composer.tsx` now checks Ctrl+C before the `disabled` guard, but the real TUI
still does not exit when Ctrl+C is written to its PTY.

The new `test_ctrl_c_when_disconnected_exits_promptly` sends Ctrl+C, sleeps,
then closes the PTY master fd before calling `_wait_for_exit`. Closing the fd
can terminate/unblock the child independently, so the test does not prove that
Ctrl+C caused the exit.

Codex reproduced both states while deliberately keeping the PTY open:

```text
exit_code_without_closing_pty_after_2s= None
connected_exit_code_without_closing_pty= None
```

The first process used a nonexistent Supervisor socket. The second was fully
connected to the fake Supervisor. Both remained alive for five seconds after
Ctrl+C and required cleanup.

### Required Repair

1. Fix the real Ctrl+C path for both connected and disconnected states.
2. In Ctrl+C PTY tests, call `_wait_for_exit` while the PTY fd is still open.
3. Close the fd only in `finally`, after the exit assertion.
4. Assert the expected exit code and verify that the Supervisor remains alive.
5. Add a regression assertion that fails on `3e8333e` before applying the fix.

Do not start Wave 4 until this real-PTY reproduction exits promptly without fd
closure, EOF, SIGTERM, or SIGKILL assistance.

## Independent Verification at Current Head

```text
npm run typecheck --prefix ui-tui                         PASS
npm run lint --prefix ui-tui                              PASS
npm test --prefix ui-tui -- --run                         100/100 PASS
npm run build --prefix ui-tui                             PASS
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v  25/25 PASS
PYTHONWARNINGS=error::ResourceWarning \
  PYTHONPATH=src python3 -m unittest discover -s tests -q 668/668 PASS
git diff 3e8333e^ 3e8333e --check                         PASS
```

The submission document reports 667 full Python tests; the current independent
run contains 668. This is a documentation mismatch, not an additional blocker.

