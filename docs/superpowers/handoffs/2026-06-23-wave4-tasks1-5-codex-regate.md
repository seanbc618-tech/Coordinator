# Wave 4 Tasks 1-5 Codex Re-Gate

Date: 2026-06-23
Branch: `external/coordinator-global-tui`
Audited head: `44350e2`
Repair commits: `2ae9170`, `a27009f`, `44350e2`

## Verdict

**Rejected pending three focused repairs.**

The original canonical-path, project-ID, policy-resolution, registration, and
deterministic-migration defects are repaired. The real three-project E2E also
passes. Fresh installation, rebind lifecycle, and detached-process cleanup still
have release-blocking defects.

## Blocking Findings

### P1: clean `npm ci` fails because package-lock is stale

From a clean detached worktree at `44350e2`:

```text
npm ci --prefix ui-tui
npm ERR! Missing: tsx@4.22.4 from lock file
npm ERR! Missing: esbuild@0.28.1 from lock file
... platform esbuild packages omitted ...
exit 1
```

`ui-tui/package.json` declares `tsx`, but the committed lockfile does not contain
the matching root dependency and transitive esbuild tree. Existing machines with
an already-populated `node_modules` can still run tests, which hides the fresh
checkout failure.

Required repair:

1. Regenerate and commit `ui-tui/package-lock.json` from the current package file.
2. Prove `npm ci --prefix ui-tui` succeeds after deleting `node_modules`.
3. Re-run the TypeScript suite from that clean install.

### P1: `SupervisorClient.rebind()` creates a duplicate connection/subscription

`rebind()` destroys the old socket and immediately creates a new one. The old
socket's asynchronous `close` handler still calls `scheduleReconnect()`. Its
timer then opens a third socket even though the replacement socket is already
connected.

Independent minimal reproduction with a 50 ms reconnect delay:

```json
{
  "connections": 3,
  "requests": [
    ["events.subscribe", "__onboarding__"],
    ["events.subscribe", "proj-new"],
    ["events.subscribe", "proj-new"]
  ]
}
```

Required repair:

1. Make intentional disconnect/rebind incapable of scheduling reconnect for the
   retired socket. A socket generation/token or removal of old listeners is
   preferable to timing assumptions.
2. Ensure successful connection cancels any stale reconnect timer.
3. Extend the real-client onboarding test to wait beyond the reconnect delay and
   assert exactly two total connections and exactly one `proj-new` subscription.
4. Keep the existing assertions for snapshot project ID and foreign-event
   filtering.

### P1: detached `Popen` ResourceWarnings remain at interpreter shutdown

The focused supervisor suite is clean while the module remains loaded, but the
strict full suite finishes with five warnings:

```text
Ran 737 tests in 262.148s
OK
ResourceWarning: subprocess ... is still running
... repeated five times ...
```

`_release_popen_wrapper()` appends each live wrapper to the module-level
`_DETACHED_CHILD_WRAPPERS` list. At interpreter shutdown the list is destroyed,
so `Popen.__del__` emits the warning that the list was intended to prevent. The
list also grows for every detached start in a long-lived caller.

Required repair:

1. Replace the global-retention workaround with an explicit detached-child
   lifecycle that produces no `Popen.__del__` warning while leaving the
   Supervisor alive after the launcher exits.
2. Add a subprocess-level test: start/attach, let the launching Python process
   exit under `-W error::ResourceWarning`, assert empty stderr and a still-live
   Supervisor, then stop it administratively.
3. Re-run the entire strict Python suite and inspect output after the `OK` line.

## Accepted Repairs

- `entry.tsx` now parses and forwards `canonicalPath`.
- Onboarding rebinds requests to the returned real project ID.
- `project.inspect` resolves repo/global policy and register validates the same
  effective verification commands.
- The migration real-schema test now uses deterministic temporary state.
- Three-project no-argument TUI E2E passes in 25.583 seconds.

## Independent Evidence

```text
TypeScript tests after npm install: 113/113 PASS
Supervisor process/server focused: 19/19 PASS
Onboarding + launcher + migration: 70/70 PASS
Three-project E2E: 1/1 PASS (25.583s)
Strict Python full suite: 737/737 OK, but emits five shutdown ResourceWarnings
Committed diff check 281a20d..44350e2: clean
Clean npm ci: FAIL
Rebind race reproduction: 3 connections, duplicate proj-new subscription
```

## Resubmission Gate

Grok should submit one focused repair commit per blocker. Claude Code should
adversarially review the exact-count rebind regression and subprocess-exit
warning test, not only the existing happy-path assertions.

```bash
rm -rf ui-tui/node_modules
npm ci --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest tests.test_supervisor_process tests.test_supervisor_server -v
PYTHONPATH=src python3 -m unittest \
  tests.test_project_onboarding_methods tests.test_tui_launcher \
  tests.test_first_run_migration tests.test_global_migration \
  tests.test_global_tui_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
git diff --check
```
