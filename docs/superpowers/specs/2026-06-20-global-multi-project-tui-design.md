# Global Multi-Project Coordinator TUI Design

Date: 2026-06-20
Status: Approved for planning

## Summary

Coordinator becomes a globally invokable local orchestration service. From any
Git repository, the operator runs "coordinator" to open a conversational TUI
scoped to that project. One global Supervisor manages multiple project loops,
while multiple TUI clients attach, detach, and reconnect independently.

The interface reuses selected MIT-licensed Hermes Agent React Ink components.
Coordinator retains its own gateway protocol, orchestration, persistence,
project registry, security policies, and agent execution.

## Goals

- Launch with "cd <repo> && coordinator".
- Provide chat with embedded live execution details.
- Keep project work running after the TUI disconnects.
- Run multiple project loops under one global Supervisor.
- Preserve allowlisting, worktrees, budgets, reviews, and audit logs.
- Reuse Hermes terminal UI behavior without importing its agent runtime.
- Migrate the current installation without losing history.

## Non-Goals

- Multiple independent Supervisors against the same state.
- A network-accessible Supervisor.
- Replacing Coordinator workers with the Hermes agent runtime.
- Copying Hermes model, MCP, memory, skill, or provider backends.
- Automatically trusting an unregistered repository.
- A graphical desktop or browser application.

## User Experience

### Launch and Onboarding

The launcher resolves the current Git root, connects to the global Supervisor,
and opens the TUI. If the Supervisor is absent, it starts it once and waits for
readiness.

A registered project restores its latest project session. An unregistered
project shows a one-time confirmation with:

- canonical repository path and identity;
- default branch and branch prefix;
- detected or proposed verification commands;
- push, merge, and review policies;
- budget defaults.

Confirmation adds the canonical repository to the allowlist. Rejection exits
without changing global configuration or the repository.

### Main Layout

The selected layout is hybrid chat plus activity:

- Header: project, goal state, Supervisor health, task counts, and budget.
- Transcript: operator and Coordinator conversation.
- Activity blocks: compact execution summaries embedded in the transcript.
- Composer: multiline input with history and slash completion.
- Footer: connection state and contextual keyboard hints.

An activity block shows task ID, title, agent, stage, elapsed time, fallback use,
latest command, and latest output. Tab expands the active block to its full live
stream and collapses it again. Narrow terminals remain usable because detail is
expandable instead of occupying a permanent side panel.

### Chat Semantics

A plain message is a project Commander instruction:

- With no active goal, it creates and activates a goal and starts planning.
- With an active goal, it appends an instruction to goal context.
- New instructions apply at the next task boundary by default.
- Ambiguous or policy-sensitive instructions produce a chat clarification.
- A message never silently terminates a worker that is modifying code.

Initial slash commands:

~~~text
/status  /tasks  /logs  /agents
/pause   /resume /stop  /shutdown
/new     /goal   /project
/help    /quit
~~~

"/quit" detaches the TUI. "/stop" stops the current project at a safe boundary.
"/shutdown" gracefully stops the global Supervisor.

## Architecture

~~~text
coordinator launcher
        |
        | current Git root + local Unix socket
        v
Coordinator React Ink TUI
        |
        | versioned JSON request/event protocol
        v
Global coordinatord Supervisor
        |
        +-- Project A loop -- worktrees -- workers/reviewers
        +-- Project B loop -- worktrees -- workers/reviewers
        +-- Project C loop -- worktrees -- workers/reviewers
~~~

### Launcher

The Python package exposes "coordinator" and the internal "coordinatord".
Existing administrative subcommands remain available as
"coordinator <subcommand>". No subcommand opens the TUI.

The launcher resolves the project, ensures the Supervisor, and starts the bundled
Node TUI. It never imports from /Users/xiafan/.hermes at runtime.

### Global Supervisor

Exactly one Supervisor owns the global database and scheduler. It provides:

- project registration and canonical path lookup;
- project-scoped goals, messages, tasks, events, and logs;
- worker and reviewer execution;
- global and per-project concurrency;
- budget enforcement and circuit breaking;
- task leases and crash recovery;
- event subscription and replay;
- project stop and global shutdown.

A process lock plus socket ownership prevents a second Supervisor from using the
same state directory.

### Project Loops and Scheduling

Each repository has an independent logical loop. Project loops share the global
agent pool and budget authority but never share goals, queues, worktrees, chat
history, or event streams.

Fair round-robin scheduling operates among runnable projects subject to:

- global maximum running tasks;
- per-project maximum running tasks;
- per-agent concurrency;
- daily task and runtime budgets;
- circuit-breaker state;
- repository integration locks.

One project cannot consume every scheduling turn while another has ready work.

### Local Protocol

The Supervisor listens only on a user-restricted Unix domain socket. It never
binds TCP.

Messages are newline-delimited, versioned JSON. Requests have protocol version,
request ID, project ID, method, and parameters. Responses correlate by request
ID. Events carry a monotonic per-project cursor.

A reconnecting TUI sends its last cursor, receives retained events after that
cursor, then switches to live delivery. The transport is separated from domain
operations so future clients need not import TUI code.

## Persistence

Global locations:

~~~text
~/.config/coordinator/       configuration and repo allowlist
~/.local/share/coordinator/  database, logs, prompts, patches, and chat
~/.local/state/coordinator/  socket, PID, lock, and runtime metadata
~~~

Persistence adds or formalizes:

- projects and canonical repository identities;
- project-scoped sessions and messages;
- Supervisor lifecycle records;
- client event cursors;
- project scheduler state;
- project references for goals, tasks, events, attempts, leases, and artifacts.

Full command output stays in attempt logs. Database events retain concise,
structured replay summaries.

## Background Lifecycle

TUI and Supervisor have separate lifetimes:

- Opening a TUI attaches to the existing Supervisor.
- Closing a TUI does not stop work.
- Multiple TUIs may attach to different projects.
- Multiple clients for one project share its session and never duplicate its loop.
- Ctrl+C detaches safely and restores terminal modes.
- Only explicit shutdown, fatal corruption, or an external service action stops
  the Supervisor.

Projects can be paused or stopped independently.

## Hermes TUI Reuse

Hermes Agent is MIT licensed by Nous Research. Coordinator retains its copyright
and license in THIRD_PARTY_NOTICES.md.

Candidate components to adapt:

- React Ink rendering and terminal setup/cleanup;
- multiline input, history, and completion;
- Markdown and streaming Markdown;
- transcript virtualization and scrolling;
- slash-command registry patterns;
- status line and overlays;
- graceful exit, resize, mouse, clipboard, and OSC52 behavior;
- selected terminal regression tests.

Coordinator does not copy Hermes gateway dispatch, agent creation, providers,
MCP discovery, memory, skills, delegation, or session execution. Hermes-specific
names, commands, telemetry, and configuration are removed.

Adapted source lives under ui-tui/ in Coordinator with clear provenance. The
installed product never depends on the local Hermes installation.

## Packaging

TypeScript is bundled to JavaScript during development and release builds. The
bundle and license notices ship as Python package data. The launcher locates them
through package resources, not a source checkout path.

Installation produces a global executable without PYTHONPATH. The implementation
plan will select the exact installer based on existing Python packaging and the
local toolchain.

## Migration

A one-time migration imports the current installation configuration, database,
logs, artifacts, and repositories into global directories.

Migration must:

- create a timestamped backup before writes;
- stage changes atomically where supported;
- be idempotent;
- retain task, goal, event, lease, and run history;
- validate schema and artifact references before activation;
- print recovery instructions on failure;
- never delete the original installation automatically.

The existing polymarket-crypto-threshold project retains its current policy.

## Safety

- Unregistered repositories require explicit confirmation.
- Canonical paths prevent duplicate identities from aliases or subdirectories.
- A moved repository requires reconfirmation.
- Workers retain Coordinator capabilities, worktrees, policy, and verification.
- Supervisor/TUI transport cannot grant worker, reviewer, or merge authority.
- Chat passes through normal policy and decomposition checks.
- Sensitive output remains local and is never served over TCP.
- Human-review and merge policies remain project-specific.
- The single-fallback limit applies independently to each project.

## Failure Handling

- TUI crash: restore terminal modes; Supervisor and workers continue.
- Supervisor absent: retry, attempt one controlled start, then show diagnostics.
- Socket disconnect: reconnect with backoff and replay from the last cursor.
- Stale lease: use the existing auditable lease recovery policy.
- Missing repository: block that project and request path confirmation.
- Migration failure: preserve old data and refuse activation.
- Protocol mismatch: show supported versions and exit cleanly.
- Slow client: bound its queue and require cursor replay.
- Missing TUI bundle: preserve administrative CLI and report a repair command.

## Delivery Phases

### Phase 1: Global Runtime Foundation

- global path resolution;
- project registry and onboarding operations;
- versioned local protocol;
- Supervisor lifecycle and single-instance lock;
- migration tooling.

### Phase 2: Multi-Project Supervisor

- project-scoped persistence;
- fair scheduling;
- shared concurrency and budgets;
- multi-client subscriptions and replay;
- project pause, stop, and crash recovery.

### Phase 3: Hermes TUI Adaptation

- licensed component import and attribution;
- hybrid chat/activity layout;
- composer, history, Markdown, scrolling, and slash commands;
- event rendering, expansion, reconnect, and terminal cleanup.

### Phase 4: Installation and End-to-End Experience

- bundled TUI packaging;
- global executable installation;
- first-run migration;
- operator documentation;
- multi-project soak and failure testing.

Each phase is independently testable and accepted before the next phase changes
its contracts.

## Testing

Unit and contract tests cover:

- project root canonicalization and onboarding;
- global path resolution;
- protocol schema, correlation, cursor ordering, and replay;
- scheduler fairness and concurrency limits;
- project isolation in persistence queries;
- Supervisor single-instance behavior;
- migration idempotency and rollback;
- slash parsing and state transitions;
- adapted terminal utilities and components.

Integration tests cover:

- launcher starts or attaches to exactly one Supervisor;
- two clients on one project do not duplicate work;
- three projects receive only their own events;
- reconnect replays missed events exactly once;
- project pause leaves other projects running;
- Supervisor restart recovers without duplicate task execution;
- worker output becomes an activity block;
- fallback events identify both agents.

Real PTY tests cover:

- launch from a target repository;
- first-run registration and rejection;
- natural chat goal creation;
- live command and output streaming;
- activity expansion;
- narrow and resized terminal layouts;
- scrolling, multiline input, history, and slash completion;
- Ctrl+C, quit, stop, and shutdown;
- terminal restoration after normal and forced exit;
- three concurrent project TUIs;
- work continuing with all TUIs detached.

The full Python and TUI suites, type checking, linting, git diff checks, and a
migration rehearsal on copied data must pass before release.

## Acceptance Criteria

- "coordinator" opens the correct registered project without PYTHONPATH or the
  Coordinator source directory.
- Unregistered repositories require explicit confirmation.
- One Supervisor runs multiple isolated loops and serves multiple TUI clients.
- Detaching the TUI never interrupts active workers.
- Reconnecting restores conversation and missed events.
- Activity blocks expose commands, output, verification, reviews, Git stages,
  and agent fallback.
- Global and project budgets remain enforceable under concurrent load.
- Supervisor and TUI crashes do not duplicate work.
- The package runs without /Users/xiafan/.hermes.
- Hermes MIT attribution is included.
- Existing history and project policy survive migration.
