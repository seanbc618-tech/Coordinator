# Local CLI Agent Coordinator

A local loop coordinator for running CLI agents against small, verified tasks
with discovery, independent evaluation, durable memory, budget caps,
scheduling, and human review points.

## Loop Overview

The coordinator implements a full loop pipeline:

1. **Discovery** — find work from inbox, recent commits, or configured commands.
2. **Planning** — cut findings into small, actionable tasks.
3. **Handoff** — each task runs in an isolated git worktree.
4. **Verification** — tests run before commit.
5. **Review** — independent spec and quality reviewers evaluate changes.
6. **Persistence** — memory, events, and artifacts are stored on disk.
7. **Scheduling** — the daemon loop runs continuously or on demand.
8. **Human review** — risky changes pause for human approval.

## Quick Commands

```bash
# Check loop readiness
PYTHONPATH=src python -m local_cli_coordinator doctor

# Scan task inbox
PYTHONPATH=src python -m local_cli_coordinator inbox scan

# Run discovery once
PYTHONPATH=src python -m local_cli_coordinator discover --once

# Run one daemon cycle
PYTHONPATH=src python -m local_cli_coordinator daemon --once

# Run continuous daemon loop
PYTHONPATH=src python -m local_cli_coordinator daemon

# Live commands, output, heartbeats, and transitions (default)
PYTHONPATH=src python -m local_cli_coordinator daemon

# Durable logs with final summary only
PYTHONPATH=src python -m local_cli_coordinator daemon --quiet

# Show task status
PYTHONPATH=src python -m local_cli_coordinator status

# Show loop status dashboard
PYTHONPATH=src python -m local_cli_coordinator status --loop

# Generate daily digest
PYTHONPATH=src python -m local_cli_coordinator digest

# View task events
PYTHONPATH=src python -m local_cli_coordinator task events <task_id>

# View task artifacts
PYTHONPATH=src python -m local_cli_coordinator task artifacts <task_id>

# Clean up stale worktrees
PYTHONPATH=src python -m local_cli_coordinator repo cleanup-worktrees
```

## Live Observability

`coordinator daemon` and `coordinator daemon --once` display live output by
default. Every pipeline stage reports the exact command, working directory,
live stdout and stderr, elapsed time, exit code, and durable log path.

Commands are displayed unredacted. Terminal display does not change rollback,
worktree, commit, push, merge, or timeout behavior.

Use `--quiet` to suppress live events and show only the final summary. Durable
logs are always written regardless of display mode.

## Configuration

### Agents (`config/agents.toml`)

```toml
[agents.claude]
command = "claude --print --dangerously-skip-permissions -p {prompt_path}"
capabilities = ["code"]
max_concurrency = 1
role = "worker"
```

Roles: `worker`, `spec_reviewer`, `quality_reviewer`, `planner`.

### Repositories (`config/repos.toml`)

```toml
[repos.myproject]
path = "/path/to/repo"
default_branch = "main"
branch_prefix = "coord/"
allow_push = false
merge_policy = "no_push"
verify_commands = ["python -m unittest"]
review_policy = "full_review"
```

Review policies: `auto`, `branch_only`, `always_human`, `risky_human`.

### Policy (`config/policy.toml`)

```toml
[task_policy]
require_single_repo = true
require_acceptance_criteria = true
require_verification_commands = true
require_handoff_summary = true
max_files_touched = 5
max_expected_minutes = 30
max_attempts = 3
max_task_runtime_seconds = 1800
max_daemon_runtime_seconds = 3600
max_tasks_per_run = 1
max_tasks_per_day = 24
max_consecutive_failures = 3

[daemon_policy]
loop_interval_seconds = 300
idle_sleep_seconds = 60
run_discovery_before_tasks = true
```

### Discovery Sources (`config/discovery.toml`)

```toml
[sources.recent_commits]
type = "git_recent_commits"
[sources.recent_commits.repos]
myproject = true

[sources.ci_alerts]
type = "command"
command = "my-ci-check.sh"
[sources.ci_alerts.repos]
myproject = true
```

Source types: `inbox`, `git_recent_commits`, `command`, `ci_command`, `issue_command`.

## Evaluator Pipeline

The evaluator pipeline enforces the PDF's warning that a generator cannot
grade its own work:

1. **Verification** — configured test commands run in the worktree.
2. **Spec review** — a separate reviewer checks acceptance criteria.
3. **Quality review** — a second reviewer evaluates implementation quality.

Tasks that fail review go to `awaiting_human` (for reviewed repos) or
`rejected` (for auto-merge repos). A Markdown review packet is written to
`tasks/review/` with all evidence.

## Budget Caps And Circuit Breakers

The daemon enforces hard limits:

- **max_task_runtime_seconds** — per-task timeout.
- **max_daemon_runtime_seconds** — per-run timeout.
- **max_tasks_per_run** — tasks per daemon cycle.
- **max_tasks_per_day** — daily task cap.
- **max_consecutive_failures** — stops the loop on repeated failures.

When a cap is reached, the daemon stops and records the reason.

## Memory And Persistence

- **Loop memory** (`state/loop_state.md`) — human-readable log of every
  processed task with outcome, branch, and next action.
- **Findings** (`state/findings/*.jsonl`) — discovery results stored as JSONL.
- **Run ledger** (`coordinator.db`) — daemon run history with counts and
  stop reasons.
- **Artifacts** — verifier logs, review logs, diffs, and review packets
  linked to each task.

## Task Leasing

Ready tasks are claimed with an atomic lease to support parallel execution:

- Each lease has an expiration time (default 30 minutes).
- Expired leases are automatically retried.
- Max concurrency is enforced per agent and globally.

## Recovery After Interruption

If a session is interrupted:

1. Check `git status --short` for uncommitted worktree changes.
2. Run `coordinator status --loop` to see current state.
3. Check `state/loop_state.md` for the last processed task.
4. The daemon lockfile (`state/coordinator.lock`) prevents duplicate runs.
   Use `--force-lock` to override a stale lock.

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

## Readiness Checklist

Run `coordinator doctor` to check:

- Discovery source configured
- State file present
- Evaluator configured
- Worktree isolation working
- Budget caps set
- Human review point configured

## Global Runtime Paths

The Coordinator stores its state in XDG-compliant directories:

| Data | Default | XDG Override |
|---|---|---|
| Config | `~/.config/coordinator/` | `$XDG_CONFIG_HOME/coordinator/` |
| Database | `~/.local/share/coordinator/coordinator.db` | `$XDG_DATA_HOME/coordinator/` |
| Socket | `~/.local/state/coordinator/coordinator.sock` | `$XDG_STATE_HOME/coordinator/` |

For testing, set `COORDINATOR_HOME` to place all three directories under one
root:

```bash
export COORDINATOR_HOME=/tmp/coordinator-test
```

## Supervisor

The Supervisor is a single-instance daemon that serves the Coordinator protocol
over a Unix socket.

```bash
# Start in foreground (required for now)
coordinator supervisor start --foreground

# Check if running
coordinator supervisor status

# Graceful shutdown
coordinator supervisor stop
```

A second `supervisor start` is rejected while one is running. The lock file
uses `O_CREAT|O_EXCL` for atomic acquisition.

## Project Registration

Register Git repositories as Coordinator projects:

```bash
# Inspect without registering
coordinator project inspect /path/to/repo

# Register (requires --yes)
coordinator project add /path/to/repo --yes
```

Project registration is idempotent and requires explicit confirmation.

## Legacy Migration

Migrate from a single-root Coordinator installation to global paths:

```bash
# Dry run (validates without writing)
coordinator migrate --source /path/to/legacy --dry-run

# Full migration
coordinator migrate --source /path/to/legacy --yes
```

Migration safety:
- Copies to a staging directory first; validates the database before touching
  live directories.
- Backs up all three target dirs (config, data, state) before overwrite.
- On failure, restores from backup and deletes newly-created directories.
- Never deletes the source.

## Single Fallback Recovery

When a worker agent gets stuck requesting interactive approval instead of
implementing, the Coordinator automatically hands the task to a different
compatible agent exactly once.

The classifier detects patterns like "should I proceed?", "may I continue?",
"would you like me to?" in agent output. If the first worker left no changes
in the worktree, the task is handed to the configured fallback agent.

Configuration in `config/agents.toml`:

```toml
[agents.claude_worker]
fallback_agents = ["grok_worker"]

[agents.grok_worker]
fallback_agents = ["claude_worker"]
```

At most one fallback attempt per task. If both agents are blocked, the task
moves to `awaiting_human`.

## Commander (Goal-Driven Automation)

The Commander adds a conversational front door for setting long-term goals.
Once confirmed, it autonomously replenishes the task queue.

### Quick Start

```bash
# Create a goal (draft)
coordinator goal "Continue the roadmap while preserving dry-run safety"

# Preview and confirm
coordinator goal confirm

# Check status
coordinator goal status

# Pause/resume
coordinator goal pause
coordinator goal resume
```

### Chat REPL

```bash
coordinator chat
```

Commands: `/status`, `/start`, `/pause`, `/resume`, `/quit`

### How It Works

1. You describe a goal in natural language.
2. Commander inspects the repo, roadmap, and current state.
3. It previews the first batch of small tasks.
4. You confirm once with `coordinator goal confirm` or `/start` in chat.
5. The daemon automatically replenishes the queue when tasks run out.
6. The goal completes when Commander marks it done.

### Configuration

Add a Commander agent to `config/agents.toml`:

```toml
[agents.codex_commander]
command = "codex exec --sandbox read-only --ask-for-approval never --ephemeral --output-schema {schema_path} \"Read {prompt_path} and return only the required JSON object.\""
capabilities = ["code", "tests", "docs", "research"]
max_concurrency = 1
role = "commander"
```

Commander policy in `config/policy.toml`:

```toml
[commander_policy]
queue_low_watermark = 1
max_tasks_per_batch = 3
max_consecutive_failures = 3
first_retry_seconds = 60
second_retry_seconds = 300
roadmap_context_max_chars = 20000
```

### Safety Boundaries

- Commander has read-only repository access.
- It cannot commit, push, merge, or write tools.
- Proposals involving credentials, live trading, or destructive migrations are rejected.
- Existing repository merge and human-review policies remain authoritative.

### Status

`coordinator status --loop` shows:

- Goal status (none, draft, active, paused, blocked, completed)
- Commander replenishment state
- Task counts by state
