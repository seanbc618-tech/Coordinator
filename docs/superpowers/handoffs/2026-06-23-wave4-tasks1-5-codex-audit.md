# Wave 4 Tasks 1-5 Codex Acceptance Audit

Date: 2026-06-23
Branch: `external/coordinator-global-tui`
Audited head: `281a20d`
Scope: Tasks 1-5 only

## Verdict

Wave 4A/B is **rejected pending repair**.

- Task 1: accepted.
- Task 2: rejected.
- Task 3: conditionally accepted for behavior; resource cleanup repair required.
- Task 4: rejected because the real launcher-to-TUI onboarding path is broken.
- Task 5: implementation tests pass, but deterministic real-schema acceptance evidence is missing.

Do not treat uncommitted Task 6 work as a repair. Submit focused commits for the
items below, then have Claude Code perform read-only adversarial review before
Codex re-runs the gate.

## Blocking Findings

### P0: launcher canonical path is discarded by the packaged TUI entry

`tui_launcher.py` launches an unregistered repository with:

```text
node entry.js <socket> __onboarding__ <canonical-path>
```

But `ui-tui/src/entry.tsx` reads only `process.argv[2]` and
`process.argv[3]`, then calls `createApp` without `canonicalPath`. A real
no-argument launch therefore never enters the onboarding state. Existing tests
assert Python argv construction and render `App` directly, so neither test
catches the broken boundary.

Required repair:

1. Parse and validate the optional canonical path in `entry.tsx`.
2. Pass it to `createApp` and `App`.
3. Add a test that exercises the actual entry argument parser or packaged entry,
   rather than directly constructing `App`.

### P0: accepted onboarding keeps using the `__onboarding__` project ID

`App` constructs one `SupervisorClient` with the initial project ID. The client
stores that ID as `readonly` and uses it for every request, subscription, and
event filter. Accepting registration only updates `activeProjectId` used by the
display. Snapshot, chat, commands, subscriptions, and event filtering remain
scoped to `__onboarding__`.

Required repair:

1. Rebind or recreate the client after registration returns the real project ID.
2. Cleanly unsubscribe/close the onboarding connection before reconnecting.
3. Reset project-scoped cursor/state as appropriate.
4. Add a real-client integration test proving the first post-registration
   `project.snapshot`, `events.subscribe`, and `chat.send` envelopes carry the
   returned project ID, and foreign events are rejected.

### P1: onboarding confirmation displays hard-coded policy values

`SupervisorMethods._inspect_result` always returns fixed push, merge, review,
daily-budget, and runtime values. It does not resolve repository or global
configuration. It can therefore display a policy different from the policy that
will govern execution; verification commands also default to empty for newly
inspected repositories.

Required repair:

1. Resolve the effective repository and global policy through the existing
   configuration path.
2. Return those effective values from `project.inspect`.
3. Add non-default policy fixtures so tests fail if constants are substituted.
4. Define and test the safe behavior when no repository configuration exists.

### P1: detached Supervisor leaves `Popen` and pipe ResourceWarnings

The Task 3 behavior suite passes, but strict execution repeatedly emits
`ResourceWarning: subprocess ... is still running` and an unclosed stderr pipe.
The detached process is intentionally alive; its Python `Popen` wrapper and test
pipe still require an explicit lifecycle strategy.

Required repair:

1. Remove the warnings without terminating the successfully detached Supervisor.
2. Close foreground-test pipes during teardown.
3. Add a subprocess/resource audit that fails on these warnings instead of only
   printing them from `__del__`.

### P1: Task 5 real-schema test is non-deterministic and pollutes later tests

In a clean worktree, `RealSchemaVerificationTests` skips because
`coordinator.db` is untracked. During a full suite, another test can create that
file in the repository root; the test then runs against accidental state and may
fail before cleanup. Against the current live database, its first selected
artifact is already missing, so the existence assertion also fails for reasons
unrelated to migration.

Required repair:

1. Build a deterministic legacy fixture from a copied schema/database inside the
   test temp directory.
2. Insert a known goal, task, event, and artifact whose file exists.
3. Assert migration, remapping, source retention, and artifact existence against
   those known records.
4. Ensure no test creates `coordinator.db` in the checkout root and no setup
   failure leaks environment variables, DB handles, or temporary directories.

## Non-Blocking Notes

- Task 1 verifies only the first 16 hexadecimal SHA-256 characters. Prefer the
  full digest before release, but this does not block Tasks 1-5 behavior.
- Moved-project detection runs `git rev-list` for each stored project. Persisted
  repository identity can be addressed as a scalability follow-up.
- The canonical plan says the launcher passes an explicit protocol version. The
  current launcher does not; either implement it or update the contract with a
  test-backed rationale before final Wave 4 acceptance.

## Verification Evidence

From a detached clean audit worktree at `281a20d`:

```text
Task 1 resource/wheel tests: 8/8 pass (wheel build required network-enabled sandbox)
Task 2 Python tests: 8/8 pass
Task 2 frontend tests: 8/8 pass, repeated six times
Task 3 process/server tests: 19/19 pass, with ResourceWarnings
Task 4 launcher/CLI tests: 19/19 pass
Task 5 migration tests: 46 pass, 1 real-schema test skipped
TypeScript full suite: 108/108 pass
npm ci --prefix ui-tui --ignore-scripts: pass
git show --check for all five commits: pass
```

The strict Python full suite is not green:

```text
Ran 733 tests in 223.467s
FAILED (failures=3, errors=2)
```

Those full-suite failures expose the Task 5 fixture/isolation problem and then
cascade into launcher tests through leaked migration state. They are not evidence
that the focused migration algorithm tests failed, but they still block the
release gate.

## Resubmission Gate

Grok should submit focused repair commits and the following evidence:

```bash
npm ci --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest tests.test_supervisor_process tests.test_supervisor_server -v
PYTHONPATH=src python3 -m unittest \
  tests.test_project_onboarding_methods tests.test_tui_launcher \
  tests.test_first_run_migration tests.test_global_migration -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src \
  python3 -m unittest discover -s tests -q
git diff --check
```

Claude Code should specifically review the real launcher/entry/client boundary,
not only mocked `App` rendering.
