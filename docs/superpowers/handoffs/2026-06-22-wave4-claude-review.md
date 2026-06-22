# Wave 4 Claude Code Review Handoff

Date: 2026-06-22
Integration baseline: `a08d304`
Role: read-only adversarial reviewer

## Scope

Review each Grok Task 1-7 commit against the matching task in
`docs/superpowers/plans/2026-06-20-global-tui-installation.md`. Do not edit
production code, tests, plans, or documentation. Return actionable findings to
Grok; Grok owns every repair.

## Review Priorities

1. Behavioral correctness and regression risk before style.
2. Tests must prove the user path, not bypass it with environment hooks, PTY fd
   closure, broad exit-code acceptance, mocks of the subject under test, or
   source-checkout-only imports.
3. Wheel verification must run without `PYTHONPATH` and without repository
   files outside the installed environment.
4. Onboarding must inspect before writing, require explicit confirmation, use
   canonical paths, and detect moved projects.
5. Supervisor startup must be single-instance under races, bounded, local-only,
   argv-based, and leave no stale PID/lock/process on failure.
6. No-argument launch must preserve all administrative subcommands and forward
   terminal signals cleanly.
7. Migration must retain the source, validate copied real state, remain
   idempotent, and recover from interruption.
8. Three-project E2E must prove isolation, fairness, replay deduplication,
   continued work after detach, and zero final leases.
9. Packaging must include attribution and the verified bundle while excluding
   Hermes runtime, test hooks, `node_modules`, caches, and local paths.

## Required Output Per Task

Lead with findings ordered P0, P1, P2 and include exact file/line references and
a reproduction command. Then state one verdict:

- `PASS`
- `REJECT - repair required`

Do not implement fixes. Do not approve based only on reported test counts. For
Tasks 3, 5, and 7, produce a concise consolidated report for Codex's integration
gate.
