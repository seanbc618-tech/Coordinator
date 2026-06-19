# Codex Commander Integration

Integrates the read-only Codex Commander workflow onto the formal loop-readiness baseline (`69a7eb2`), preserving all LE-13–LE-33 features while adding goal-driven task replenishment.

## Branch

```
agent/grok/codex-commander-integration
```

Base: `69a7eb2` (`codex/loop-readiness-doctor`)

## What Changed

- Goal persistence (`goals`, `commander_runs`, `commander_messages`, `task_goal_links`)
- Commander JSON protocol, admission gate, and read-only runner
- `coordinator goal` and `coordinator chat` CLI commands
- Daemon replenishment for active goals with bounded retries
- Goal progress in `status --loop` and daily digest
- Adversarial acceptance tests and final report

## Baseline Preservation

This branch adds Commander on top of `69a7eb2` without deleting:

- `migrations/005_atomic_task_leases.sql`
- Atomic `claim_next_ready_task` leasing
- `coordinator discover`, `task events`, `task artifacts`
- `repo cleanup-worktrees`
- Polymarket repo config and Claude/Grok/Pi agent roles
- Repo verification command inheritance for auto-planned tasks

## Acceptance Fixes

- Chat works for active/paused/blocked goals (`/status`, `/pause`, `/resume`)
- Commander replenishment errors surface as `replenishment_error:` instead of silent `no ready tasks`

## Verification

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v   # 341 tests
PYTHONPATH=src python3 -m local_cli_coordinator doctor
PYTHONPATH=src python3 -m local_cli_coordinator goal status
PYTHONPATH=src python3 -m local_cli_coordinator status --loop
PYTHONPATH=src python3 -m local_cli_coordinator daemon --once
git diff --check 69a7eb2..HEAD
```

## Push

```bash
git push -u origin agent/grok/codex-commander-integration
```

Report: `docs/superpowers/specs/2026-06-19-codex-commander-final-report.md`