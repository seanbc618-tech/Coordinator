# External CLI Delivery Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver LE-13 through LE-33 with Grok CLI, Pi Agent, and Claude Code implementing isolated commits while Codex reviews and integrates every task.

**Architecture:** `codex/loop-readiness-doctor` is the sole integration branch. Each LE task runs in a fresh worktree from the latest accepted baseline, writes durable logs and a handoff report under the main repository's ignored `runs/external-agents/`, and cannot push or merge. Codex applies a spec, quality, targeted-test, and full-suite gate before cherry-picking an accepted commit.

**Tech Stack:** Git worktrees, Python 3.12 `unittest`, Grok CLI, Pi Agent, Claude Code, TOML, Markdown handoffs.

---

## Sources Of Truth

- Delivery policy: `docs/superpowers/specs/2026-06-19-external-cli-delivery-pipeline-design.md`
- Feature requirements: `docs/superpowers/plans/2026-06-17-loop-engineering-coordinator-upgrades.md`
- Integration worktree: `/Users/xiafan/.config/superpowers/worktrees/Coordinator/loop-readiness-doctor`
- Runtime records: `/Users/xiafan/Coordinator/runs/external-agents/LE-XX/`
- Task worktrees: `/Users/xiafan/Coordinator/worktrees/agent-<cli>-le-XX/`

## Per-Task Acceptance Transaction

Every LE task uses the same complete transaction. A later task cannot begin
from a commit that has not completed all nine steps.

- [ ] Record `git rev-parse HEAD` from the integration worktree as `baseline.txt`.
- [ ] Create `agent/<cli>/le-XX-<slug>` and its fresh worktree at that exact baseline.
- [ ] Save a self-contained `prompt.md` containing the full taskbook section, authority boundary, TDD requirement, and handoff format.
- [ ] Run the assigned CLI in non-interactive mode with edit approval, persistent stdout/stderr, and an explicit prohibition on push, merge, rebase, or extra tasks.
- [ ] Confirm the task worktree is clean and exactly one local task commit exists above the recorded baseline.
- [ ] Review the complete diff for specification compliance, scope, behavior, error handling, compatibility, and useful tests.
- [ ] Run the taskbook's targeted verification, then `PYTHONPATH=src python3 -m unittest discover -s tests -v`, then `git diff --check`.
- [ ] If rejected, write `review.md` with concrete findings and resume the same owner in the same worktree; repeat review and verification.
- [ ] If accepted, cherry-pick the commit into the integration branch, record `accepted.txt`, and remove only the clean completed task worktree.

## Task 1: Preflight The Three CLI Workers

**Files:**
- Read: `docs/superpowers/specs/2026-06-19-external-cli-delivery-pipeline-design.md`
- Read: `docs/superpowers/plans/2026-06-17-loop-engineering-coordinator-upgrades.md`
- Runtime: `/Users/xiafan/Coordinator/runs/external-agents/preflight/`

- [ ] Verify the integration worktree is clean and points at the delivery-plan commit.
- [ ] Run the full test suite and record the passing count as the delegation baseline.
- [ ] Run a no-edit, non-interactive identity probe through Grok, Pi, and Claude Code.
- [ ] Record each executable path, version, exit code, supported permission controls, and output format.
- [ ] Refuse to dispatch implementation to a CLI whose authentication or headless mode is not working.

Expected result: three successful probes and a clean integration baseline. A
failed worker is diagnosed or temporarily removed from assignment before Wave 1.

## Task 2: Wave 1 Discovery Foundation

**Assignment:** Grok implements LE-13.

**Files:**
- Modify: `src/local_cli_coordinator/models.py`
- Create: `src/local_cli_coordinator/discovery.py`
- Create: `tests/test_discovery_models.py`

- [ ] Execute the per-task transaction for LE-13 from the latest baseline.
- [ ] Require JSONL round-trip tests, stable `Finding` fields, and storage below `state/findings/`.
- [ ] Run `PYTHONPATH=src python3 -m unittest tests/test_discovery_models.py -v`.
- [ ] Integrate only after the full suite also passes.

Expected commit: `feat: model discovery findings`.

## Task 3: Wave 2 Discovery And Timing Lanes

Start each lane from the accepted LE-13 baseline. Tasks in a lane are
sequential and receive a fresh worktree after their predecessor is integrated.

| Lane | Owner | Ordered transactions |
| --- | --- | --- |
| Discovery executors | Grok | LE-14, then LE-15 |
| Planner | Claude Code | LE-16, then LE-17 |
| Timing config | Pi Agent | LE-19 |

- [ ] Dispatch LE-14, LE-16, and LE-19 only after LE-13 acceptance.
- [ ] Accept LE-14 before dispatching LE-15.
- [ ] Accept LE-16 before dispatching LE-17.
- [ ] Integrate accepted parallel commits one at a time and rerun the full suite after each cherry-pick.
- [ ] If integration exposes a semantic conflict, reject the later integration and return it to its owner rebased through a fresh worktree from the new baseline.

Expected result: LE-14 through LE-17 and LE-19 are independently committed,
reviewed, integrated, and green.

## Task 4: Wave 3 Discovery Integration And Daemon

| Order | Owner | Transaction | Required accepted baseline |
| --- | --- | --- | --- |
| 1 | Pi Agent | LE-18 | LE-14 through LE-17 |
| 2 | Grok | LE-20 | LE-18 and LE-19 |
| 3 | Claude Code | LE-21 | LE-20 |

- [ ] Execute LE-18 and manually verify `coordinator discover --once` output counts.
- [ ] Execute LE-20 and verify both continuous and `--once` behavior remain bounded by runtime caps.
- [ ] Execute LE-21 and verify second-instance refusal, forced lock behavior, and stale-lock logging.
- [ ] Run the full suite after each accepted integration.

Expected result: discovery is manually inspectable and the daemon can loop as a
single guarded local process.

## Task 5: Wave 4 Human Control

Dispatch these three transactions in parallel from the accepted LE-21 baseline:

| Owner | Transaction | Primary surface |
| --- | --- | --- |
| Claude Code | LE-22 | review packets and engine artifacts |
| Grok | LE-23 | risk policy and merge override |
| Pi Agent | LE-24 | comprehension digest CLI |

- [ ] Review LE-22 packets for all required evidence and artifact linkage.
- [ ] Review LE-23 for conservative risk classification and precedence over auto-merge.
- [ ] Review LE-24 output for script-friendly summaries and durable daily files.
- [ ] Integrate one commit at a time, resolving behavioral interactions through owner repairs rather than Codex feature edits.

Expected result: unsafe or unclear work pauses for human review, and daily loop
activity can be explained from one digest.

## Task 6: Wave 5 Independent Hardening

Dispatch these three transactions in parallel from the accepted Wave 4 baseline:

| Owner | Transaction | Primary surface |
| --- | --- | --- |
| Pi Agent | LE-25 | safe worktree cleanup |
| Claude Code | LE-26 | atomic task leases |
| Grok | LE-28 | command-backed connectors |

- [ ] Reject any LE-25 implementation that can remove a dirty worktree without explicit force.
- [ ] Exercise LE-26 lease acquisition concurrently and verify expiration and concurrency caps.
- [ ] Exercise LE-28 command failure and confirm the daemon remains alive with a logged failure.
- [ ] Integrate one commit at a time and run the full suite after each integration.

Expected result: worktree lifecycle, ledger concurrency, and connector boundaries
are independently hardened.

## Task 7: Wave 6 Shared Runtime Integration

These transactions are strictly sequential:

| Order | Owner | Transaction |
| --- | --- | --- |
| 1 | Grok | LE-27 |
| 2 | Pi Agent | LE-29 |
| 3 | Claude Code | LE-30 |

- [ ] Verify LE-27 obeys task, budget, and circuit-breaker caps across multiple leases.
- [ ] Verify LE-29 orders events and artifacts and returns nonzero for missing task IDs.
- [ ] Verify LE-30 remains plain-text and script-friendly while reporting every required loop field.
- [ ] Run each targeted command and the full suite before dispatching the next task.

Expected result: the daemon can process bounded parallel work and operators can
inspect both task-level and loop-level state.

## Task 8: Wave 7 Release Proof

- [ ] Dispatch LE-31 to Grok and LE-32 to Pi Agent in parallel from the accepted LE-30 baseline.
- [ ] Review LE-31 as a real end-to-end behavior test, with no mocked-away core loop stages.
- [ ] Review LE-32 commands against the actual CLI and reject documentation-only inventions.
- [ ] Integrate both accepted commits and run the full suite.
- [ ] Dispatch LE-33 to Claude Code as an independent release auditor.
- [ ] After its report, Codex independently reruns the full suite, doctor, diff check, taskbook completion audit, and clean-status check.
- [ ] Declare completion only when both the external audit and Codex verification pass without unresolved findings.

Expected result: all LE-13 through LE-33 requirements have accepted commits,
the complete local loop is tested and documented, and the integration worktree
is clean.
