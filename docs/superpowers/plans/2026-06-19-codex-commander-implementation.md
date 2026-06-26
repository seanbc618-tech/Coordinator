# Codex Commander Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Codex Commander that accepts a conversational long-term goal, previews one batch for confirmation, and autonomously replenishes small validated tasks.

**Architecture:** Persist one non-terminal goal and Commander interactions in SQLite. A dedicated runner invokes Codex CLI with a strict JSON contract, a deterministic gate converts safe proposals into linked tasks, and the existing daemon replenishes an active goal when its ready queue is empty. Worker, reviewer, worktree, and repository policies remain unchanged.

**Tech Stack:** Python 3.11+, `argparse`, `sqlite3`, `json`, `dataclasses`, the existing process runner, Markdown artifacts, and `unittest`.

**Design:** `docs/superpowers/specs/2026-06-19-codex-commander-design.md`

---

## Wave Ownership

| Wave | Task | Owner | Reviewer |
|---|---|---|---|
| 1 | 1. Goal persistence | Claude Code | Grok |
| 1 | 2. Protocol and role | Grok | Claude Code |
| 2 | 3. Commander runner | Claude Code | Grok |
| 2 | 4. Admission gate | Grok | Claude Code |
| 3 | 5. Goal/chat CLI | Claude Code | Grok |
| 3 | 6. Status and memory | Grok | Claude Code |
| 4 | 7. Daemon replenishment | Claude Code | Grok |
| 4 | 8. Retry and safety stops | Grok | Claude Code |
| 5 | 9. End-to-end and docs | Claude Code | Grok |
| 5 | 10. Adversarial acceptance | Grok | Codex |

Use one `agent/<agent>/commander-XX-*` branch and worktree per task. Owners modify only listed files and commit once. Reviewers report findings without merging. Codex reviews and integrates each wave.

---

### Task 1: Goal Persistence

**Owner:** Claude Code

**Files:**
- Create: `migrations/006_commander_goals.sql`
- Create: `src/local_cli_coordinator/goals.py`
- Create: `tests/test_goals.py`
- Modify: `src/local_cli_coordinator/models.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

```python
def test_only_one_nonterminal_goal_is_allowed(self):
    create_goal(self.conn, "Roadmap", "Finish roadmap", ["dry-run"], ["demo"])
    with self.assertRaises(sqlite3.IntegrityError):
        create_goal(self.conn, "Other", "Do other work", [], ["demo"])

def test_run_message_and_task_link_round_trip(self):
    goal_id = create_goal(self.conn, "Roadmap", "Finish roadmap", [], ["demo"])
    add_commander_message(self.conn, goal_id, "user", "Begin")
    run_id = start_commander_run(self.conn, goal_id, "initial_plan", 1, Path("prompt.md"))
    finish_commander_run(self.conn, run_id, status="succeeded", exit_code=0, timed_out=False)
    link_task_to_goal(self.conn, goal_id, "task-1", "batch-1", "fp-1", "Advances goal")
    self.assertEqual(get_latest_commander_run(self.conn, goal_id)["status"], "succeeded")
    self.assertEqual(goal_for_task(self.conn, "task-1")["id"], goal_id)
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_goals tests.test_db -v
```

Expected: missing module or Commander tables.

- [ ] **Step 3: Implement schema and repository**

The migration creates `goals`, `commander_runs`, `commander_messages`, and `task_goal_links`. Required goal columns are objective, JSON completion criteria/constraints/repo IDs, status and lifecycle timestamps, progress, stop reason, Commander failure count/retry time, and draft preview path. Add a unique partial index over `goals((1))` for `draft`, `active`, `paused`, and `blocked`, plus a unique `(goal_id, proposal_fingerprint)` index.

Add:

```python
GOAL_STATES = frozenset({"draft", "active", "paused", "blocked",
                         "completed", "failed", "abandoned"})
NONTERMINAL_GOAL_STATES = frozenset({"draft", "active", "paused", "blocked"})
```

Implement `create_goal`, `get_goal`, `active_goal`, `transition_goal`, `update_goal_progress`, message CRUD, run start/finish/list, task linking/listing, `linked_task_counts`, `goal_for_task`, failure recording, and failure clearing. JSON arrays use `json.dumps` and `json.loads`; invalid states raise `ValueError`.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_goals tests.test_db -v
git add migrations/006_commander_goals.sql src/local_cli_coordinator/goals.py src/local_cli_coordinator/models.py tests/test_goals.py tests/test_db.py
git commit -m "feat: persist commander goals and runs"
```

---

### Task 2: Commander Protocol And Role

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/commander_protocol.py`
- Create: `tests/test_commander_protocol.py`
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `tests/test_review_config.py`

- [ ] **Step 1: Write failing tests**

```python
def test_unknown_response_fields_are_rejected(self):
    raw = json.dumps({"schema_version": 1, "goal_status": "active",
                      "progress_summary": "Ready", "tasks": [],
                      "stop_reason": None, "surprise": True})
    with self.assertRaisesRegex(ValueError, "unknown fields"):
        parse_commander_response(raw)

def test_commander_role_is_selectable(self):
    self.assertEqual(select_agent_by_role(self.config, "commander").id, "codex")
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_protocol tests.test_review_config -v
```

- [ ] **Step 3: Implement strict protocol**

```python
COMMANDER_SCHEMA_VERSION = 1
COMMANDER_GOAL_STATUSES = frozenset({"active", "blocked", "completed"})

@dataclass(frozen=True)
class CommanderTaskProposal:
    title: str
    repo: str
    capabilities: list[str]
    goal: str
    acceptance_criteria: list[str]
    verification_commands: list[str]
    expected_files: int
    expected_minutes: int
    parent_task_id: str | None
    rationale: str

@dataclass(frozen=True)
class CommanderResponse:
    schema_version: int
    goal_status: str
    progress_summary: str
    tasks: list[CommanderTaskProposal]
    stop_reason: str | None
```

Implement `parse_commander_response` and `commander_response_schema`. Reject unknown/missing keys, wrong types, blank required strings, unsupported status/version, negative estimates, more than three tasks, more than five criteria, and completed responses without a stop reason. Add `commander` to a validated `SUPPORTED_AGENT_ROLES`.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_protocol tests.test_review_config -v
git add src/local_cli_coordinator/commander_protocol.py src/local_cli_coordinator/config.py tests/test_commander_protocol.py tests/test_review_config.py
git commit -m "feat: define commander response protocol"
```

---

### Task 3: Read-Only Commander Runner

**Owner:** Claude Code

**Files:**
- Create: `src/local_cli_coordinator/commander_runner.py`
- Create: `tests/test_commander_runner.py`
- Modify: `config/agents.toml`

- [ ] **Step 1: Write failing tests**

```python
def test_runner_renders_paths_and_persists_artifacts(self):
    result = run_commander(self.conn, self.config, self.root,
                           self.goal_id, "initial_plan", 30)
    self.assertTrue(result.succeeded)
    self.assertEqual(result.response.progress_summary, "Ready")
    self.assertIn("Finish roadmap", result.prompt_path.read_text())
    self.assertTrue(result.raw_output_path.exists())

def test_runner_refuses_worker_role(self):
    with self.assertRaisesRegex(ValueError, "commander agent"):
        run_commander(self.conn, worker_only_config(), self.root,
                      self.goal_id, "initial_plan", 30)
```

Use a local Python fixture command; tests never invoke cloud CLI.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_runner -v
```

- [ ] **Step 3: Implement runner**

```python
@dataclass(frozen=True)
class CommanderRunResult:
    succeeded: bool
    response: CommanderResponse | None
    run_id: int
    prompt_path: Path
    raw_output_path: Path
    parsed_output_path: Path | None
    exit_code: int
    timed_out: bool
    error: str | None
```

Implement `build_commander_context` and `run_commander`. Context contains goal, constraints, repos, 20 linked tasks, five messages, rejected fingerprints, budgets, and roadmap files capped at 20,000 characters. Artifacts live under `runs/commander/<goal>/<run>/`. Render `{prompt_path}`, `{schema_path}`, and `{repo_path}` token-by-token after `shlex.split`. Use only the configured `commander` role and existing `run_command`. Permit one separately recorded schema-repair call.

Configure:

```toml
[agents.codex_commander]
command = "codex exec --sandbox read-only --ask-for-approval never --ephemeral --output-schema {schema_path} \\"Read {prompt_path} and return only the required JSON object.\\""
capabilities = ["code", "tests", "docs", "research"]
max_concurrency = 1
role = "commander"
```

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_runner -v
git add src/local_cli_coordinator/commander_runner.py tests/test_commander_runner.py config/agents.toml
git commit -m "feat: run Codex commander read-only"
```

---

### Task 4: Deterministic Admission Gate

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/commander_policy.py`
- Create: `tests/test_commander_policy.py`
- Modify: `src/local_cli_coordinator/goals.py`

- [ ] **Step 1: Write failing tests**

```python
def test_admission_inherits_verification_and_links_task(self):
    result = admit_commander_response(self.conn, self.config, self.root,
                                      self.goal_id, response())
    task = get_task(self.conn, result.accepted_task_ids[0])
    self.assertEqual(task["verification_commands"], "python -m unittest")
    self.assertEqual(goal_for_task(self.conn, task["id"])["id"], self.goal_id)

def test_unsafe_proposals_are_rejected(self):
    text = " ".join(admit_commander_response(
        self.conn, self.config, self.root, self.goal_id, unsafe_response()
    ).rejection_reasons)
    self.assertIn("file limit", text)
    self.assertIn("high-risk", text)
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_policy -v
```

- [ ] **Step 3: Implement admission**

```python
@dataclass(frozen=True)
class CommanderAdmissionResult:
    accepted_task_ids: list[str]
    rejection_reasons: list[str]
    batch_id: str
```

Implement `proposal_fingerprint`, `proposal_rejection_reasons`, and `admit_commander_response`. SHA-256 covers normalized goal ID, repo, title, goal, and sorted criteria. Reject non-allowlisted/out-of-goal repos, absent worker capability, oversized estimates, duplicate fingerprint/title, and credential, secret, live trading, funds, market order, destructive migration, drop table, or disable security signals. In one `BEGIN IMMEDIATE` transaction, inherit repo verification, insert task/event, and link task. Add a non-committing linked-task helper to `goals.py`.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_policy -v
git add src/local_cli_coordinator/commander_policy.py src/local_cli_coordinator/goals.py tests/test_commander_policy.py
git commit -m "feat: gate commander task proposals"
```

---

### Task 5: Goal Commands And Chat REPL

**Owner:** Claude Code

**Files:**
- Create: `src/local_cli_coordinator/commander_service.py`
- Create: `tests/test_goal_cli.py`
- Create: `tests/test_commander_chat.py`
- Modify: `src/local_cli_coordinator/cli.py`

- [ ] **Step 1: Write failing tests**

```python
def test_goal_text_creates_draft_preview(self):
    result = run_cli("--root", str(self.root), "goal",
                     "Finish", "the", "roadmap")
    self.assertIn("Goal draft", result.stdout)
    self.assertIn("goal confirm", result.stdout)

def test_goal_confirm_activates_preview(self):
    result = run_cli("--root", str(self.root), "goal", "confirm")
    self.assertIn("active", result.stdout)

def test_chat_start_without_preview_is_refused(self):
    with patch("builtins.input", side_effect=["/start", "/quit"]):
        result = run_cli("--root", str(self.root), "chat")
    self.assertIn("no draft goal", result.stdout)
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_goal_cli tests.test_commander_chat -v
```

- [ ] **Step 3: Implement service and routing**

```python
@dataclass(frozen=True)
class GoalPlanPreview:
    goal_id: str
    progress_summary: str
    proposals: list[CommanderTaskProposal]
```

Implement `create_and_preview_goal`, `confirm_goal`, `pause_goal`, `resume_goal`, and `abandon_goal`. Creation stores draft, user message, and preview artifact. Confirmation revalidates/admit the saved preview exactly once and activates only with an accepted task. Register `goal` with `nargs=\"*\"`: reserve `confirm`, `status`, `pause`, `resume`, `abandon`; otherwise join objective words. Register `chat` with normal text and `/status`, `/start`, `/pause`, `/resume`, `/quit`. Only draft goals may be refined.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_goal_cli tests.test_commander_chat tests.test_cli -v
git add src/local_cli_coordinator/commander_service.py src/local_cli_coordinator/cli.py tests/test_goal_cli.py tests/test_commander_chat.py
git commit -m "feat: add commander goal and chat commands"
```

---

### Task 6: Goal Status And Durable Memory

**Owner:** Grok

**Files:**
- Create: `src/local_cli_coordinator/commander_memory.py`
- Create: `tests/test_commander_memory.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Modify: `src/local_cli_coordinator/digest.py`
- Modify: `tests/test_loop_status.py`
- Modify: `tests/test_digest.py`

- [ ] **Step 1: Write failing tests**

```python
def test_empty_active_goal_waits_for_replenishment(self):
    result = run_cli("--root", str(self.root), "status", "--loop")
    self.assertIn("Goal: active", result.stdout)
    self.assertIn("waiting for Commander replenishment", result.stdout)

def test_no_goal_requests_long_term_goal(self):
    result = run_cli("--root", str(self.root), "status", "--loop")
    self.assertIn("waiting for a long-term goal", result.stdout)
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_memory tests.test_loop_status tests.test_digest -v
```

- [ ] **Step 3: Implement projection**

```python
COMMANDER_MEMORY_RELATIVE_PATH = Path("state/commander_memory.md")

def commander_memory_path(root: Path) -> Path:
    return root / COMMANDER_MEMORY_RELATIVE_PATH

def goal_status_summary(conn) -> tuple[str, str]:
    goal = active_goal(conn)
    if goal is None:
        return "Goal: none", "waiting for a long-term goal"
    counts = linked_task_counts(conn, goal["id"])
    if goal["status"] == "active" and counts.get("ready", 0) == 0:
        return "Goal: active", "waiting for Commander replenishment"
    return f"Goal: {goal['status']}", goal["stop_reason"] or goal["progress_summary"]

def write_commander_memory(conn, root: Path, goal_id: str) -> Path:
    goal = get_goal(conn, goal_id)
    tasks = list_linked_tasks(conn, goal_id)[:10]
    lines = [
        "# Commander Memory", "", f"Status: {goal['status']}",
        f"Objective: {goal['objective'][:2000]}",
        f"Progress: {goal['progress_summary'][:2000]}",
        "", "## Recent outcomes",
    ]
    lines.extend(f"- {task['title']}: {task['state']}" for task in tasks)
    lines.extend(["", "## Next action", goal_status_summary(conn)[1]])
    path = commander_memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
```

Memory contains objective, constraints, status, progress, current batch, ten outcomes, latest run, stop reason, and next action. Escape control characters and cap each string at 2,000 characters. Add Goal to `status --loop` and active progress to digest while preserving existing sections.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_memory tests.test_loop_status tests.test_digest -v
git add src/local_cli_coordinator/commander_memory.py src/local_cli_coordinator/cli.py src/local_cli_coordinator/digest.py tests/test_commander_memory.py tests/test_loop_status.py tests/test_digest.py
git commit -m "feat: expose commander goal progress"
```

---

### Task 7: Daemon Queue Replenishment

**Owner:** Claude Code

**Files:**
- Modify: `src/local_cli_coordinator/commander_service.py`
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/cli.py`
- Create: `tests/test_commander_replenishment.py`
- Modify: `tests/test_daemon_loop.py`

- [ ] **Step 1: Write failing tests**

```python
def test_active_empty_goal_replenishes(self):
    result = run_daemon_cycle(self.conn, self.config, self.root)
    self.assertEqual(result.commander_tasks_admitted, 2)

def test_nonactive_goal_does_not_replenish(self):
    for state in ("draft", "paused", "completed"):
        set_goal_state(self.conn, self.goal_id, state)
        self.assertEqual(maybe_replenish_goal(
            self.conn, self.config, self.root).status, "not_eligible")

def test_ready_task_prevents_commander_call(self):
    create_ready_task(self.conn)
    self.assertEqual(maybe_replenish_goal(
        self.conn, self.config, self.root).status, "queue_not_low")
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_replenishment tests.test_daemon_loop -v
```

- [ ] **Step 3: Implement lifecycle**

```python
@dataclass(frozen=True)
class ReplenishmentResult:
    status: str
    admitted_task_ids: list[str]
    rejected_reasons: list[str]
    commander_run_id: int | None
```

Implement `maybe_replenish_goal`. Invoke only for an active goal with zero ready tasks, no linked lease, elapsed retry time, and no live Commander run. Mark calls older than task timeout interrupted. Admit proposals, update progress/memory, and clear failures. Completion requires all linked tasks terminal; blocked response stores reason. Extend `DaemonCycleResult` with `commander_tasks_admitted=0` and `commander_status=None`. Run discovery, replenishment, then leasing; format admission count in CLI.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_replenishment tests.test_daemon_loop tests.test_multi_task_run -v
git add src/local_cli_coordinator/commander_service.py src/local_cli_coordinator/engine.py src/local_cli_coordinator/cli.py tests/test_commander_replenishment.py tests/test_daemon_loop.py
git commit -m "feat: replenish active goals from daemon"
```

---

### Task 8: Retry And Safety Stops

**Owner:** Grok

**Files:**
- Modify: `src/local_cli_coordinator/commander_service.py`
- Modify: `src/local_cli_coordinator/commander_runner.py`
- Modify: `src/local_cli_coordinator/commander_policy.py`
- Create: `tests/test_commander_failures.py`
- Modify: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write failing tests**

```python
def test_timeout_schedules_retry_without_losing_goal(self):
    result = maybe_replenish_goal(self.conn, self.config, self.root)
    goal = get_goal(self.conn, self.goal_id)
    self.assertEqual(result.status, "retry_scheduled")
    self.assertEqual(goal["status"], "active")
    self.assertEqual(goal["commander_failures"], 1)

def test_third_failure_pauses(self):
    run_three_failed_cycles(self.conn, self.config, self.root)
    self.assertEqual(get_goal(self.conn, self.goal_id)["status"], "paused")

def test_high_risk_only_batch_blocks(self):
    self.assertEqual(maybe_replenish_goal(
        self.conn, self.config, self.root).status, "blocked_high_risk")
```

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_failures tests.test_circuit_breaker -v
```

- [ ] **Step 3: Implement bounded policy**

Retry after 60 then 300 seconds; pause on third failure. Classify quota/rate-limit/429, timeout, process, protocol, invalid proposal, and high risk. Successful admission clears counters. All-high-risk blocks immediately. Resume prints prior reason and clears counters; daemon never auto-resumes. Status shows retry time and failure count.

- [ ] **Step 4: Verify GREEN and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_failures tests.test_circuit_breaker tests.test_commander_replenishment -v
git add src/local_cli_coordinator/commander_service.py src/local_cli_coordinator/commander_runner.py src/local_cli_coordinator/commander_policy.py src/local_cli_coordinator/cli.py tests/test_commander_failures.py tests/test_circuit_breaker.py
git commit -m "feat: bound commander retries and safety stops"
```

---

### Task 9: Two-Batch End-To-End And Docs

**Owner:** Claude Code

**Files:**
- Create: `tests/test_commander_e2e.py`
- Modify: `src/local_cli_coordinator/config.py`
- Modify: `config/policy.toml`
- Modify: `README.md`

- [ ] **Step 1: Write failing two-batch test**

```python
def test_two_dependent_batches_complete_goal(self):
    preview = create_and_preview_goal(self.conn, self.config,
                                      self.root, "Build two slices")
    confirm_goal(self.conn, self.config, self.root)
    first = run_daemon_cycle(self.conn, self.config, self.root)
    second = run_daemon_cycle(self.conn, self.config, self.root)
    run_daemon_cycle(self.conn, self.config, self.root)
    self.assertEqual(first.tasks_processed, 1)
    self.assertEqual(second.tasks_processed, 1)
    self.assertEqual(get_goal(self.conn, preview.goal_id)["status"], "completed")
    runs = list_commander_runs(self.conn, preview.goal_id)
    self.assertIn("first task: done",
                  Path(runs[1]["prompt_path"]).read_text().lower())
```

Fake Commander proposes `first.txt`, requires its persisted outcome before proposing `second.txt`, then completes. No cloud calls.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_e2e -v
```

- [ ] **Step 3: Add configuration and docs**

Add `CommanderPolicyConfig` and:

```toml
[commander_policy]
queue_low_watermark = 1
max_tasks_per_batch = 3
max_consecutive_failures = 3
first_retry_seconds = 60
second_retry_seconds = 300
roadmap_context_max_chars = 20000
```

Document `chat`, goal status/confirm, daemon, status, slash commands, one confirmation, read-only boundary, artifacts, retries, and no-goal behavior. Add only integration glue demonstrated by the failing test.

- [ ] **Step 4: Verify and commit**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_e2e -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
git add tests/test_commander_e2e.py src/local_cli_coordinator/config.py config/policy.toml README.md
git commit -m "test: prove autonomous commander batches"
```

---

### Task 10: Adversarial Acceptance

**Owner:** Grok

**Files:**
- Modify: only files required by reproduced failing tests
- Create: `docs/superpowers/specs/2026-06-19-codex-commander-final-report.md`

- [ ] **Step 1: Review scope**

```bash
git diff --check 0ef3d7e..HEAD
git log --oneline --reverse 0ef3d7e..HEAD
PYTHONPATH=src python3 -m local_cli_coordinator doctor
```

Review write access, bypassed admission, duplicate races, multiple active goals, stale runs, secret persistence, malformed JSON, auto-resume, and no-goal daemon behavior.

- [ ] **Step 2: Add tests only for reproduced defects**

Required attack cases: worker role cannot become Commander; concurrent replenishment admits one fingerprint; context does not copy environment values; completion cannot skip nonterminal tasks; restart interrupts stale runs; daemon without goal preserves inbox behavior. Each test must fail before its production fix. Do not modify production for an unreproduced concern.

- [ ] **Step 3: Verify**

```bash
PYTHONPATH=src python3 -m unittest tests.test_commander_protocol tests.test_commander_runner tests.test_commander_policy tests.test_goal_cli tests.test_commander_chat tests.test_commander_memory tests.test_commander_replenishment tests.test_commander_failures tests.test_commander_e2e -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check 0ef3d7e..HEAD
```

- [ ] **Step 4: Run no-goal smoke**

```bash
PYTHONPATH=src python3 -m local_cli_coordinator goal status
PYTHONPATH=src python3 -m local_cli_coordinator status --loop
PYTHONPATH=src python3 -m local_cli_coordinator daemon --once
```

Expected: zero exits, explicit no-goal status, no Codex invocation, and no managed-repo mutation.

- [ ] **Step 5: Report and commit**

Record commit scope, test count, smoke output, warnings, and every design requirement.

```bash
git add docs/superpowers/specs/2026-06-19-codex-commander-final-report.md
git commit -m "docs: record Codex commander acceptance"
```

---

## Codex Integration Gate

After each wave, Codex runs focused tests, `git diff --check`, and commit-scope review. Final gate:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m local_cli_coordinator doctor
PYTHONPATH=src python3 -m local_cli_coordinator goal status
PYTHONPATH=src python3 -m local_cli_coordinator status --loop
PYTHONPATH=src python3 -m local_cli_coordinator daemon --once
git diff --check 0ef3d7e..HEAD
git status --short
```

Acceptance requires a clean worktree, zero failures, no Commander call without an active confirmed goal, no write-capable Commander configuration, and a passing two-batch end-to-end scenario.
