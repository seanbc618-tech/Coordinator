# Phase 3 Codex Final Audit, Round 2

Date: 2026-06-22
Branch: `external/coordinator-global-tui`
Audited submission: `ea90313`
Submission document: `2026-06-22-phase3-codex-acceptance-submission.md`
Verdict: **REJECT - three acceptance blockers remain**

## Findings

### P1: Production bundle contains command-execution test hooks

`ui-tui/src/app.tsx` reads `COORDINATOR_TUI_TEST_SUBMIT` after connecting and
passes every pipe-separated entry directly to the production `handleSubmit`.
For example, setting:

```text
COORDINATOR_TUI_TEST_SUBMIT=/shutdown|/shutdown
```

causes the production executable to issue `system.shutdown` without interactive
input. `COORDINATOR_TUI_TEST_UNCAUGHT=1` also deliberately crashes the
production executable.

Both names and their behavior are present in `ui-tui/dist/entry.js`; these are
therefore not test-only hooks. This violates the Gate E requirement that the
production artifact exclude test behavior and creates an undocumented control
surface for a destructive command.

Required repair:

- Remove both hooks from `App` and from the production bundle.
- Put forced-error behavior in a test-only entry point or dependency-injected
  test harness that is not bundled into `dist/entry.js`.
- Add a bundle scan asserting that neither `COORDINATOR_TUI_TEST_` token is
  present.

### P1: Composer interaction requirements are still not automated

The following PTY tests do not type into the composer despite their Gate D/E
mapping:

- `test_pty_types_hello_and_sends_chat`
- `test_pty_shutdown_once_sends_no_rpc`
- `test_pty_shutdown_twice_sends_one_rpc`
- `test_pty_shutdown_status_shutdown_sends_no_rpc`

They set `COORDINATOR_TUI_TEST_SUBMIT`, which enters at `App.handleSubmit` and
bypasses `Composer.useInput`, input state, Enter handling, and the actual
`Composer -> App` callback boundary. The submission report acknowledges this
limitation but simultaneously marks the real composer acceptance requirement
as covered.

Required repair:

- Drive the existing PTY with `_type_string` and `_type_enter`, using paced
  input and readiness synchronization where needed.
- Assert the rendered composer/input result and the resulting RPC count.
- Keep the pure `decideSubmit` tests; they are useful but do not replace the
  end-to-end composer test.

### P1: Ctrl+C cannot detach while disconnected or reconnecting

`Composer.useInput` returns immediately when `disabled` before checking
Ctrl+C. Because `exitOnCtrlC` is false, the TUI can become impossible to detach
normally while the Supervisor is unavailable.

Independent real-PTY reproduction against a nonexistent socket:

```text
exit_code_after_offline_ctrl_c= None
```

The process was still alive three seconds after Ctrl+C and required cleanup.

Required repair:

- Handle Ctrl+C before the `disabled` guard.
- Add a PTY test that starts against an unavailable socket, sends Ctrl+C, and
  asserts prompt exit without killing the Supervisor or requiring SIGKILL.

## Independent Verification

The claimed suites otherwise reproduce successfully:

```text
npm run typecheck --prefix ui-tui                         PASS
npm run lint --prefix ui-tui                              PASS
npm test --prefix ui-tui -- --run                         100/100 PASS
npm run build --prefix ui-tui                             PASS
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v  24/24 PASS
PYTHONWARNINGS=error::ResourceWarning \
  PYTHONPATH=src python3 -m unittest discover -s tests -q 667/667 PASS
git diff --check                                           PASS
```

The green suites establish broad regression safety, but they do not waive the
three behavioral and artifact blockers above.

## Resubmission Gate

Do not add Phase 4 functionality in this repair. Resubmit only after:

1. production test hooks are absent from source execution paths and bundle;
2. all four composer scenarios use real PTY keystrokes;
3. offline Ctrl+C exits cleanly;
4. TypeScript, PTY, strict Python, build, bundle scan, and `git diff --check`
   all pass.

