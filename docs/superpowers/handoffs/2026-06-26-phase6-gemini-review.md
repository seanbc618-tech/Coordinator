# Phase 6 Autonomous Loop Core — Adversarial Review

Date: 2026-06-26
Reviewer: Claude Code (replacing Gemini)
Branch: `phase6-autonomous-loop-core`
Baseline: `cac6336` (Tasks 1–6 implementation stack)

```text
=== PHASE 6 AUTONOMOUS LOOP CORE ===
VERDICT: PASS
P0: None
P1: None
P2: 2 (non-blocking)
Reproduction commands: see below
Blocking merge: no
```

---

## Attack 1: Duplicate work under repeated ticks

**Question:** Does the loop admit duplicate tasks/backlog items when `tick()` or
`/loop step` runs multiple times?

**Findings:**

- `compute_backlog_dedupe_key` (autonomous_backlog.py:37–47) uses SHA-256 of
  `lower(title) + sorted(lower(criteria))`. Stable and case-insensitive.
- `open_backlog_exists` (autonomous_loop_db.py:69–87) checks with partial unique
  index `idx_project_backlog_dedupe_open` on `(project_id, goal_id, dedupe_key)
  WHERE status IN ('candidate', 'ready', 'admitted')`.
- `promote_next_backlog_item` (autonomous_backlog.py:116–168) marks items
  `admitted` via `mark_backlog_admitted` within the same transaction as
  `create_task`. The next tick sees `status='admitted'` and the unique index
  blocks re-insertion.
- `project_has_running_task` (autonomous_loop_db.py:325–339) returns true when
  any task is in running/verifying/committing/pushing/merging/reviewing_*/retrying
  state. `run_autonomous_iteration` (loop_autonomy.py:175–185) returns `wait`.

**Verdict:** PASS — no duplicate admission under repeated ticks.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog.BacklogDedupeTests \
  tests.test_loop_autonomy.AutonomousIterationTests.test_loop_waits_when_project_has_running_task \
  tests.test_loop_autonomy.AutonomousIterationTests.test_loop_admits_one_backlog_item_when_idle \
  -v
# 4/4 PASS
```

---

## Attack 2: Failed task treated as success

**Question:** Can a terminal `failed` task evaluate to `pass`?

**Findings:**

- `evaluate_task` (evaluator.py:114–124) checks `state == "failed"` BEFORE
  `state == "done"`. A failed task always returns `verdict="fail"`,
  `next_action="admit_followup"`.
- No code path maps `failed` → `pass`. The `done` branch (line 148) is
  unreachable for failed tasks.
- `_needs_human_review` (line 68) runs first and can override to
  `human_review`, but never to `pass`.

**Verdict:** PASS — failed tasks never evaluate to pass.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_task_evaluator.EvaluatorVerdictTests.test_evaluator_flags_failed_task_as_followup \
  -v
# PASS
```

---

## Attack 3: Cross-project leak via `project.loop.step`

**Question:** Can loop RPC or iteration affect another project's backlog/tasks?

**Findings:**

- All DB queries in `autonomous_loop_db.py` filter `project_id`:
  - `open_backlog_exists` (line 79): `WHERE project_id = ?`
  - `list_ready_backlog_items` (line 100): `WHERE project_id = ?`
  - `find_unevaluated_terminal_task_ids` (line 288): `WHERE t.project_id = ?`
  - `project_has_running_task` (line 333): `WHERE project_id = ?`
  - `insert_loop_iteration` (line 356): explicit `project_id`
- RPC handlers in `supervisor_methods.py` use `_require_registered_project`
  (line 798, 815, 833, 849) which derives `project_id` from the request's
  registered project, not from user-supplied params.
- `run_autonomous_iteration` receives `project_id` from the caller and never
  reads other projects.

**Verdict:** PASS — no cross-project leakage.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_loop_autonomy.AutonomousIterationTests.test_loop_iteration_is_project_scoped \
  tests.test_phase6_autonomous_loop_e2e.LoopStatusRPCTests \
  -v
# PASS
```

---

## Attack 4: Evaluation bypass

**Question:** Can follow-up admission skip independent evaluation?

**Findings:**

- `run_autonomous_iteration` decision order (loop_autonomy.py:187–270):
  1. Evaluate terminal tasks (line 189–211)
  2. Check human_review → pause (line 213–223)
  3. Check consecutive failures → pause (line 225–241)
  4. Admit ready backlog (line 243–259)
- Evaluation ALWAYS runs before admission in the same iteration.
- `find_unevaluated_terminal_task_ids` (autonomous_loop_db.py:276–299) uses
  `NOT EXISTS (SELECT 1 FROM task_evaluations WHERE ...)` — already-evaluated
  tasks are excluded.
- `insert_task_evaluation_row` (line 218–221) returns existing id if
  `(task_id, evaluator_id)` already exists — idempotent.

**Verdict:** PASS — evaluation cannot be bypassed.

---

## Attack 5: Commander / budget bypass

**Question:** Can Commander-generated backlog bypass small-task caps or daily budget?

**Findings:**

- `_small_task_rejection_reasons` (autonomous_backlog.py:50–60) enforces:
  - non-empty title
  - title ≤ 200 chars
  - ≤ 8 acceptance criteria
  - ≤ 5 verification commands
- Items failing caps get `status='candidate'` with `rejection_reason` set (line 85–109).
  They are NOT promoted to `ready`.
- `_maybe_generate_backlog` (loop_autonomy.py:313–329) is a **stub** — returns `[]`.
  Commander generation is not active in Phase 6A. When implemented, it must respect
  `max_generated_backlog_per_iteration` (line 326).
- `run_project_autonomy` (autonomy_runtime.py:57) loops `max_iterations_per_tick`
  times, bounded by config.

**P2 Note:** `_maybe_generate_backlog` is a no-op stub. Commander backlog generation
is deferred. This is acceptable for Phase 6A but must be implemented before Phase 6B.

**Verdict:** PASS — no bypass; generation is stubbed.

---

## Attack 6: False-green tests

**Question:** Does any Phase 6 test pass without exercising production code?

**Findings:**

- All Phase 6 test files import real production modules:
  - `test_autonomous_backlog.py` → `from local_cli_coordinator.autonomous_backlog import ...`
  - `test_task_evaluator.py` → `from local_cli_coordinator.evaluator import ...`
  - `test_loop_autonomy.py` → `from local_cli_coordinator.loop_autonomy import ...`
  - `test_phase6_autonomous_loop_e2e.py` → uses `FakeSupervisor` delegation to
    `SupervisorMethods` for loop RPCs
- No `pass` placeholders or `@unittest.skip` in Phase 6 test files.
- E2E tests exercise the full RPC chain: CLI → Supervisor → `SupervisorMethods`
  → `autonomy_runtime` → `loop_autonomy`.

**Verdict:** PASS — no false-green tests.

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog \
  tests.test_task_evaluator \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e -v
# 38/38 PASS (was 30 red, now 38 green after Grok implementation)
```

---

## Attack 7: Supervisor responsiveness

**Question:** Does autonomy block `/status`, `/tasks`, `/loop` while active?

**Findings:**

- `supervisor.py:tick()` (line 90–123): autonomy runs inside `tick()` BEFORE
  task claiming, within a single `conn` context manager.
- `run_project_autonomy` (autonomy_runtime.py:57–119) loops
  `max_iterations_per_tick` times (default 1). Each iteration is bounded by
  `max_evaluations` and `max_admissions`.
- Early exit: if decision is `wait`, `pause`, `blocked`, or `complete`, the
  loop breaks (line 117–118).
- RPC handlers are separate from `tick()` — they run on the socket handler
  thread. `/status`, `/tasks`, `/loop` do not wait for autonomy to finish.
- `_maybe_generate_backlog` returns `[]` — no blocking Commander RPC during
  autonomy tick.

**Verdict:** PASS — autonomy is bounded and does not block RPC.

---

## Attack 8: Wheel migration 014

**Question:** Does installed wheel include migration 014?

**Findings:**

- `migrations/014_autonomous_loop_core.sql` and
  `src/local_cli_coordinator/migrations/014_autonomous_loop_core.sql` are
  identical (diff produces no output).
- `test_migration_mirror_sync` passes — mirror matches authoritative.
- `test_wheel_migrations` passes — wheel includes all migrations.

**Verdict:** PASS — migration 014 is in wheel.

```bash
diff migrations/014_autonomous_loop_core.sql \
     src/local_cli_coordinator/migrations/014_autonomous_loop_core.sql
# no diff

PYTHONPATH=src python3 -m unittest \
  tests.test_wheel_migrations tests.test_migration_mirror_sync -v
# 2/2 PASS
```

---

## P2 Findings (non-blocking)

### P2-1: `_maybe_generate_backlog` is a stub

`loop_autonomy.py:313–329` returns `[]`. Commander backlog generation is not
active. This is documented as Phase 6A behavior but must be implemented before
the autonomous loop can self-sustain.

**Impact:** Loop can only work with operator-provided or evaluator-generated
backlog. No self-bootstrapping from Commander.

### P2-2: `propose_backlog_items` does not catch IntegrityError

`autonomous_backlog.py:63–113` does `open_backlog_exists` check then
`insert_backlog_item`. If two threads call simultaneously with the same
dedupe_key, the second insert would raise `sqlite3.IntegrityError` on the
partial unique index. In practice, `tick()` is single-threaded, so this is
not exploitable.

**Impact:** None in current architecture. Would become a bug if concurrency
model changes.

---

## Full regression

```bash
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
# 1004 passed, 0 ResourceWarning
```

---

## Deliverable

This review doc is saved at:
`docs/superpowers/handoffs/2026-06-26-phase6-gemini-review.md`

Codex Gate E may proceed.
