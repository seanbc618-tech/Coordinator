# Multi-Project Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run isolated project loops fairly under one Supervisor with shared agent concurrency, budgets, leases, and replayable event streams.

**Architecture:** Add project scope to persisted execution records, then place a fair scheduler and subscription service above the existing engine. Existing single-root engine behavior remains usable through a project runtime adapter.

**Tech Stack:** Python 3.11+, sqlite3, threading, Unix sockets, unittest.

---

## Ownership and Order

- Claude Code: Tasks 1, 2, and 5.
- Grok: Tasks 3, 4, and 6.
- Claude Code: Task 7 integration.
- Codex: review after Tasks 2, 4, and 7.
- Start only after Phase 1 acceptance.

### Task 1: Add Project Scope to Persistence

**Files:**
- Create: `migrations/009_project_scope.sql`
- Modify: `src/local_cli_coordinator/db.py`
- Create: `tests/test_project_scope.py`
- Modify: `tests/helpers.py`

- [ ] **Step 1: Write failing isolation tests**

Create two projects and overlapping task titles. Assert task counts, ready-task
selection, events, goals, daemon runs, leases, and artifacts can be queried by
project without returning rows from the other project. Also assert the migration
backfills legacy rows into a deterministic legacy project.

- [ ] **Step 2: Run tests and confirm schema failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_project_scope -v`
Expected: FAIL because project scope columns do not exist.

- [ ] **Step 3: Add migration 009**

Add nullable `project_id` references to execution tables, insert a
`legacy-default` project when legacy data exists, backfill all old rows, and add
indexes beginning with `project_id` for task state, event cursor, goal status,
lease expiry, and daemon run time. SQLite foreign-key compatibility must be tested
against a database upgraded through migration 008.

- [ ] **Step 4: Add explicit scoped database APIs**

Add `project_id` as a required keyword argument to new APIs:

~~~python
def project_task_counts(conn, *, project_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select state, count(*) count from tasks where project_id = ? group by state",
        (project_id,),
    ).fetchall()
    return {row["state"]: row["count"] for row in rows}
~~~

Provide scoped counterparts for create/list/claim task, goals, events, leases,
runs, and artifacts. Keep legacy unscoped functions only as compatibility
wrappers that raise when more than one active project exists.

- [ ] **Step 5: Run focused and migration tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_project_scope tests.test_db -v`
Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add migrations/009_project_scope.sql src/local_cli_coordinator/db.py tests/test_project_scope.py tests/helpers.py
git commit -m "feat: scope Coordinator state by project"
~~~

### Task 2: Introduce Project Runtime Adapters

**Files:**
- Create: `src/local_cli_coordinator/project_runtime.py`
- Create: `tests/test_project_runtime.py`
- Modify: `src/local_cli_coordinator/engine.py`

- [ ] **Step 1: Write failing adapter tests**

Verify that a `ProjectRuntime` loads the registered repo config, maps global run
paths into `data_dir/projects/<project_id>/`, invokes one existing daemon cycle
with project-scoped database operations, and never changes process cwd globally.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_project_runtime -v`
Expected: FAIL.

- [ ] **Step 3: Implement the runtime value object**

~~~python
@dataclass(frozen=True)
class ProjectRuntime:
    project_id: str
    repo_root: Path
    state_root: Path
    config: CoordinatorConfig

    @property
    def runs_dir(self) -> Path:
        return self.state_root / "projects" / self.project_id / "runs"
~~~

Extract a `run_project_cycle(conn, runtime, reporter)` entry point from
`run_daemon_cycle`. The existing function constructs a compatibility runtime and
delegates, preserving current tests.

- [ ] **Step 4: Run engine regressions**

Run: `PYTHONPATH=src python3 -m unittest tests.test_project_runtime tests.test_loop_e2e tests.test_engine -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/project_runtime.py src/local_cli_coordinator/engine.py tests/test_project_runtime.py
git commit -m "refactor: isolate project loop runtime"
~~~

### Task 3: Build Fair Project Scheduling

**Files:**
- Create: `src/local_cli_coordinator/supervisor_scheduler.py`
- Create: `tests/test_supervisor_scheduler.py`

- [ ] **Step 1: Write deterministic scheduler tests**

Use a fake clock and fake runtimes. Verify round-robin order A/B/C/A, skipping
paused and circuit-broken projects, per-project concurrency, global concurrency,
agent capacity, no starvation after one project replenishes repeatedly, and clean
idle waiting.

- [ ] **Step 2: Run tests and confirm import failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_scheduler -v`
Expected: FAIL.

- [ ] **Step 3: Implement a small scheduler**

~~~python
@dataclass(frozen=True)
class ScheduleDecision:
    project_id: str
    reason: str = "ready"


class FairProjectScheduler:
    def __init__(self, project_ids: list[str]) -> None:
        self._order = deque(project_ids)

    def next(self, is_runnable: Callable[[str], bool]) -> ScheduleDecision | None:
        for _ in range(len(self._order)):
            project_id = self._order[0]
            self._order.rotate(-1)
            if is_runnable(project_id):
                return ScheduleDecision(project_id)
        return None
~~~

Keep policy evaluation outside the rotation primitive. Add
`SupervisorScheduler.tick()` to combine registry state, budgets, concurrency, and
`run_project_cycle`.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_scheduler -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor_scheduler.py tests/test_supervisor_scheduler.py
git commit -m "feat: schedule project loops fairly"
~~~

### Task 4: Persist and Replay Project Events

**Files:**
- Create: `migrations/010_supervisor_events.sql`
- Create: `src/local_cli_coordinator/supervisor_events.py`
- Create: `tests/test_supervisor_events.py`

- [ ] **Step 1: Write failing event tests**

Verify per-project monotonic cursors, ordered replay after a cursor, project
isolation, bounded payload size, subscriber queue overflow, reconnect without
duplicates, and transaction rollback without cursor gaps being exposed.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_events -v`
Expected: FAIL.

- [ ] **Step 3: Add the event stream table and broker**

The table stores project ID, integer cursor, event type, JSON payload, and time,
with a unique project/cursor key.

`EventBroker.publish(conn, project_id, event_type, payload)` allocates the next
cursor and inserts the event in one immediate transaction, commits, then notifies
bounded subscriber queues. `EventBroker.replay(conn, project_id, after,
limit=1000)` queries only that project with `cursor > after`, ordered ascending,
and returns validated `EventEnvelope` values.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_events -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add migrations/010_supervisor_events.sql src/local_cli_coordinator/supervisor_events.py tests/test_supervisor_events.py
git commit -m "feat: persist replayable project events"
~~~

### Task 5: Enforce Shared Agent Capacity and Budgets

**Files:**
- Create: `src/local_cli_coordinator/supervisor_capacity.py`
- Create: `tests/test_supervisor_capacity.py`
- Modify: `src/local_cli_coordinator/engine.py`

- [ ] **Step 1: Write failing capacity tests**

Verify global running-task cap, per-project cap, existing per-agent concurrency,
daily task budget across projects, runtime accounting, release after exceptions,
and that one fallback attempt remains one task but adds runtime.

- [ ] **Step 2: Run tests and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_capacity -v`
Expected: FAIL.

- [ ] **Step 3: Implement transactional capacity leases**

Expose `try_acquire_capacity` and `release_capacity` using database transactions.
A capacity lease records project, task, agent, acquired time, and expiry. The
scheduler must acquire before starting a worker thread and release in `finally`.

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_capacity tests.test_task_leases tests.test_circuit_breaker -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor_capacity.py src/local_cli_coordinator/engine.py tests/test_supervisor_capacity.py
git commit -m "feat: enforce shared Supervisor capacity"
~~~

### Task 6: Add Multi-Client Supervisor Methods

**Files:**
- Modify: `src/local_cli_coordinator/supervisor_server.py`
- Create: `src/local_cli_coordinator/supervisor_methods.py`
- Create: `tests/test_supervisor_methods.py`

- [ ] **Step 1: Write failing request tests**

Cover `project.status`, `chat.send`, `project.pause`, `project.resume`,
`project.stop`, `events.subscribe`, and `events.replay`. Verify authorization by
project ID, two clients on one project, and clients on separate projects.

- [ ] **Step 2: Run tests and confirm method-not-found responses**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods -v`
Expected: FAIL.

- [ ] **Step 3: Implement the method registry**

Use a dictionary of explicit method names to handler callables. Every project
method first resolves an active registered project. `chat.send` calls the existing
Commander service through a project-scoped connection. Subscription writes events
only to that client and uses bounded queues.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods tests.test_supervisor_server -v`
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor_server.py src/local_cli_coordinator/supervisor_methods.py tests/test_supervisor_methods.py
git commit -m "feat: serve project-scoped Supervisor methods"
~~~

### Task 7: Run the Multi-Project Supervisor Loop

**Files:**
- Create: `src/local_cli_coordinator/supervisor.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_multi_project_supervisor.py`
- Modify: `README.md`

- [ ] **Step 1: Write an end-to-end failing test**

Create three temporary Git repositories and projects, enqueue one fake task per
project, connect two clients, run a bounded Supervisor, and assert fair processing,
isolated events, detach-safe execution, and no duplicate work after restart.

- [ ] **Step 2: Run test and confirm failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_multi_project_supervisor -v`
Expected: FAIL.

- [ ] **Step 3: Compose server, scheduler, broker, and runtime**

`Supervisor.run()` owns the socket thread, scheduler tick loop, worker executor,
and graceful shutdown event. It records lifecycle events and joins workers at a
safe boundary. Add diagnostic status fields for projects, clients, active work,
budget, and uptime.

- [ ] **Step 4: Update administrative status and docs**

`coordinator supervisor status` prints process and per-project summaries.
Document one Supervisor, multi-project behavior, stop semantics, and temporary
COORDINATOR_HOME testing.

- [ ] **Step 5: Run phase verification**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_project_scope tests.test_project_runtime tests.test_supervisor_scheduler tests.test_supervisor_events tests.test_supervisor_capacity tests.test_supervisor_methods tests.test_multi_project_supervisor -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
~~~

Expected: all tests PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/local_cli_coordinator/supervisor.py src/local_cli_coordinator/cli.py tests/test_multi_project_supervisor.py README.md
git commit -m "feat: run isolated multi-project loops"
~~~

## Phase 2 Acceptance

- Run three fake projects concurrently for at least 50 scheduler ticks.
- Confirm no project waits behind more than two other runnable projects.
- Attach and detach clients while workers run.
- Restart the Supervisor and confirm no duplicate task commits.
- Inspect every database query added in this phase for project scoping.
- Codex reviews migration compatibility and concurrency failure paths.
