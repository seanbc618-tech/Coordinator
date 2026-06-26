# Single-Fallback Agent Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect non-interactive approval blocks and hand the unchanged task worktree to one different compatible worker exactly once.

**Architecture:** Classify each worker result before changed-file validation, persist attempt classification and lineage, and let the engine perform one guarded fallback only when the first worker left no changes.

**Tech Stack:** Python 3.11+, sqlite3 migrations, TOML configuration, unittest.

---

## Ownership and Order

- Claude Code: Tasks 1, 3, and 5.
- Grok: Tasks 2, 4, and 6.
- Codex: review after Tasks 2, 4, and 6.
- Complete this plan before the global runtime and TUI plans.

### Task 1: Classify Interactive Worker Blocks

**Files:**
- Create: `src/local_cli_coordinator/agent_result.py`
- Create: `tests/test_agent_result.py`

- [ ] **Step 1: Write failing classifier tests**

Use the two real Claude responses captured on 2026-06-20 as positive fixtures.
Also test case-insensitivity, approval-before-implementation, exit-plan-mode,
"tell me to proceed", ordinary implementation summaries, generic questions, test
failures, non-zero exits, and timeouts.

- [ ] **Step 2: Run and confirm import failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_result -v`
Expected: FAIL.

- [ ] **Step 3: Implement explicit classification**

Define `AgentResultClass` values `completed`, `interactive_blocked`,
`command_failed`, and `timed_out`. Define a frozen `ClassifiedAgentResult` with
classification and reason code. Use a short list of compiled high-confidence
patterns; return named codes such as `plan_exit_approval` and
`implementation_approval`. Non-zero and timeout take precedence over text.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_result -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/agent_result.py tests/test_agent_result.py
git commit -m "feat: classify interactive worker blocks"
~~~

### Task 2: Persist Attempt Results and Fallback Lineage

**Files:**
- Create: `migrations/007_attempt_results.sql`
- Modify: `src/local_cli_coordinator/db.py`
- Create: `tests/test_attempt_results.py`

- [ ] **Step 1: Write failing persistence tests**

Verify attempt start/finish, result class, reason, fallback parent, one-fallback
count, restart persistence, invalid parent task, and attempts ordered by ID.

- [ ] **Step 2: Run and confirm schema failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_attempt_results -v`
Expected: FAIL.

- [ ] **Step 3: Add migration and APIs**

Add nullable `result_class`, `result_reason`, and
`fallback_from_attempt_id` to attempts plus an index on task ID and ID. Add
`start_attempt`, `finish_attempt`, `list_attempts`, and
`fallback_count_for_task`. Validate that fallback parent belongs to the same task.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_attempt_results tests.test_db -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add migrations/007_attempt_results.sql src/local_cli_coordinator/db.py tests/test_attempt_results.py
git commit -m "feat: persist worker attempt outcomes"
~~~

### Task 3: Configure Compatible Fallback Workers

**Files:**
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `config/agents.toml`
- Modify: `tests/test_config.py`
- Create: `tests/test_agent_selection.py`

- [ ] **Step 1: Write failing configuration tests**

Verify ordered fallback parsing, unknown IDs, self-reference, reviewer rejection,
capability filtering, unavailable concurrency, and no implicit Codex fallback.

- [ ] **Step 2: Run and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_config tests.test_agent_selection -v`
Expected: FAIL.

- [ ] **Step 3: Extend AgentConfig and selection**

Add an immutable tuple-of-strings `fallback_agents` field with an empty default. Implement
`select_fallback_agent(config, primary, capabilities, unavailable_ids)` that
returns the first configured, different, worker-role, capability-compatible,
available agent.

- [ ] **Step 4: Update shipped workers**

Change Claude to `--permission-mode acceptEdits` and instruct it to implement
directly without entering plan mode or requesting approval. Add a separate
`grok_worker` role; keep `grok_spec_reviewer` reviewer-only. Configure
Claude-to-Grok and Grok-to-Claude fallback lists.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_config tests.test_agent_selection -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/config.py config/agents.toml tests/test_config.py tests/test_agent_selection.py
git commit -m "feat: configure single fallback workers"
~~~

### Task 4: Record Attempts in the Agent Runner

**Files:**
- Modify: `src/local_cli_coordinator/agent.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `tests/test_agent.py`
- Create: `tests/test_agent_attempts.py`

- [ ] **Step 1: Write failing runner tests**

Verify every worker invocation creates and finishes one attempt, log paths are
distinct per attempt, output text is classified from the log, command exceptions
finish the attempt, and fallback lineage is recorded.

- [ ] **Step 2: Run and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent tests.test_agent_attempts -v`
Expected: FAIL.

- [ ] **Step 3: Add per-attempt run directories**

The initial attempt uses `runs/<task>/attempt-1/agent.log` and the fallback uses
`attempt-2/agent.log`. Engine starts the attempt before `run_agent` and finishes
it in `finally` with exit, classification, reason, log path, and optional parent.

- [ ] **Step 4: Preserve compatibility artifacts**

Add each attempt log as an artifact. Keep a task-level latest-agent-log artifact
or compatibility pointer so existing event and operator commands still work.

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent tests.test_agent_attempts -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/agent.py src/local_cli_coordinator/engine.py tests/test_agent.py tests/test_agent_attempts.py
git commit -m "feat: record each worker attempt"
~~~

### Task 5: Execute One Guarded Cross-Agent Fallback

**Files:**
- Create: `src/local_cli_coordinator/fallback.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_agent_fallback.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write failing engine tests**

Cover blocked Claude then successful Grok, both blocked, no eligible fallback,
fallback count already one after restart, first attempt with tracked changes,
first attempt with untracked changes, ordinary no-op, non-zero exit, timeout, and
fallback success continuing through verification.

- [ ] **Step 2: Run and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_fallback -v`
Expected: FAIL.

- [ ] **Step 3: Implement the fallback decision**

`decide_fallback` returns run, fail, or human-review. It permits run only for
`interactive_blocked`, fallback count zero, an eligible different worker, and a
worktree with no tracked or untracked changes since base commit.

- [ ] **Step 4: Integrate without recursion**

Extract one `run_worker_attempt` helper. Engine calls it once, evaluates the
decision, and may call it one second time. A loop or recursive retry is forbidden.
Partial changes transition to `awaiting_human` and are preserved.

- [ ] **Step 5: Run focused regressions**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_fallback tests.test_engine tests.test_task_leases -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/fallback.py src/local_cli_coordinator/engine.py tests/test_agent_fallback.py tests/test_engine.py
git commit -m "feat: hand blocked work to one fallback agent"
~~~

### Task 6: Expose Fallback in Live Output and Status

**Files:**
- Modify: `src/local_cli_coordinator/reporting.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `tests/test_reporting.py`
- Modify: `tests/test_loop_status.py`
- Create: `tests/test_fallback_e2e.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing output tests**

Assert live lines for blocked reason, fallback 1/1, source and destination agents,
partial-change skip, no eligible fallback, exhausted fallback, and final result.
Status must show current attempt and fallback usage.

- [ ] **Step 2: Run and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_reporting tests.test_loop_status tests.test_fallback_e2e -v`
Expected: FAIL.

- [ ] **Step 3: Emit structured recovery events**

Use reporter stages `worker_blocked`, `fallback_selected`,
`fallback_started`, and `fallback_exhausted`. Do not parse display strings in
status; query attempt records and structured task events.

- [ ] **Step 4: Add an exact-two-attempt E2E fixture**

The first fake worker prints the captured plan approval and exits zero. The second
writes one file and exits zero. Assert two attempts, no third process invocation,
verification success, final task success, and restart preserving fallback count.

- [ ] **Step 5: Run full verification**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_agent_result tests.test_attempt_results tests.test_agent_selection tests.test_agent_attempts tests.test_agent_fallback tests.test_fallback_e2e -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
~~~

Expected: all tests PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/reporting.py src/local_cli_coordinator/cli.py tests/test_reporting.py tests/test_loop_status.py tests/test_fallback_e2e.py README.md
git commit -m "feat: report single-agent fallback recovery"
~~~

## Acceptance

- Reproduce the real Claude plan-mode output with a fake worker.
- Observe exactly one switch from Claude to Grok.
- Confirm no switch when the first worker changed any file.
- Confirm no third invocation after two blocked attempts.
- Restart between attempts and prove the cap remains enforced.
- Run the full suite and git diff check.
- Codex reviews command configuration, attempt lineage, task state, and live output.
