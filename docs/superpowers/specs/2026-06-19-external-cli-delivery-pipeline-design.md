# External CLI Delivery Pipeline Design

## Objective

Complete LE-13 through LE-33 by delegating implementation to Grok CLI, Pi
Agent, and Claude Code while Codex retains authority for review, integration,
and final acceptance. Work proceeds in dependency-aware waves so agents do not
modify shared foundations concurrently.

## Authority Boundary

Each external agent may:

- work only in its assigned Git worktree and branch;
- implement one LE task at a time;
- add or update tests required by that task;
- run local verification commands; and
- create a local commit with the taskbook's requested commit message.

External agents must not push, merge, rebase the integration branch, modify
another agent's worktree, or begin an unassigned follow-up task. Codex alone
reviews commits, requests repairs, integrates accepted commits, and declares a
task complete.

## Branch And Handoff Protocol

The integration baseline is `codex/loop-readiness-doctor`. Every task starts
from its latest accepted commit in a fresh worktree. Branches use
`agent/<cli>/le-XX-<slug>`.

Each agent receives a self-contained prompt containing the taskbook section,
allowed files, acceptance criteria, verification command, current baseline
commit, and the authority boundary. Its handoff must report:

- commit hash;
- files changed;
- tests run and their results;
- implementation summary;
- known risks or unresolved questions; and
- confirmation that it did not push or merge.

Agent output and command logs are persisted outside the worktree so a failed
or interrupted session can be resumed without relying on chat context.

## Delivery Waves

Tasks within a lane are sequential. Separate lanes in the same wave may run in
parallel only when their listed files and required behavior are independent.
No downstream wave begins until Codex accepts and integrates every prerequisite.

### Wave 1: Discovery Foundation

| Task | Owner | Dependency |
| --- | --- | --- |
| LE-13 Discovery Result Model | Grok | LE-12 |

### Wave 2: Discovery And Timing Lanes

| Lane | Owner | Tasks | Dependency |
| --- | --- | --- | --- |
| Discovery executors | Grok | LE-14, then LE-15 | LE-13 |
| Planner | Claude Code | LE-16, then LE-17 | LE-13 |
| Daemon timing config | Pi Agent | LE-19 | LE-12 |

### Wave 3: Discovery Integration And Daemon

| Task | Owner | Dependency |
| --- | --- | --- |
| LE-18 Discover CLI | Pi Agent | LE-14 through LE-17 |
| LE-20 Continuous Daemon Loop | Grok | LE-18 and LE-19 |
| LE-21 Single-Instance Lock | Claude Code | LE-20 |

### Wave 4: Human Control

After LE-21, these tasks may run in parallel from the same accepted baseline.

| Task | Owner |
| --- | --- |
| LE-22 Review Inbox | Claude Code |
| LE-23 Risk-Based Review Policy | Grok |
| LE-24 Daily Comprehension Digest | Pi Agent |

### Wave 5: Independent Hardening

After Wave 4 integration, these tasks may run in parallel.

| Task | Owner |
| --- | --- |
| LE-25 Worktree Cleanup | Pi Agent |
| LE-26 Parallel Task Leasing | Claude Code |
| LE-28 Generic Connector Interface | Grok |

### Wave 6: Shared Runtime Integration

These tasks are sequential because they converge on the database, engine, and
CLI surfaces.

| Task | Owner | Dependency |
| --- | --- | --- |
| LE-27 Multi-Task Daemon Run | Grok | LE-26 |
| LE-29 Event And Artifact Views | Pi Agent | LE-27 |
| LE-30 Loop Status Summary | Claude Code | LE-29 |

### Wave 7: Release Proof

LE-31 and LE-32 may run in parallel after LE-30. LE-33 begins only after both
are accepted and integrated.

| Task | Owner |
| --- | --- |
| LE-31 End-To-End Loop Scenario | Grok |
| LE-32 README Loop Operations | Pi Agent |
| LE-33 Release Check | Claude Code |

Codex repeats LE-33 independently and is the only authority that can declare
the Coordinator upgrade complete.

## Acceptance Gate

For every task, Codex performs these checks before integration:

1. Compare the diff against the exact LE acceptance criteria and allowed scope.
2. Review behavior, error handling, compatibility, and test quality.
3. Run the task's targeted verification command.
4. Run the full test suite.
5. Run `git diff --check` and confirm the task branch is clean after commit.
6. Accept and integrate the commit, or return concrete findings to the same
   agent for repair in the existing task worktree.

A task is rejected if it changes unrelated files, weakens existing safeguards,
skips required tests, pushes or merges, leaves an uncommitted worktree, or does
not satisfy an acceptance criterion. Downstream tasks never build on rejected
work.

## Failure And Recovery

Each task has a bounded execution attempt. Timeout, quota exhaustion, CLI
failure, or an incomplete handoff leaves the branch unintegrated. The recorded
prompt, log, branch, commit state, and Codex review findings provide the restart
context. Repairs stay with the original owner unless the CLI is unavailable;
reassignment always starts from the last accepted integration baseline.

Only one Codex review runs at a time. This keeps integration deterministic and
ensures that the latest accepted baseline is always obvious.
