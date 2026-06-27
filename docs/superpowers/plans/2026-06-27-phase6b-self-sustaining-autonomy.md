# Phase 6B Self-Sustaining Autonomy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase 6 autonomous loop self-sustaining by letting Commander generate small backlog items when an active project goal has no ready work, while preserving evaluator-before-follow-up, dedupe, budget caps, and operator visibility.

**Architecture:** Keep the Phase 6A loop core. Add a Commander-to-backlog adapter that converts validated Commander task proposals into `BacklogDraft` rows, never directly into `tasks`. Wire this adapter into `_maybe_generate_backlog()` with strict caps, short timeout, run-state hygiene, and events.

**Tech Stack:** Python `unittest`, existing SQLite schema from migration 014, existing Commander schema v2, existing Supervisor event broker/RPC, minimal TypeScript updates only if slash formatting needs to display new fields.

---

## 0. Why Phase 6B Exists

Phase 6A passed as the autonomous loop foundation, but its `_maybe_generate_backlog()` is intentionally a stub. That means the loop can evaluate tasks and promote existing backlog, but it cannot yet keep working by asking Commander for the next small batch.

Phase 6B closes that gap:

```text
active goal
  -> evaluate terminal tasks
  -> if no ready backlog and project is idle
  -> ask Commander for 1-3 small proposals
  -> convert proposals into backlog rows
  -> next loop iteration promotes backlog to tasks
```

Important: Commander generation must not bypass Phase 6A's backlog/evaluator safety model.

---

## 1. Scope

### In Scope

- Commander-backed backlog generation from `_maybe_generate_backlog()`.
- New adapter from `CommanderTaskProposal` to `BacklogDraft`.
- Commander run failure handling in autonomous generation.
- Dedupe race hardening for backlog inserts.
- Missing-config startup diagnostic improvement for clean-wheel operation.
- Loop/backlog status fields showing generation results and errors.
- Tests proving generated backlog is not immediately admitted in the same iteration.

### Out of Scope

- LLM evaluator.
- New UI panels.
- Multi-service architecture.
- Changing merge/push policy.
- Scheduled overnight mode.
- Agent performance scoring.
- Changing Commander schema v2 unless a test proves schema v2 cannot express the needed behavior.

---

## 2. Ownership

| Role | Work |
| --- | --- |
| **Grok** | Main implementation: adapter, `_maybe_generate_backlog`, dedupe race hardening, Supervisor events/status, startup diagnostics. |
| **Claude Code** | Red tests, deterministic fake Commander fixtures, docs, handoff. Avoid deep loop logic. |
| **Gemini / .pi agent** | Adversarial review only. Focus on bypasses, duplicate generation, infinite loops, false-green tests, and UX mismatch. |
| **Codex** | Gate A/B/C/D/E sign-off. Reject if Commander can create tasks without backlog, if loop can generate infinitely, or if clean-wheel smoke regresses. |

---

## 3. File Map

Create:

- `src/local_cli_coordinator/commander_backlog.py`  
  Converts Commander responses into backlog drafts and handles generation result summaries.
- `tests/test_commander_backlog.py`  
  Unit tests for proposal-to-backlog conversion and rejection.
- `docs/superpowers/handoffs/2026-06-27-phase6b-acceptance.md`  
  Final implementation handoff.
- `docs/superpowers/handoffs/2026-06-27-phase6b-gemini-review.md`  
  Adversarial review result.

Modify:

- `src/local_cli_coordinator/loop_autonomy.py`  
  Replace `_maybe_generate_backlog()` stub with bounded Commander generation.
- `src/local_cli_coordinator/autonomous_backlog.py`  
  Catch `sqlite3.IntegrityError` on duplicate open backlog insert and continue safely.
- `src/local_cli_coordinator/autonomy_runtime.py`  
  Publish `backlog.item` events for generated backlog rows and expose generation error/reason in status.
- `src/local_cli_coordinator/config.py`  
  Add `commander_generation_timeout_seconds` under `[autonomy]`.
- `src/local_cli_coordinator/cli.py` or `src/local_cli_coordinator/supervisor_process.py`  
  Surface missing config files as a clear startup error instead of a 30 second ready timeout.
- `tests/test_loop_autonomy.py`  
  Generation decision tests.
- `tests/test_phase6_autonomous_loop_e2e.py`  
  RPC/headless loop-step generation tests.
- `tests/test_supervisor_process.py`  
  Missing config startup diagnostic.
- `docs/autonomous-loop.md` and `docs/cli.md`  
  Document self-sustaining generation and expected `/loop` output.

---

## 4. Behavioral Contracts

### 4.1 Generation Pre-Conditions

`_maybe_generate_backlog()` may run only when all are true:

- global `config.autonomy.enabled` is true;
- matching repo config has `autonomy_enabled = true`;
- active project goal exists;
- project has no running task when `wait_when_running = true`;
- ready backlog count is below `MIN_READY_BACKLOG`;
- `max_generated_backlog_per_iteration > 0`;
- no Commander run is already active for the goal;
- Commander retry timing is not active.

### 4.2 Generation Output

Commander output must be converted to backlog rows:

```text
CommanderTaskProposal
  -> BacklogDraft(source="commander", title, rationale, criteria, verify commands)
  -> project_backlog_items(status="ready" or "candidate")
```

It must not call:

- `db.create_task()`
- `admit_commander_response()`
- `_admit_task_proposal()`
- `maybe_replenish_goal()`

Those paths create tasks directly and bypass Phase 6 backlog control.

### 4.3 Iteration Semantics

If generation succeeds:

- The current iteration returns `LoopDecision(decision="generate")`.
- `generated_backlog_ids` contains backlog item ids.
- No task is admitted in the same iteration.
- The next Supervisor tick may promote ready backlog into tasks.

If Commander says goal completed:

- If all linked tasks are terminal, transition goal to `completed`.
- If linked tasks are not terminal, keep goal active and record progress.

If Commander fails:

- record Commander failure using existing goal failure mechanism;
- return `wait` or `pause` with a concrete reason;
- never create placeholder backlog.

---

## 5. Task Breakdown

### Task 0 — Claude Code: Red Tests for Self-Sustaining Generation

**Files:**

- Create: `tests/test_commander_backlog.py`
- Modify: `tests/test_loop_autonomy.py`
- Modify: `tests/test_phase6_autonomous_loop_e2e.py`
- Modify: `tests/test_supervisor_process.py`

- [ ] **Step 1: Add unit red tests for Commander-to-backlog conversion**

In `tests/test_commander_backlog.py`, add a `CommanderBacklogConversionTests`
class with these exact test names:

- `test_commander_task_proposal_converts_to_backlog_draft`
- `test_generation_never_creates_task_directly`
- `test_generation_caps_to_configured_max`

Expected pre-implementation failure: `ModuleNotFoundError: local_cli_coordinator.commander_backlog`.

- [ ] **Step 2: Add loop generation red tests**

In `tests/test_loop_autonomy.py`, add these exact test names:

- `test_loop_generates_backlog_when_idle_and_empty`
- `test_loop_generation_does_not_admit_task_same_iteration`
- `test_loop_does_not_generate_when_ready_backlog_exists`
- `test_loop_does_not_generate_when_commander_run_active`
- `test_duplicate_generated_backlog_is_idempotent`

Use a fake Commander runner hook or monkeypatch `loop_autonomy.run_commander` after Grok exposes a patchable seam.

- [ ] **Step 3: Add RPC/headless red tests**

In `tests/test_phase6_autonomous_loop_e2e.py`, add these exact test names:

- `test_loop_step_generates_backlog_and_reports_generate`
- `test_backlog_rpc_shows_commander_generated_item`

Expected final behavior:

```text
Loop status [proj-example]
  last: generate — generated 1 backlog draft(s)
```

- [ ] **Step 4: Add missing-config diagnostic red test**

In `tests/test_supervisor_process.py`, add
`test_supervisor_start_reports_missing_config_file`.

Expected output must include:

```text
missing config file
agents.toml
```

It must not wait the full Supervisor ready timeout.

- [ ] **Step 5: Run red tests**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_commander_backlog \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e \
  tests.test_supervisor_process -v
```

Expected: new tests fail for missing implementation, while unrelated existing tests continue to pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_commander_backlog.py tests/test_loop_autonomy.py \
  tests/test_phase6_autonomous_loop_e2e.py tests/test_supervisor_process.py
git commit -m "test: capture Phase 6B self-sustaining generation contracts"
```

Codex Gate A reviews only the red-test quality.

### Task 1 — Grok: Commander-to-Backlog Adapter

**Files:**

- Create: `src/local_cli_coordinator/commander_backlog.py`
- Modify: `tests/test_commander_backlog.py`

- [ ] **Step 1: Implement adapter dataclass**

Create:

```python
@dataclass(frozen=True)
class CommanderBacklogGeneration:
    inserted_ids: tuple[str, ...]
    rejected_reasons: tuple[str, ...]
    progress_summary: str
    goal_status: str
    stop_reason: str | None
```

- [ ] **Step 2: Implement proposal conversion**

Add:

```python
def proposal_to_backlog_draft(proposal: CommanderTaskProposal) -> BacklogDraft:
    return BacklogDraft(
        source="commander",
        title=proposal.title,
        rationale=proposal.goal or proposal.rationale,
        acceptance_criteria=list(proposal.acceptance_criteria),
        verification_commands=list(proposal.verification_commands),
        execution_policy="normal",
        priority=50,
    )
```

Do not carry `repo`, `expected_files`, or `expected_minutes` into the backlog row directly; those are validation inputs.

- [ ] **Step 3: Implement response-to-backlog admission**

Add:

```python
def commander_response_to_backlog(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int,
    response: CommanderResponse,
    max_items: int,
) -> CommanderBacklogGeneration:
    drafts = [
        proposal_to_backlog_draft(proposal)
        for proposal in response.tasks[:max_items]
    ]
    inserted = propose_backlog_items(
        conn,
        project_id=project_id,
        goal_id=goal_id,
        drafts=drafts,
    )
    return CommanderBacklogGeneration(
        inserted_ids=tuple(inserted),
        rejected_reasons=tuple(),
        progress_summary=response.progress_summary,
        goal_status=response.goal_status,
        stop_reason=response.stop_reason,
    )
```

If `response.intent != "task_request"` or `response.tasks` is empty, return an empty generation result with a rejection reason `no task proposals`.

- [ ] **Step 4: Run adapter tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_backlog -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/commander_backlog.py tests/test_commander_backlog.py
git commit -m "feat: convert Commander proposals into autonomous backlog"
```

### Task 2 — Grok: Implement `_maybe_generate_backlog()`

**Files:**

- Modify: `src/local_cli_coordinator/loop_autonomy.py`
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `tests/test_loop_autonomy.py`

- [ ] **Step 1: Add config field**

Extend `AutonomyConfig` with:

```python
commander_generation_timeout_seconds: int = 45
```

Load from `[autonomy]`:

```toml
commander_generation_timeout_seconds = 45
```

If missing, default to `45`.

- [ ] **Step 2: Import Commander seams in `loop_autonomy.py`**

Use these imports:

```python
from .commander_backlog import commander_response_to_backlog
from .commander_runner import CommanderRunActiveError, classify_commander_failure, run_commander
from .goals import (
    clear_commander_failures,
    get_goal,
    linked_tasks_all_terminal,
    record_commander_failure,
    transition_goal,
    update_goal_progress,
)
```

- [ ] **Step 3: Replace `_maybe_generate_backlog()` stub**

Behavior:

```python
def _maybe_generate_backlog(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int,
    config: CoordinatorConfig,
    root: Path,
) -> list[str]:
    autonomy = _autonomy_settings(config)
    if autonomy is None or not autonomy.enabled:
        return []
    max_generated = int(autonomy.max_generated_backlog_per_iteration)
    if max_generated <= 0:
        return []
    timeout = int(getattr(autonomy, "commander_generation_timeout_seconds", 45))
    try:
        result = run_commander(
            conn,
            config,
            root,
            goal_id,
            "replenishment",
            timeout,
        )
    except CommanderRunActiveError:
        return []
    except ValueError:
        return []
    if not result.succeeded or result.response is None:
        record_commander_failure(conn, goal_id)
        return []
    generation = commander_response_to_backlog(
        conn,
        project_id=project_id,
        goal_id=goal_id,
        response=result.response,
        max_items=max_generated,
    )
    update_goal_progress(conn, goal_id, generation.progress_summary)
    if generation.inserted_ids:
        clear_commander_failures(conn, goal_id)
    if generation.goal_status == "completed" and linked_tasks_all_terminal(conn, goal_id):
        transition_goal(conn, goal_id, "completed", stop_reason=generation.stop_reason or "completed by Commander")
    return list(generation.inserted_ids)
```

The actual function signature must include `root: Path`; pass the registered project root from `run_autonomous_iteration()`.

- [ ] **Step 4: Ensure no same-iteration admission**

The `run_autonomous_iteration()` decision order must remain:

1. evaluate,
2. admit existing ready backlog,
3. generate new backlog,
4. return decision.

Do not re-check ready backlog after generation.

- [ ] **Step 5: Run loop tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_loop_autonomy tests.test_commander_backlog -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/local_cli_coordinator/loop_autonomy.py \
  src/local_cli_coordinator/config.py \
  tests/test_loop_autonomy.py
git commit -m "feat: generate autonomous backlog from Commander"
```

Codex Gate B reviews this task.

### Task 3 — Grok: Dedupe Race Hardening and Failure Reasons

**Files:**

- Modify: `src/local_cli_coordinator/autonomous_backlog.py`
- Modify: `src/local_cli_coordinator/autonomous_loop_db.py`
- Modify: `tests/test_autonomous_backlog.py`
- Modify: `tests/test_loop_autonomy.py`

- [ ] **Step 1: Catch duplicate insert races**

In `propose_backlog_items()`, wrap the existing `insert_backlog_item` call
with `try/except sqlite3.IntegrityError`. On a duplicate-open-work
integrity error, skip the duplicate and continue inserting the remaining drafts.

Only swallow errors caused by the open backlog dedupe index. Re-raise unrelated integrity errors.

- [ ] **Step 2: Persist generation no-op reasons**

When Commander returns no task proposals, `_maybe_generate_backlog()` should return `[]`, and the parent iteration reason should remain:

```text
no backlog ready and Commander generated no tasks
```

Update `LoopDecision.reason` for this branch. Do not claim `generate` unless at least one backlog row was inserted.

- [ ] **Step 3: Add tests**

Add these exact test names:

- `test_duplicate_insert_race_is_idempotent`
- `test_no_task_commander_response_records_wait_reason`

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_autonomous_backlog tests.test_loop_autonomy -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/autonomous_backlog.py \
  src/local_cli_coordinator/autonomous_loop_db.py \
  tests/test_autonomous_backlog.py tests/test_loop_autonomy.py
git commit -m "fix: make autonomous backlog generation idempotent"
```

### Task 4 — Grok: Supervisor Events, RPC Status, and Startup Diagnostic

**Files:**

- Modify: `src/local_cli_coordinator/autonomy_runtime.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/supervisor_process.py`
- Modify: `tests/test_phase6_autonomous_loop_e2e.py`
- Modify: `tests/test_supervisor_process.py`

- [ ] **Step 1: Publish generated backlog events**

For each generated backlog id, publish:

```python
broker.publish(
    conn,
    project_id,
    "backlog.item",
    {
        "action": "generated",
        "backlog_id": backlog_id,
        "goal_id": decision.goal_id,
    },
)
```

- [ ] **Step 2: Improve `/loop` status payload**

`build_loop_status_payload()` should include:

```python
"generation": {
    "enabled": config.autonomy.enabled,
    "max_per_iteration": config.autonomy.max_generated_backlog_per_iteration,
    "timeout_seconds": config.autonomy.commander_generation_timeout_seconds,
}
```

Also surface `last_iteration.generated_count` if available.

- [ ] **Step 3: Missing config diagnostic**

Before detached Supervisor spawn waits for readiness, check:

```text
paths.config_dir / "agents.toml"
paths.config_dir / "repos.toml"
paths.config_dir / "policy.toml"
```

If any are missing, return immediately with:

```text
error: missing config file: <path>
```

Do not spawn a child process in this case.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_phase6_autonomous_loop_e2e \
  tests.test_supervisor_process -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/autonomy_runtime.py \
  src/local_cli_coordinator/cli.py \
  src/local_cli_coordinator/supervisor_process.py \
  tests/test_phase6_autonomous_loop_e2e.py \
  tests/test_supervisor_process.py
git commit -m "feat: surface autonomous generation status"
```

Codex Gate C reviews this task.

### Task 5 — Claude Code: Docs and Acceptance Handoff

**Files:**

- Modify: `docs/autonomous-loop.md`
- Modify: `docs/cli.md`
- Create: `docs/superpowers/handoffs/2026-06-27-phase6b-acceptance.md`

- [ ] **Step 1: Document Phase 6B behavior**

Add to `docs/autonomous-loop.md`:

```text
Self-sustaining generation

When autonomy is enabled and an active goal has no ready backlog, Coordinator
asks Commander for up to N small task proposals. These proposals become backlog
items first. They are not admitted as worker tasks until a later loop iteration.
```

- [ ] **Step 2: Document operator commands**

Add examples:

```bash
coordinator --print -p "/loop"
coordinator --print -p "/loop step"
coordinator --print -p "/backlog"
```

- [ ] **Step 3: Document config**

Add:

```toml
[autonomy]
enabled = true
max_generated_backlog_per_iteration = 3
commander_generation_timeout_seconds = 45

[[repos]]
path = "/Users/xiafan/polymarket-crypto-threshold"
autonomy_enabled = true
```

- [ ] **Step 4: Write handoff**

The handoff must include:

- commit list,
- focused test commands,
- full suite command,
- clean-wheel configured smoke,
- explicit statement that Commander generation creates backlog, not tasks.

- [ ] **Step 5: Commit**

```bash
git add docs/autonomous-loop.md docs/cli.md \
  docs/superpowers/handoffs/2026-06-27-phase6b-acceptance.md
git commit -m "docs: document Phase 6B self-sustaining autonomy"
```

### Task 6 — Gemini / .pi Agent: Adversarial Review

**Files:**

- Create: `docs/superpowers/handoffs/2026-06-27-phase6b-gemini-review.md`

Review checklist:

- [ ] Commander generation cannot call `create_task()`.
- [ ] Commander generation cannot call `admit_commander_response()`.
- [ ] One loop iteration cannot both generate and admit the same backlog item.
- [ ] Duplicate Commander proposals across repeated ticks do not create duplicate open backlog.
- [ ] A running task prevents generation when `wait_when_running = true`.
- [ ] Commander run active returns quickly and does not block the loop.
- [ ] Failed Commander run records failure and does not create placeholder work.
- [ ] Missing config startup returns an immediate error.
- [ ] Tests fail if `_maybe_generate_backlog()` is reverted to `return []`.

Expected review format:

```text
=== PHASE 6B SELF-SUSTAINING AUTONOMY ===
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0:
P1:
P2:
Blocking merge: yes/no
```

Commit:

```bash
git add docs/superpowers/handoffs/2026-06-27-phase6b-gemini-review.md
git commit -m "docs: record Phase 6B adversarial review"
```

### Task 7 — Codex Gate D/E

Owner: Codex

Run:

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_commander_backlog \
  tests.test_autonomous_backlog \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e \
  tests.test_supervisor_process -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
python3 -m build
```

Configured clean-wheel smoke:

```bash
tmpdir="$(mktemp -d)"
mkdir -p "$tmpdir/home/config"
cp config/*.toml "$tmpdir/home/config/"
python3 -m venv "$tmpdir/venv"
"$tmpdir/venv/bin/pip" install dist/*.whl
COORDINATOR_HOME="$tmpdir/home" \
  "$tmpdir/venv/bin/coordinator" project add \
  /Users/xiafan/polymarket-crypto-threshold --yes
cd /Users/xiafan/polymarket-crypto-threshold
COORDINATOR_HOME="$tmpdir/home" \
  "$tmpdir/venv/bin/coordinator" --print -p "/loop"
```

Optional generated-backlog smoke may use a fake Commander fixture only if it runs through the real `run_commander()` path.

Sign-off file:

`docs/superpowers/handoffs/2026-06-27-phase6b-codex-signoff.md`

---

## 6. Acceptance Criteria

Phase 6B is complete only when:

- `_maybe_generate_backlog()` can create backlog rows from Commander proposals.
- Generated backlog rows are project-scoped and goal-scoped.
- Generated backlog rows are not admitted into tasks until a later iteration.
- Repeated ticks do not duplicate generated backlog.
- Commander failure does not create fake work.
- Missing config produces a direct error instead of a 30 second readiness timeout.
- `/loop` shows generation status and last generation decision.
- Full Python, TUI, build, wheel migration, and clean-wheel smoke pass.

---

## 7. Recommended Prompt to Grok

```text
Implement Phase 6B from:
/Users/xiafan/Coordinator/docs/superpowers/plans/2026-06-27-phase6b-self-sustaining-autonomy.md

You are main implementer. Wait for Claude Task 0 red tests first, then do Tasks 1-4 one commit per task. Do not directly create tasks from Commander generation; convert Commander proposals into project_backlog_items only. Stop at Codex Gate points.
```

## 8. Recommended Prompt to Claude Code

```text
Do only Task 0 and Task 5 from:
/Users/xiafan/Coordinator/docs/superpowers/plans/2026-06-27-phase6b-self-sustaining-autonomy.md

Task 0: write red tests and deterministic fixtures only. Do not implement production loop logic.
Task 5: docs and acceptance handoff only.
```

## 9. Recommended Prompt to Gemini / .pi Agent

```text
Perform Task 6 adversarial review from:
/Users/xiafan/Coordinator/docs/superpowers/plans/2026-06-27-phase6b-self-sustaining-autonomy.md

Focus on bypasses: direct task creation, duplicate generated backlog, same-iteration generate+admit, Commander active blocking, false-green tests, and missing-config diagnostics.
```
