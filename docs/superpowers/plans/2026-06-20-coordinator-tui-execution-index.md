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

- Claude Code: Tasks 1 through 7 implementation, one focused commit per task.
- Grok: adversarial review after each wave; production repairs stay on the
  responsible Claude task branch.
- Codex: integration, scope/license review, PTY review, and final acceptance.
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

### 2026-06-22 Checkpoint

- task: Wave 2 Multi-Project Supervisor acceptance
- owner: Claude Code implementation, Grok adversarial repair, Codex acceptance
- branch: `external/live-daemon-observability`
- commit: `f67c449`
- focused tests: Phase 2 adversarial gates and injected lease-cleanup failures
- integration head: `f67c449`
- status: accepted; Wave 3 Hermes TUI is ready to start
- Wave 3 integration branch: `external/coordinator-global-tui`
- Phase 3 implementation handoff:
  `../handoffs/2026-06-22-phase3-claude-code-implementation.md`
- Phase 3 review handoff:
  `../handoffs/2026-06-22-phase3-grok-adversarial-review.md`

### 2026-06-22 Phase 3 Acceptance Checkpoint

- integration head reviewed: `bbdab7e`
- TypeScript tests: 83 passed
- PTY tests: 9 passed, but Gate D/E behavioral assertions are incomplete
- Python tests: 652 passed with strict ResourceWarning policy
- status: rejected pending final Gate D/E repair
- repair handoff:
  `../handoffs/2026-06-22-phase3-final-acceptance-repair.md`

## Final Acceptance

- No unresolved task branches or unreviewed commits.
- Migrations 007 through 010 apply in order to copied legacy and fresh databases.
- Full Python and TypeScript suites pass.
- Built wheel includes the TUI and attribution but excludes Hermes runtime code.
- One Supervisor serves three isolated project TUIs.
- Detach/reconnect and Supervisor restart do not duplicate work.
- Single fallback never invokes a third worker.
- Current polymarket project history and safety policy survive migration.
