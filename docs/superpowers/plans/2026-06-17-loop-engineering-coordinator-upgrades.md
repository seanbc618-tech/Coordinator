# Loop Engineering Coordinator Upgrades Taskbook

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this taskbook one task at a time. Do not batch unrelated tasks. Each task should end with tests and a commit.

**Goal:** Upgrade the local CLI coordinator from an MVP task runner into a real loop system with discovery, independent evaluation, durable memory, budget caps, scheduling, and human review points.

**Source:** `/Users/xiafan/Downloads/Loop-Engineering-The-Complete-Guide-v260615.pdf`, 36 pages, generated 2026-06-15. The key source ideas are the five loop moves, six loop parts, generator/evaluator split, loop cost controls, and the first-loop checklist.

**Current Baseline:** The repo already has Markdown task intake, SQLite task ledger, repo allowlist, per-task git worktrees, configurable CLI agents, verification commands, commit/push/merge policies, CLI commands, and generated task writing.

**Execution Rule:** Start at the first unchecked task. If a session is interrupted by quota limits, resume from this file, rerun the task's focused tests, inspect `git status --short`, and continue from the next unchecked task.

---

## PDF Principles To Preserve

The PDF's loop model maps cleanly to this coordinator:

- **Discovery:** the loop should find work from sources such as inbox, CI, issues, commits, or local notes.
- **Handoff:** every actionable finding should become a small task and get an isolated worktree.
- **Verification:** a separate evaluator must be able to say no; tests alone are necessary but not sufficient.
- **Persistence:** memory must live on disk and in the ledger, not only in a chat context.
- **Scheduling:** repeated automatic runs are what make it a loop rather than a one-off CLI.

The first-loop checklist becomes our readiness checklist:

- discovery source
- state file
- evaluator
- isolation
- token/budget cap
- human review point

---

## Current Gap Map

| Loop Element | Current State | Gap |
| --- | --- | --- |
| Discovery source | Manual `tasks/inbox/*.md` scan | No automatic discovery from commits, CI, issues, or configured commands |
| State file | SQLite ledger plus generated tasks | No human-readable cross-round memory file |
| Evaluator | Verification command runner | No independent spec/quality reviewer agent gate |
| Isolation | Worktree per task | No cleanup, stale worktree detection, or parallel lease management |
| Budget cap | Basic policy max files and attempts config | No runtime budget, daily cap, timeout, or circuit breaker |
| Human review point | Per-repo merge policy | No explicit review inbox or risk-based pause gate |
| Scheduling | `daemon --once` | No interval loop, lockfile, or next-run state |
| Connectors | Local filesystem and git CLI only | No generic discovery connector interface |
| Comprehension guard | Logs/artifacts exist | No summary digest to keep the user aware of loop output |

---

## Phase 1: Loop Readiness And Persistent Memory

### LE-01: Add Loop Readiness Doctor

**Why:** The PDF's checklist says a loop is risky unless discovery source, state file, evaluator, isolation, budget cap, and human review point are present.

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/config.py`
- Create: `src/local_cli_coordinator/readiness.py`
- Create: `tests/test_readiness.py`

**Acceptance Criteria:**
- `coordinator doctor` prints a loop readiness section.
- It reports pass/fail for discovery, state file, evaluator, worktree isolation, budget cap, and human review point.
- Missing optional loop features are warnings, not crashes.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_readiness.py tests/test_cli.py -v`
- `PYTHONPATH=src python3 -m local_cli_coordinator doctor`

**Commit:** `feat: add loop readiness doctor`

### LE-02: Add Human-Readable Loop Memory File

**Why:** The PDF stresses that memory must live on disk, not only in context.

**Files:**
- Create: `src/local_cli_coordinator/memory.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_memory.py`

**Acceptance Criteria:**
- The coordinator maintains `state/loop_state.md`.
- Each processed task appends a compact entry: task id, repo, title, outcome, branch, verifier result, and next action.
- `coordinator status` shows the loop memory path when it exists.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_memory.py tests/test_engine.py -v`

**Commit:** `feat: persist loop memory on disk`

### LE-03: Add Per-Repo Memory Handoff To Agent Prompts

**Why:** Each agent turn should receive relevant durable memory without depending on previous chat context.

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/config.py`
- Create: `tests/test_prompt_context.py`

**Acceptance Criteria:**
- Prompt generation includes a `## Loop Memory` section when `state/loop_state.md` exists.
- Prompt generation includes a `## Repo Memory` section if configured repo memory exists.
- Missing memory files are ignored safely.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_prompt_context.py tests/test_engine.py -v`

**Commit:** `feat: include durable memory in agent prompts`

---

## Phase 2: Independent Evaluator Pipeline

### LE-04: Extend Task States For Review Gates

**Why:** The PDF's strongest warning is that a generator cannot grade its own work. We need explicit review states.

**Files:**
- Modify: `src/local_cli_coordinator/models.py`
- Create: `migrations/002_review_states.sql`
- Modify: `src/local_cli_coordinator/db.py`
- Create: `tests/test_review_states.py`

**Acceptance Criteria:**
- Add states: `reviewing_spec`, `reviewing_quality`, `awaiting_human`, `rejected`.
- Existing databases migrate cleanly.
- `transition_task` accepts the new states and rejects unknown states.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_review_states.py tests/test_db.py -v`

**Commit:** `feat: add evaluator review states`

### LE-05: Add Reviewer Agent Configuration

**Why:** Generator and evaluator should be structurally separate, with different commands/instructions.

**Files:**
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `config/agents.toml`
- Create: `tests/test_review_config.py`

**Acceptance Criteria:**
- Config supports agent roles: `worker`, `spec_reviewer`, `quality_reviewer`, `planner`.
- Existing agent configs default to `worker`.
- Agent selection can choose by role and capabilities.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_review_config.py tests/test_config.py -v`

**Commit:** `feat: configure evaluator agent roles`

### LE-06: Add Spec Review Gate

**Why:** A separate evaluator should verify that the code matches the task's acceptance criteria before quality review.

**Files:**
- Create: `src/local_cli_coordinator/review.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_spec_review.py`

**Acceptance Criteria:**
- After verification commands pass, the engine runs a configured `spec_reviewer`.
- The reviewer receives task goal, acceptance criteria, changed files, and diff path.
- If reviewer exits nonzero, task becomes `rejected` or `awaiting_human` based on repo policy.
- Reviewer logs are stored as artifacts.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_spec_review.py tests/test_engine.py -v`

**Commit:** `feat: add spec review gate`

### LE-07: Add Quality Review Gate

**Why:** A second evaluator should pick holes in implementation quality, not just acceptance criteria.

**Files:**
- Modify: `src/local_cli_coordinator/review.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_quality_review.py`

**Acceptance Criteria:**
- Quality review runs after spec review passes.
- It can fail the task before commit/push.
- Its prompt includes changed files, diff path, verifier log path, and repo policy.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_quality_review.py tests/test_engine.py -v`

**Commit:** `feat: add quality review gate`

### LE-08: Require Fresh Evidence Before Done

**Why:** The PDF says the loop needs something that can say no; our engine should refuse `done` without evidence.

**Files:**
- Modify: `src/local_cli_coordinator/db.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_done_gate.py`

**Acceptance Criteria:**
- A task cannot transition to `done` unless verifier, spec review, and quality review artifacts exist, except repos explicitly configured as `review_policy = "tests_only"`.
- Failed or missing evidence leaves the task in `failed`, `rejected`, or `awaiting_human`.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_done_gate.py tests/test_engine.py -v`

**Commit:** `feat: require evidence before task completion`

---

## Phase 3: Budget Caps And Circuit Breakers

### LE-09: Add Runtime Budget Configuration

**Why:** The PDF warns that unattended loops can blow through usage, time, and retries.

**Files:**
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `config/policy.toml`
- Create: `tests/test_budget_config.py`

**Acceptance Criteria:**
- Policy config includes `max_task_runtime_seconds`, `max_daemon_runtime_seconds`, `max_tasks_per_run`, `max_tasks_per_day`, and `max_consecutive_failures`.
- Missing values have conservative defaults.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_budget_config.py tests/test_config.py -v`

**Commit:** `feat: configure loop budget caps`

### LE-10: Enforce Command Timeouts

**Why:** Agent, verifier, and reviewer commands must not run forever.

**Files:**
- Modify: `src/local_cli_coordinator/agent.py`
- Modify: `src/local_cli_coordinator/verify.py`
- Modify: `src/local_cli_coordinator/review.py`
- Create: `tests/test_command_timeouts.py`

**Acceptance Criteria:**
- Agent, verifier, and reviewer subprocesses accept timeout seconds.
- Timeout results are logged and treated as failures.
- The engine records timeout failures in task events.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_command_timeouts.py tests/test_agent.py tests/test_verify.py -v`

**Commit:** `feat: enforce command timeouts`

### LE-11: Add Daily Run Ledger And Circuit Breaker

**Why:** A loop needs a hard stop if it starts failing repeatedly or processing too much.

**Files:**
- Create: `migrations/003_run_ledger.sql`
- Modify: `src/local_cli_coordinator/db.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_circuit_breaker.py`

**Acceptance Criteria:**
- DB records daemon runs with started/ended time, tasks processed, failures, and stop reason.
- `run_one_ready_task` refuses new tasks if daily or consecutive failure caps are reached.
- CLI reports the stop reason clearly.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_circuit_breaker.py tests/test_engine.py -v`

**Commit:** `feat: add loop circuit breaker`

---

## Phase 4: Discovery And Planner

### LE-12: Add Discovery Source Config

**Why:** The loop should find work by itself instead of waiting only for human-written Markdown tasks.

**Files:**
- Create: `config/discovery.toml`
- Modify: `src/local_cli_coordinator/config.py`
- Create: `tests/test_discovery_config.py`

**Acceptance Criteria:**
- Discovery config supports sources: `inbox`, `git_recent_commits`, `command`, `ci_command`, and `issue_command`.
- Sources can be enabled/disabled per repo.
- Invalid source types fail config loading with a clear error.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_discovery_config.py tests/test_config.py -v`

**Commit:** `feat: configure discovery sources`

### LE-13: Add Discovery Result Model

**Why:** Discovery needs a stable intermediate format before planner/splitter turns findings into tasks.

**Files:**
- Modify: `src/local_cli_coordinator/models.py`
- Create: `src/local_cli_coordinator/discovery.py`
- Create: `tests/test_discovery_models.py`

**Acceptance Criteria:**
- Add `Finding` dataclass with id, repo, source, title, body, severity, evidence, and discovered_at.
- Findings can be serialized to and loaded from JSONL under `state/findings/`.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_discovery_models.py -v`

**Commit:** `feat: model discovery findings`

### LE-14: Implement Git Recent Commits Discovery

**Why:** The PDF's first-loop example includes recent commits as a discovery source.

**Files:**
- Modify: `src/local_cli_coordinator/discovery.py`
- Create: `tests/test_git_discovery.py`

**Acceptance Criteria:**
- Discovery can scan recent commits for a repo and produce findings with commit hash and subject.
- It stores a cursor so the same commit is not rediscovered every run.
- It respects repo allowlist.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_git_discovery.py -v`

**Commit:** `feat: discover recent commit findings`

### LE-15: Implement Command-Based Discovery

**Why:** Local CLI first means arbitrary configured commands should provide discovery data without adding network services.

**Files:**
- Modify: `src/local_cli_coordinator/discovery.py`
- Create: `tests/test_command_discovery.py`

**Acceptance Criteria:**
- A discovery source can run a configured local command.
- JSONL output is parsed into findings.
- Bad JSON or nonzero exit creates a logged discovery failure without crashing the daemon.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_command_discovery.py -v`

**Commit:** `feat: discover findings from configured commands`

### LE-16: Add Planner Rules To Convert Findings Into Small Tasks

**Why:** The PDF emphasizes cutting findings into small handoffs before agents start writing.

**Files:**
- Create: `src/local_cli_coordinator/planner.py`
- Modify: `src/local_cli_coordinator/tasks.py`
- Create: `tests/test_planner_rules.py`

**Acceptance Criteria:**
- Planner converts a finding into one or more `TaskDraft` objects.
- It refuses broad findings with `needs_split` reasons.
- Generated task files include source finding id and evidence.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_planner_rules.py tests/test_planner.py -v`

**Commit:** `feat: plan small tasks from findings`

### LE-17: Add LLM Planner Hook With Rule-Based Guard

**Why:** The user chose hybrid task splitting: LLM proposes, rules engine rejects oversized tasks.

**Files:**
- Modify: `src/local_cli_coordinator/planner.py`
- Modify: `src/local_cli_coordinator/agent.py`
- Create: `tests/test_llm_planner_hook.py`

**Acceptance Criteria:**
- Planner can call a configured `planner` CLI agent.
- Its output must parse into `TaskDraft` objects.
- Policy checks run after planner output and reject oversized or vague tasks.
- If no planner agent is configured, rule-based planner still works.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_llm_planner_hook.py tests/test_policy.py -v`

**Commit:** `feat: add guarded planner agent hook`

### LE-18: Add `discover` CLI Command

**Why:** Before scheduling, discovery must be runnable and inspectable manually.

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_discover_cli.py`

**Acceptance Criteria:**
- `coordinator discover --once` runs enabled discovery sources.
- It writes findings to disk.
- It prints discovered, skipped, and failed counts.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_discover_cli.py -v`

**Commit:** `feat: add discovery CLI command`

---

## Phase 5: Scheduling And Daemon Loop

### LE-19: Add Daemon Loop Config

**Why:** `daemon --once` is one run; scheduling is what makes it a loop.

**Files:**
- Modify: `config/policy.toml`
- Modify: `src/local_cli_coordinator/config.py`
- Create: `tests/test_daemon_config.py`

**Acceptance Criteria:**
- Config supports `loop_interval_seconds`, `idle_sleep_seconds`, and `run_discovery_before_tasks`.
- Defaults are conservative and suitable for local CLI use.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_daemon_config.py tests/test_config.py -v`

**Commit:** `feat: configure daemon loop timing`

### LE-20: Implement Continuous Daemon Loop

**Why:** The coordinator should turn round after round without manual prompting.

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_daemon_loop.py`

**Acceptance Criteria:**
- `coordinator daemon` runs until stopped or until a configured max runtime cap is reached.
- Each cycle can run discovery, import/generated tasks, and process ready tasks.
- `coordinator daemon --once` remains supported.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_daemon_loop.py tests/test_cli_commands.py -v`

**Commit:** `feat: run continuous daemon loop`

### LE-21: Add Lockfile Single-Instance Guard

**Why:** Two coordinator daemons should not process the same ledger at the same time.

**Files:**
- Create: `src/local_cli_coordinator/locks.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_locks.py`

**Acceptance Criteria:**
- Daemon creates a lockfile under `state/coordinator.lock`.
- A second daemon exits with a clear error unless `--force-lock` is used.
- Stale lock handling is explicit and logged.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_locks.py -v`

**Commit:** `feat: guard daemon with lockfile`

---

## Phase 6: Human Review And Comprehension Guard

### LE-22: Add Review Inbox

**Why:** The PDF says a loop should pause at a human review point instead of auto-ing all the way through.

**Files:**
- Create: `tasks/review/.gitkeep`
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/tasks.py`
- Create: `tests/test_review_inbox.py`

**Acceptance Criteria:**
- Tasks that need human review write a Markdown review packet to `tasks/review/`.
- Packet includes title, repo, branch, changed files, verification evidence, reviewer results, and suggested action.
- Review packet is linked as an artifact.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_review_inbox.py tests/test_engine.py -v`

**Commit:** `feat: write human review packets`

### LE-23: Add Risk-Based Human Review Policy

**Why:** Fully automatic is acceptable only for low-risk repos/tasks; risky changes should pause.

**Files:**
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `config/repos.toml`
- Modify: `src/local_cli_coordinator/policy.py`
- Create: `tests/test_review_policy.py`

**Acceptance Criteria:**
- Repo config supports `review_policy = "auto" | "branch_only" | "always_human" | "risky_human"`.
- Risky signals include too many files, migrations, dependency files, failed reviewer confidence, or protected paths.
- Human review policy overrides auto-merge.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_review_policy.py tests/test_push_merge.py -v`

**Commit:** `feat: add risk-based human review policy`

### LE-24: Add Daily Comprehension Digest

**Why:** The PDF warns about comprehension rot; the user needs a digest of what the loop changed and why.

**Files:**
- Create: `src/local_cli_coordinator/digest.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_digest.py`

**Acceptance Criteria:**
- `coordinator digest` summarizes tasks completed, failed, rejected, awaiting human review, and top changed files.
- Digest can write `state/digests/YYYY-MM-DD.md`.
- Digest includes enough context for the user to explain what changed.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_digest.py -v`

**Commit:** `feat: generate loop comprehension digest`

---

## Phase 7: Worktree Lifecycle And Parallelism

### LE-25: Add Worktree Cleanup And Stale Detection

**Why:** Long-running loops need to clean old worktrees and identify stuck branches.

**Files:**
- Modify: `src/local_cli_coordinator/gitops.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_worktree_cleanup.py`

**Acceptance Criteria:**
- `coordinator repo cleanup-worktrees` reports stale worktrees.
- It can remove completed task worktrees only when safe.
- It never removes worktrees with uncommitted changes unless explicitly forced.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_worktree_cleanup.py tests/test_gitops.py -v`

**Commit:** `feat: clean completed worktrees safely`

### LE-26: Add Parallel Task Leasing

**Why:** Worktrees allow parallelism, but the ledger must prevent two workers from taking the same task.

**Files:**
- Create: `migrations/004_task_leases.sql`
- Modify: `src/local_cli_coordinator/db.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Create: `tests/test_task_leases.py`

**Acceptance Criteria:**
- Ready tasks are claimed with an atomic lease.
- Expired leases can be retried.
- Max concurrency is enforced per agent and globally.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_task_leases.py tests/test_engine.py -v`

**Commit:** `feat: lease tasks for parallel execution`

### LE-27: Add Multi-Task Daemon Run

**Why:** Once leasing exists, a daemon cycle should be able to process several small tasks within caps.

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_multi_task_run.py`

**Acceptance Criteria:**
- A daemon cycle can process up to `max_tasks_per_run`.
- It stops on budget/circuit breaker limits.
- It reports processed, failed, blocked, and skipped counts.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_multi_task_run.py -v`

**Commit:** `feat: process multiple leased tasks per run`

---

## Phase 8: Connectors And Observability

### LE-28: Add Generic Connector Interface

**Why:** The PDF says a loop that only sees the filesystem is small. For local CLI, connectors should start as configured commands.

**Files:**
- Create: `src/local_cli_coordinator/connectors.py`
- Modify: `src/local_cli_coordinator/config.py`
- Create: `tests/test_connectors.py`

**Acceptance Criteria:**
- Connector config supports named local commands with input/output contracts.
- Connectors can be used by discovery and persistence steps.
- Connector failures are logged without crashing the daemon.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_connectors.py -v`

**Commit:** `feat: add local connector interface`

### LE-29: Add Event And Artifact CLI Views

**Why:** Operators need to see why the loop did what it did, not only final task state.

**Files:**
- Modify: `src/local_cli_coordinator/db.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_events_cli.py`

**Acceptance Criteria:**
- `coordinator task events <task_id>` prints ordered state transitions and notes.
- `coordinator task artifacts <task_id>` prints artifacts with kind and path.
- Missing task ids return a nonzero code with a clear message.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_events_cli.py tests/test_cli_commands.py -v`

**Commit:** `feat: expose task events and artifacts`

### LE-30: Add Loop Status Summary

**Why:** The user needs a top-level dashboard in CLI form before any TUI.

**Files:**
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_loop_status.py`

**Acceptance Criteria:**
- `coordinator status --loop` shows readiness, last run, next run, budget use, active leases, and human review count.
- Output remains plain text and script-friendly.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_loop_status.py tests/test_cli_commands.py -v`

**Commit:** `feat: show loop status summary`

---

## Phase 9: End-To-End Loop Hardening

### LE-31: Add End-To-End Local Loop Scenario

**Why:** The system should prove one whole loop turn: discovery, planning, handoff, verification, review, persistence, and review packet or merge.

**Files:**
- Create: `tests/test_loop_e2e.py`

**Acceptance Criteria:**
- Test creates a temporary git repo and coordinator root.
- Discovery command emits one finding.
- Planner writes one task.
- Worker changes one file.
- Verifier passes.
- Spec and quality reviewers pass.
- Engine commits and either pushes branch or writes review packet based on repo policy.
- Memory and run ledger are updated.

**Verification:**
- `PYTHONPATH=src python3 -m unittest tests/test_loop_e2e.py -v`

**Commit:** `test: add end-to-end loop scenario`

### LE-32: Update README With Loop Operations

**Why:** The current README describes the first version; it should teach the upgraded loop workflow.

**Files:**
- Modify: `README.md`

**Acceptance Criteria:**
- README documents discovery, daemon loop, evaluator roles, budget caps, human review inbox, memory files, and recovery after interruption.
- Quick commands include doctor, discover, daemon, digest, and review packet flow.

**Verification:**
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m local_cli_coordinator doctor`

**Commit:** `docs: document loop operations`

### LE-33: Final Verification And Release Check

**Why:** Before calling the upgraded coordinator ready, verify the whole local loop surface.

**Files:**
- Modify only if verification exposes a real defect.

**Acceptance Criteria:**
- All tests pass.
- `doctor` reports all required loop readiness checks as present or explicitly configured off.
- `git status --short` is clean.
- The taskbook has each completed task checked off or mirrored into a follow-up task.

**Verification:**
- `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m local_cli_coordinator doctor`
- `git status --short`

**Commit:** only if fixes or docs are changed.

---

## Recommended Execution Order

Do not jump straight to scheduling. The safer order is:

1. LE-01 to LE-03: make memory and readiness visible.
2. LE-04 to LE-08: install independent evaluator gates.
3. LE-09 to LE-11: add budget caps and circuit breakers.
4. LE-12 to LE-18: add discovery and planner.
5. LE-19 to LE-21: turn `daemon --once` into a real loop.
6. LE-22 to LE-24: add human review and comprehension guard.
7. LE-25 to LE-30: harden lifecycle, parallelism, connectors, and observability.
8. LE-31 to LE-33: prove the end-to-end loop and update docs.

This order follows the PDF's warning: build the "no" mechanism and stop conditions before letting the loop run unattended.
