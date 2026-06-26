# Codex Commander Design

> Date: 2026-06-19
> Status: Approved design
> Scope: Conversational goal intake and autonomous queue replenishment

## Problem

The Coordinator currently executes ready tasks reliably, but it has no
conversational front door and cannot decide what work should enter an empty
queue. Operators must create task Markdown manually, and an empty queue is
reported only as `no ready tasks`. This leaves the execution engine without a
product-level coordinator.

## Decisions

- Codex CLI is the Commander agent.
- Commander is read-only and never edits a managed repository.
- Workers remain responsible for code changes in isolated worktrees.
- The rule engine remains the final authority over task admission.
- A new long-term goal requires one plan confirmation before execution.
- After confirmation, queue replenishment is fully automatic until the goal
  completes, pauses, blocks, or reaches a circuit breaker.
- Durable Coordinator state, rather than a Codex session, provides memory.

## User Experience

### Conversational entry

```bash
coordinator chat
```

The operator can describe a goal in natural language. Commander inspects the
allowlisted repository, roadmap, current task state, and safety constraints,
then previews the first batch of small tasks. The operator enters `/start` once
to activate the goal. Later batches do not require confirmation.

The chat REPL supports:

- `/status`: show the active goal, progress, and current batch.
- `/start`: confirm the initial plan and activate the goal.
- `/pause`: stop queue replenishment without interrupting a running task.
- `/resume`: resume queue replenishment.
- `/quit`: leave chat without stopping the daemon.

### One-shot entry

```bash
coordinator goal "Continue the roadmap while preserving dry-run safety"
coordinator goal confirm
coordinator goal status
coordinator goal pause
coordinator goal resume
```

`coordinator goal` creates a draft goal and prints the initial plan. The
operator confirms it once through `coordinator chat` and `/start`, or by
running `coordinator goal confirm`.

### Status behavior

`coordinator status --loop` distinguishes these states:

- waiting for a long-term goal;
- waiting for initial confirmation;
- active with ready or running tasks;
- active and waiting for Commander replenishment;
- paused, blocked, completed, or stopped by a circuit breaker.

An empty queue must not be presented as an unexplained failure.

## Architecture

```text
chat / goal command
        |
        v
Codex Commander (read-only)
        |
        v
structured proposal
        |
        v
rule-engine admission gate
        |
        v
ready task queue
        |
        v
worker -> verification -> reviewers -> commit/push/merge policy
        |
        v
durable result and goal progress
        |
        +----> Commander replenishes the queue
```

### Commander runner

Agent configuration gains a distinct `commander` role. The existing `planner`
role remains available for finding-to-task planning; it is not implicitly given
goal authority. The configured Codex CLI agent is assigned the `commander`
role for this installation.

The Commander runner invokes Codex CLI non-interactively with a read-only
sandbox, no approval escalation, and an ephemeral CLI session. Every call is
given a complete context packet containing:

- the active goal and completion definition;
- immutable safety constraints;
- allowlisted repository metadata;
- repository roadmap and selected memory files;
- task states and recent task outcomes;
- verification and review summaries;
- rejected proposal fingerprints;
- remaining runtime, task, failure, and concurrency budgets.

The runner records command metadata, duration, exit status, timeout status,
raw output, parsed output, and prompt artifacts. Secrets and environment values
must not be copied into Commander memory or prompts.

### Structured output

Commander output is validated against a versioned JSON schema. The minimum
response contract is:

```json
{
  "schema_version": 1,
  "goal_status": "active",
  "progress_summary": "What changed and why the next batch is useful",
  "tasks": [
    {
      "title": "One focused change",
      "repo": "allowlisted_repo_id",
      "capabilities": ["code"],
      "goal": "Concrete outcome",
      "acceptance_criteria": ["Observable result"],
      "verification_commands": [],
      "expected_files": 2,
      "expected_minutes": 20,
      "parent_task_id": null
    }
  ],
  "stop_reason": null
}
```

An empty `verification_commands` list inherits the repository verification
commands. Unknown fields are rejected to prevent silent protocol drift.

### Admission gate

Commander proposes work but cannot enqueue it directly. Existing policy checks
and new Commander checks enforce:

- repository allowlist;
- required goal and acceptance criteria;
- matching worker capability;
- maximum files and expected duration;
- no cross-repository or mixed research-and-code task;
- no duplicate of active, completed, rejected, or recently failed work;
- a traceable relationship to the active long-term goal;
- mandatory pause for configured high-risk operations.

Rejected proposals are stored with machine-readable reasons. Commander may
replan once with those reasons. A second invalid proposal pauses replenishment
instead of looping indefinitely.

## Autonomous Lifecycle

Goals use these states:

- `draft`: created but not confirmed;
- `active`: confirmed and eligible for autonomous replenishment;
- `paused`: intentionally stopped, preserving state;
- `blocked`: cannot progress without human or external action;
- `completed`: completion criteria are satisfied;
- `failed`: unrecoverable Commander protocol or state failure.

The first release permits at most one non-terminal goal (`draft`, `active`,
`paused`, or `blocked`) per Coordinator root. A new goal requires the existing
goal to be completed or explicitly abandoned. Historical terminal goals remain
queryable.

When an active goal has fewer than one ready task, and no replenishment run is
active, the daemon asks Commander for the next batch. Each batch contains at
most three tasks. Existing policy defaults continue to limit a task to three
files and 30 expected minutes. Accepted tasks enter the normal lease-based
queue and use the existing worktree, verification, review, commit, push, and
merge pipeline.

After each terminal task transition, the Coordinator updates goal progress and
stores a concise outcome. Commander sees those outcomes during the next
replenishment call. It must explain how each proposed task advances the goal.

## Durable State

New database migrations add:

### `goals`

- id, title, objective, completion criteria, constraints;
- repository IDs;
- status and confirmation timestamps;
- created, updated, completed, and paused timestamps;
- latest progress summary and stop reason.

### `commander_runs`

- goal ID, trigger, schema version, and run status;
- prompt and raw-output artifact paths;
- parsed progress summary and stop reason;
- exit code, timeout, duration, and error detail;
- created and completed timestamps.

### `commander_messages`

- goal ID, role, content, and created timestamp.

### `task_goal_links`

- goal ID, task ID, batch ID, proposal fingerprint, and rationale.

`state/commander_memory.md` provides a human-readable projection of the active
goal, recent outcomes, current batch, and next action. The database remains the
source of truth.

## Safety Boundaries

Commander has read-only repository access. It cannot call commit, push, merge,
or write tools. The execution engine alone performs state transitions and
dispatches workers.

The loop pauses before proposals involving credentials, live trading, funds,
irreversible data changes, destructive migrations, security-policy changes,
or any repository-specific high-risk category. Existing repository merge and
human-review policies remain authoritative.

## Failure Handling

- Codex timeout or quota failure keeps the goal active and schedules bounded
  retry with backoff.
- Invalid JSON records the raw response and permits one schema-repair attempt.
- Two invalid proposals pause replenishment with an actionable reason.
- Duplicate or oversized tasks are rejected before queue insertion.
- Process restarts recover entirely from the database and artifact files.
- A stale Commander run is marked interrupted before a replacement starts.
- Consecutive failure and daily budget circuit breakers apply to Commander
  calls as well as worker tasks.

The loop stops or pauses when the goal is complete, no valuable next step can
be found, repeated failures exceed policy, all proposals are rejected, a budget
is exhausted, or a high-risk boundary requires human review.

## Testing

Unit coverage includes goal transitions, confirmation, context construction,
schema validation, admission rules, duplicate fingerprints, repository command
inheritance, memory projection, and status messages.

Integration coverage uses a fake Commander CLI to verify:

- chat or goal creation and one-time confirmation;
- initial batch admission;
- automatic replenishment below the queue threshold;
- worker and reviewer outcomes feeding the next Commander call;
- restart recovery without conversational session state;
- timeout, malformed output, quota failure, and retry behavior;
- oversized, duplicate, cross-repository, and high-risk proposal rejection;
- completed, paused, blocked, and circuit-breaker outcomes.

An end-to-end scenario must execute at least two Commander batches and prove
that the second batch depends on the persisted result of the first.

## Delivery Boundaries

This feature remains in the existing single-process CLI architecture. It does
not introduce a web service, message broker, remote scheduler, or multi-service
deployment. The Commander protocol is isolated behind a runner interface so a
different CLI agent can be configured later without changing goal or queue
semantics.
