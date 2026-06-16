# Local CLI Agent Coordinator

A local coordinator for running CLI agents against small, verified tasks.

## First Version

- Human tasks enter through `tasks/inbox/*.md`.
- Runtime state is stored in `coordinator.db`.
- Repositories must be listed in `config/repos.toml`.
- Agents are configured in `config/agents.toml`.
- Every task runs in its own git worktree and branch.
- Verification runs before commit and push.

## Quick Commands

```bash
PYTHONPATH=src python -m local_cli_coordinator doctor
PYTHONPATH=src python -m local_cli_coordinator inbox scan
PYTHONPATH=src python -m local_cli_coordinator status
PYTHONPATH=src python -m local_cli_coordinator daemon --once
```

## Task Format

```md
# Task: Small focused change

repo: example
priority: normal
capabilities: [code]
verification: [python -m unittest]

## Goal

Make one focused change.

## Acceptance Criteria

- Verification passes.
- The task stays within the configured file-change limit.
```
