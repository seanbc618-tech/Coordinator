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

## Self-sustaining generation

When autonomy is enabled and an active goal has no ready backlog, Coordinator
asks Commander for up to N small task proposals. These proposals become backlog
items first. They are not admitted as worker tasks until a later loop iteration.

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
commander_generation_timeout_seconds = 45
wait_when_running = true                 # wait if serial task is running
require_evaluation_before_followup = true
pause_after_consecutive_failures = 3     # pause goal after N fails
```

Per-repo opt-in (required even when global autonomy is enabled):

```toml
# repos.toml
[[repos]]
path = "/Users/xiafan/polymarket-crypto-threshold"
autonomy_enabled = true
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

## Autonomous run sessions (Phase 6C)

Phase 6C adds durable **run sessions** so Coordinator can keep working while you
step away. A run session is not the same as a manual `/loop step`:

| Action | Starts a run session? | Behavior |
|--------|----------------------|----------|
| `/loop step` | No | One bounded iteration, then stops |
| `/loop start` | Yes | Supervisor keeps ticking until caps/stop |

Recommended first sequence on a real project:

```bash
coordinator --print -p "/loop"          # confirm autonomy + active goal
coordinator --print -p "/loop start"    # begin unattended session
coordinator --print -p "/loop run"      # inspect run id, iterations, idle count
coordinator --print -p "/loop pause"    # pause without losing session state
coordinator --print -p "/loop resume"   # resume ticking
coordinator --print -p "/loop stop"     # operator stop with durable reason
```

Stop conditions (persisted in `autonomous_run_sessions`):

- `max iterations reached`
- `max runtime reached`
- `idle limit reached` (repeated wait with no backlog/goal/work)
- operator `/loop stop`
- loop decisions `pause`, `blocked`, or `complete`

Active run sessions wake the Supervisor scheduler even when no worker task is
ready yet. Idle ticks apply `idle_backoff_seconds` before the next attempt.

## Slash commands

| Command | RPC method | Description |
|---------|------------|-------------|
| `/loop` | `project.loop.status` | Active goal, last decision, backlog counts, active run |
| `/backlog` | `project.backlog` | Latest backlog items with status |
| `/evals` | `project.evaluations` | Latest task evaluations |
| `/loop step` | `project.loop.step` | Run one bounded iteration (not a run session) |
| `/loop start` | `project.loop.start` | Start unattended autonomous run session |
| `/loop stop` | `project.loop.stop` | Stop active run (`reason: operator stop`) |
| `/loop pause` | `project.loop.pause` | Pause active run session |
| `/loop resume` | `project.loop.resume` | Resume paused run session |
| `/loop run` | `project.loop.run.status` | Show active run counters only |

Operator examples:

```bash
coordinator --print -p "/loop"
coordinator --print -p "/loop start"
coordinator --print -p "/loop run"
coordinator --print -p "/loop step"
coordinator --print -p "/backlog"
```

After Commander generation, `/loop` may show:

```text
Loop status [proj-example]
  run: running run-abc123, iterations=3, idle=1
  last: generate — generated 1 backlog draft(s)
```

## Failure modes

| Condition | Behavior |
|-----------|----------|
| Duplicate backlog item | Rejected silently (dedupe_key match) |
| Human review required | Iteration pauses; `/loop` shows reason |
| Repeated failures (3+) | Goal paused; requires operator resume |
| Budget exhausted | Iteration waits; `/loop` shows cap reached |
| No active goal | Iteration waits; `/loop` shows "no active goal" |
| Running task + serial policy | Iteration waits; `/loop` shows running task id |

## Data model

Migration 014:

- `project_backlog_items` — durable backlog with dedupe
- `task_evaluations` — one evaluation per task/evaluator pair
- `loop_iterations` — every decision is persisted with reason

Migration 015 (Phase 6C):

- `autonomous_run_sessions` — durable run controller state per project
- `autonomous_run_steps` — per-tick audit trail linked to loop iterations

## Disabling autonomy

Per-repo: set `autonomy_enabled = false` in `repos.toml`.

Globally: set `enabled = false` in `[autonomy]` section of `policy.toml`.

Disabling takes effect on the next supervisor tick. Running tasks are not
affected.
