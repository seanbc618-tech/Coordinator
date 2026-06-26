# Wave 4 Grok Implementation Handoff

Date: 2026-06-22
Integration branch: `external/coordinator-global-tui`
Accepted baseline: `a08d304`
Role: primary implementer

## Objective

Execute all seven tasks in
`docs/superpowers/plans/2026-06-20-global-tui-installation.md` in order. Ship
packaged installation, onboarding, detached Supervisor startup, no-argument TUI
launch, first-run migration, three-project E2E/soak verification, and operator
documentation.

## Execution Rules

1. Start from exactly `a08d304`; do not reuse a branch based on an older head.
2. Use TDD and record the expected failing test before implementation.
3. Make one focused commit per numbered task using the plan's commit message.
4. Stay within each task's listed files unless a required dependency is
   documented before editing it.
5. Do not modify migrations 007 through 010 or weaken Phase 1-3 gates.
6. Do not commit `node_modules`, build caches, virtual environments, temporary
   wheels, runtime state, sockets, logs, or local project data.
7. After every task, run its focused commands plus `git diff --check` and send
   the commit hash and exact output summary to Claude Code for review.
8. Claude Code reviews only. Repair every accepted finding yourself and send a
   replacement commit or an explicitly scoped follow-up commit.
9. Stop for Codex integration gates after Tasks 3, 5, and 7.

## Waves

### Wave 4A: Tasks 1-3

- Package and locate the TUI bundle.
- Add guided project onboarding.
- Launch and detach the global Supervisor.

Gate evidence:

```bash
PYTHONPATH=src python3 -m unittest tests.test_tui_bundle -v
PYTHONPATH=src python3 -m unittest tests.test_project_onboarding_methods -v
npm test --prefix ui-tui -- projectOnboarding.test.tsx --run
PYTHONPATH=src python3 -m unittest tests.test_supervisor_process tests.test_supervisor_server -v
git diff --check
```

### Wave 4B: Tasks 4-5

- Make no-argument `coordinator` open the current Git project.
- Integrate first-run legacy migration.

Gate evidence:

```bash
PYTHONPATH=src python3 -m unittest tests.test_tui_launcher tests.test_cli -v
PYTHONPATH=src python3 -m unittest tests.test_first_run_migration tests.test_global_migration -v
git diff --check
```

### Wave 4C: Tasks 6-7

- Prove three-project operation and run the deterministic soak.
- Complete installation, migration, TUI, and troubleshooting documentation.

Gate evidence is the full Phase 4 command set from the canonical plan,
including a fresh-wheel install and no-argument real-PTY smoke test.

## Stop Conditions

Stop and report rather than improvising when:

- packaging requires including Hermes runtime or `node_modules`;
- migration would modify or delete the source root;
- a launcher path requires shell expansion or a network listener;
- a test can pass only by closing a PTY fd before lifecycle completion;
- project onboarding writes state before explicit confirmation;
- focused or inherited Phase 1-3 tests fail.

## Delivery Format

For each task provide: task number, commit hash, files changed, red test, green
test, `git diff --check`, known limitations, and Claude review disposition.
