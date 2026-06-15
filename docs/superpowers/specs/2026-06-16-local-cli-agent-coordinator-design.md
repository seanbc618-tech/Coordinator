# Local CLI Agent Coordinator Design

Date: 2026-06-16

## Goal

Build a local coordinator that can run CLI agents continuously: split work into small tasks, assign each task to a configured CLI agent, verify the result, commit and push successful work, then create the next small tasks automatically.

The coordinator should not depend on any agent remembering context. Context lives in Markdown task files, a SQLite task ledger, git branches, logs, and handoff summaries.

## First-Version Scope

The first version is a single local program with a daemon loop and CLI commands.

It supports:

- Markdown task inbox for human-written goals.
- SQLite ledger for task state, attempts, logs, and evidence.
- Configurable local CLI agents.
- Repository allowlist.
- One git worktree and branch per task.
- Automatic verification, commit, push, and optional merge based on repo policy.
- Small-task enforcement through an LLM planner plus rule-based gate.
- Failure recovery through retry, reassignment, smaller task splitting, or blocked state.

It does not support in the first version:

- Browser automation.
- Web dashboard.
- Cloud queue infrastructure.
- Multi-service deployment.
- Autonomous operation outside allowlisted repositories.

## Core Principle

Coordinator does not manage agents by memory. It manages agents by small tasks, a durable ledger, verification evidence, and isolated git workspaces.

The main loop is:

```text
Markdown task
-> split into small tasks
-> write to SQLite
-> create worktree and branch
-> select CLI agent
-> run agent
-> run verification commands
-> commit
-> push
-> apply repo merge policy
-> generate next tasks
-> repeat
```

## Directory Layout

```text
Coordinator/
  config/
    agents.yaml
    repos.yaml
    policy.yaml
  tasks/
    inbox/
    accepted/
    generated/
    blocked/
  runs/
    <task-id>/
      prompt.md
      agent.log
      verifier.log
      handoff.md
      diff.patch
  worktrees/
    <repo-name>/
      <task-id>/
  coordinator.db
  src/
  docs/
```

## Configuration

### Agents

Agents are registered by command template and capabilities. The coordinator core does not hard-code Codex, Claude, Pi, Grok, or any other agent.

```yaml
agents:
  codex:
    command: "codex exec --json"
    capabilities: ["code", "tests", "review", "docs"]
    max_concurrency: 2

  claude-code:
    command: "claude --print"
    capabilities: ["code", "debug", "refactor"]
    max_concurrency: 1

  pi:
    command: "./profiles/erleng/run.sh"
    capabilities: ["long-running", "verification", "ops"]
    max_concurrency: 1
```

The command receives a generated prompt file, task metadata, repo path, and worktree path through environment variables or command arguments.

### Repositories

The coordinator can only work inside allowlisted repositories.

```yaml
repos:
  polymarket-weather-arb:
    path: /Users/xiafan/polymarket-weather-arb
    default_branch: main
    remote: origin
    branch_prefix: coord/
    allow_push: true
    merge_policy: push_branch_only
    verify_commands:
      - "uv run pytest"

  experiment-repo:
    path: /Users/xiafan/experiment-repo
    default_branch: main
    remote: origin
    branch_prefix: coord/
    allow_push: true
    merge_policy: auto_merge_default_branch
    verify_commands:
      - "npm run check"
      - "npm test"
```

Merge policies:

- `push_branch_only`: push the task branch and mark the task done.
- `auto_merge_default_branch`: merge verified task branch into the repo default branch and push the default branch.
- `no_push`: commit locally but do not push.

### Policy

```yaml
task_policy:
  require_single_repo: true
  require_acceptance_criteria: true
  require_verification_commands: true
  require_handoff_summary: true
  max_files_touched: 3
  max_expected_minutes: 30
  max_attempts: 3
  split_if_touches_multiple_subsystems: true
  split_if_research_and_code_are_mixed: true
```

These limits are not security theater. They exist to keep agent context small and make each task recoverable by another agent.

## Task Format

Human-written tasks go into `tasks/inbox/*.md`.

```md
# Task: Add regression coverage for weather parser

repo: polymarket-weather-arb
priority: normal
capabilities: [tests, code]

## Goal

Add focused regression coverage for low/minimum temperature parsing.

## Acceptance Criteria

- Adds tests for low/minimum temperature titles.
- Does not touch trading, dashboard, or NOAA fetch code.
- `uv run pytest tests/test_rules.py -q` passes.

## Notes

Keep the change small. If production behavior is wrong but the task is too broad,
create a follow-up task instead of expanding scope.
```

Generated subtasks use the same format and live in `tasks/generated/`.

## Small-Task Splitting

Splitting uses a hybrid model:

1. LLM planner reads a large task and proposes small subtasks.
2. Rule gate checks each subtask.
3. Oversized subtasks are rejected and sent back for smaller splitting.
4. Accepted subtasks enter the ledger as executable tasks.

The rule gate rejects tasks that:

- Touch multiple repositories.
- Mix research, implementation, and review in one task.
- Lack acceptance criteria.
- Lack verification commands.
- Are expected to touch too many files.
- Depend on unstated context from a previous agent conversation.

Each completed task must produce a handoff:

```md
## Result

What changed.

## Evidence

Commands run and their result.

## Changed Files

Files changed.

## Follow-Up Tasks

Small next tasks that should be queued.

## Handoff Context

What the next agent needs to know.
```

## State Machine

Each task has one current state.

```text
inbox
planned
ready
running
verifying
committing
pushing
merging
done
```

Failure states:

```text
failed
retrying
reassigned
needs_split
blocked
```

The daemon never assumes an in-memory state is authoritative. SQLite is the source of truth.

## SQLite Ledger

Minimum tables:

- `tasks`: task metadata, state, repo, branch, worktree path, priority.
- `attempts`: each agent run, command, start time, end time, exit code.
- `events`: append-only state transitions and notes.
- `artifacts`: paths to logs, prompts, diffs, handoff files, verification output.
- `agents`: configured agents and observed health.
- `repos`: imported repo policy snapshot.

The database should make it possible to answer:

- What is running now?
- What changed?
- Which agent did it?
- What verification passed or failed?
- What task should run next?
- Where is the worktree and branch?

## Agent Execution

For each ready task, coordinator:

1. Creates branch name: `coord/<task-id>-<slug>`.
2. Creates git worktree under `worktrees/<repo-name>/<task-id>/`.
3. Writes a bounded prompt to `runs/<task-id>/prompt.md`.
4. Selects an agent by required capabilities, concurrency, and previous task outcomes.
5. Runs the agent command in the worktree.
6. Captures stdout, stderr, exit code, changed files, and patch.

Agents are expected to work only inside the assigned worktree.

## Verification

Verification is mandatory before commit and push.

Verification sources:

- Task acceptance criteria.
- Repo `verify_commands`.
- Optional task-specific commands.
- Policy checks such as changed file count and forbidden path checks.

If verification fails, coordinator records the evidence and either:

- retries with the same agent,
- reassigns to another agent,
- asks planner to split the task smaller,
- marks the task blocked after max attempts.

## Commit, Push, and Merge

After verification passes:

1. Coordinator creates a commit with a generated message.
2. Coordinator pushes the task branch if repo policy allows push.
3. Coordinator applies the repo merge policy.
4. Coordinator records commit hash, branch name, remote push result, and merge result.

Commit message format:

```text
<short task summary>

Task: <task-id>
Agent: <agent-id>
Verification:
- <command>: passed
```

## CLI Surface

First-version commands:

```bash
coordinator daemon
coordinator inbox scan
coordinator status
coordinator task list
coordinator task show <task-id>
coordinator task retry <task-id>
coordinator task block <task-id>
coordinator agent list
coordinator repo list
coordinator logs <task-id>
coordinator doctor
```

The first version can be plain CLI output. A richer TUI can be added later without changing the ledger.

## Recovery

Coordinator should survive restarts.

On startup, it checks:

- tasks stuck in `running`, `verifying`, `committing`, `pushing`, or `merging`;
- whether their worktree exists;
- whether there are uncommitted changes;
- whether commits were created;
- whether branch was pushed;
- whether merge happened.

It then resumes from the safest confirmed step rather than starting over blindly.

## MVP Build Order

1. Project scaffold and config loading.
2. SQLite ledger and state transitions.
3. Markdown inbox parser.
4. Repo allowlist validation.
5. Worktree and branch creation.
6. Shell-based generic CLI agent adapter.
7. Verification runner.
8. Commit and push flow.
9. Small-task planner interface and rule gate.
10. Daemon loop and status commands.

This order builds the reliable task machine first, then adds autonomy.

## Implementation Defaults

Use these defaults unless implementation uncovers a concrete reason to change them:

- Language: Python.
- CLI: standard `argparse` first; add richer terminal output later only if needed.
- Database: SQLite through Python `sqlite3`.
- Migrations: plain SQL migration files applied in order.
- Config: YAML if a dependency is already available; otherwise TOML to keep the first version dependency-light.
- Planner: a configured CLI agent with `planner` capability, run through the same adapter system as other agents.
- TUI: plain terminal status tables in the first version, not an interactive full-screen app.

## Acceptance Criteria For This Design

- The first version is local-only and CLI/TUI-only.
- It supports arbitrary configured CLI agents.
- It uses Markdown for human task input and SQLite for durable machine state.
- It only operates on allowlisted repositories.
- It isolates every task in a git worktree and branch.
- It can automatically verify, commit, push, and optionally merge based on repo policy.
- It keeps tasks small and rejects oversized work before assigning an agent.
- It records enough evidence for another agent or human to continue after failure.
