# Phase 3 Codex Final Audit, Round 5

Date: 2026-06-22
Branch: `external/coordinator-global-tui`
Audited repair: `d4faf9f`
Verdict: **REJECT - one signal-lifecycle P1 remains**

## Accepted Parts of the Repair

- Ctrl+C exits with code 0 while the PTY fd remains open.
- `/quit` exits with code 0 while the PTY fd remains open.
- Both paths restore `ICANON` and `ECHO` and leave the Supervisor alive.
- Immediate and delayed SIGKILL implementations are removed.
- Production test hooks remain absent and real composer keystroke tests pass.

## P1: SIGTERM Still Hangs While the PTY Is Open

The interactive paths use `performDetach`, but `lifecycle.ts` routes SIGTERM
through `cleanup(); process.exit(143)`. This retains the original blocking
process-exit behavior.

Both committed SIGTERM PTY tests hide the failure by closing the PTY fd before
waiting for process exit:

- `test_tui_sigterm_exits_cleanly`
- `test_terminal_cleanup_after_sigterm`

Codex ran an independent regression test that kept the fd open. The test and
Node TUI remained alive beyond the eight-second deadline and required manual
SIGKILL cleanup. This reproduces the same false-positive pattern previously
fixed for Ctrl+C.

## Required Repair

1. Route SIGTERM and SIGHUP through a clean signal-aware detach path that
   releases Ink/stdin/stdout without closing the PTY master.
2. Preserve conventional signal exit codes (`143` for SIGTERM and `129` for
   SIGHUP), rather than forcing interactive exit code 0.
3. Update both SIGTERM tests to keep the fd open until `_wait_for_exit`
   completes; close it only in `finally`.
4. Assert exit code 143, restored `ICANON/ECHO`, and Supervisor liveness.
5. Add equivalent fd-open coverage for SIGHUP or explicitly remove SIGHUP
   support from the lifecycle contract.
6. Do not use SIGKILL, delayed kill helpers, or PTY closure as completion aids.

## Independent Verification

The non-signal suites are green at the audited head:

```text
npm run typecheck --prefix ui-tui                         PASS
npm run lint --prefix ui-tui                              PASS
npm test --prefix ui-tui -- --run                         100/100 PASS
npm run build --prefix ui-tui                             PASS
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v  26/26 PASS
PYTHONWARNINGS=error::ResourceWarning \
  PYTHONPATH=src python3 -m unittest discover -s tests -q 669/669 PASS
git diff --check                                           PASS
```

The PTY suite remains green because its SIGTERM cases close the fd before the
assertion. Phase 3 cannot be accepted until the fd-open signal reproduction
passes.
