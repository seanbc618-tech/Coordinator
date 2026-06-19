# Final Acceptance Repair Summary

## Current Baseline

- Integration branch: `codex/loop-readiness-doctor`
- Reviewed commit: `c67b85b`
- Existing full suite: 215 tests pass
- Doctor: 4 PASS / 2 WARN
- Final acceptance: rejected until every issue below is repaired and independently reverified

Passing the existing suite is not sufficient. Several required production paths
are absent from the tests or fail only when real project configuration is loaded.

## Blocking Findings

1. `status --loop` closes its database connection before calling
   `circuit_breaker_reason()`. With valid configuration it crashes with
   `sqlite3.ProgrammingError: Cannot operate on a closed database`.
2. `status --loop` omits required last-run, next-run, and actual budget-usage
   fields.
3. `coordinator digest` calls `_open_db()` with the wrong signature and always
   exits with an error when a database exists.
4. Daemon discovery does not run configured discovery sources. It only imports
   inbox/generated tasks and plans findings already on disk.
5. Task leases are not used by the normal daemon path. The lease implementation
   also uses an unprotected select-then-insert sequence, so two database
   connections can acquire the same task.
6. Worktree cleanup removes every clean coordinator-managed worktree without
   checking that its task is complete. Its `--force` path also fails to pass
   `--force` to `git worktree remove`.
7. Review packets receive the stale pre-worktree task row, so the actual branch
   is blank even though it has been persisted to the database.
8. `git diff --check 88d8ede..HEAD` reports 20 trailing-whitespace errors in
   `digest.py` and `test_digest.py`.
9. The delivery report says there were no merge commits, but `e59106c` is a
   merge commit. Its task ownership counts also omit LE-18 from the Claude row.

## Assignment Boundary

Claude Code owns the CLI and cleanup repair set:

- `src/local_cli_coordinator/cli.py`
- `src/local_cli_coordinator/gitops.py`
- `src/local_cli_coordinator/digest.py`
- corresponding status, digest, and cleanup tests
- whitespace cleanup and factual corrections to the final delivery report

Grok owns the autonomous runtime repair set:

- `src/local_cli_coordinator/engine.py`
- `src/local_cli_coordinator/db.py`
- lease migrations when required
- `src/local_cli_coordinator/discovery.py`
- `src/local_cli_coordinator/review_inbox.py`
- corresponding daemon, discovery, lease, review-packet, and E2E tests

The write sets must remain disjoint. Neither worker may edit the other worker's
files, merge, push, rewrite history, or declare the upgrade complete.

## Integration Order

1. Start both workers from commit `c67b85b` in separate worktrees.
2. Review each commit independently against its prompt.
3. Integrate Grok first, then rebase or replay Claude's commit on the new
   integration baseline if necessary.
4. Run targeted tests, the complete suite, configured CLI smoke tests, doctor,
   and `git diff --check 88d8ede..HEAD`.
5. Only Codex may issue the final completion declaration.
