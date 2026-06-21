# Phase 3 Claude Code Implementation Handoff

## Objective

Implement Phase 3, Hermes TUI Adaptation, from the accepted Phase 2 baseline
`f67c449`. The integration target is `external/coordinator-global-tui`.

Read these documents before changing code:

- `docs/superpowers/specs/2026-06-20-global-multi-project-tui-design.md`
- `docs/superpowers/plans/2026-06-20-hermes-coordinator-tui.md`
- `docs/superpowers/plans/2026-06-20-coordinator-tui-execution-index.md`

Use `/Users/xiafan/.hermes` only as an upstream reference. The delivered product
must not import it or depend on it at runtime.

## Ownership

Claude Code implements Tasks 1 through 7. Grok performs adversarial review and
must not rewrite accepted production code unless Codex explicitly returns a
defect for repair. Codex owns integration and final acceptance.

## Execution Rules

1. Start every task from the latest accepted
   `external/coordinator-global-tui` integration head.
2. Use one branch and worktree per task: `agent/claude/tui-0N-<slug>`.
3. Read only the assigned task and referenced design sections before starting.
4. Write the failing test first and record the failing command.
5. Stay within the files listed by the plan task. Report required scope changes
   before making them.
6. Produce exactly one focused commit per task.
7. Do not merge, cherry-pick, push, or start the next dependent task.
8. End every task with commit, changed files, focused tests, full relevant tests,
   `git diff --check`, known limitations, and Hermes-derived files/attribution.

## Wave Order

### Wave 3A: Licensed Package Boundary

- Task 1: Create the licensed TUI package.
- Mandatory gate: package tests, typecheck, attribution audit, dependency audit,
  and proof that no Hermes runtime module or local absolute path is imported.
- Stop for Codex and Grok review.

### Wave 3B: Transport and State

- Task 2: Implement the Unix-socket client.
- Task 3: Model transcript and activity state.
- These may use separate worktrees from the accepted Task 1 head.
- Integrate Task 2 before Task 3.
- Stop for protocol/cursor/reducer review.

### Wave 3C: Rendering

- Task 4: Build hybrid chat and activity rendering.
- Verify 120-, 80-, and 50-column behavior with deterministic snapshots.
- Stop for layout and Hermes-scope review.

### Wave 3D: Interaction and Lifecycle

- Task 5: Add composer, history, and slash commands.
- Task 6: Handle reconnect and terminal lifecycle.
- Task 6 starts only after Task 5 is accepted.
- Stop for destructive-command, reconnect, detach, and terminal-reset review.

### Wave 3E: PTY Integration

- Task 7: Verify against a fake Supervisor and create the deterministic bundle.
- Do not add the global no-argument launcher; that belongs to Phase 4.
- Stop for final Phase 3 acceptance.

## Non-Negotiable Boundaries

- Preserve Supervisor protocol version 1 unless Codex approves a protocol change.
- The TUI is a client. It cannot gain worker, reviewer, commit, merge, or push
  authority.
- Ctrl+C and `/quit` detach only; they never stop project work.
- `/stop` and `/shutdown` remain distinct and destructive actions require
  confirmation.
- Reconnect replays from the last committed cursor and deduplicates events.
- Keep live output bounded in memory; full logs remain server-side.
- Do not copy Hermes model, provider, gateway, MCP, memory, skill, voice, image,
  telemetry, desktop sidecar, or agent-runtime code.
- Preserve upstream copyright and attribution comments for adapted code.

## Phase 3 Verification

Run after Task 7:

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
PYTHONPATH=src python3 -m unittest tests.test_tui_pty -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
git diff --check
```

Do not declare Phase 3 complete. Submit the seven commits and verification report
to Grok and Codex for acceptance.
