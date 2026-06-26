# Grok Repair Assignment

You are repairing a rejected final acceptance for the local CLI Coordinator.
Start from commit `c67b85b` in a fresh worktree and create exactly one local
commit. Do not push, merge, rebase, rewrite history, or declare the project
complete. Another worker is modifying CLI and cleanup files, so obey the write
boundary below and do not revert changes made by others.

## Your Exclusive Write Scope

- `src/local_cli_coordinator/engine.py`
- `src/local_cli_coordinator/db.py`
- `src/local_cli_coordinator/discovery.py`
- `src/local_cli_coordinator/review_inbox.py`
- `migrations/005_atomic_task_leases.sql` if a migration is required
- `tests/test_daemon_loop.py`
- `tests/test_discover_cli.py`
- `tests/test_command_discovery.py`
- `tests/test_task_leases.py`
- `tests/test_multi_task_run.py`
- `tests/test_review_inbox.py`
- `tests/test_loop_e2e.py`

Do not modify `cli.py`, `gitops.py`, `digest.py`, loop-status/digest/cleanup
tests, or the final delivery report.

## Required Repairs

### 1. Execute discovery sources inside daemon cycles

- When `run_discovery_before_tasks` is true, each daemon cycle must execute all
  enabled configured discovery sources before planning and importing tasks.
- Support `git_recent_commits`, `command`, `ci_command`, `issue_command`, and
  the existing inbox behavior without importing CLI-private functions.
- Move reusable discovery orchestration into `discovery.py`; the engine should
  call that API directly.
- A source failure must be logged, counted, and isolated without crashing the
  daemon.
- Add an integration test where a configured command emits a finding and one
  call to `run_daemon_cycle()` discovers, persists, plans, imports, and processes
  it without any manual pre-discovery call.

The current `run_discovery_phase()` only imports task files and plans findings
that already exist on disk. The existing E2E test hides this by calling
`discover_from_command()` manually before starting the daemon.

### 2. Make leases atomic and use them in production

- A normal daemon cycle must claim each ready task through the lease API before
  processing it; `next_ready_task()` must not be the production claim path.
- Enforce both selected-agent concurrency and global concurrency from real
  configuration, not hard-coded defaults that callers never provide.
- Make acquisition atomic across separate SQLite connections. A
  select-then-insert sequence without a transaction or database constraint is
  not atomic.
- Expired leases must remain retryable and active leases must prevent duplicate
  processing.
- Add a concurrent two-connection regression test proving only one claimant can
  acquire a task.
- Ensure leases release on success, failure, timeout, and exceptions.
- Keep bounded multi-task processing and circuit-breaker behavior intact.

Choose a SQLite design that remains correct under contention, such as an
immediate transaction plus a constraint/conditional write. Do not merely add a
thread lock, because separate daemon processes use separate connections.

### 3. Put the actual branch in review packets

- After `set_task_branch_and_worktree()`, ensure review-packet generation uses
  the persisted branch or an updated task object.
- Add an engine-level test that drives a real task into `awaiting_human` and
  asserts the packet contains its computed `coord/...` branch, not an empty
  string or `None`.
- Preserve artifact linkage and existing verification/reviewer evidence.

### 4. Strengthen the end-to-end proof

- Rewrite the E2E scenario so discovery is triggered by the daemon itself.
- Assert discovery persistence, planning, task import, worker execution,
  verification, both reviewer stages, commit creation, memory/run-ledger update,
  and the policy-selected final action.
- Do not manually call discovery or planner before the daemon cycle.
- Avoid mocking away the core loop stages.

## TDD And Verification

For each bug, first add a regression test and confirm it fails for the expected
reason. Then implement the smallest coherent fix.

Run at minimum:

```bash
PYTHONPATH=src python3 -m unittest tests/test_daemon_loop.py tests/test_discover_cli.py tests/test_command_discovery.py -v
PYTHONPATH=src python3 -m unittest tests/test_task_leases.py tests/test_multi_task_run.py -v
PYTHONPATH=src python3 -m unittest tests/test_review_inbox.py tests/test_loop_e2e.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
git status --short
```

Commit message:

```text
fix: complete autonomous runtime acceptance
```

## Handoff

Return the commit hash, exact files changed, regression tests added, every
verification command and result, remaining risks, and confirmation that you did
not push, merge, or rebase.
