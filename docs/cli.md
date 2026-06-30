# Coordinator CLI Prompt Modes

> **Phase 10 merged** — this file now covers the operator control tower
> (`/inbox`, `/attention`, `/summary`, `/notify`, `/decision`, `/dismiss`),
> durable operator items, notification policy with local sinks, and safe
> decision routing through existing RPCs.
> Phase 9 covers GitHub delivery slash commands
> (`/deliver`, `/prs`, `/ci`, `/delivery`, `/merge-policy`), durable delivery
> records, evidence-backed PR bodies, CI polling, and bounded CI recovery.
> Phase 8 covers evidence review slash commands
> (`/evidence`, `/review`, `/risk`, `/merge-ready`), durable task evidence,
> rules-v2 completion gates, risk assessment, and review packets v2.
> Phase 7 covers strategic autonomy (`/strategy`, `/recoveries`, `/agents`,
> `/overnight`), milestone-linked backlog, bounded recovery proposals, agent
> scorecards, and overnight quiet-hour summaries.
> Phase 6D covers machine-readable admin `--json` output, `coordinator init`,
> `config explain`, permission modes, worker-state snapshots, event schema v2
> replay, the mock-provider harness, and operability slash commands (`/plan`,
> `/scan`, `/jump`, `/open`). Earlier phases cover `@file` context,
> `--resume`/`--fork`, execution policy, `/approve`, `/cancel`, `/retry`,
> `/dashboard`, `/task <id> log`, and `/loop` autonomous loop commands.
> See [troubleshooting](troubleshooting.md) for error codes,
> [autonomous-loop](autonomous-loop.md) for loop configuration, and
> [migration](migration.md) for schema changes (migrations 012–020).

Phase 5.3 adds Pi-inspired headless entry points on top of the global Supervisor
`chat.send` path. The Ink TUI remains the default interactive shell.

## Prompt and print

```bash
# Send a message, then open the TUI (default)
coordinator "检查这个项目状态"

# Print reply without opening Ink
coordinator --print -p "你好"

# Positional prompt with print
coordinator 检查项目状态 --print
```

## JSON output

```bash
coordinator --print --mode json -p "现在有什么任务？"
```

Stdout is a single JSON object with keys: `ok`, `project_id`, `goal_id`,
`user_reply`, `intent`, `admitted`, `rejected`, `accepted_task_ids`, `error`.

## Continue latest goal

```bash
coordinator --continue --print -p "下一步做什么？"
```

Binds to the newest non-terminal goal for the current git project.

## Local slash commands (print mode)

Leading `/` dispatches deterministic RPCs without Commander:

```bash
coordinator --print -p "/status"
coordinator --print -p "/tasks"
coordinator --print -p "/dashboard"
coordinator --print -p "/task <task-id>"
coordinator --print -p "/task <task-id> log"
coordinator --print -p "/task <task-id> cancel"
coordinator --print -p "/approve <task-id>"
coordinator --print -p "/retry <task-id>"
coordinator --print -p "/plan"
coordinator --print -p "/scan"
coordinator --print -p "/strategy"
coordinator --print -p "/recoveries"
coordinator --print -p "/agents"
coordinator --print -p "/overnight"
coordinator --print -p "/overnight start --until 08:00"
coordinator --print -p "/evidence <task-id>"
coordinator --print -p "/review <task-id>"
coordinator --print -p "/risk <task-id>"
coordinator --print -p "/merge-ready <task-id>"
coordinator --print -p "/deliver <task-id>"
coordinator --print -p "/prs"
coordinator --print -p "/ci <task-id>"
coordinator --print -p "/delivery <task-id>"
coordinator --print -p "/merge-policy"
coordinator --print -p "/inbox"
coordinator --print -p "/attention"
coordinator --print -p "/summary"
coordinator --print -p "/summary morning"
coordinator --print -p "/notify --dry-run"
coordinator --print -p "/decision <item-id>"
coordinator --print -p "/dismiss <item-id>"
coordinator operator summary --json
coordinator --print -p "/jump <task-id> log"
coordinator --print -p "/open <task-id> log"
coordinator --mode json -p "/plan"
coordinator --mode json -p "/strategy"
coordinator --mode json -p "/evidence <task-id>"
```

Operability slash commands (`/plan`, `/scan`, `/jump`, `/open`) call Supervisor
RPCs (`project.plan`, `project.scan`, `project.jump`). They are read-only and
return paths or hints only — no editor or shell is spawned.

Task control notes:

- `cancel` stops the worker process (SIGTERM → grace → SIGKILL), releases the
  lease, and marks the task `failed`. Worktrees are **preserved** by default.
- `log` tails registered attempt/verifier/agent artifacts (64 KiB cap, rate
  limited). The TUI also receives live `task.log.append` push events while
  workers run.

Unknown slash commands return a local error and never call `chat.send`.

## Skip TUI after prompt

```bash
coordinator --no-tui -p "记录一下进度"
coordinator --print -p "总结状态"   # --print implies --no-tui
```

## Machine-readable admin output (Phase 6D)

Selected administrative commands accept `--json` and emit a stable envelope:

```json
{
  "ok": true,
  "command": "doctor",
  "schema_version": 1,
  "generated_at": "2026-06-28T12:00:00Z",
  "data": {},
  "warnings": [],
  "errors": []
}
```

On failure, `ok` is `false` and `errors` contains typed objects with `code`,
`message`, and optional `hint`. Scripts should parse keys, not prose substrings.

Supported commands:

```bash
coordinator doctor --json
coordinator supervisor status --json
coordinator config --json
coordinator config explain --json
coordinator loop --json
coordinator loop run --json
coordinator init --dry-run --json
```

Print-mode slash commands also accept `--json` when routed through the admin
envelope (for example `/status`, `/dashboard`, `/plan`, `/scan`, `/jump`).

`--mode json` on prompt commands remains the smaller public chat schema.
`--mode rpc` returns the Supervisor `ResponseEnvelope` protocol shape.

## Config inspection

```bash
coordinator config
coordinator config --json
coordinator config explain
coordinator config explain policy.max_tasks_per_day
coordinator config explain --json
```

Shows agents, repo allowlist, policy caps, permission modes, and XDG/runtime
paths. `config explain` reports which file or default produced each effective
setting. Secret-like values are redacted in text and JSON output.

Permission modes (`read-only`, `workspace-write`, `danger`) and per-agent tool
allowlists appear in `config` and `config explain` output. Defaults keep
Commander and reviewers read-only and workers at workspace-write; merge/push
remain governed by repo policy.

## Project bootstrap

```bash
cd /path/to/your-repo
coordinator init --dry-run --json
coordinator init --yes
coordinator init --repo-id polymarket_crypto_threshold --verify "uv run pytest" --yes
```

`coordinator init` discovers the current git root (or `--path`), scaffolds global
`agents.toml`, `repos.toml`, and `policy.toml` under `COORDINATOR_HOME` or XDG
config, and adds a repo allowlist entry. Autonomy stays **off** unless
`--autonomy on` is passed explicitly. Existing `agents.toml` files are preserved
on repeat runs.

## Safe admin commands (dry-run first)

Destructive repo/task operations require a confirm token from dry-run:

```bash
coordinator repo cleanup-worktrees
coordinator repo cleanup-worktrees --apply --confirm <token>

coordinator task rollback <task-id>
coordinator task rollback <task-id> --apply --confirm <token>

coordinator supervisor drain
```

## Examples (polymarket)

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator supervisor restart
coordinator --print -p "你好"
coordinator --mode json --print -p "现在有什么任务？"
coordinator --print -p "/dashboard"
coordinator --print -p "/task <task-id> log"
coordinator --continue --print -p "生成一个只读验收任务"
coordinator config
coordinator "打开 TUI 继续"
```

After upgrading Coordinator, restart the global Supervisor when no tasks are
`running` so new RPCs such as `supervisor.dashboard` are available to the live
process.

Greetings and status questions should not create tasks. Only explicit task
requests may admit work after Commander policy checks.

## File context

Attach repo-relative files to the prompt with `@` tokens:

```bash
coordinator @README.md -p "检查文档中的安装步骤"
coordinator @docs/tui.md @docs/cli.md --print -p "找出矛盾"
coordinator --mode json @pyproject.toml -p "总结配置"
```

Rules:

- References are resolved from the current working directory, not the git root.
- Each file must exist, be a regular file, decode as UTF-8, and be ≤ 128 KiB.
- Combined limit: 512 KiB and at most 16 files.
- Duplicate canonical paths are included once.
- `@@name` escapes the syntax and becomes the literal token `@name`.

JSON mode adds a `context_files` array with `path`, `size`, and `sha256` for
each attached file.

## Goal sessions

```bash
# Resume the latest resumable goal (interactive selector in TTY)
coordinator --resume

# Resume a specific goal by ID
coordinator --resume 42 -p "继续分析"

# Fork a terminal goal into a new draft
coordinator --fork 17 -p "只保留文档修复部分"
```

Rules:

- `--continue`, `--resume`, and `--fork` are mutually exclusive.
- `--resume` without an ID lists candidates (exit 2 in non-interactive mode).
- Fork creates a new draft goal; it does not copy tasks or execution history.
- Cross-project resume/fork is rejected.

## Execution tool controls

Restrict which execution stages Commander may use for this request:

```bash
# Conversation only — no task proposals admitted
coordinator --no-tools -p "解释当前状态"

# Allow only specific stages (aliases: grep→search, write→edit)
coordinator --tools read,grep -p "只读检查风险"

# Exclude specific stages from the server policy
coordinator --exclude-tools push,merge -p "修复但不要发布"
```

Vocabulary: `read`, `search`, `test`, `edit`, `commit`, `push`, `merge`.

Precedence:

- `--tools` and `--no-tools` are mutually exclusive.
- `--tools` and `--exclude-tools` may be combined; exclusion wins.
- Restrictions never enable stages that server-side repo policy forbids.

JSON mode includes the effective `execution_policy` in the output.

## RPC mode

```bash
coordinator --mode rpc -p "/status"
```

RPC mode is headless and prints one JSON-encoded Supervisor protocol
`ResponseEnvelope`. It is intentionally protocol-level and versioned by
`protocol_version`. JSON mode remains the smaller stable public CLI schema.

Errors also produce a valid `ResponseEnvelope` with `ok: false` and
`request_id` prefixed `cli-local-`.

## Task control (Phase 5.5)

```bash
coordinator --print -p "/approve <task-id>"
coordinator --print -p "/cancel <task-id>"
coordinator --print -p "/retry <task-id>"
coordinator --print -p "/task <task-id> log"
```

- `/approve` transitions `awaiting_human` → merge path.
- `/cancel` transitions running → failed/blocked; releases lease.
- `/retry` transitions failed/blocked → ready; respects `max_attempts`.
- `/task <id> log` returns incremental log tail (cap 64 KiB).

## Dashboard (Phase 5.5)

```bash
coordinator --print -p "/dashboard"
```

Returns per-project: goal status, task counts by state, active workers, and
strategic counts (`active_milestones`, `pending_recoveries`, `overnight_summaries`).
Also returns aggregate `autonomous_runs` counts (`running`, `paused`, `stopped`).
No `project_id` required; aggregate counts only (no cross-project title leakage).

## Strategic autonomy (Phase 7)

```bash
coordinator --print -p "/strategy"
coordinator --print -p "/recoveries"
coordinator --print -p "/agents"
coordinator --print -p "/overnight"
coordinator --print -p "/overnight start --until 08:00"
```

All commands route through Supervisor RPC — never local shortcuts:

| Slash | RPC | What you see |
|---|---|---|
| `/strategy` | `project.strategy` | Current milestone title, priority, active count |
| `/recoveries` | `project.recoveries` | Pending recovery proposals for failed tasks |
| `/agents` | `project.agents` | Per-agent scorecards and routing preference |
| `/overnight` | `project.overnight` | Quiet-hour window and latest overnight summary |

- **Milestones** are project-scoped. `/strategy` never shows another project's
  milestone titles.
- **Recoveries** are deduped per failed task. At most one open proposal exists;
  admission requires a recorded `fail`/`blocked` evaluation and creates a normal
  ready backlog item (not an immediate worker task).
- **Agent scorecards** influence worker routing only among capable agents. One
  agent on cooldown does not block others.
- **Overnight quiet hours** pause autonomous run ticking without killing active
  workers. Configure in `policy.toml`:

```toml
[overnight]
quiet_start = "22:00"
quiet_end = "08:00"
enabled = false
```

`/loop status` also reports `current_milestone` when an active milestone exists.

## Evidence review gates (Phase 8)

```bash
coordinator --print -p "/evidence <task-id>"
coordinator --print -p "/review <task-id>"
coordinator --print -p "/risk <task-id>"
coordinator --print -p "/merge-ready <task-id>"
```

All commands route through Supervisor RPC — never local DB shortcuts:

| Slash | RPC | What you see |
|---|---|---|
| `/evidence` | `project.evidence` | Durable command, diff, and acceptance evidence rows |
| `/review` | `project.review` | Completion gate status, blockers, risk, packet paths |
| `/risk` | `project.risk` | Latest risk level, reasons, human-review flag |
| `/merge-ready` | `project.merge_ready` | Merge readiness under repo `review_policy` and evidence |

- **Evidence** is project-scoped. `/evidence` never returns another project's
  command output or failure summaries.
- **Failed commands** are stored with `status: failed` and block the completion
  gate; they cannot be hidden from evidence listings.
- **Code tasks** require durable changed-file evidence. No-op worker runs fail
  the completion gate and are classified as risky.
- **Acceptance criteria** must map to `acceptance` evidence (or rule-inferred
  coverage from passing verification + changed files) before a task can reach
  `done`.
- **Reviewer verdicts** (`rules-v2`) are written by the server-side evaluator
  only. Worker stdout cannot forge approve/reject verdicts.
- **Review packets v2** are written under
  `<repo>/.coordinator/review_packets_v2/<task-id>.{json,md}` with secret
  redaction. Paths cannot escape the repo root.
- **`/merge-ready`** respects existing repo policy. It does not expand
  auto-merge; risky migrations, protected paths, or `review_policy` human-review
  rules return `merge_ready: false` even when verification passed.

After worker attempts, the engine records command and diff evidence automatically.
The done transition runs `evaluate_completion_evidence` and `assess_task_risk`
before marking a task `done` or routing it to `awaiting_human`.

## GitHub delivery loop (Phase 9)

Delivery sits **after** local merge-readiness. It does not bypass evidence gates,
repo allowlists, `allow_push`, or human-review policy.

```bash
coordinator --print -p "/merge-ready <task-id>"
coordinator --print -p "/deliver <task-id>"
coordinator --print -p "/prs"
coordinator --print -p "/ci <task-id>"
coordinator --print -p "/delivery <task-id>"
coordinator --print -p "/merge-policy"
```

| Slash | RPC | What you see |
|---|---|---|
| `/deliver` | `project.deliver` | Policy decision, blockers, PR URL when allowed |
| `/prs` | `project.prs` | Project-scoped open delivery PR records |
| `/ci` | `project.ci` | Poll GitHub checks for the task's delivery PR |
| `/delivery` | `project.delivery` | Durable delivery record for one task |
| `/merge-policy` | `project.merge_policy` | Per-repo `allow_push`, `merge_policy`, `review_policy` |

- **`/deliver`** evaluates Phase 8 evidence and merge-readiness before calling
  `gh`. When `allow_push=false` or human review is required, delivery is blocked
  with explicit blockers — no push or PR is created.
- **PR bodies** are built from server-side review packets and evidence summaries.
  Worker stdout cannot forge reviewer verdicts in the PR description.
- **CI state** is classified as pending, pass, fail, cancelled, or skipped from
  `gh pr checks`. Failed CI creates at most one open `ci_repair` recovery
  proposal per delivery record.
- **Delivery records** and events are stored in SQLite (migration 019) and are
  always scoped to the current project.

For local testing, set `COORDINATOR_GH_EXECUTABLE` and `COORDINATOR_GH_PREFIX`
to point at `tests/fixtures/fake_gh.py`. Production uses the real `gh` binary
with argv-only invocation (no shell interpolation).

## Operator control tower (Phase 10)

One project-scoped command center for human attention, delivery state, recovery,
and autonomous run health.

```bash
coordinator --print -p "/inbox"
coordinator --print -p "/attention"
coordinator --print -p "/summary"
coordinator --print -p "/summary morning"
coordinator --print -p "/notify --dry-run"
coordinator --print -p "/decision <item-id>"
coordinator --print -p "/dismiss <item-id>"
coordinator operator summary --json
```

| Slash | RPC | What you see |
|---|---|---|
| `/inbox` | `operator.inbox` | All open operator items for the current project |
| `/attention` | `operator.attention` | Warning/error/critical items only |
| `/summary` | `operator.summary` | Counts and redacted highlights |
| `/notify` | `operator.notify` | Durable notification deliveries (`--dry-run` previews) |
| `/decision` | `operator.decision` | Routes to existing safe RPC (dry-run by default in print mode) |
| `/dismiss` | `operator.dismiss` | Marks an item dismissed |

- **Inbox items** are durable, deduped, and project-scoped (migration 020).
- **`operator.decision`** never mutates tables directly; it routes through
  `project.task.approve`, `project.task.retry`, `project.task.cancel`,
  `project.deliver`, or read-only RPCs.
- **Notifications** use local `file`/`stdout` sinks by default; the `command`
  sink requires `policy.notifications.allow_command_sink = true` and receives
  JSON on stdin (no shell interpolation).
- **Summaries** are deterministic and redacted — no raw prompts, tokens, or log bodies.

## Project brain (Phase 11)

Per-project indexing, knowledge cards, bounded context packets, and durable
memories from task outcomes.

```bash
coordinator --print -p "/brain"
coordinator --print -p "/map"
coordinator --print -p "/where add retry policy"
coordinator --print -p "/why src/local_cli_coordinator/db.py"
coordinator --print -p "/impact src/local_cli_coordinator/db.py"
coordinator --print -p "/context task-abc"
```

| Slash | RPC | Notes |
|---|---|---|
| `/brain` | `project.brain` | Latest snapshot (git head, file count) |
| `/map` | `project.map` | Knowledge cards with citations |
| `/where` | `project.where` | Heuristic path matches for a query |
| `/why` | `project.why` | Related paths for a target file |
| `/impact` | `project.impact` | Alias of `/why` |
| `/context` | `project.context` | Bounded, redacted context packet for a task |

- Indexer honors `.gitignore`, skips secrets/vendor dirs, redacts at ingest.
- Context packets prune low-priority cards before failing on budget.
- Commander and worker prompts cite persisted `packet_id` for audit.

## Autonomous loop (Phase 6 / 6B / 6C)

```bash
coordinator --print -p "/loop"
coordinator --print -p "/loop start"
coordinator --print -p "/loop run"
coordinator --print -p "/loop step"
coordinator --print -p "/backlog"
coordinator --print -p "/evals"
```

- `/loop` — active goal, last iteration decision, backlog counts, active run.
- `/loop start` — start an unattended autonomous run session (requires autonomy).
- `/loop run` — show active run id, iteration count, idle count.
- `/loop pause` / `/loop resume` / `/loop stop` — control the active session.
- `/loop step` — run one bounded iteration only; does **not** start a session.
- `/backlog` — latest backlog items with status and linked task ids.
- `/evals` — latest task evaluations (verdict, summary, next_action).

When an active goal has no ready backlog, `/loop step` may ask Commander for
small proposals. Those proposals become backlog items first — they are **not**
admitted as worker tasks in the same iteration. A later tick promotes ready
backlog into tasks.

Example `/loop` output after generation:

```text
Loop status [proj-example]
  last: generate — generated 1 backlog draft(s)
```

Autonomy is **off by default**. Enable per-repo in `repos.toml`:

```toml
[[repos]]
path = "/Users/xiafan/polymarket-crypto-threshold"
autonomy_enabled = true
```

Or globally in `policy.toml`:

```toml
[autonomy]
enabled = true
max_iterations_per_tick = 1
max_evaluations_per_iteration = 3
max_admissions_per_iteration = 1
max_generated_backlog_per_iteration = 3
commander_generation_timeout_seconds = 45
```

See [autonomous-loop](autonomous-loop.md) for full configuration and failure modes.

## Worker-state snapshots (Phase 6D)

Every worker terminal path (success, failure, cancel) writes a redacted
`post_attempt` snapshot to `worker_state_snapshots` (migration 016). Snapshots
record command, cwd, exit code, changed files, and verification summary — not
environment values, tokens, or full prompt text.

Inspect the latest snapshot from task detail:

```bash
coordinator --print -p "/task <task-id>"
coordinator --mode json -p "/task <task-id>"
```

The JSON payload includes `worker_state.snapshot_id`, `state_type`, and
`log_path` when a snapshot exists.

## Event schema v2 replay (Phase 6D)

Legacy `supervisor_events` remain authoritative for TUI replay. Newly published
events are mirrored into `supervisor_events_v2` with monotonic per-project `seq`
and a `legacy_cursor` link.

Replay v2 events through the Supervisor RPC:

```bash
coordinator --mode rpc -p "/status"   # any connected project context
```

Or call `events.v2.replay` with params `{"after": 0, "limit": 100}` via the
Supervisor client. Canonical names include `task.created`, `task.failed`,
`commander.completed`, `loop.iteration`, and `run.started`.

## Mock-provider parity harness (Phase 6D)

Run deterministic Commander or worker fixtures without live model binaries:

```bash
coordinator mock-provider run commander \
  --fixture tests/fixtures/commander/one-task.json
coordinator mock-provider run worker \
  --fixture tests/fixtures/worker/success.json
```

The harness validates fixture schema, renders the configured agent command,
checks prompt file existence (Commander), and asserts output shape. It never
calls network or live model CLIs. Configure agents with `mock-provider` in the
command template to route production invocations through the harness in CI.