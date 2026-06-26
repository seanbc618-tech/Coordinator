# Grok Handoff: Phase 5.4 Main Implementation

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Baseline: `0f43ac6`
Design: `docs/superpowers/specs/2026-06-26-phase5-4-context-sessions-tools-design.md`
Plan: `docs/superpowers/plans/2026-06-26-phase5-4-context-sessions-tools.md`

You own production implementation. Execute the plan in strict wave order.

## Start Rule

Wait for Claude's Task 0 commit, then implement Tasks 1–3. Stop for Codex Gate A.
After Gate A, wait for Claude Task 4, implement Tasks 5–6, and stop for Gate B.
After Gate B, wait for Claude Task 7, implement Tasks 8–9, then integrate Claude
Task 10 and perform Task 11.

## Required Production Commits

1. `feat: parse bounded file references in CLI prompts`
2. `feat: validate project file context at CLI and Supervisor`
3. `feat: persist file manifests and redact Commander prompts`
4. `feat: add project goal resume and fork lineage`
5. `feat: expose project goal resume and fork in CLI`
6. `feat: persist restrictive execution policies on Commander tasks`
7. `feat: enforce task execution stages and add RPC output mode`
8. final gate record if needed

## Hard Requirements

- Supervisor revalidates every file, goal, and requested policy.
- Persist no referenced file body in SQLite or completed prompt artifacts.
- Resume/fork never cross project boundaries.
- Fork creates draft only and copies no tasks, attempts, runs, leases, or
  artifacts.
- Restrictions can only remove execution stages.
- `--no-tools` admits zero tasks.
- A no-edit task that changes files fails.
- A no-commit task preserves its worktree in `awaiting_human`.
- RPC mode emits exactly one protocol envelope and never opens TUI.
- Keep root/package migrations byte-identical.

## Do Not

- Do not use CLI-only validation as a security boundary.
- Do not describe tool restrictions as OS sandboxing.
- Do not combine all work into one commit.
- Do not weaken Claude tests when they expose a production bug.
- Do not continue to the next wave before Codex signs the current gate.

For every commit report focused red/green evidence, changed files, migration
impact, and known limitations.
