# Phase 3 Codex Final Audit, Round 4

Date: 2026-06-22
Branch: `external/coordinator-global-tui`
Audited repair: `f308156`
Verdict: **REJECT - SIGKILL is not an acceptable detach implementation**

## Findings

### P1: Ctrl+C leaves the user's terminal in raw mode

`App.handleDetach` immediately sends `SIGKILL` to the TUI process. SIGKILL
cannot run React unmount, process exit handlers, `resetTerminalModes`, or any
other cleanup.

Codex reproduced the real PTY state while keeping its fd open:

```text
exit_code= -9
before_icanon= False before_echo= False
after_icanon= False after_echo= False
```

The process disappears, but the terminal remains non-canonical with echo
disabled. This fails the Gate D/E terminal cleanup requirement and can leave the
user's shell visibly broken after Ctrl+C.

The tests currently accept `-9` as success and never assert terminal flags, so
they encode the regression rather than detect it.

### P1: `/quit` still hangs with the PTY open

`/quit` still calls `process.exit(0)` directly in `App.handleSubmit`. The same
real-PTY reproduction used for Ctrl+C remains blocked:

```text
quit_exit_code_with_fd_open= None
```

The process was still alive seven seconds after typing `/quit` and Enter. This
violates the requirement that Ctrl+C and `/quit` both detach without affecting
project work.

### P1: Detached kill helper can target a reused PID

`spawnKillFailsafe` starts an untracked, detached shell containing:

```text
sleep N && kill -9 <pid>
```

If the TUI exits before the sleep ends, the detached helper is not cancelled.
The operating system may reuse the PID before the helper wakes, allowing it to
kill an unrelated process. A normal shutdown path must not leave a delayed
unowned kill command behind.

## Required Repair

1. Remove immediate SIGKILL from `App.handleDetach`.
2. Remove the detached shell kill helper and the new SIGKILL timers.
3. Use Ink's `useApp().exit()` to unmount first; close the Supervisor client and
   restore terminal modes through one idempotent lifecycle owner.
4. Do not call `process.exit()` before Ink has unmounted. Route `/quit` through
   the same clean detach path as Ctrl+C.
5. Eliminate or consolidate the duplicate lifecycle systems in `entry.tsx` and
   `App` so signal ownership and cleanup order are deterministic.
6. PTY tests must keep the fd open and require a clean expected exit code; `-9`
   must fail the test.
7. After Ctrl+C and `/quit`, assert `ICANON` and `ECHO` are restored while the
   PTY fd remains open, and verify the Supervisor still responds.

Do not begin Wave 4 until both Ctrl+C and `/quit` satisfy those assertions
without fd closure, SIGKILL, SIGTERM, or test cleanup assistance.

## Preserved Work

The Round 2 fixes remain present: production test hooks are absent and composer
commands use real PTY keystrokes. This rejection is limited to detach and
terminal lifecycle behavior introduced or left unresolved by `f308156`.

