# Single-Fallback Agent Recovery Design

Date: 2026-06-20
Status: Approved for planning

## Problem

Coordinator currently treats a worker process with exit code zero as successful
until it checks the worktree. A non-interactive Claude worker can enter plan mode,
ask for approval, exit zero, and make no changes. Coordinator eventually marks the
task failed, but it cannot distinguish this interaction block from an ordinary
no-op and cannot recover without operator intervention.

The daemon also stops after its per-run runtime cap. That cap is useful for one
run, but operators need an explicit continuous mode that starts a fresh bounded
run instead of making the service silently disappear.

## Goals

- Detect workers that return an approval request or other interactive block.
- Retry the same task with one different worker at most once.
- Preserve the task, worktree, logs, attempts, and policy boundaries.
- Make the fallback visible in live output and persistent status data.
- Keep every daemon run bounded while allowing an explicit continuous service.

## Non-Goals

- Unlimited retries or agent tournaments.
- Treating test, verification, review, policy, or Git failures as agent blocks.
- Bypassing CLI permission systems.
- Allowing workers to push, merge, or work outside the configured repository.
- Replacing Commander task decomposition.

## Selected Approach

Use semantic block detection followed by one cross-agent fallback. The initial
worker runs normally. If its result is classified as an interactive block,
Coordinator records the attempt and invokes one compatible fallback worker in
the same worktree. The second result is final for automatic execution.

This is preferred over changing only the Claude command because other CLIs can
also request interaction. It is preferred over retrying the same worker because
that commonly repeats the same failure and spends more budget without adding a
new execution path.

## Worker Configuration

The shipped Claude worker command will use `--permission-mode acceptEdits`
instead of `auto`. Its instruction will state that it is running non-interactively,
must implement directly, and must not enter plan mode or request approval.

Coordinator will not use `bypassPermissions`. Worktree isolation and repository
allowlisting remain necessary controls, but they are not an operating-system
sandbox and must not be treated as permission bypass justification.

Each worker may declare an ordered `fallback_agents` list. The initial scope uses:

```toml
[agents.claude_worker]
fallback_agents = ["grok_worker"]

[agents.grok_worker]
fallback_agents = ["claude_worker"]
```

`grok_worker` is a new worker-role configuration. The existing
`grok_spec_reviewer` remains reviewer-only and is never selected as a fallback.

Only agents with role `worker`, matching capabilities, available concurrency,
and a different agent ID are eligible. Codex can be configured as a fallback but
is not implicitly selected.

## Result Classification

Worker completion is classified before changed-file validation:

1. `timed_out`: the process exceeded the task timeout.
2. `command_failed`: the process returned a non-zero exit code.
3. `interactive_blocked`: output contains a high-confidence interaction signal.
4. `completed`: none of the above applies.

Initial high-confidence signals are case-insensitive phrases describing:

- approval required before implementation;
- a request to exit plan mode;
- a request for the operator to say "proceed";
- inability to continue in non-interactive mode without confirmation.

Detection must use a small, testable classifier with named reason codes. It must
not classify generic questions, ordinary summaries, test failures, or review
feedback as interactive blocks. Raw matched text is retained in the agent log;
events store only the reason code and a concise explanation.

An exit-zero result with no changed files and no interaction signal remains the
existing `no changed files` failure. It does not trigger fallback automatically.

## Attempt and Fallback Flow

1. Claim a ready task and create or reuse its isolated worktree.
2. Run the selected worker and persist its attempt and log.
3. Classify the process result.
4. For `interactive_blocked`, emit a fallback event and select one eligible agent.
5. Run the fallback in the same worktree with the same prompt and task timeout.
6. Continue changed-file, policy, verification, review, commit, and push stages
   only after a non-blocked worker result.
7. If fallback is unavailable or also blocked, finish the task as `failed` with
   a precise next action.

The fallback count is stored per task execution and has a hard maximum of one.
It is not reset by daemon restarts. Commander must not create a duplicate active
task merely because a fallback was exhausted.

Partial changes left by a blocked first worker are preserved for diagnosis but
are not silently handed to the fallback. Before fallback, Coordinator compares
the worktree against the task base commit. If partial changes exist, the task is
marked `awaiting_human` rather than risking mixed authorship. Automatic fallback
therefore applies only when the blocked attempt made no tracked or untracked
changes.

## State and Persistence

No new public task state is required. Attempt-level classification is persisted
on the attempt record, with a migration adding:

- `result_class`: nullable classification string;
- `result_reason`: concise reason code or error summary;
- `fallback_from_attempt_id`: nullable reference to the blocked attempt.

Events record `worker blocked`, `fallback selected`, `fallback started`, and
`fallback exhausted`. The final task state remains compatible with current
states: successful work continues through verification, while exhausted recovery
ends in `failed`.

## Observability

Live output must show:

- the blocked worker and reason;
- `fallback 1/1` and the selected worker;
- whether fallback was skipped because of partial changes or no eligible worker;
- the final attempt outcome.

`status --loop` will include active task, current agent, attempt number, fallback
usage, elapsed time, and the latest worker output summary. Full stdout and stderr
remain in per-attempt logs.

## Continuous Daemon Mode

The existing daemon command remains a bounded run. Add an explicit continuous
mode that repeatedly invokes bounded daemon runs:

- each run still respects `max_daemon_runtime_seconds`;
- each task still respects `max_task_runtime_seconds`;
- the daily task budget and circuit breaker span all runs;
- shutdown signals stop after the current safe boundary;
- an idle interval prevents busy looping;
- run boundaries are emitted and persisted.

Reaching the per-run cap is reported as `run completed: runtime cap`, followed by
the next run countdown. It is not reported as a task failure.

## Safety and Budget Rules

- Maximum fallback attempts per task execution: one.
- Initial execution plus fallback produces two billable and auditable attempts.
- The fallback remains part of one task for the daily task-count cap, but its
  elapsed time counts toward task, daemon-run, and daily runtime limits.
- Fallback cannot increase repository, file-count, command, or timeout limits.
- A worker never inherits reviewer or commander authority.
- Verification always runs independently after worker completion.
- Existing merge and human-review policies remain unchanged.

## Error Handling

- No eligible fallback: fail with `interactive block; no eligible fallback`.
- Fallback also blocked: fail with `interactive block after fallback 1/1`.
- Partial changes before fallback: move to `awaiting_human` and preserve evidence.
- Fallback command failure or timeout: use normal command failure handling.
- Database write failure: stop processing the task rather than running an
  unrecorded fallback.

## Testing

Unit tests will cover classifier positives, near-miss negatives, agent selection,
capability filtering, the one-fallback limit, partial-change protection, attempt
persistence, and event formatting.

Engine tests will cover:

- Claude-style plan approval output followed by a successful Grok fallback;
- two blocked workers resulting in failure;
- no eligible fallback;
- ordinary exit-zero no-op without fallback;
- non-zero exit without fallback;
- fallback success proceeding through verification;
- daemon restart not resetting the fallback allowance.

CLI tests will cover live fallback events, status rendering, bounded daemon
behavior, continuous run rollover, graceful shutdown, and idle waiting.

The full test suite, `git diff --check`, and an isolated fake-agent end-to-end run
must pass before acceptance. The fake-agent run must prove there are exactly two
worker attempts and no third invocation.

## Acceptance Criteria

- A Claude plan-mode approval response is detected despite exit code zero.
- The same task is handed to one different compatible worker exactly once.
- No automatic fallback occurs after any partial worktree modification.
- A second block cannot trigger a third worker invocation.
- Attempts, reason codes, logs, and fallback lineage survive daemon restart.
- Operators can observe the switch live and through `status --loop`.
- Continuous mode rolls over bounded runs without weakening existing budgets.
- Existing task, review, Git, worktree, and readiness behavior remains compatible.
