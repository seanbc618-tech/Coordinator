# Phase 6 Autonomous Loop Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Coordinator from an interactive task launcher into a durable autonomous project loop: active goal -> small backlog -> isolated worker tasks -> independent evaluation -> memory/update -> next task, with strict caps and human-visible control.

**Architecture:** Keep the current single Supervisor process and SQLite store. Add an autonomy layer beside the existing Commander, task engine, and Supervisor scheduler. Do not introduce another service. The Supervisor owns project ticks; the autonomy layer decides whether to evaluate terminal work, admit backlog, ask Commander for more small tasks, pause, or wait. TUI/CLI remain clients of Supervisor RPCs.

**Tech Stack:** Python `unittest`, SQLite migrations mirrored in `migrations/` and `src/local_cli_coordinator/migrations/`, existing Supervisor RPC protocol, current TypeScript Ink TUI only for read/control surfaces.

---

## 0. Current Gap

Phase 5.x made Coordinator usable, but it is still operator-driven:

- The user must ask for tasks.
- Commander can propose/admit tasks, but no durable backlog governs next steps.
- Finished tasks are not independently evaluated before follow-up planning.
- Supervisor can run project tasks, but it does not own a complete "keep working until goal is done" loop.
- TUI/log improvements help observation, but do not make the system more autonomous.

Phase 6 must prioritize the loop core over more UI polish.

---

## 1. Non-Negotiable Constraints

- One global Supervisor process; no multi-service architecture.
- Repo allowlist and per-repo policy remain authoritative.
- Every generated task must be small:
  - one repo,
  - one purpose,
  - bounded verification,
  - expected runtime <= configured cap,
  - no broad refactors unless explicitly requested.
- Autonomous admission must stop when:
  - budget is exhausted,
  - a project has a running task and policy says wait,
  - repeated failures exceed threshold,
  - evaluation says human review is required,
  - the goal is complete or blocked.
- Independent evaluation is required before the loop uses a task as successful evidence.
- No hidden infinite loops: every autonomous iteration records why it acted or why it waited.
- Gemini must review for false-green tests, rubber-stamp evaluation, duplicate task admission, and budget bypass.

---

## 2. Ownership

| Role | Responsibility |
| --- | --- |
| **Grok** | Main implementer. Owns production modules, migrations, Supervisor integration, final fixes. One coherent commit per task. |
| **Claude Code** | Auxiliary. Writes red tests, deterministic fixtures, docs, handoff records, and low-risk CLI/TUI display updates. Avoid giving Claude deep scheduler/evaluator logic. |
| **Gemini / .pi agent** | Adversarial reviewer. No production edits unless explicitly assigned. Reviews for false greens, concurrency holes, cross-project leaks, and UX/product mismatch. |
| **Codex** | Gate owner. Signs off Gate A/B/C/D/E, rejects vague or overbroad changes, keeps scope aligned with the original autonomous coordinator goal. |

---

## 3. New Data Model

Add migration `014_autonomous_loop_core.sql` in both migration roots:

- `migrations/014_autonomous_loop_core.sql`
- `src/local_cli_coordinator/migrations/014_autonomous_loop_core.sql`

Schema:

```sql
CREATE TABLE IF NOT EXISTS project_backlog_items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
    verification_commands_json TEXT NOT NULL DEFAULT '[]',
    execution_policy TEXT NOT NULL DEFAULT 'normal',
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'candidate',
    dedupe_key TEXT NOT NULL,
    linked_task_id TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    admitted_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(goal_id) REFERENCES goals(id),
    FOREIGN KEY(linked_task_id) REFERENCES tasks(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_backlog_dedupe_open
ON project_backlog_items(project_id, goal_id, dedupe_key)
WHERE status IN ('candidate', 'ready', 'admitted');

CREATE INDEX IF NOT EXISTS idx_project_backlog_project_status
ON project_backlog_items(project_id, status, priority, created_at);

CREATE TABLE IF NOT EXISTS task_evaluations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    task_id TEXT NOT NULL,
    evaluator_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    next_action TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, evaluator_id)
);

CREATE INDEX IF NOT EXISTS idx_task_evaluations_project
ON task_evaluations(project_id, goal_id, created_at);

CREATE TABLE IF NOT EXISTS loop_iterations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    goal_id INTEGER,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    evaluated_count INTEGER NOT NULL DEFAULT 0,
    admitted_count INTEGER NOT NULL DEFAULT 0,
    generated_count INTEGER NOT NULL DEFAULT 0,
    caps_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_loop_iterations_project
ON loop_iterations(project_id, goal_id, started_at);
```

Allowed enum values:

- `project_backlog_items.source`: `operator`, `commander`, `evaluator`
- `project_backlog_items.status`: `candidate`, `ready`, `admitted`, `rejected`, `done`, `superseded`
- `task_evaluations.verdict`: `pass`, `fail`, `needs_followup`, `blocked`, `human_review`
- `task_evaluations.next_action`: `none`, `admit_followup`, `ask_commander`, `pause_goal`, `human_review`
- `loop_iterations.decision`: `wait`, `evaluate`, `admit`, `generate`, `pause`, `complete`, `blocked`

---

## 4. New Modules

### 4.1 `src/local_cli_coordinator/autonomous_backlog.py`

Responsibilities:

- Normalize Commander/evaluator proposals into backlog items.
- Reject duplicate open work.
- Enforce small-task caps before a task can become `ready`.
- Promote one backlog item into a real task via existing `db.create_task()`.

Required public API:

```python
@dataclass(frozen=True)
class BacklogDraft:
    source: str
    title: str
    rationale: str
    acceptance_criteria: list[str]
    verification_commands: list[str]
    execution_policy: str = "normal"
    priority: int = 50


def compute_backlog_dedupe_key(title: str, acceptance_criteria: Sequence[str]) -> str:
    """Return a stable lowercase hash key for duplicate open-work detection."""


def propose_backlog_items(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    drafts: Sequence[BacklogDraft],
) -> list[str]:
    """Insert non-duplicate candidate/ready items and return inserted ids."""


def promote_next_backlog_item(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    goal_id: int | None,
    repo_path: Path,
    max_items: int = 1,
) -> list[str]:
    """Create ready tasks from backlog and mark items admitted."""
```

Use existing task creation path only:

```python
task_id = create_task(
    conn,
    title=item.title,
    goal=item.rationale,
    repo=str(repo_path),
    acceptance_criteria=criteria,
    verification_commands=verify_commands,
    project_id=project_id,
    execution_policy=item.execution_policy,
)
```

### 4.2 `src/local_cli_coordinator/evaluator.py`

Responsibilities:

- Detect terminal tasks that have not been evaluated.
- Record one deterministic evaluation per task.
- Decide whether follow-up is needed.
- Never call external LLMs in Phase 6A. This is a rule-based evaluator first.

Required public API:

```python
@dataclass(frozen=True)
class TaskEvaluation:
    task_id: str
    project_id: str
    goal_id: int | None
    verdict: str
    summary: str
    evidence: dict[str, Any]
    next_action: str


def find_unevaluated_terminal_tasks(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int,
) -> list[str]:
    """Return terminal project task ids without a rules-v1 evaluation."""


def evaluate_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    evaluator_id: str = "rules-v1",
) -> TaskEvaluation:
    """Build a deterministic evaluation from task state, events, and artifacts."""


def record_task_evaluation(
    conn: sqlite3.Connection,
    evaluation: TaskEvaluation,
) -> str:
    """Persist exactly one evaluation per task/evaluator pair."""
```

Phase 6A verdict rules:

- `pass`: task reached accepted terminal state and verification passed or the task is explicitly read-only/report-only with evidence.
- `fail`: task failed, verifier failed, or worker returned success while required evidence is missing.
- `needs_followup`: task completed but produced a bounded next step or a known failure point.
- `blocked`: missing configuration, unavailable repo, budget/cap prevents progress.
- `human_review`: task touches merge/push, credentials, trading, funds, or policy-required human review.

### 4.3 `src/local_cli_coordinator/loop_autonomy.py`

Responsibilities:

- Run one autonomous decision iteration for one project.
- Keep the iteration bounded and observable.
- Feed the Supervisor with ready tasks only when policy allows.

Public API:

```python
@dataclass(frozen=True)
class LoopDecision:
    project_id: str
    goal_id: int | None
    decision: str
    reason: str
    evaluated_count: int = 0
    admitted_task_ids: tuple[str, ...] = ()
    generated_backlog_ids: tuple[str, ...] = ()


def run_autonomous_iteration(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    config: CoordinatorConfig,
    paths: RuntimePaths,
    max_evaluations: int,
    max_admissions: int,
) -> LoopDecision:
    """Run one bounded decision cycle and persist a loop_iterations row."""
```

Decision order:

1. If no active goal: `wait`.
2. If project is paused/stopped: `wait`.
3. If running tasks exist and policy says serial: `wait`.
4. Evaluate terminal unevaluated tasks.
5. If evaluation requires human review: `pause`.
6. If there are ready backlog items and capacity exists: admit up to cap.
7. If backlog is too small and budget allows: ask Commander for 1-3 tiny backlog drafts.
8. If no useful next action: mark goal `blocked` or `complete` with reason.

---

## 5. Config

Extend policy config with safe defaults:

```toml
[autonomy]
enabled = false
max_iterations_per_tick = 1
max_evaluations_per_iteration = 3
max_admissions_per_iteration = 1
max_generated_backlog_per_iteration = 3
wait_when_running = true
require_evaluation_before_followup = true
pause_after_consecutive_failures = 3
```

Per repo override:

```toml
[[repos]]
path = "/Users/xiafan/polymarket-crypto-threshold"
autonomy_enabled = true
```

The global default remains off. The project can opt in.

---

## 6. RPC and CLI Surfaces

Add read/control methods to `supervisor_methods.py`:

| Method | Purpose |
| --- | --- |
| `project.loop.status` | Active goal, last iteration decision, backlog counts, unevaluated terminal count, caps. |
| `project.backlog` | List latest backlog items with status and linked task ids. |
| `project.evaluations` | List latest task evaluations. |
| `project.loop.step` | Run one bounded autonomous iteration. Disabled unless project autonomy is enabled or `force=true` and policy allows. |

CLI/TUI slash mapping can be minimal:

- `/loop` -> `project.loop.status`
- `/backlog` -> `project.backlog`
- `/evals` -> `project.evaluations`
- `/loop step` -> `project.loop.step`

Do not build a new dashboard panel in Phase 6 unless needed for tests. Text output is enough.

---

## 7. Task Breakdown

### Task 0 — Claude Code: Red Tests and Fixtures

Owner: Claude Code  
Commit message: `test: capture Phase 6 autonomous loop contracts`

- [ ] Add `tests/test_autonomous_backlog.py`.
- [ ] Add `tests/test_task_evaluator.py`.
- [ ] Add `tests/test_loop_autonomy.py`.
- [ ] Add `tests/test_phase6_autonomous_loop_e2e.py`.
- [ ] Add failing assertions only; do not implement production logic.

Required red test names:

- `test_backlog_dedupes_duplicate_open_items`
- `test_backlog_promote_creates_project_task`
- `test_evaluator_records_terminal_task_once`
- `test_evaluator_flags_failed_task_as_followup`
- `test_loop_waits_when_project_has_running_task`
- `test_loop_admits_one_backlog_item_when_idle`
- `test_loop_records_every_iteration_reason`
- `test_project_loop_status_is_project_scoped`

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_autonomous_backlog \
  tests.test_task_evaluator \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e -v
```

Expected before implementation: tests fail with missing modules/methods.

### Task 1 — Grok: Migration 014 and Data Access

Owner: Grok  
Commit message: `feat: add autonomous loop persistence`

- [ ] Add mirrored migration `014_autonomous_loop_core.sql`.
- [ ] Add data helpers to `db.py` or a small dedicated module.
- [ ] Update migration mirror test expectations if needed.
- [ ] Ensure wheel migration tests still run without `PYTHONPATH`.

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_migration_mirror_sync \
  tests.test_wheel_migrations \
  tests.test_autonomous_backlog -v
```

Codex Gate A reviews this task.

### Task 2 — Grok: Backlog Governance

Owner: Grok  
Commit message: `feat: govern autonomous project backlog`

- [ ] Implement `autonomous_backlog.py`.
- [ ] Enforce duplicate open item rejection.
- [ ] Enforce small-task caps before `ready`.
- [ ] Promote ready backlog items via `create_task()` only.
- [ ] Publish `task.created` event through existing path, not manual event insertion.

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_autonomous_backlog tests.test_commander_policy -v
```

Gemini review focus:

- duplicate task loopholes,
- hand INSERT bypass,
- cross-project leakage,
- admitting broad work as "small".

### Task 3 — Grok: Rule-Based Independent Evaluator

Owner: Grok  
Commit message: `feat: evaluate terminal tasks before follow-up`

- [ ] Implement `evaluator.py`.
- [ ] Use task rows, events, artifacts, verifier results, and execution policy.
- [ ] Record one evaluation per `(task_id, evaluator_id)`.
- [ ] Expose evidence as JSON, not prose-only logs.
- [ ] Do not call Commander/LLM here.

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_task_evaluator tests.test_done_gate tests.test_engine -v
```

Codex Gate B reviews this task.

### Task 4 — Grok: Autonomous Iteration Engine

Owner: Grok  
Commit message: `feat: run bounded autonomous loop iterations`

- [ ] Implement `loop_autonomy.py`.
- [ ] Decision order must match Section 4.3.
- [ ] Every iteration writes `loop_iterations`.
- [ ] The iteration must be idempotent under immediate retry.
- [ ] It must not admit new tasks while serial policy sees running tasks.
- [ ] It must respect `max_evaluations` and `max_admissions`.

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_loop_autonomy tests.test_autonomous_backlog tests.test_task_evaluator -v
```

Codex Gate C reviews this task.

### Task 5 — Grok: Supervisor Integration

Owner: Grok  
Commit message: `feat: integrate autonomous iteration into supervisor ticks`

- [ ] Wire autonomy into `MultiProjectSupervisor.tick()` before scheduling work.
- [ ] Only run for registered projects with autonomy enabled.
- [ ] Preserve existing task scheduling fairness.
- [ ] Preserve manual `/task retry`, `/task approve`, `/cancel` behavior.
- [ ] Emit supervisor events:
  - `loop.iteration`
  - `task.evaluated`
  - `backlog.item`
- [ ] Never block socket clients for a long Commander run. Commander generation must be capped and recorded.

Run:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_multi_project_supervisor \
  tests.test_phase2_gate \
  tests.test_loop_autonomy \
  tests.test_phase6_autonomous_loop_e2e -v
```

Codex Gate D reviews this task.

### Task 6 — Grok: RPC and Minimal Slash Commands

Owner: Grok  
Commit message: `feat: expose autonomous loop status and backlog`

- [ ] Add RPC methods:
  - `project.loop.status`
  - `project.backlog`
  - `project.evaluations`
  - `project.loop.step`
- [ ] Add slash mappings:
  - `/loop`
  - `/backlog`
  - `/evals`
  - `/loop step`
- [ ] Keep response formatting compact and scannable.
- [ ] Include last iteration reason and next expected action.

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods tests.test_cli_prompt -v
npm test --prefix ui-tui -- --run
```

### Task 7 — Claude Code: Docs, Handoff, and Smoke Script

Owner: Claude Code  
Commit message: `docs: document autonomous loop core`

- [ ] Update `docs/cli.md`.
- [ ] Add `docs/autonomous-loop.md`.
- [ ] Add handoff `docs/superpowers/handoffs/2026-06-26-phase6-autonomous-loop-acceptance.md`.
- [ ] Include a real smoke sequence for `/Users/xiafan/polymarket-crypto-threshold`.
- [ ] Document how to turn autonomy on/off per repo.
- [ ] Document failure modes:
  - duplicate backlog,
  - human review pause,
  - repeated failure pause,
  - budget exhausted,
  - no active goal.

Smoke sequence:

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator --print -p "/loop"
coordinator --print -p "/backlog"
coordinator --print -p "/evals"
coordinator --print -p "/loop step"
```

### Task 8 — Gemini: Adversarial Review

Owner: Gemini / .pi agent  
Commit message if documenting only: `docs: record Phase 6 adversarial review`

Review exactly:

- Does the loop admit duplicate work under repeated ticks?
- Can a terminal failed task be treated as success?
- Can `project.loop.step` affect another project?
- Can evaluation be bypassed by direct backlog admission?
- Can budget/cap checks be bypassed by Commander-generated tasks?
- Does any test pass with no production path exercised?
- Does Supervisor remain responsive while autonomy is active?
- Does wheel install include migration 014?

Expected output:

```text
VERDICT: PASS | CONDITIONAL PASS | FAIL
P0:
P1:
P2:
Blocking merge: yes/no
```

Save review at:

`docs/superpowers/handoffs/2026-06-26-phase6-gemini-review.md`

### Task 9 — Codex: Final Gate E

Owner: Codex  
Commit message if signoff doc only: `docs: sign off Phase 6 autonomous loop core`

Run:

```bash
git diff --check
PYTHONWARNINGS=error::ResourceWarning PYTHONPATH=src python3 -m unittest discover -s tests -q
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
python3 -m build
PYTHONPATH=src python3 -m unittest tests.test_wheel_migrations -v
```

Then run a clean-wheel smoke:

```bash
tmpdir="$(mktemp -d)"
python3 -m venv "$tmpdir/venv"
"$tmpdir/venv/bin/pip" install dist/*.whl
cd /Users/xiafan/polymarket-crypto-threshold
"$tmpdir/venv/bin/coordinator" --print -p "/loop"
```

Signoff requires:

- all tests pass,
- migration 014 exists in installed wheel,
- no ResourceWarning,
- `/loop` works from a real project directory,
- one forced `/loop step` records exactly one iteration and does not duplicate tasks.

---

## 8. Acceptance Criteria

Phase 6 is complete only when all are true:

- A project with an active goal can run one autonomous iteration without user chat.
- Terminal tasks get exactly one independent evaluation.
- The loop can admit one next small task from backlog when idle.
- Duplicate backlog/task admission is prevented across repeated ticks.
- Supervisor remains responsive to `/status`, `/tasks`, and `/loop` while autonomy is active.
- Per-repo autonomy can be disabled immediately.
- All decisions are visible in persisted `loop_iterations` and client events.
- The real polymarket repo smoke proves the installed `coordinator` command can show loop state from the project directory.

---

## 9. Explicit Non-Goals

- No new web UI.
- No separate autonomy daemon.
- No full LLM evaluator in Phase 6A.
- No automatic merge-to-main expansion beyond existing repo policy.
- No broad self-modification of Coordinator itself unless the active project is Coordinator and policy permits it.
- No attempt to solve multi-day strategic planning yet. Phase 6 creates the mechanical loop core; strategy quality can improve in Phase 7.

---

## 10. Suggested Phase 7 Backlog

After Phase 6 lands:

- LLM-backed evaluator with rule-based fallback.
- Project health model: recurring checks, stale dependency checks, CI drift.
- Long-horizon milestone planner over backlog.
- Agent performance scoring and automatic routing.
- Better TUI dashboard panel for loop reasoning and backlog.
- Scheduled overnight mode with quiet hours and notification summaries.
