# Grok Handoff: Phase 5.2 Main Implementation

You are the primary implementer.

Repository: `/Users/xiafan/Coordinator`
Branch: `external/coordinator-global-tui`
Baseline: `397ddbf`
Plan: `docs/superpowers/plans/2026-06-25-phase5-2-conversation-runtime.md`
Design: `docs/superpowers/specs/2026-06-25-phase5-2-conversation-runtime-design.md`

## Start Order

1. Start Task 1 immediately. Its Supervisor tests are owned by you.
2. Claude Code works in parallel only on chat/layout red tests.
3. Before Task 2, integrate Claude Code's Task 0 commit.
4. Implement Tasks 1–4 in order, one commit per task.
5. After each commit, stop and give Gemini the commit hash for adversarial
   review. Fix blockers before continuing.
6. After Claude's Task 5 test/docs commits, perform Task 6 integration.

## Non-Negotiable Requirements

- Do not use `pkill` or process-name matching in product code.
- Do not silently attach to an incompatible Supervisor.
- Do not expose `progress_summary`, duplicate-title internals, or raw admission
  diagnostics as the normal user reply.
- Ordinary conversation and status questions create zero tasks.
- Only explicit task requests may admit tasks.
- Unknown slash commands never reach Commander.
- Keep Hermes/Ink and existing Supervisor architecture.
- Preserve repo policy, budgets, worktrees, verification, review, and Git
  behavior.

## Required Commits

1. `feat: add Supervisor runtime identity and safe restart`
2. `feat: separate Commander user replies from orchestration state`
3. `feat: present Commander task outcomes in operator language`
4. `fix: stabilize TUI transcript layout and slash routing`
5. Integration/bundle commit only if needed.

## Evidence Required Per Commit

- focused failing test before implementation;
- focused passing test after implementation;
- `git diff --check`;
- changed-file list;
- known limitations;
- exact hash sent to Gemini.

Do not claim full completion until the isolated XDG full suite, PTY/E2E, wheel
test, and real polymarket smoke all pass.
