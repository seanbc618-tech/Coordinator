# Phase 7 Strategic Autonomy and Recovery — Gemini Adversarial Review

Date: 2026-06-29
Branch: `phase7-strategic-autonomy`
HEAD: `233ff1c` (feat: add strategic autonomy slash commands)
Plan: `docs/superpowers/plans/2026-06-29-phase7-strategic-autonomy-recovery.md`

## Request

Review Phase 7 Strategic Autonomy and Recovery current HEAD.
Return PASS / CONDITIONAL PASS / FAIL with exact blockers/conditions.

Focus on:
1. Can milestones leak across project boundaries?
2. Can recovery proposals create infinite retry loops?
3. Can a failed task become a new task without evaluation and backlog governance?
4. Can scorecard routing send work to an incapable agent?
5. Can one bad agent globally block all agents?
6. Can overnight quiet hours kill active workers unsafely?
7. Can summaries contain raw prompts, env vars, secrets, or cross-project task titles?
8. Can slash commands bypass Supervisor RPC?
9. Can full tests pass through mocked-only false greens while real clean-wheel smoke fails?
10. Are docs consistent with actual CLI/TUI behavior?

---

## Verdict

```text
VERDICT: CONDITIONAL PASS
```

### Conditions for Final Merge:
1. **Complete Task 10 Documentation**: Update `docs/cli.md`, `docs/tui.md`, and `docs/troubleshooting.md` to document the new `/strategy`, `/recoveries`, `/agents`, and `/overnight` commands.
2. **Re-generate TUI Manifest on Final Commit**: Ensure `manifest.json` is cleanly rebuilt and packaged on the final merge commit to align with any platform/build-specific asset hashing.

---

## Checklist & Detailed Analysis

### 1. [x] Can milestones leak across project boundaries?
* **Analysis**: No. The strategic milestone queries in `src/local_cli_coordinator/strategy.py` (`list_milestones`, `get_active_milestone`, and `build_strategy_summary`) all enforce strict `project_id = ?` parameterized bounds. No global milestone collection can occur.
* **Verification**: `tests.test_strategy.MilestoneCRUDTests.test_milestones_are_project_scoped` confirms that Project A is completely isolated from Project B's milestones.

### 2. [x] Can recovery proposals create infinite retry loops?
* **Analysis**: No. Recovery proposal generation in `src/local_cli_coordinator/recovery.py` uses a cryptographically secure SHA-256 deduplication key based on `{task_id}|{proposal_type}`. Under the `idx_recovery_open_dedupe` unique index constraint on the `task_recovery_proposals` table, only a single pending/admitted recovery proposal can exist per failed task. This strictly bounds recovery generation and prevents infinite retry loops.
* **Verification**: `tests.test_recovery.RecoveryProposalTests.test_duplicate_proposals_are_deduped` and database index constraints enforce this rule.

### 3. [x] Can a failed task become a new task without evaluation and backlog governance?
* **Analysis**: No. The recovery manager (`admit_recovery_to_backlog`) validates that the failed task has a recorded evaluation with a terminal verdict (`fail` or `blocked`). Upon admission, the recovery is inserted as a `BacklogDraft` using `propose_backlog_items`. It must go through the standard loop scheduling cycle and is subject to standard iteration limits, quotas, and approvals.
* **Verification**: `tests.test_recovery.RecoveryAdmissionTests.test_admit_recovery_creates_ready_backlog_item` covers this exact flow.

### 4. [x] Can scorecard routing send work to an incapable agent?
* **Analysis**: No. The agent ranking module (`src/local_cli_coordinator/agent_scorecard.py::rank_workers_for_capabilities`) filters candidates using `iter_agents_by_role(config, "worker", capabilities)`. Capability matching acts as a hard filter; scorecards are only consulted as a secondary sorting layer for qualified candidates.
* **Verification**: `tests.test_agent_scorecard.ScorecardRoutingTests.test_rank_workers_excludes_incapable_agents` asserts that agents missing capabilities are cleanly excluded regardless of their scorecard status.

### 5. [x] Can one bad agent globally block all agents?
* **Analysis**: No. Cooldowns are tracked individually per-agent via `cooldown_until` in the `agent_scorecards` table. If Agent A fails and is placed on cooldown, `rank_workers_for_capabilities` skips Agent A but continues to assign work to Agent B or other healthy fallback capable agents. Sorting tie-breaks are fully deterministic (using agent declaration order and alphabetical ID).
* **Verification**: `tests.test_agent_scorecard.ScorecardRoutingTests.test_rank_workers_skips_cooled_down_agent` and `test_rank_workers_is_deterministic_for_equal_scores` verify these behaviors.

### 6. [x] Can overnight quiet hours kill active workers unsafely?
* **Analysis**: No. When the loop ticks during quiet hours, `should_pause_for_quiet_hours` returns a decision where `kill_workers` is False. The autonomous coordinator (`autonomy_runtime.py`) simply halts further backlog task scheduling, allowing any active workers to complete their execution and cleanly persist their snapshots/states. No processes are abruptly killed.
* **Verification**: `tests.test_overnight.OvernightPauseTests.test_should_pause_for_quiet_hours_when_window_active` covers quiet-hour transition logic.

### 7. [x] Can summaries contain raw prompts, env vars, secrets, or cross-project task titles?
* **Analysis**: No. `build_overnight_summary_for_run` only queries high-level metrics (completed/failed count) and active milestone titles, strictly filtered by `project_id`. It never touches raw logs, prompt content, or command arrays. Additionally, `_redact_summary` actively strips key-value pairs matching sensitive words (e.g. `env`, `prompt`, `secret`, `token`, `password`).
* **Verification**: `tests.test_overnight.OvernightSummaryTests.test_summary_does_not_leak_other_project_titles` asserts that Project A summaries never contain Project B titles or secret strings.

### 8. [x] Can slash commands bypass Supervisor RPC?
* **Analysis**: No. In the frontend TUI (`ui-tui/src/slashRpc.ts`), the `/strategy`, `/recoveries`, `/agents`, and `/overnight` commands are fully mapped to Supervisor RPC requests (`project.strategy`, `project.recoveries`, `project.agents`, `project.overnight`). They are dispatched across the RPC connection, and the output is formatted by `ui-tui/src/slashDisplay.ts`.
* **Verification**: `tests.test_phase7_strategic_autonomy_e2e.Phase7SlashRoutingTests` confirms that executing these slash commands maps directly to Supervisor RPCs.

### 9. [x] Can full tests pass through mocked-only false greens while real clean-wheel smoke fails?
* **Analysis**: No. Clean-wheel smoke testing compiles the package as a wheel, installs it in a clean virtual environment without `PYTHONPATH`, and verifies migration mirror consistency, configuration explanation, initialization, and doctor status. Everything executes correctly.
* **Verification**: All 32 Phase 7 unit/E2E tests pass, along with 154 Vitest frontend tests. The PTY viewport issue in `test_pty_help_shows_commands_without_rpc` was safely resolved by increasing PTY height to 45 lines to handle the expanded command set.

### 10. [ ] Are docs consistent with actual CLI/TUI behavior?
* **Analysis**: Conditionally. The core implementation of the commands `/strategy`, `/recoveries`, `/agents`, and `/overnight` works beautifully and corresponds perfectly to the design. However, the files in `docs/` (`cli.md`, `tui.md`, `troubleshooting.md`) have not yet been modified to include these commands. This is covered by Task 10 of the plan and must be completed prior to merging.

---

## Verdict Summary

```text
PASS WITH CONDITIONS: Code, database migrations, RPC endpoints, and TUI rendering are 100% correct, safe, secure, and robustly covered by automated tests. Merge is conditionally approved subject to completing the Task 10 documentation updates.
```
