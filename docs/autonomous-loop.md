# Autonomous Loop Core

Phase 6 turns Coordinator from an interactive task launcher into a durable
autonomous project loop: active goal → small backlog → isolated worker tasks →
independent evaluation → memory/update → next task.

## How it works

Each autonomous iteration (one tick) follows this decision order:

1. **No active goal?** → `wait`
2. **Project paused/stopped?** → `wait`
3. **Running task + serial policy?** → `wait`
4. **Evaluate terminal tasks** — rule-based verdict on completed/failed work
5. **Human review required?** → `pause`
6. **Ready backlog + capacity?** → admit up to cap
7. **Backlog too small + budget?** → ask Commander for 1–3 tiny drafts
8. **No useful action?** → mark goal `blocked` or `complete`

Every iteration records its decision and reason in `loop_iterations`.

## Configuration

### Per-repo opt-in

```toml
# repos.toml
[[repos]]
path = "/Users/xiafan/polymarket-crypto-threshold"
autonomy_enabled = true
```

### Global defaults

```toml
# policy.toml
[autonomy]
enabled = false                          # master switch (off by default)
max_iterations_per_tick = 1              # iterations per supervisor tick
max_evaluations_per_iteration = 3        # terminal tasks to evaluate
max_admissions_per_iteration = 1         # backlog items to admit
max_generated_backlog_per_iteration = 3  # Commander proposals to request
wait_when_running = true                 # wait if serial task is running
require_evaluation_before_followup = true
pause_after_consecutive_failures = 3     # pause goal after N fails
```

## Backlog

Backlog items are small, bounded proposals from three sources:

- `operator` — human-created via CLI
- `commander` — LLM-generated proposals
- `evaluator` — follow-up items from completed task evaluation

Each item has a `dedupe_key` (case-insensitive hash of title + acceptance
criteria). Duplicate open items are rejected automatically.

Items flow: `candidate` → `ready` → `admitted` (linked to a real task).

## Evaluation

Terminal tasks (done/failed/blocked) get exactly one evaluation per evaluator
(default: `rules-v1`). The evaluator is rule-based in Phase 6A — no LLM calls.

| Verdict | Meaning | next_action |
|---------|---------|-------------|
| `pass` | Task completed, verification passed | `none` |
| `fail` | Task failed or verification failed | `admit_followup` or `ask_commander` |
| `needs_followup` | Completed but produced a next step | `admit_followup` |
| `blocked` | Missing config, budget exhausted | `pause_goal` |
| `human_review` | Touches merge/push/credentials/funds | `human_review` |

## Slash commands

| Command | RPC method | Description |
|---------|------------|-------------|
| `/loop` | `project.loop.status` | Active goal, last decision, backlog counts |
| `/backlog` | `project.backlog` | Latest backlog items with status |
| `/evals` | `project.evaluations` | Latest task evaluations |
| `/loop step` | `project.loop.step` | Run one bounded iteration |

## Failure modes

| Condition | Behavior |
|-----------|----------|
| Duplicate backlog item | Rejected silently (dedupe_key match) |
| Human review required | Iteration pauses; `/loop` shows reason |
| Repeated failures (3+) | Goal paused; requires operator resume |
| Budget exhausted | Iteration waits; `/loop` shows cap reached |
| No active goal | Iteration waits; `/loop` shows "no active goal" |
| Running task + serial policy | Iteration waits; `/loop` shows running task id |

## Data model (migration 014)

Three new tables:

- `project_backlog_items` — durable backlog with dedupe
- `task_evaluations` — one evaluation per task/evaluator pair
- `loop_iterations` — every decision is persisted with reason

## Disabling autonomy

Per-repo: set `autonomy_enabled = false` in `repos.toml`.

Globally: set `enabled = false` in `[autonomy]` section of `policy.toml`.

Disabling takes effect on the next supervisor tick. Running tasks are not
affected.
