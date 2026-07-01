# Coordinator TUI Operator Guide

The Coordinator TUI is a terminal interface for chatting with Commander, watching
live task activity, and controlling project scheduling. One detached Supervisor
serves every open TUI session.

## Open the TUI

From inside a Git repository (root or any subdirectory):

```bash
coordinator
```

Coordinator resolves the repository, ensures the Supervisor is running, and
launches the packaged TUI bundle. No socket path or project ID is required.

**Outside a Git repository**, Coordinator prints a concise error and exits with
code 2.

## Project onboarding

The first time you open a repository, Coordinator shows a **one-time confirmation
screen** before chat:

- Canonical path and repo id
- Default branch and branch prefix
- Verification commands detected in the repo
- Policies: push, merge, and review settings
- Budget defaults: max tasks per day and max task runtime (seconds)

**Enter** accepts and registers the project. **Esc** rejects and exits without
writing anything.

There is no “always trust parent directory” shortcut. Each repository is confirmed
explicitly.

### Moved repositories

If a registered project’s files moved to a new path, the onboarding screen warns
that the repository moved and asks you to confirm the new canonical location.

## Commander-backed chat (Phase 5)

Wave 4 routed chat through the Supervisor but only echoed `Received: {text}`.
Phase 5 connects chat to the real Commander service: messages are inspected,
safe task proposals are admitted into the existing pipeline, and results appear
as coordinator messages plus `task.created` / `commander.completed` events.

**Chat requires an active goal.** Before sending plain-language messages:

1. Create a draft goal: `/goal Continue the roadmap while keeping tests green`
2. Confirm it: `/goal confirm`
3. Chat normally at the `❯` prompt

Until you confirm, chat is rejected with `Goal is draft. Run /goal confirm before
chatting.` Use `/goal` with no arguments to view the current goal and status.

While Commander runs, the transcript shows `Commander is thinking…` and admits
or rejects proposed tasks with reasons. A single Commander run is in flight per
goal at a time.

## Trusted runtime (Phase 5.2)

Coordinator compares the running Supervisor against the installed TUI bundle.
If the server is too old or missing required capabilities, launch fails with:

```text
Supervisor is incompatible with this Coordinator install.
Run: coordinator supervisor restart
```

Repair with:

```bash
coordinator supervisor restart
```

This sends a graceful shutdown, waits for the socket and lock to clear, then
starts one fresh Supervisor process with a new PID.

## Conversation vs tasks vs slash commands

| Input kind | Example | Behavior |
|---|---|---|
| Greeting / status question | `你好`, `如何启动？` | Commander answers in natural language; **no tasks** are created |
| Explicit task request | `创建一个只读任务，运行 uv run ruff check…` | Commander may admit tasks after policy checks |
| Slash command | `/tasks`, `/task <id>` | Deterministic local or RPC handling; **never** sent to Commander |
| Unknown slash | `/taskz` | Local error: `Unknown command: /taskz. Use /help.` |

Visible chat text is Commander's `user_reply`. Internal orchestration memory and
policy rejection details stay in `commander.completed` diagnostics (expandable in
the activity area), not in the main transcript.

## Plain-language use

Type natural-language messages at the `❯` prompt:

- Steer an active goal (“focus on tests first”, “pause risky changes”)
- Ask Commander to propose the next small batch of tasks
- Request status updates or course corrections

Messages that do not start with `/` are sent as `chat.send` to Commander (not a
local echo). The TUI waits for the Supervisor's persisted `chat.message` event
so your text appears once. Slash commands call Supervisor RPC methods (except
`/help`, which is generated locally) and render structured results in the
transcript.

When Commander admits a task, the activity block shows its **title**, **goal**,
and **verification commands** immediately — not only an opaque task id.

**Report-only tasks** (baseline/acceptance checks with `tests` capability and no
code edits) may finish `done` after verification even when no files changed.
Code-edit tasks still fail with `no changed files` when the agent produces no
patch.

When the connection is **paused** or **offline**, the composer shows
`(paused — type to queue)` and queues input until the connection returns.

## Slash commands

Type `/` followed by a command name. Tab completes partial commands while the
input starts with `/`.

| Command | What it does |
|---|---|
| `/status` | Show task counts, paused/stopped state |
| `/tasks` | List project tasks (title, state, goal summary, latest note) |
| `/task <id>` | Show one task in detail (goal, verify commands, last event, attempt log) |
| `/plan` | Show active goal, autonomous run, backlog, and next action |
| `/scan` | Read-only diagnostics (git tree, verify commands, failed tasks, agents) |
| `/strategy` | Show current milestone objective (`project.strategy`) |
| `/evidence <id>` | Show durable task evidence (`project.evidence`) |
| `/review <id>` | Show evidence review summary (`project.review`) |
| `/risk <id>` | Show task risk assessment (`project.risk`) |
| `/merge-ready <id>` | Check merge readiness under repo policy (`project.merge_ready`) |
| `/deliver <id>` | Deliver task branch to GitHub under policy (`project.deliver`) |
| `/prs` | List project delivery PR records (`project.prs`) |
| `/ci <id>` | Poll GitHub CI for a task delivery (`project.ci`) |
| `/delivery <id>` | Show delivery record for a task (`project.delivery`) |
| `/merge-policy` | Show repo merge and push policy (`project.merge_policy`) |
| `/heal` | Run bounded PR self-healing cycle (`project.pr.heal`, dry-run) |
| `/stale` | List stale delivery PRs (`project.pr.health`) |
| `/ci failures` | List PRs with failed CI (`project.pr.health`) |
| `/reviews` | Ingest unresolved PR review comments (`project.pr.reviews`) |
| `/pr update <id>` | Refresh PR evidence section (`project.pr.update_evidence`) |
| `/rebase <id>` | Dry-run safe rebase (`project.pr.rebase`) |
| `/inbox` | Show operator inbox (`operator.inbox`) |
| `/attention` | Show items needing attention (`operator.attention`) |
| `/summary` | Operator summary (`operator.summary`) |
| `/notify` | Dispatch notifications (`operator.notify`, use `--dry-run`) |
| `/notify test` | Dry-run notification delivery test (`operator.notify`) |
| `/approvals` | List pending external approval requests (`operator.approvals`) |
| `/channels` | Show approval channel configs (`operator.channels`) |
| `/approve token` | Approve external approval token (`operator.approval.approve`) |
| `/reject` | Reject external approval token (`operator.approval.reject`) |
| `/decision` | Route safe action for inbox item (`operator.decision`) |
| `/dismiss` | Dismiss inbox item (`operator.dismiss`) |
| `/recoveries` | List pending failure recovery proposals |
| `/agents` | Show agent scorecards and routing preference |
| `/overnight` | Quiet-hour window and latest overnight summary |
| `/dashboard` | Daily operator view: pause state, counts, approvals, health, next actions |
| `/doctor` | Safe doctor repair dry-run (`operator.doctor`) |
| `/repair` | Plan repairs (default dry-run; `--apply` requires confirmation) |
| `/health` | Agent health from durable attempt records (`operator.health`) |
| `/morning` | Morning handoff summary (`operator.morning`) |
| `/why <task-id>` | Explain task failure (`operator.explain_failure`; paths still use `project.why`) |
| `/pause all` | Pause all projects globally (`global.pause`) |
| `/resume all` | Resume projects paused by the last global pause (`global.resume`) |
| `/jump <target>` | Resolve a task, log, goal, worktree, or supervisor log path (hint only) |
| `/open <target>` | Alias of `/jump` (does not launch an editor) |
| `/logs` | Show recent logs |

### PR and CI self-healing (Phase 12)

`/heal`, `/stale`, `/ci failures`, `/reviews`, `/pr update`, and `/rebase` call
Supervisor RPC methods. They watch delivery records for the **current project**
and never force-push or merge without explicit policy.

- `/heal` — dry-run bounded cycle: watch PR health, classify CI failures, skip
  duplicate repair proposals.
- `/stale` — list PRs whose base branch advanced locally or on GitHub.
- `/ci failures` — list deliveries with failed check state.
- `/reviews` — fetch unresolved review comments via `gh`, store as evidence,
  create operator items and brain memories (untrusted text only).
- `/pr update <delivery-id>` — dry-run evidence refresh; prior failure sections
  remain in the PR body.
- `/rebase <delivery-id>` — dry-run rebase in a detached worktree; conflicts
  leave the main worktree clean. `--apply` requires `allow_push` and passes
  human-review gates.

### Operator control tower (Phase 10)

`/inbox`, `/attention`, `/summary`, `/notify`, `/decision`, and `/dismiss` call
Supervisor RPC methods. Items are project-scoped; summaries and notifications
are redacted.

- `/inbox` — durable operator items from tasks, delivery, recovery, runs, and config.
- `/attention` — warning, error, and critical items only.
- `/summary` — counts and highlights; `/summary morning` adds overnight context.
- `/notify --dry-run` — preview notification deliveries without side effects.
- `/decision <item-id>` — returns routed RPC; destructive actions require confirmation.
- `/dismiss <item-id>` — dismiss an open item without mutating source state.

### GitHub delivery commands (Phase 9)

`/deliver`, `/prs`, `/ci`, `/delivery`, and `/merge-policy` call Supervisor RPC
methods. They never bypass the socket connection or read delivery tables locally.

- `/deliver <task-id>` — evaluate policy, create or update an evidence-backed PR,
  poll CI, and record delivery status. Blocked when merge-readiness or repo
  policy forbids push.
- `/prs` — open delivery records with PR numbers for the **current project only**.
- `/ci <task-id>` — refresh check state for the task's delivery PR.
- `/delivery <task-id>` — durable delivery status, PR URL, CI state, and
  evidence packet path.
- `/merge-policy` — configured `allow_push`, `merge_policy`, and `review_policy`
  per repo (does not imply auto-merge).

### Evidence review commands (Phase 8)

`/evidence`, `/review`, `/risk`, and `/merge-ready` call Supervisor RPC
methods. They require a task id argument and never bypass the socket connection
or read the database locally.

- `/evidence <task-id>` — command, diff, and acceptance evidence rows for the
  current project only.
- `/review <task-id>` — completion gate (`completion_allowed`), blockers, risk
  level, and review packet v2 paths when present.
- `/risk <task-id>` — latest persisted risk assessment with human-review flag.
- `/merge-ready <task-id>` — whether the task is merge-ready under repo
  `review_policy` and durable evidence (does not force merge or expand
  auto-merge policy).

Review packets v2 live under `.coordinator/review_packets_v2/` inside the
project repo. Credential-like strings are redacted in JSON and Markdown output.

### Strategic autonomy commands (Phase 7)

`/strategy`, `/recoveries`, `/agents`, and `/overnight` call Supervisor RPC
methods. They never bypass the socket connection or read the database locally.

- `/strategy` — highest-priority active milestone for the current project.
- `/recoveries` — bounded repair/diagnostic proposals for failed terminal tasks.
- `/agents` — per-agent successes, failures, cooldowns, and preferred rank.
- `/overnight` — configured quiet window (`quiet_start`–`quiet_end`) and the
  latest persisted overnight summary (task counts only; no prompts or secrets).
- `/overnight start --until 08:00` — passes schedule args to `project.overnight`.

`/dashboard` adds per-project strategic counts: active milestones, pending
recoveries, and overnight summary count — still without cross-project task titles.

### Operability commands (Phase 6D)

`/plan`, `/scan`, `/jump`, and `/open` call Supervisor RPC methods
(`project.plan`, `project.scan`, `project.jump`). They are read-only diagnostics
— no editor, shell, or filesystem mutation is spawned from the TUI.

- `/plan` — active goal summary, autonomous run status, backlog counts, and the
  next scheduler decision.
- `/scan` — repo cleanliness, configured verify commands, missing agent
  binaries, and recent failed tasks.
- `/jump <task-id|goal|log|worktree>` — resolves an absolute path or command
  hint for manual inspection.
- `/open <target>` — same as `/jump`; displays the hint only.

Failed or cancelled tasks may include a worker-state snapshot reference in
`/task <id>` detail (see [cli.md](cli.md#worker-state-snapshots-phase-6d)).
| `/help` | List available commands (local; works offline) |
| `/pause` | Pause scheduling for this project |
| `/resume` | Resume scheduling |
| `/stop` | Stop this project at the next safe boundary (**destructive**) |
| `/shutdown` | Shut down the entire Supervisor (**destructive**) |
| `/new` | Start a new conversation |
| `/goal` | Create draft goal (`/goal <objective>`), confirm (`/goal confirm`), or view status (`/goal`) |
| `/project` | Switch project context |
| `/help` | List available commands |
| `/quit` | Detach the TUI (workers keep running) |

### Destructive commands

`/stop` and `/shutdown` require **double confirmation**. Type the same command
twice in a row to proceed. Coordinator prints a confirmation prompt after the
first attempt.

## Activity blocks

While tasks run, the transcript shows **activity blocks** — one per task:

| Icon | Meaning |
|---|---|
| `●` | Running / in progress |
| `⟐` | Verification |
| `◉` | Review |
| `⎇` | Git operation |
| `✓` | Done |

Each block shows the task title, agent name, current stage, and elapsed time.

### Expanding activity

Press **Tab** (when not completing a slash command) to expand the focused activity
block. Expanded blocks show:

- The latest shell command
- The last ten output lines
- A bordered detail view

At terminal widths under 60 columns, activity stays compact even when expanded.

### Fallback display

When a worker agent stalls asking for interactive approval, Coordinator may hand
the task to a configured fallback agent. The activity block shows a yellow
warning:

```
⚠ worker-a→worker-b
```

Expanded view adds usage counts: `fallback: worker-a → worker-b (1/1)`.

At most one fallback attempt is made per task. If both agents are blocked, the
task moves to human review.

## Detach vs stop vs shutdown

| Action | Effect on Supervisor | Effect on workers |
|---|---|---|
| **Detach** (`/quit`, Ctrl+C) | Keeps running | Keep running |
| **Stop** (`/stop`) | Keeps running | This project stops at safe boundary |
| **Shutdown** (`/shutdown`) | Shuts down | All projects stop |

**Detach** closes your terminal session cleanly and restores canonical terminal
mode. Workers and other TUI sessions are unaffected. Re-run `coordinator` from
the same repo to reconnect; missed events replay from your last cursor.

**Stop** pauses new work for one project. Use `/resume` to continue.

**Shutdown** ends the global Supervisor. The next `coordinator` launch starts
a fresh Supervisor.

## Multiple project terminals

Open one TUI per repository in separate terminal windows or tabs:

```bash
# Terminal 1
cd ~/projects/app-a && coordinator

# Terminal 2
cd ~/projects/app-b && coordinator
```

All sessions share one Supervisor and one Unix socket. Each TUI subscribes only
to its own `project_id`; foreign events are ignored.

You can detach every client and workers continue. Reconnecting replays events
from the stored cursor.

## Budgets and caps

Budget defaults appear on the onboarding screen:

- **Max tasks per day** — daily task cap for the project
- **Max task runtime (seconds)** — per-task timeout

The daemon also enforces global caps configured in `config/policy.toml` (per-run
limits, consecutive-failure circuit breakers, etc.). When a cap is reached, the
Supervisor stops scheduling and records the reason. Check `/status` or
`coordinator status --loop` for counts and stop reasons.

## Human-review states

Tasks that fail independent review or exhaust fallback attempts move to
`awaiting_human`. In the TUI:

- Activity stages show `review: …` during spec and quality review
- Completed review failures appear as `done: awaiting_human` or similar
  terminal stages
- `/status` includes task counts by state, including `awaiting_human`

Review packets are written to `tasks/review/` on disk. Approve or reject from
the administrative CLI:

```bash
coordinator task show <task_id>
coordinator status --loop
```

## Connection states

The header shows connection state:

| State | Meaning |
|---|---|
| `connected` | Live event stream |
| `reconnecting` | Temporary disconnect; replay in progress |
| `offline` | Cannot reach Supervisor |

While reconnecting, destructive-command confirmations are cleared for safety.

## Keyboard reference

| Key | Action |
|---|---|
| Enter | Submit message or confirm onboarding |
| Esc | Reject onboarding and exit |
| Tab | Complete `/` commands; expand activity |
| Up / Down | Input history |
| Ctrl+C | Detach (same as `/quit`) |

## See also

- [Installation](install.md)
- [Migration](migration.md)
- [Troubleshooting](troubleshooting.md)