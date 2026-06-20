# Coordinator Recovery and Global TUI Execution Index

Date: 2026-06-20
Integration baseline: external/live-daemon-observability at or after 1dfd649
Execution model: external Claude Code and Grok worktrees; Codex acceptance only

## Plan Order

1. [Single-Fallback Agent Recovery](2026-06-20-single-fallback-agent-recovery.md)
   - 6 tasks
   - migration 007
2. [Global Runtime Foundation](2026-06-20-global-runtime-foundation.md)
   - 6 tasks
   - migration 008
3. [Multi-Project Supervisor](2026-06-20-multi-project-supervisor.md)
   - 7 tasks
   - migrations 009 and 010
4. [Hermes-Based Coordinator TUI](2026-06-20-hermes-coordinator-tui.md)
   - 7 tasks
5. [Global Installation and End-to-End TUI](2026-06-20-global-tui-installation.md)
   - 7 tasks

Total: 33 independently committed tasks.

## Branch Model

Create one integration branch from the accepted baseline:

~~~text
external/coordinator-global-tui
~~~

Each task uses a dedicated worktree and branch:

~~~text
agent/claude/<phase>-<task>
agent/grok/<phase>-<task>
~~~

Workers do not merge their own branches. After focused verification, the
integration owner cherry-picks one task commit at a time in plan order.

## Wave Pipeline

### Wave 0: Agent Recovery

- Claude: classifier, configuration, fallback engine.
- Grok: attempt migration, attempt runner integration, fallback observability.
- Gate: exact-two-attempt E2E and full Python suite.

### Wave 1: Global Foundation

- Claude: runtime paths, project registry, migration.
- Grok: protocol and Unix socket server.
- Claude: administrative integration.
- Gate: migration rehearsal and single-instance socket test.

### Wave 2: Multi-Project Supervisor

- Claude: project scope, runtime adapters, shared capacity, integration.
- Grok: scheduler, event replay, multi-client methods.
- Gate: three-project deterministic scheduler and restart test.

### Wave 3: TUI

- Grok: package scaffold, socket client, layout, lifecycle, PTY integration.
- Claude: state reducer and composer/slash commands.
- Gate: TypeScript suite, bundle inspection, PTY widths, terminal cleanup.

### Wave 4: Installation

- Claude: package resources, detached process, migration, documentation.
- Grok: onboarding, launcher, multi-project soak.
- Gate: wheel install, three real PTYs, detach/reconnect, full release suite.

## Acceptance Rules Per Task

1. Worker reads only its assigned plan task and referenced design section.
2. Worker starts from the current integration head.
3. Worker writes tests first and records the failing command.
4. Worker changes only listed files unless the handoff explicitly expands scope.
5. Worker runs focused verification and git diff checks.
6. Worker commits exactly one task.
7. Codex reviews scope, behavior, tests, and migration compatibility.
8. Rejected work is repaired on the task branch; it is not patched on integration.
9. Accepted work is cherry-picked, then the integration smoke suite runs.
10. The next dependent task starts only after the gate commit is accepted.

## Resume Checkpoint

After every accepted task, update this file with:

~~~text
- task:
- owner:
- branch:
- commit:
- focused tests:
- integration head:
- status:
~~~

A resumed session reads the latest checkpoint and the referenced plan task. It
does not reconstruct progress from chat history.

## Final Acceptance

- No unresolved task branches or unreviewed commits.
- Migrations 007 through 010 apply in order to copied legacy and fresh databases.
- Full Python and TypeScript suites pass.
- Built wheel includes the TUI and attribution but excludes Hermes runtime code.
- One Supervisor serves three isolated project TUIs.
- Detach/reconnect and Supervisor restart do not duplicate work.
- Single fallback never invokes a third worker.
- Current polymarket project history and safety policy survive migration.
