# Claude Code Repair Assignment

You are repairing a rejected final acceptance for the local CLI Coordinator.
Start from commit `c67b85b` in a fresh worktree and create exactly one local
commit. Do not push, merge, rebase, rewrite history, or declare the project
complete. Another worker is modifying the autonomous runtime files, so obey the
write boundary below and do not revert changes made by others.

## Your Exclusive Write Scope

- `src/local_cli_coordinator/cli.py`
- `src/local_cli_coordinator/gitops.py`
- `src/local_cli_coordinator/digest.py`
- `tests/test_loop_status.py`
- `tests/test_digest.py`
- `tests/test_worktree_cleanup.py`
- `docs/superpowers/specs/2026-06-19-loop-upgrade-final-delivery-report.md`

Do not modify `engine.py`, `db.py`, migrations, `discovery.py`,
`review_inbox.py`, or their tests.

## Required Repairs

### 1. Repair `status --loop`

- Keep the database connection open for every query, including
  `circuit_breaker_reason()`.
- Add a regression test with a valid config that previously triggered
  `sqlite3.ProgrammingError`.
- Show all LE-30 fields: readiness, last run, next run, actual budget usage,
  active leases, and human-review count.
- Keep output plain text and script-friendly.
- Derive last-run data from `daemon_runs` and calculate next run from the last
  run plus configured interval. Represent unavailable values explicitly rather
  than inventing data.
- Budget usage must show current measured usage beside configured caps, not only
  print maximum values.

Reproduction before the fix:

```bash
PYTHONPATH=src python3 -m local_cli_coordinator status --loop
```

With repository configuration this currently exits 1 because the database has
already been closed.

### 2. Repair `coordinator digest`

- Call `_open_db(root, args.db)` with its real signature.
- Add a CLI-level test that initializes a database, invokes `digest`, asserts
  exit code 0, and verifies `state/digests/YYYY-MM-DD.md` is created.
- Keep existing direct unit tests for digest generation.

Reproduction before the fix:

```bash
PYTHONPATH=src python3 -m local_cli_coordinator --root <root-with-db> digest
```

It currently reports `_open_db() missing 1 required positional argument: 'db'`.

### 3. Make worktree cleanup task-aware and safe

- Remove a worktree only when it is coordinator-managed and its corresponding
  task is in an explicitly completed state.
- Never remove active, running, leased, awaiting-human, or unknown worktrees.
- Continue skipping dirty worktrees unless `--force` is explicitly supplied.
- When forced, pass the required force flag to `git worktree remove`.
- Add real temporary-Git-repository tests for active clean, completed clean,
  completed dirty without force, and completed dirty with force cases.
- Report stale/missing registrations without treating every managed worktree as
  stale.

### 4. Clean and correct delivery artifacts

- Remove all trailing whitespace from `digest.py` and `test_digest.py`.
- Correct the delivery report's agent counts and state that merge commit
  `e59106c` exists. Do not mark final acceptance as passed.
- Do not hide or delete historical facts.

## TDD And Verification

For each bug, first add a regression test and confirm it fails for the expected
reason. Then implement the smallest coherent fix.

Run at minimum:

```bash
PYTHONPATH=src python3 -m unittest tests/test_loop_status.py tests/test_digest.py tests/test_worktree_cleanup.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m local_cli_coordinator status --loop
PYTHONPATH=src python3 -m local_cli_coordinator doctor
git diff --check 88d8ede..HEAD
git status --short
```

Commit message:

```text
fix: repair operator CLI acceptance failures
```

## Handoff

Return the commit hash, exact files changed, regression tests added, every
verification command and result, remaining risks, and confirmation that you did
not push, merge, or rebase.
