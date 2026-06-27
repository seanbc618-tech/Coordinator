# Claude Code Handoff: Phase 6 Autonomous Loop Adversarial Review

> **Replaces Gemini Task 8** (Gemini agent unavailable / repeated errors).
> You are a **read-only adversarial reviewer**. Do not implement production fixes,
> do not open PRs, do not stage unrelated local files.

Repository: `/Users/xiafan/Coordinator`  
Branch: **`phase6-autonomous-loop-core`**  
Baseline: `3e062ef` (Task 7 docs) · implementation stack `e21cb5c`…`cac6336` (Tasks 1–6)

Write your result to:

`docs/superpowers/handoffs/2026-06-26-phase6-gemini-review.md`

(Keep this filename so Codex Gate E can find it per plan.)

Commit message for result doc only:

`docs: record Phase 6 adversarial review`

---

## Required verdict format

```text
=== PHASE 6 AUTONOMOUS LOOP CORE ===
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0:
P1:
P2:
Reproduction commands: (exact bash for each finding)
Blocking merge: yes | no
```

Severity guide:

| Level | Meaning |
|-------|---------|
| **P0** | Duplicate task admission, cross-project leak, evaluation bypass, budget bypass, data loss |
| **P1** | False-green test, rubber-stamp evaluator, race under repeated ticks, Supervisor hang |
| **P2** | Doc drift, naming, non-blocking UX |

---

## Inputs (read in this order)

| Doc | Purpose |
|-----|---------|
| `docs/superpowers/plans/2026-06-26-phase6-autonomous-loop-core.md` | Plan + acceptance |
| `docs/autonomous-loop.md` | Operator contract |
| `docs/superpowers/handoffs/2026-06-26-phase6-autonomous-loop-acceptance.md` | Claude Task 7 acceptance |

### Code hotspots

| Area | Paths |
|------|-------|
| Migration 014 | `migrations/014_autonomous_loop_core.sql`, package mirror |
| DB helpers | `src/local_cli_coordinator/autonomous_loop_db.py` |
| Backlog | `src/local_cli_coordinator/autonomous_backlog.py` |
| Evaluator | `src/local_cli_coordinator/evaluator.py` |
| Loop engine | `src/local_cli_coordinator/loop_autonomy.py` |
| Runtime / RPC payloads | `src/local_cli_coordinator/autonomy_runtime.py` |
| Supervisor tick | `src/local_cli_coordinator/supervisor.py` |
| RPC | `src/local_cli_coordinator/supervisor_methods.py` |
| Slash | `src/local_cli_coordinator/cli_chat.py` |
| Config | `src/local_cli_coordinator/config.py` (`[autonomy]`, `autonomy_enabled`) |
| Red / E2E tests | `tests/test_autonomous_backlog.py`, `test_task_evaluator.py`, `test_loop_autonomy.py`, `test_phase6_autonomous_loop_e2e.py` |

---

## Attack checklist (plan Task 8 — review exactly these)

### 1. Duplicate work under repeated ticks

**Question:** Does the loop admit duplicate tasks/backlog items when `tick()` or
`/loop step` runs multiple times?

**Checks:**

1. `compute_backlog_dedupe_key` + partial unique index on open statuses.
2. `promote_next_backlog_item` marks items `admitted` before next tick.
3. `run_autonomous_iteration` idempotency when running task exists → `wait`.

**Reproduction:**

```bash
cd /Users/xiafan/Coordinator
git checkout phase6-autonomous-loop-core
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog.BacklogDedupeTests \
  tests.test_loop_autonomy.AutonomousIterationTests.test_loop_waits_when_project_has_running_task \
  tests.test_loop_autonomy.AutonomousIterationTests.test_loop_admits_one_backlog_item_when_idle \
  -v
```

**Fail if:** second identical `propose_backlog_items` inserts; second idle iteration admits same backlog twice.

### 2. Failed task treated as success

**Question:** Can a terminal `failed` task evaluate to `pass`?

**Checks:**

1. `evaluate_task` verdict rules for `state == failed`.
2. No code path maps `failed` → `pass` without evidence.

**Reproduction:**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_task_evaluator.EvaluatorVerdictTests.test_evaluator_flags_failed_task_as_followup \
  -v
```

**Fail if:** `evaluate_task` returns `pass` for `failed` rows.

### 3. Cross-project leak via `project.loop.step`

**Question:** Can loop RPC or iteration affect another project's backlog/tasks?

**Checks:**

1. All queries filter `project_id`.
2. `project.loop.step` uses `request.project_id` only.
3. E2E test `test_project_loop_status_is_project_scoped`.

**Reproduction:**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_loop_autonomy.AutonomousIterationTests.test_loop_iteration_is_project_scoped \
  tests.test_phase6_autonomous_loop_e2e.LoopStatusRPCTests \
  -v
```

**Fail if:** iteration evaluates/admits work for a different `project_id`.

### 4. Evaluation bypass

**Question:** Can follow-up admission skip independent evaluation?

**Checks:**

1. `require_evaluation_before_followup` in config (default true).
2. Loop decision order: evaluate before admit.
3. `find_unevaluated_terminal_tasks` excludes already-evaluated tasks.

**Reproduction:**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_task_evaluator tests.test_loop_autonomy -v
```

**Fail if:** backlog promote runs while unevaluated terminal tasks exist and policy requires evaluation first (unless explicitly documented exception).

### 5. Commander / budget bypass

**Question:** Can Commander-generated backlog bypass small-task caps or daily budget?

**Checks:**

1. `_small_task_rejection_reasons` in `autonomous_backlog.py`.
2. `_maybe_generate_backlog` capped; Commander path not unbounded in `tick()`.
3. Supervisor does not block socket clients on long Commander runs during autonomy tick.

**Fail if:** unbounded `propose_backlog_items` from Commander without cap; autonomy tick awaits unbounded Commander RPC.

### 6. False-green tests

**Question:** Does any Phase 6 test pass without exercising production code?

**Checks:**

1. Red tests import real modules (not stubs).
2. E2E uses `FakeSupervisor` delegation to `SupervisorMethods` for loop RPCs.
3. No `pass` placeholders left in Phase 6 test files.

**Reproduction:**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog \
  tests.test_task_evaluator \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e -v
```

### 7. Supervisor responsiveness

**Question:** Does autonomy block `/status`, `/tasks`, `/loop` while active?

**Checks:**

1. `run_project_autonomy` bounded by `max_iterations_per_tick` and eval/admit caps.
2. Autonomy runs inside `tick()` before claim, not on RPC thread indefinitely.

**Spot-check:** read `supervisor.py` `tick()` ordering.

### 8. Wheel migration 014

**Question:** Does installed wheel include migration 014?

**Reproduction:**

```bash
python3 -m build
PYTHONPATH=src python3 -m unittest tests.test_wheel_migrations tests.test_migration_mirror_sync -v
```

**Fail if:** mirror drift or wheel missing `014_autonomous_loop_core.sql`.

---

## Optional manual probes

```bash
# Full regression (expect ~1027 passed)
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q

# Phase 6 only
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog \
  tests.test_task_evaluator \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e -v
```

---

## Deliverable

1. Fill `docs/superpowers/handoffs/2026-06-26-phase6-gemini-review.md` with verdict + findings.
2. Set `Blocking merge: no` only if no P0 and no open P1 that hides a real regression.
3. Do **not** proceed to Codex Gate E yourself — hand the review doc back to Grok/operator.

After your doc lands with **PASS** or **CONDITIONAL PASS** (no blocking P0), Codex runs Task 9 Gate E.