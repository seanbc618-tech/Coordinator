# Phase 6C Autonomous Run Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Coordinator capable of running an unattended, operator-controlled autonomous loop session that continuously evaluates, generates backlog, admits tasks, waits, backs off, and stops safely with durable status and audit records.

**Architecture:** Keep one global Supervisor process and the existing Phase 6A/6B loop primitives. Add a small run-controller layer that stores autonomous run sessions in SQLite, exposes `project.loop.start|stop|pause|resume|run.status` RPCs, and makes active run sessions wake the Supervisor scheduler even when no worker task is ready. The controller never bypasses `run_project_autonomy()`; it only decides when and how often to call it, when to back off, and when to stop.

**Tech Stack:** Python `unittest`, SQLite migration 015 mirrored in both migration roots, existing Supervisor RPC protocol and `EventBroker`, current CLI/TUI slash routing, no new service or daemon process.

---

## 0. Why Phase 6C Exists

Phase 6A built one bounded autonomous iteration. Phase 6B made an idle goal self-sustaining by asking Commander for backlog. But the operator still has to manually call `/loop step`, and the Supervisor scheduler can sleep forever if a project has no claimable task yet.

Phase 6C closes that operational gap:

```text
operator starts loop run
  -> Supervisor records an autonomous run session
  -> active run session makes the project schedulable even without ready tasks
  -> each tick runs bounded Phase 6 autonomy
  -> run controller records decision, heartbeat, backoff, and stop reason
  -> loop stops on caps, repeated idle, repeated failures, timeout, or operator stop
```

This is the first phase where Coordinator should feel like it can be left running without babysitting.

---

## 1. Non-Negotiable Contracts

- Do not add another long-running service. The global Supervisor remains the only process.
- Do not create tasks directly from run sessions. All work still flows through Phase 6 backlog and task admission.
- Do not run uncontrolled tight loops. Every session has max iterations, max runtime, idle backoff, and stop conditions.
- Do not make all projects runnable just because autonomy is enabled. Only active run sessions may wake idle projects.
- Do not hide wait reasons. Every tick must record the latest decision and reason in the session.
- Do not let `/loop start` bypass repo allowlist or per-repo `autonomy_enabled` unless an explicit `force=true` test path is used.
- Do not treat a manual `/loop step` as starting an unattended session.
- Do not block chat/status/dashboard RPCs while a run session is active.
- Gemini must review for runaway loops, scheduler starvation, duplicate session races, false-green tests, and process restart persistence.

---

## 2. Ownership

| Role | Work |
| --- | --- |
| **Grok** | Main implementer. Owns all tests, production code, docs, and handoff commits. One coherent commit per task. |
| **Gemini / .pi agent** | Adversarial review only. No production edits unless Codex explicitly requests a repair task. |
| **Codex** | Gate owner. Reviews Gate A/B/C/D/E/F, rejects if active sessions can spin, bypass backlog, or get lost after Supervisor restart. |

No Claude Code in Phase 6C.

---

## 3. Scope

### In Scope

- Durable autonomous run sessions.
- Start/stop/pause/resume/status RPCs and CLI slash commands.
- Scheduler wake-up for active run sessions with no ready task.
- Backoff and stop conditions for idle or repeated wait loops.
- Supervisor restart persistence: active sessions survive process restart and resume ticking.
- Operator-visible run status in `/loop` and aggregate counts in `/dashboard`.
- Clean-wheel smoke for `/loop start`, `/loop run`, and `/loop stop`.

### Out of Scope

- LLM evaluator.
- New TUI dashboard panel.
- Overnight scheduling by clock time.
- Multi-service architecture.
- Push/merge policy changes.
- Agent scorecards or model routing.
- Remote GitHub Actions integration.

---

## 4. File Map

Create:

- `migrations/015_autonomous_run_sessions.sql`
- `src/local_cli_coordinator/migrations/015_autonomous_run_sessions.sql`
- `src/local_cli_coordinator/autonomous_runs.py`
- `tests/test_autonomous_runs.py`
- `tests/test_phase6c_autonomous_run_e2e.py`
- `docs/superpowers/handoffs/2026-06-28-phase6c-acceptance.md`
- `docs/superpowers/handoffs/2026-06-28-phase6c-gemini-review.md`

Modify:

- `src/local_cli_coordinator/config.py`
- `src/local_cli_coordinator/autonomy_runtime.py`
- `src/local_cli_coordinator/loop_autonomy.py`
- `src/local_cli_coordinator/supervisor.py`
- `src/local_cli_coordinator/supervisor_methods.py`
- `src/local_cli_coordinator/cli_chat.py`
- `src/local_cli_coordinator/task_control.py`
- `ui-tui/src/slash.ts`
- `ui-tui/src/slashDisplay.ts`
- `tests/test_migration_mirror_sync.py` if required by the existing mirror guard
- `tests/test_supervisor_methods.py`
- `tests/test_multi_project_supervisor.py`
- `tests/test_cli_prompt.py`
- `tests/test_phase5_5_dashboard.py`
- `tests/test_phase6_autonomous_loop_e2e.py`
- `docs/autonomous-loop.md`
- `docs/cli.md`

---

## 5. Data Model

Add migration `015_autonomous_run_sessions.sql` in both migration roots. The files must be byte-identical.

```sql
CREATE TABLE IF NOT EXISTS autonomous_run_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    status TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'continuous',
    started_by TEXT NOT NULL DEFAULT 'operator',
    max_iterations INTEGER NOT NULL DEFAULT 100,
    max_runtime_seconds INTEGER NOT NULL DEFAULT 28800,
    idle_backoff_seconds INTEGER NOT NULL DEFAULT 30,
    max_idle_iterations INTEGER NOT NULL DEFAULT 12,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    idle_iteration_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_decision TEXT,
    last_reason TEXT,
    next_tick_after TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat_at TEXT,
    ended_at TEXT,
    stop_reason TEXT,
    FOREIGN KEY(goal_id) REFERENCES goals(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_autonomous_run_one_active
ON autonomous_run_sessions(project_id)
WHERE status IN ('running', 'paused');

CREATE INDEX IF NOT EXISTS idx_autonomous_run_status_next_tick
ON autonomous_run_sessions(status, next_tick_after, project_id);

CREATE TABLE IF NOT EXISTS autonomous_run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    loop_iteration_id TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    evaluated_count INTEGER NOT NULL DEFAULT 0,
    admitted_count INTEGER NOT NULL DEFAULT 0,
    generated_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(run_id) REFERENCES autonomous_run_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_autonomous_run_steps_run
ON autonomous_run_steps(run_id, created_at);
```

Allowed session statuses: `running`, `paused`, `stopping`, `stopped`, `completed`, `failed`, `expired`.

Allowed modes: `continuous`, `until_idle`, `until_goal_done`.

---

## 6. Public API Contracts

### `autonomous_runs.py`

```python
@dataclass(frozen=True)
class AutonomousRunOptions:
    max_iterations: int = 100
    max_runtime_seconds: int = 28800
    idle_backoff_seconds: int = 30
    max_idle_iterations: int = 12
    mode: str = "continuous"


@dataclass(frozen=True)
class AutonomousRunSnapshot:
    id: str
    project_id: str
    goal_id: int | None
    status: str
    mode: str
    iteration_count: int
    idle_iteration_count: int
    failure_count: int
    last_decision: str | None
    last_reason: str | None
    next_tick_after: str | None
    stop_reason: str | None
```

Required functions:

```python
def start_run_session(conn, *, project_id, goal_id, options, started_by="operator"):
    """Create one running session per project or return the existing active session."""


def pause_run_session(conn, *, project_id):
    """Move the active running session to paused."""


def resume_run_session(conn, *, project_id):
    """Move the active paused session to running."""


def stop_run_session(conn, *, project_id, reason):
    """Stop the active running/paused/stopping session."""


def get_active_run_session(conn, *, project_id):
    """Return running or paused session for a project."""


def project_has_runnable_run_session(conn, *, project_id, now=None):
    """Return True only when a running session is due for a tick."""


def record_run_step(conn, *, run_id, decision, loop_iteration_id, idle_backoff_seconds):
    """Persist a run step, update heartbeat/counters, and return the updated session."""
```

Stop rules in `record_run_step()`:

- If `iteration_count >= max_iterations`: status `completed`, stop reason `max iterations reached`.
- If runtime exceeds `max_runtime_seconds`: status `expired`, stop reason `max runtime reached`.
- If `idle_iteration_count >= max_idle_iterations`: status `completed`, stop reason `idle limit reached`.
- If decision is `pause`, `blocked`, or `complete`: status matches the terminal meaning and stores the loop reason.
- Decisions `wait` with reasons containing `running task`, `no active goal`, or `no backlog ready` count as idle.
- Decisions `admit`, `evaluate`, or `generate` reset `idle_iteration_count` to 0.

### Runtime API

Add to `autonomy_runtime.py`:

```python
def run_project_autonomy_session(
    conn,
    *,
    project_id,
    config,
    paths,
    broker,
    paused_projects=None,
    stopped_projects=None,
):
    """Run one due active session tick and record autonomous_run_steps."""
```

This function must call existing `run_project_autonomy()` or `run_autonomous_iteration()`; it must not duplicate loop decision logic.

### Supervisor RPC Methods

Add methods:

- `project.loop.start`
- `project.loop.stop`
- `project.loop.pause`
- `project.loop.resume`
- `project.loop.run.status`

Response shape:

```json
{
  "project_id": "proj-example",
  "run": {
    "id": "run-abc123",
    "status": "running",
    "mode": "continuous",
    "iteration_count": 0,
    "idle_iteration_count": 0,
    "failure_count": 0,
    "last_decision": null,
    "last_reason": null,
    "next_tick_after": null,
    "stop_reason": null
  }
}
```

---

## 7. Task Breakdown

### Task 0: Red Tests for Run Sessions and Scheduler Wake-Up

**Owner:** Grok

**Files:**

- Create: `tests/test_autonomous_runs.py`
- Create: `tests/test_phase6c_autonomous_run_e2e.py`
- Modify: `tests/test_multi_project_supervisor.py`
- Modify: `tests/test_cli_prompt.py`

- [ ] **Step 1: Add migration/db red tests**

Create `tests/test_autonomous_runs.py` with these test names:

```python
class AutonomousRunSessionTests(unittest.TestCase):
    def test_start_run_session_creates_one_active_session_per_project(self): ...
    def test_pause_resume_stop_update_session_status(self): ...
    def test_record_run_step_applies_idle_backoff(self): ...
    def test_record_run_step_stops_after_max_iterations(self): ...
    def test_record_run_step_stops_after_idle_limit(self): ...
```

Expected pre-implementation failure: `ModuleNotFoundError: local_cli_coordinator.autonomous_runs` or missing table.

- [ ] **Step 2: Add scheduler wake-up red test**

In `tests/test_multi_project_supervisor.py`, add:

```python
def test_active_autonomous_run_makes_project_runnable_without_ready_task(self):
    """A project with an active run session must be scheduled even with no ready tasks."""
```

The test must create a registered project, active goal, autonomy-enabled config, and active run session. Assert one `supervisor.tick()` records a loop iteration or run step even when `project_has_claimable_task()` would be false.

- [ ] **Step 3: Add RPC/headless red tests**

Create `tests/test_phase6c_autonomous_run_e2e.py` with:

```python
class AutonomousRunRpcTests(unittest.TestCase):
    def test_loop_start_creates_running_session(self): ...
    def test_loop_status_includes_active_run(self): ...
    def test_loop_stop_marks_session_stopped(self): ...
    def test_loop_pause_resume_round_trip(self): ...
    def test_loop_start_rejects_when_autonomy_disabled_without_force(self): ...
```

- [ ] **Step 4: Add CLI slash red tests**

In `tests/test_cli_prompt.py`, add:

```python
def test_loop_start_slash_maps_to_project_loop_start(self): ...
def test_loop_stop_slash_maps_to_project_loop_stop(self): ...
def test_loop_run_status_slash_maps_to_project_loop_run_status(self): ...
```

Expected RPC mappings:

- `/loop start` -> `project.loop.start`
- `/loop stop` -> `project.loop.stop`
- `/loop pause` -> `project.loop.pause`
- `/loop resume` -> `project.loop.resume`
- `/loop run` -> `project.loop.run.status`

- [ ] **Step 5: Run red tests**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_runs \
  tests.test_phase6c_autonomous_run_e2e \
  tests.test_multi_project_supervisor \
  tests.test_cli_prompt -v
```

Expected: new Phase 6C tests fail for missing implementation; unrelated existing tests should still pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_autonomous_runs.py tests/test_phase6c_autonomous_run_e2e.py \
  tests/test_multi_project_supervisor.py tests/test_cli_prompt.py
git commit -m "test: capture Phase 6C autonomous run contracts"
```

**Codex Gate A:** Review red-test quality only. Reject if tests can pass without durable DB sessions or without scheduler wake-up.

---

### Task 1: Migration 015 and Run Session DB Helpers

**Owner:** Grok

**Files:**

- Create: `migrations/015_autonomous_run_sessions.sql`
- Create: `src/local_cli_coordinator/migrations/015_autonomous_run_sessions.sql`
- Create: `src/local_cli_coordinator/autonomous_runs.py`
- Modify: `tests/test_autonomous_runs.py`

- [ ] **Step 1:** Add migration in both roots using the exact schema in section 5.
- [ ] **Step 2:** Implement `AutonomousRunOptions`, `AutonomousRunSnapshot`, and all public functions in section 6.
- [ ] **Step 3:** Make `start_run_session()` idempotent: return an existing `running` or `paused` session for the project instead of raising on the unique index.
- [ ] **Step 4:** Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_autonomous_runs tests.test_migration_mirror_sync -v
```

- [ ] **Step 5:** Commit:

```bash
git add migrations/015_autonomous_run_sessions.sql \
  src/local_cli_coordinator/migrations/015_autonomous_run_sessions.sql \
  src/local_cli_coordinator/autonomous_runs.py tests/test_autonomous_runs.py
git commit -m "feat: persist autonomous run sessions"
```

**Codex Gate B:** Verify migration packaging and session stop/backoff semantics.

---

### Task 2: Run Controller Integration in Autonomy Runtime

**Owner:** Grok

**Files:**

- Modify: `src/local_cli_coordinator/autonomy_runtime.py`
- Modify: `src/local_cli_coordinator/loop_autonomy.py`
- Modify: `src/local_cli_coordinator/autonomous_loop_db.py` only if needed to fetch latest iteration IDs
- Modify: `tests/test_autonomous_runs.py`
- Modify: `tests/test_phase6c_autonomous_run_e2e.py`

- [ ] **Step 1:** Add `iteration_id: str | None = None` to `LoopDecision`, and in `run_autonomous_iteration()` preserve the return value from `_persist_iteration()` before returning.
- [ ] **Step 2:** Implement `run_project_autonomy_session()`:
  1. Look up active run session.
  2. Return `[]` when no session exists.
  3. Return `[]` when session is paused.
  4. Return `[]` when `next_tick_after` is in the future.
  5. Run exactly one bounded autonomy call.
  6. Record each decision with `record_run_step()`.
  7. Publish `loop.run.step` and `loop.run.status` events.
- [ ] **Step 3:** Confirm the new runtime path does not import or call `create_task()`, `admit_commander_response()`, or `_admit_task_proposal()`.
- [ ] **Step 4:** Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_runs \
  tests.test_loop_autonomy \
  tests.test_phase6c_autonomous_run_e2e -v
```

- [ ] **Step 5:** Commit:

```bash
git add src/local_cli_coordinator/autonomy_runtime.py \
  src/local_cli_coordinator/loop_autonomy.py \
  src/local_cli_coordinator/autonomous_loop_db.py \
  tests/test_autonomous_runs.py tests/test_phase6c_autonomous_run_e2e.py
git commit -m "feat: run autonomous sessions through loop runtime"
```

**Codex Gate C:** Reject if one session tick can run unbounded iterations or if generated backlog is admitted in the same run step.

---

### Task 3: Supervisor Scheduler Wake-Up and Run Session RPCs

**Owner:** Grok

**Files:**

- Modify: `src/local_cli_coordinator/supervisor.py`
- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Modify: `tests/test_multi_project_supervisor.py`
- Modify: `tests/test_supervisor_methods.py`
- Modify: `tests/test_phase6c_autonomous_run_e2e.py`

- [ ] **Step 1:** In `MultiProjectSupervisor._is_project_runnable()`, return true when either `project_is_runnable(...)` is true, or `project_has_runnable_run_session(...)` is true and `project_autonomy_enabled(...)` is true. Keep pause, stop, capacity, and executor checks.
- [ ] **Step 2:** In `MultiProjectSupervisor.tick()`, run `run_project_autonomy_session(...)` for due active sessions. If a session tick generates/evaluates/admitted work but no task is claimable yet, return normally.
- [ ] **Step 3:** Add Supervisor methods `project.loop.start`, `project.loop.stop`, `project.loop.pause`, `project.loop.resume`, and `project.loop.run.status`.
- [ ] **Step 4:** `project.loop.start` must require registered project, config, paths, active goal, and enabled autonomy unless `force=true`.
- [ ] **Step 5:** Update `build_loop_status_payload()` to include a `run` object or `None`.
- [ ] **Step 6:** Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_multi_project_supervisor \
  tests.test_supervisor_methods \
  tests.test_phase6c_autonomous_run_e2e \
  tests.test_phase6_autonomous_loop_e2e -v
```

- [ ] **Step 7:** Commit:

```bash
git add src/local_cli_coordinator/supervisor.py \
  src/local_cli_coordinator/supervisor_methods.py \
  src/local_cli_coordinator/autonomy_runtime.py \
  tests/test_multi_project_supervisor.py tests/test_supervisor_methods.py \
  tests/test_phase6c_autonomous_run_e2e.py tests/test_phase6_autonomous_loop_e2e.py
git commit -m "feat: control autonomous run sessions from Supervisor"
```

**Codex Gate D:** Reject if a project with active run session cannot wake without ready tasks, if duplicate starts create two active sessions, or if pause/stop is memory-only and lost after restart.

---

### Task 4: CLI Slash Commands and Dashboard Summary

**Owner:** Grok

**Files:**

- Modify: `src/local_cli_coordinator/cli_chat.py`
- Modify: `src/local_cli_coordinator/task_control.py`
- Modify: `tests/test_cli_prompt.py`
- Modify: `tests/test_phase5_5_dashboard.py`
- Modify: `ui-tui/src/slash.ts`
- Modify: `ui-tui/src/slashDisplay.ts`

- [ ] **Step 1:** Extend `/loop` slash parsing:

| Command | Method | Params |
| --- | --- | --- |
| `/loop` | `project.loop.status` | `{}` |
| `/loop step` | `project.loop.step` | `{ "force": true }` |
| `/loop start` | `project.loop.start` | `{}` |
| `/loop stop` | `project.loop.stop` | `{ "reason": "operator stop" }` |
| `/loop pause` | `project.loop.pause` | `{}` |
| `/loop resume` | `project.loop.resume` | `{}` |
| `/loop run` | `project.loop.run.status` | `{}` |

- [ ] **Step 2:** Update `_format_loop_status()` to show `run: none` when no run exists, or `run: running run-abc123, iterations=3, idle=1` when a run exists.
- [ ] **Step 3:** Add aggregate dashboard counts only:

```json
"autonomous_runs": {
  "running": 1,
  "paused": 0,
  "stopped": 2
}
```

Do not include task titles, backlog titles, goal text, or run reasons in the cross-project dashboard.

- [ ] **Step 4:** Update TUI slash help/display for `/loop start`, `/loop stop`, `/loop pause`, `/loop resume`, and `/loop run`.
- [ ] **Step 5:** Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_cli_prompt tests.test_phase5_5_dashboard -v
npm run typecheck --prefix ui-tui
npm test --prefix ui-tui -- --run
```

- [ ] **Step 6:** Commit:

```bash
git add src/local_cli_coordinator/cli_chat.py src/local_cli_coordinator/task_control.py \
  tests/test_cli_prompt.py tests/test_phase5_5_dashboard.py \
  ui-tui/src/slash.ts ui-tui/src/slashDisplay.ts
git commit -m "feat: expose autonomous run controls in CLI and TUI"
```

**Codex Gate E:** Reject if CLI can start a run while autonomy is disabled without explicit force, or if dashboard leaks project-specific titles/reasons across projects.

---

### Task 5: Restart Persistence and Clean-Wheel Smoke

**Owner:** Grok

**Files:**

- Modify: `tests/test_phase6c_autonomous_run_e2e.py`
- Modify: `tests/test_supervisor_process.py`
- Modify: `docs/superpowers/handoffs/2026-06-28-phase6c-acceptance.md`

- [ ] **Step 1:** Add `test_running_autonomous_session_survives_supervisor_restart`.
- [ ] **Step 2:** Document this clean-wheel smoke in the handoff:

```bash
python3 -m build
tmpdir="$(mktemp -d)"
mkdir -p "$tmpdir/home/config"
cp config/*.toml "$tmpdir/home/config/"
python3 -m venv "$tmpdir/venv"
"$tmpdir/venv/bin/pip" install dist/*.whl
COORDINATOR_HOME="$tmpdir/home" \
  "$tmpdir/venv/bin/coordinator" project add \
  /Users/xiafan/polymarket-crypto-threshold --yes
cd /Users/xiafan/polymarket-crypto-threshold
COORDINATOR_HOME="$tmpdir/home" "$tmpdir/venv/bin/coordinator" --print -p "/loop"
COORDINATOR_HOME="$tmpdir/home" "$tmpdir/venv/bin/coordinator" --print -p "/loop start"
COORDINATOR_HOME="$tmpdir/home" "$tmpdir/venv/bin/coordinator" --print -p "/loop run"
COORDINATOR_HOME="$tmpdir/home" "$tmpdir/venv/bin/coordinator" --print -p "/loop stop"
```

If default config keeps autonomy disabled, the smoke must show the exact temp `policy.toml` and `repos.toml` mutation used to enable autonomy for the smoke project.

- [ ] **Step 3:** Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_phase6c_autonomous_run_e2e tests.test_supervisor_process -v
```

- [ ] **Step 4:** Commit:

```bash
git add tests/test_phase6c_autonomous_run_e2e.py tests/test_supervisor_process.py \
  docs/superpowers/handoffs/2026-06-28-phase6c-acceptance.md
git commit -m "test: verify autonomous run restart persistence"
```

---

### Task 6: Documentation and Gemini Review Handoff

**Owner:** Grok writes docs, Gemini reviews after Grok finishes.

**Files:**

- Modify: `docs/autonomous-loop.md`
- Modify: `docs/cli.md`
- Create: `docs/superpowers/handoffs/2026-06-28-phase6c-acceptance.md`
- Create: `docs/superpowers/handoffs/2026-06-28-phase6c-gemini-review.md`

- [ ] **Step 1:** Update user docs with the difference between `/loop step` and `/loop start`, pause/resume/stop, stop conditions, and the recommended first real-project command sequence.
- [ ] **Step 2:** Write acceptance handoff with commit list, test results, clean-wheel smoke output, known P2 limitations, and exact Codex gate commands.
- [ ] **Step 3:** Ask Gemini for adversarial review using this checklist:

```text
Review Phase 6C current HEAD. Return PASS / CONDITIONAL PASS / FAIL.
Focus on:
1. Can active autonomous run sessions spin in a tight loop?
2. Can duplicate /loop start calls create duplicate active sessions?
3. Can run sessions bypass backlog and create tasks directly?
4. Can active sessions wake idle projects without making every project runnable?
5. Are paused/stopped sessions persisted, not only stored in memory?
6. Does Supervisor restart preserve active run sessions?
7. Does dashboard avoid leaking project-specific task/backlog/goal titles?
8. Do tests fail if scheduler wake-up is reverted?
9. Do tests fail if record_run_step stops applying idle/max-iteration caps?
10. Does clean-wheel smoke prove installed-wheel behavior without PYTHONPATH?
```

- [ ] **Step 4:** Commit:

```bash
git add docs/autonomous-loop.md docs/cli.md \
  docs/superpowers/handoffs/2026-06-28-phase6c-acceptance.md \
  docs/superpowers/handoffs/2026-06-28-phase6c-gemini-review.md
git commit -m "docs: document Phase 6C autonomous run controller"
```

---

### Task 7: Codex Final Gate F

**Owner:** Codex

Run after Gemini returns PASS or after Grok fixes Gemini blockers.

```bash
git diff --check
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_runs \
  tests.test_phase6c_autonomous_run_e2e \
  tests.test_multi_project_supervisor \
  tests.test_supervisor_methods \
  tests.test_cli_prompt \
  tests.test_phase5_5_dashboard \
  tests.test_phase6_autonomous_loop_e2e -v
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
python3 -m build
```

Clean-wheel smoke must also run from an installed wheel with no `PYTHONPATH`.

Codex should create:

- `docs/superpowers/handoffs/2026-06-28-phase6c-codex-signoff.md`

Reject if:

- Task 6 Gemini review is missing or not PASS.
- Full suite fails.
- Clean-wheel smoke needs `PYTHONPATH`.
- Starting a run can spin without backoff.
- Active sessions are lost after Supervisor restart.
- Dashboard leaks cross-project titles/reasons.

---

## 8. Suggested Execution Order

1. Task 0 red tests.
2. Gate A by Codex.
3. Task 1 migration and DB helpers.
4. Gate B by Codex.
5. Task 2 runtime controller.
6. Gate C by Codex.
7. Task 3 Supervisor scheduler/RPC.
8. Gate D by Codex.
9. Task 4 CLI/TUI/dashboard surfaces.
10. Gate E by Codex.
11. Task 5 restart persistence.
12. Task 6 docs and Gemini review.
13. Task 7 Codex final Gate F.

---

## 9. Self-Review Checklist

- [x] Plan keeps one Supervisor process.
- [x] Plan does not let run sessions directly create tasks.
- [x] Plan includes durable DB state for pause/resume/stop.
- [x] Plan includes scheduler wake-up for idle autonomous projects.
- [x] Plan includes backoff and hard stop caps.
- [x] Plan includes restart persistence.
- [x] Plan assigns Grok implementation and Gemini review only.
- [x] Plan includes Codex gates and clean-wheel smoke.
