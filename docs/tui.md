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

## Plain-language use

Type natural-language messages at the `❯` prompt:

- Steer an active goal (“focus on tests first”, “pause risky changes”)
- Ask Commander to propose the next small batch of tasks
- Request status updates or course corrections

Messages that do not start with `/` are sent as `chat.send` to Commander (not a
local echo). Slash commands call Supervisor RPC methods and render structured
results in the transcript.

When the connection is **paused** or **offline**, the composer shows
`(paused — type to queue)` and queues input until the connection returns.

## Slash commands

Type `/` followed by a command name. Tab completes partial commands while the
input starts with `/`.

| Command | What it does |
|---|---|
| `/status` | Show task counts, paused/stopped state |
| `/tasks` | List project tasks |
| `/logs` | Show recent logs |
| `/agents` | List active agents |
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