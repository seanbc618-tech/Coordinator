# Phase 5.1 TUI Task Visibility and Worker Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase 5 Commander-backed TUI understandable and reliable in real use: users can see what task was created, inspect why it failed, use `/help`, avoid duplicate local chat echoes, and run report-only acceptance tasks without false failure.

**Architecture:** Keep Hermes/Ink TUI as the shell and keep Supervisor RPC as the only backend boundary. Add small project-scoped RPC surfaces for task detail/help, enrich existing events instead of introducing a second event channel, and fix worker execution at the engine boundary where prompt placement and report-only task semantics currently break.

**Tech Stack:** Python `sqlite3` Supervisor backend, existing Coordinator engine/Commander modules, TypeScript Ink TUI, existing Python `unittest` and Vitest/PTY suites.

---

## Current Evidence From Real Smoke

User ran `coordinator` from `/Users/xiafan/polymarket-crypto-threshold` after Phase 5. The following behavior is confirmed:

- Goal creation and `/goal confirm` work.
- `chat.send` reaches Commander and admits a task.
- `/tasks` works but is too terse.
- `/help` fails with `unsupported method 'system.help'`.
- The user's chat text appears twice.
- Created task `task-7e442d068a8d` failed before doing useful work.

Database inspection showed:

```text
id: task-7e442d068a8d
title: Run baseline acceptance checks
state: failed
goal: Run the repo's existing verification commands and report whether the current baseline passes or fails without changing code.
acceptance_criteria:
  `uv run pytest -q` has been executed and the result is recorded.
  `uv run ruff check src/ tests/` has been executed and the result is recorded.
verification_commands:
  uv run pytest -q
  uv run ruff check src/ tests/
failure event note: no changed files
agent log: I need permission to read the prompt file.
```

Root causes:

1. TUI receives `task.created` with only `{task_id, goal_id}`, so the user cannot tell what was created.
2. `/tasks` returns only `id/title/state/repo/priority`, so it cannot explain goal, criteria, commands, worktree, or last failure.
3. `/help` is registered in `ui-tui/src/slash.ts` as `system.help`, but Supervisor does not implement `system.help`.
4. TUI optimistically appends user chat locally, then Supervisor publishes the same user `chat.message`, producing duplicate display.
5. Worker prompt is written outside the task worktree under global runtime state; Claude CLI can refuse to read it when instructed to work only in the current worktree.
6. Engine treats every task with no changed files as failed. That is correct for code-edit tasks but wrong for read-only/report-only tasks such as baseline acceptance checks.

## Role Assignment

Grok owns production implementation. Gemini owns adversarial review and final PASS/FAIL. Claude Code gets only bounded, low-complexity tasks that are easy to verify:

- add or update Vitest formatting tests;
- add simple Python unit tests around RPC outputs;
- update docs after Grok's code lands;
- run focused gate commands and paste exact output.

Do not assign Claude Code architectural changes, engine state-machine changes, Supervisor concurrency changes, or Commander prompt design.

## File Map

Expected production files:

- `src/local_cli_coordinator/db.py`: add task detail query helpers, last event/attempt/artifact helpers if missing.
- `src/local_cli_coordinator/supervisor_methods.py`: add `project.task`, `system.help`, enrich `project.tasks`.
- `src/local_cli_coordinator/supervisor_commander.py`: enrich `task.created` payloads with task details.
- `src/local_cli_coordinator/engine.py`: copy/write prompt into worktree-accessible path and support report-only tasks.
- `src/local_cli_coordinator/config.py`: if needed, add a conservative policy flag for report-only detection, but prefer deriving from task metadata and capabilities.
- `ui-tui/src/slash.ts`: add `/task` and fix `/help` handling.
- `ui-tui/src/submitDecision.ts`: allow local-only slash commands if `/help` is handled client-side.
- `ui-tui/src/slashDisplay.ts`: format detailed task and richer task lists.
- `ui-tui/src/eventReducer.ts`: render richer `task.created`; dedupe local/server user chat.
- `ui-tui/src/app.tsx`: remove or gate optimistic chat echo if reducer dedupe is not enough.
- `ui-tui/src/domain.ts`: extend activity metadata fields if needed.
- `src/local_cli_coordinator/tui_bundle/`: rebuilt after UI changes.

Expected tests:

- `tests/test_supervisor_methods.py`
- `tests/test_supervisor_commander.py`
- `tests/test_engine.py`
- `tests/test_tui_pty.py`
- `tests/test_global_tui_e2e.py`
- `ui-tui/src/__tests__/slash.test.ts`
- `ui-tui/src/__tests__/slashDisplay.test.ts`
- `ui-tui/src/__tests__/eventReducer.test.ts`
- `ui-tui/src/__tests__/submitDecision.test.ts`

Expected docs:

- `docs/tui.md`
- `docs/troubleshooting.md`
- `docs/superpowers/handoffs/2026-06-23-phase5-1-tui-task-visibility-worker-reliability.md`

---

## Task 0: Reproduction Fixtures and Acceptance Baseline

**Owner:** Claude Code

**Purpose:** Capture the real failure as tests before Grok changes production code.

**Files:**
- Modify: `tests/test_supervisor_methods.py`
- Modify: `tests/test_supervisor_commander.py`
- Modify: `ui-tui/src/__tests__/eventReducer.test.ts`
- Modify: `ui-tui/src/__tests__/slash.test.ts`

- [ ] **Step 1: Add a Supervisor method test for task detail absence**

Add a test that creates a project task with goal, acceptance criteria, verification commands, branch/worktree, a failed event note, and an attempt log artifact. It should assert the future `project.task` method returns those fields.

Expected request shape:

```python
resp = self.methods.handle(
    self.conn,
    _request("project.task", project_id=self.project_id, args="task-detail-1"),
)
self.assertTrue(resp.ok, resp.error)
self.assertEqual(resp.result["task"]["id"], "task-detail-1")
self.assertEqual(resp.result["task"]["goal"], "Run baseline checks")
self.assertIn("uv run pytest -q", resp.result["task"]["verification_commands"])
self.assertEqual(resp.result["latest_event"]["note"], "no changed files")
self.assertIn("agent.log", resp.result["artifacts"][0]["path"])
```

- [ ] **Step 2: Add a task.created payload test**

In `tests/test_supervisor_commander.py`, patch Commander admission to create a task and assert emitted `task.created` contains at least:

```python
{
    "task_id": "task-...",
    "title": "...",
    "state": "ready",
    "goal": "...",
    "acceptance_criteria": "...",
    "verification_commands": ["..."],
}
```

Expected before Grok fix: FAIL because only `task_id` and `goal_id` are present.

- [ ] **Step 3: Add `/help` parser/display test**

In `ui-tui/src/__tests__/slash.test.ts`, assert `/help` remains recognized. If Grok chooses client-local help, the expected decision should be a local action, not a Supervisor RPC. If Grok chooses backend help, assert method is `system.help`.

Use exact behavior after Grok chooses the implementation; do not invent a third behavior.

- [ ] **Step 4: Add duplicate user-message reducer test**

In `ui-tui/src/__tests__/eventReducer.test.ts`, start with transcript containing a local user message `hi`, then reduce a server `chat.message` event with role `user` and text `hi`. The transcript should still contain exactly one visible user message.

Expected after fix:

```ts
const visibleUsers = result.transcript.filter(
  item => item.kind === 'message' && item.role === 'user' && item.text === 'hi',
)
expect(visibleUsers).toHaveLength(1)
```

- [ ] **Step 5: Run focused failing tests**

Run:

```bash
npm test --prefix ui-tui -- --run ui-tui/src/__tests__/slash.test.ts ui-tui/src/__tests__/eventReducer.test.ts
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods tests.test_supervisor_commander -v
```

Expected: tests for new behavior fail before production changes.

- [ ] **Step 6: Commit**

```bash
git add tests/test_supervisor_methods.py tests/test_supervisor_commander.py ui-tui/src/__tests__/slash.test.ts ui-tui/src/__tests__/eventReducer.test.ts
git commit -m "test: capture Phase 5.1 TUI task visibility regressions"
```

---

## Task 1: Project Task Detail RPC and Rich Task Lists

**Owner:** Grok

**Purpose:** Let the user answer “what task did it create?” from inside the TUI.

**Files:**
- Modify: `src/local_cli_coordinator/db.py`
- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Modify: `tests/test_supervisor_methods.py`

- [ ] **Step 1: Add DB helper `project_get_task_detail`**

Add a helper that scopes by both `project_id` and `task_id`; never return a task from another project.

Suggested interface:

```python
def project_get_task_detail(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "select * from tasks where project_id = ? and id = ?",
        (project_id, task_id),
    ).fetchone()
```

Also add helpers or inline queries for:

```sql
select * from events where project_id = ? and task_id = ? order by id desc limit 1
select * from attempts where task_id = ? order by id desc limit 1
select kind, path from artifacts where project_id = ? and task_id = ? order by id
```

- [ ] **Step 2: Add `project.task` method**

Register `"project.task": self._handle_project_task`.

Expected request:

```json
{"method": "project.task", "project_id": "proj-...", "params": {"args": "task-..."}}
```

Expected success result:

```python
{
    "task": {
        "id": row["id"],
        "title": row["title"],
        "state": row["state"],
        "repo": row["repo"],
        "priority": row["priority"],
        "capabilities": row["capabilities"],
        "goal": row["goal"],
        "acceptance_criteria": row["acceptance_criteria"],
        "verification_commands": row["verification_commands"].splitlines(),
        "branch": row["branch"],
        "worktree_path": row["worktree_path"],
        "source_path": row["source_path"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    },
    "latest_event": {
        "old_state": latest["old_state"],
        "new_state": latest["new_state"],
        "note": latest["note"],
        "created_at": latest["created_at"],
    } if latest else None,
    "latest_attempt": {
        "agent_id": attempt["agent_id"],
        "exit_code": attempt["exit_code"],
        "result_class": attempt["result_class"],
        "result_reason": attempt["result_reason"],
        "log_path": attempt["log_path"],
        "completed_at": attempt["completed_at"],
    } if attempt else None,
    "artifacts": [{"kind": row["kind"], "path": row["path"]} for row in artifacts],
}
```

Expected errors:

- empty args: `"task id is required"`
- unknown task: `"task '...' not found in project '...'"`
- foreign task: same unknown-task error; do not leak existence.

- [ ] **Step 3: Enrich `project.tasks`**

Keep it compact, but include enough detail for a human:

```python
{
    "id": row["id"],
    "title": row["title"],
    "state": row["state"],
    "repo": row["repo"],
    "priority": row["priority"],
    "goal": row["goal"],
    "latest_note": latest_event_note,
}
```

Do not include full logs in `project.tasks`.

- [ ] **Step 4: Run tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/local_cli_coordinator/db.py src/local_cli_coordinator/supervisor_methods.py tests/test_supervisor_methods.py
git commit -m "feat(supervisor): expose project task details"
```

---

## Task 2: Rich `task.created` Events and TUI Task Rendering

**Owner:** Grok

**Purpose:** The moment Commander admits a task, the TUI should show its title and purpose, not just an opaque id.

**Files:**
- Modify: `src/local_cli_coordinator/supervisor_commander.py`
- Modify: `ui-tui/src/domain.ts`
- Modify: `ui-tui/src/eventReducer.ts`
- Modify: `ui-tui/src/components/ActivityBlock.tsx`
- Modify: `tests/test_supervisor_commander.py`
- Modify: `ui-tui/src/__tests__/eventReducer.test.ts`

- [ ] **Step 1: Enrich `task.created` payload**

After each accepted task id, query the inserted task and publish:

```python
{
    "task_id": task_id,
    "goal_id": result.goal_id,
    "title": task["title"],
    "state": task["state"],
    "repo": task["repo"],
    "goal": task["goal"],
    "acceptance_criteria": task["acceptance_criteria"],
    "verification_commands": [
        line for line in task["verification_commands"].splitlines() if line
    ],
}
```

If the task row is unexpectedly missing, publish the old minimal event and include `"detail_unavailable": True`; do not crash the chat request.

- [ ] **Step 2: Extend Activity type**

Add optional fields to `Activity`:

```ts
goal?: string | null
acceptanceCriteria?: string | null
verificationCommands?: string[]
state?: string | null
latestNote?: string | null
```

- [ ] **Step 3: Render task purpose in activity block**

For a created task, show:

```text
task-... created
Run baseline acceptance checks
Goal: Run the repo's existing verification commands...
Verify: uv run pytest -q; uv run ruff check src/ tests/
```

Keep it compact; do not render full acceptance criteria unless expanded mode is active.

- [ ] **Step 4: Update reducer tests**

Assert a `task.created` event with `goal` and `verification_commands` stores these fields and renders via `ActivityBlock` snapshot or text test.

- [ ] **Step 5: Run tests**

```bash
npm test --prefix ui-tui -- --run ui-tui/src/__tests__/eventReducer.test.ts
PYTHONPATH=src python3 -m unittest tests.test_supervisor_commander -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/local_cli_coordinator/supervisor_commander.py ui-tui/src/domain.ts ui-tui/src/eventReducer.ts ui-tui/src/components/ActivityBlock.tsx tests/test_supervisor_commander.py ui-tui/src/__tests__/eventReducer.test.ts
git commit -m "feat(tui): show admitted task purpose immediately"
```

---

## Task 3: Slash Help, `/task`, and Duplicate User Message Fix

**Owner:** Grok

**Purpose:** Make the TUI feel like a direct interface instead of a half-wired command proxy.

**Files:**
- Modify: `ui-tui/src/slash.ts`
- Modify: `ui-tui/src/submitDecision.ts`
- Modify: `ui-tui/src/slashDisplay.ts`
- Modify: `ui-tui/src/app.tsx`
- Modify: `ui-tui/src/eventReducer.ts`
- Modify: `src/local_cli_coordinator/supervisor_methods.py`
- Modify: `ui-tui/src/__tests__/slash.test.ts`
- Modify: `ui-tui/src/__tests__/slashDisplay.test.ts`
- Modify: `ui-tui/src/__tests__/submitDecision.test.ts`
- Modify: `ui-tui/src/__tests__/eventReducer.test.ts`
- Modify: `tests/test_supervisor_methods.py`

- [ ] **Step 1: Add `/task` command**

Add:

```ts
{ name: '/task', description: 'Show one task in detail', method: 'project.task' }
```

Expected usage:

```text
/task task-7e442d068a8d
```

- [ ] **Step 2: Fix `/help`**

Preferred implementation: make `/help` local-only so it cannot fail when Supervisor is old or offline.

Extend `SubmitDecision`:

```ts
| { action: 'local-help'; newPending: null }
```

In `decideSubmit`, return `local-help` for `/help`.

The help text should be generated from `SLASH_COMMANDS`, excluding hidden internal commands if any. It must include `/goal`, `/tasks`, `/task`, `/logs`, `/status`, `/quit`.

Expected TUI output:

```text
Commands:
/status - Show project status
/goal <objective> - Create a draft goal
/goal confirm - Activate the draft goal
/tasks - List project tasks
/task <id> - Show one task in detail
/logs - Show recent logs
/quit - Detach the TUI
```

If Grok chooses backend `system.help` instead, Gemini must verify `/help` works while connected and fails gracefully while offline. The client-local approach is recommended.

- [ ] **Step 3: Remove duplicate user chat**

Preferred implementation: stop optimistic local echo in `app.tsx` for plain chat and rely on Supervisor's persisted `chat.message` event.

Current code appends:

```ts
{ id: `user-${Date.now()}`, kind: 'message', role: 'user', text: decision.text }
```

Remove that append for `decision.action === 'chat'`, then call:

```ts
void client.request('chat.send', { text: decision.text }).catch(() => {})
```

If a responsive local echo is desired later, implement a pending-message id and reconcile with server events. Do not ship duplicate visible messages.

- [ ] **Step 4: Add reducer guard anyway**

Even after removing optimistic echo, add a reducer guard for replay edge cases:

```ts
function isDuplicateRecentUserMessage(state: TuiState, text: string): boolean {
  const last = state.transcript[state.transcript.length - 1]
  return last?.kind === 'message' && last.role === 'user' && last.text === text
}
```

Use it in `reduceChatMessage` for `role === 'user'`.

- [ ] **Step 5: Format `project.task`**

Add `project.task` case to `formatSlashResponse`:

```text
Task task-... [failed] Run baseline acceptance checks
Goal: Run the repo's existing verification commands...
Verify:
- uv run pytest -q
- uv run ruff check src/ tests/
Last event: running -> failed: no changed files
Latest attempt: claude_worker exit=0 interactive_blocked
Log: /Users/.../agent.log
Worktree: /Users/.../worktrees/...
```

Truncate long goal/criteria/log paths only if needed for readability; never omit them entirely.

- [ ] **Step 6: Run tests**

```bash
npm test --prefix ui-tui -- --run ui-tui/src/__tests__/slash.test.ts ui-tui/src/__tests__/submitDecision.test.ts ui-tui/src/__tests__/slashDisplay.test.ts ui-tui/src/__tests__/eventReducer.test.ts
PYTHONPATH=src python3 -m unittest tests.test_supervisor_methods -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ui-tui/src/slash.ts ui-tui/src/submitDecision.ts ui-tui/src/slashDisplay.ts ui-tui/src/app.tsx ui-tui/src/eventReducer.ts src/local_cli_coordinator/supervisor_methods.py ui-tui/src/__tests__/slash.test.ts ui-tui/src/__tests__/submitDecision.test.ts ui-tui/src/__tests__/slashDisplay.test.ts ui-tui/src/__tests__/eventReducer.test.ts tests/test_supervisor_methods.py
git commit -m "feat(tui): add help and task detail commands"
```

---

## Task 4: Worker Prompt Accessibility and Report-Only Task Semantics

**Owner:** Grok

**Purpose:** Fix the real execution failure: worker could not read the prompt and report-only tasks were falsely failed for having no changed files.

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/runner.py` if needed by command interpolation.
- Modify: `tests/test_engine.py`
- Modify: `tests/test_phase2_gate.py` if there are source audit gates that need updating.

- [ ] **Step 1: Write/copy prompt into the task worktree**

After worktree creation and before `run_worker_attempt`, create:

```python
worktree_prompt = worktree / ".coordinator" / task["id"] / "prompt.md"
worktree_prompt.parent.mkdir(parents=True, exist_ok=True)
worktree_prompt.write_text(prompt.read_text(encoding="utf-8"), encoding="utf-8")
add_artifact(conn, task["id"], "worktree_prompt", worktree_prompt)
```

Then pass `worktree_prompt` to `run_worker_attempt`.

Important: the global prompt under runtime state may remain for audit, but worker command must reference the worktree-local prompt.

- [ ] **Step 2: Protect against committing `.coordinator` prompt artifacts**

Before collecting changed files, either:

- add `.coordinator/` to the task worktree's `.git/info/exclude`, or
- filter `.coordinator/` out of `collect_changed_files_since`.

Preferred: write to `.git/info/exclude` because it keeps git status clean:

```python
exclude_path = worktree / ".git" / "info" / "exclude"
with exclude_path.open("a", encoding="utf-8") as fh:
    fh.write("\n# Coordinator runtime prompts\n.coordinator/\n")
```

Handle linked worktree `.git` files correctly. If `.git` is a file, resolve the gitdir from its `gitdir: ...` contents.

- [ ] **Step 3: Add report-only task detection**

Use conservative detection. A task is report-only if all of these are true:

- capabilities include `tests` and do not include code-editing capabilities such as `code`, `implementation`, `frontend`, `backend`;
- goal or title contains one of: `report`, `baseline`, `acceptance checks`, `without changing code`, `read-only`;
- acceptance criteria require recording/reporting command results.

Add helper:

```python
def _is_report_only_task(task: dict) -> bool:
    capabilities = {part.strip() for part in task["capabilities"].split(",") if part.strip()}
    text = " ".join([
        task.get("title", ""),
        task.get("goal", ""),
        task.get("acceptance_criteria", ""),
    ]).lower()
    edit_caps = {"code", "implementation", "frontend", "backend"}
    markers = [
        "report",
        "baseline",
        "acceptance checks",
        "without changing code",
        "read-only",
    ]
    return "tests" in capabilities and not (capabilities & edit_caps) and any(m in text for m in markers)
```

Do not treat arbitrary docs or code tasks as report-only.

- [ ] **Step 4: For report-only tasks, run verification before changed-file failure**

Current flow fails before verification when `changed_files` is empty. For report-only tasks:

1. run the task's `verification_commands`;
2. add verification artifacts;
3. if verification passes, finish task as `done` with note `"report-only verification passed"` even with no changed files;
4. if verification fails, finish task as `failed` with note including the failing command summary;
5. do not create a diff artifact or commit.

Expected high-level branch:

```python
changed_files = collect_changed_files_since(worktree, base_commit)
if not changed_files and _is_report_only_task(task):
    commands = [line for line in task["verification_commands"].splitlines() if line] or repo.verify_commands
    verification = run_verification(commands, worktree, run_dir, timeout_seconds=config.policy.max_task_runtime_seconds, reporter=reporter)
    add_artifact(conn, task["id"], "verification_log", verification.log_path)
    if verification.ok:
        _finish_task(conn, root, task["id"], "done", "report-only verification passed", verifier_result="passed", next_action="none")
    else:
        _finish_task(conn, root, task["id"], "failed", f"report-only verification failed: {verification.summary}", verifier_result="failed", next_action="inspect verification log")
    return True
if not changed_files:
    ...
```

Use the actual `VerificationResult` fields in this codebase; do not invent `ok`, `log_path`, or `summary` if names differ.

- [ ] **Step 5: Ensure interactive-blocked fallback still works**

If Claude says it cannot read prompt, classification should be `interactive_blocked` and fallback can run once. After prompt relocation, the same command should no longer fail for that reason.

Add/adjust test to assert the prompt path passed to `run_agent` is inside the worktree.

- [ ] **Step 6: Run tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_engine tests.test_phase2_gate -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/local_cli_coordinator/engine.py src/local_cli_coordinator/runner.py tests/test_engine.py tests/test_phase2_gate.py
git commit -m "fix(engine): run workers from worktree prompts and allow report-only tasks"
```

---

## Task 5: Failure Reason Surfacing in Events and Logs

**Owner:** Grok

**Purpose:** When a task fails, TUI should explain why without forcing the user to leave the app.

**Files:**
- Modify: `src/local_cli_coordinator/engine.py`
- Modify: `src/local_cli_coordinator/reporter.py` if event reporting supports task stage events.
- Modify: `src/local_cli_coordinator/supervisor_events.py` only if needed.
- Modify: `ui-tui/src/eventReducer.ts`
- Modify: `ui-tui/src/components/ActivityBlock.tsx`
- Modify: `tests/test_engine.py`
- Modify: `ui-tui/src/__tests__/eventReducer.test.ts`

- [ ] **Step 1: Publish task terminal event with reason**

When `_finish_task` transitions to `failed`, `blocked`, `needs_split`, `awaiting_human`, or `done`, ensure the event payload visible to TUI includes:

```python
{
    "task_id": task_id,
    "result": state,
    "reason": note,
    "next_action": next_action,
}
```

If existing event model only has DB state events, add a Supervisor broker event such as `task.done` or `task.stage` at the same boundary used by live daemon observability.

- [ ] **Step 2: Show failure reason in ActivityBlock**

For failed task:

```text
task-... failed
Reason: no changed files
Next: inspect agent output and retry
```

After Task 4, the real baseline task should no longer fail as `no changed files`, but this display is still needed for genuine failures.

- [ ] **Step 3: Include latest attempt class**

If available from `project.task`, show:

```text
Attempt: claude_worker exit=0 interactive_blocked — permission required
```

This can be limited to `/task <id>` in Phase 5.1; live activity block can stay shorter.

- [ ] **Step 4: Tests**

Add reducer test for:

```ts
reduceEvent(state, makeEvent(2, 'task.done', {
  task_id: 'task-1',
  result: 'failed',
  reason: 'agent command failed',
  next_action: 'inspect agent log and retry',
}))
```

Expected activity stage or displayed note includes both `failed` and `agent command failed`.

- [ ] **Step 5: Run tests**

```bash
npm test --prefix ui-tui -- --run ui-tui/src/__tests__/eventReducer.test.ts
PYTHONPATH=src python3 -m unittest tests.test_engine -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/local_cli_coordinator/engine.py src/local_cli_coordinator/reporter.py ui-tui/src/eventReducer.ts ui-tui/src/components/ActivityBlock.tsx tests/test_engine.py ui-tui/src/__tests__/eventReducer.test.ts
git commit -m "feat(tui): surface task failure reasons"
```

---

## Task 6: PTY and Real Smoke Gates

**Owner:** Grok for implementation; Claude Code may run commands and collect output only.

**Purpose:** Prove the exact user scenario is fixed through TUI and Supervisor, not just unit tests.

**Files:**
- Modify: `tests/test_tui_pty.py`
- Modify: `tests/test_global_tui_e2e.py`
- Create or modify: `docs/superpowers/handoffs/2026-06-23-phase5-1-tui-task-visibility-worker-reliability.md`

- [ ] **Step 1: Add PTY test for `/help`**

Spawn fake Supervisor TUI, type `/help`, assert output includes `/task <id>` and does not include `unsupported method`.

- [ ] **Step 2: Add PTY test for no duplicate user chat**

Type a chat message once. Assert the final frame contains the user message exactly once and contains Commander response once.

- [ ] **Step 3: Add PTY test for `/task <id>`**

Fake Supervisor should respond to `project.task` with a failed baseline task. Assert frame includes:

```text
Run baseline acceptance checks
uv run pytest -q
no changed files
agent.log
```

- [ ] **Step 4: Add E2E report-only task smoke**

Use fake Commander to admit a report-only task with verification commands. The E2E must show:

- task created with title and goal visible;
- no duplicate user message;
- task reaches `done` when verification passes and no files changed;
- `/task <id>` explains the verification result.

- [ ] **Step 5: Run focused gates**

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
PYTHONPATH=src python3 -m unittest tests.test_tui_pty tests.test_global_tui_e2e -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_tui_pty.py tests/test_global_tui_e2e.py docs/superpowers/handoffs/2026-06-23-phase5-1-tui-task-visibility-worker-reliability.md
git commit -m "test: gate Phase 5.1 TUI real-use fixes"
```

---

## Task 7: Docs, Bundle, and Acceptance Report

**Owner:** Claude Code for docs draft; Grok for final bundle/build commit.

**Purpose:** Keep user-facing docs honest and bundle the TUI changes.

**Files:**
- Modify: `docs/tui.md`
- Modify: `docs/troubleshooting.md`
- Modify: `src/local_cli_coordinator/tui_bundle/`
- Modify: `docs/superpowers/handoffs/2026-06-23-phase5-1-tui-task-visibility-worker-reliability.md`

- [ ] **Step 1: Update TUI docs**

Document:

- `/help`
- `/tasks`
- `/task <id>`
- report-only tasks
- where logs live
- why a task can be `failed`, `blocked`, `needs_split`, or `awaiting_human`

- [ ] **Step 2: Update troubleshooting**

Add exact symptom mappings:

```text
Symptom: "unsupported method 'system.help'"
Expected after Phase 5.1: should not happen. Reinstall package or restart Supervisor.

Symptom: user message appears twice
Expected after Phase 5.1: should not happen. Check TUI bundle version.

Symptom: task failed with "no changed files"
Meaning: code-edit task produced no patch. Report-only tasks should not fail for this reason.

Symptom: agent says it cannot read prompt file
Expected after Phase 5.1: prompt path should be inside task worktree under .coordinator/.
```

- [ ] **Step 3: Rebuild TUI bundle**

```bash
npm run build --prefix ui-tui
```

Expected: build succeeds and `src/local_cli_coordinator/tui_bundle/` updates.

- [ ] **Step 4: Run full gates in isolated XDG**

Use isolated XDG to avoid the user's real global Supervisor polluting tests:

```bash
rm -rf /private/tmp/coordinator-phase5-1-xdg
mkdir -p /private/tmp/coordinator-phase5-1-xdg/config /private/tmp/coordinator-phase5-1-xdg/data /private/tmp/coordinator-phase5-1-xdg/state
XDG_CONFIG_HOME=/private/tmp/coordinator-phase5-1-xdg/config \
XDG_DATA_HOME=/private/tmp/coordinator-phase5-1-xdg/data \
XDG_STATE_HOME=/private/tmp/coordinator-phase5-1-xdg/state \
PYTHONWARNINGS=error::ResourceWarning \
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

Also run:

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
git diff --check
```

Expected: all PASS.

- [ ] **Step 5: Manual smoke script**

From a real project:

```bash
cd /Users/xiafan/polymarket-crypto-threshold
coordinator
```

Manual checks:

```text
/help
/goal
/tasks
/task <latest-task-id>
hi，请生成 1 个很小的后续任务
/tasks
/task <new-task-id>
/quit
```

Expected:

- `/help` prints commands, no unsupported method.
- user message appears once.
- task creation shows title and goal immediately.
- `/tasks` includes a meaningful summary.
- `/task <id>` shows goal, verification commands, last event, attempt/log path.
- report-only task does not fail merely because no files changed.

- [ ] **Step 6: Commit**

```bash
git add docs/tui.md docs/troubleshooting.md src/local_cli_coordinator/tui_bundle docs/superpowers/handoffs/2026-06-23-phase5-1-tui-task-visibility-worker-reliability.md
git commit -m "docs: document Phase 5.1 TUI task visibility"
```

---

## Gemini Adversarial Review Checklist

Gemini should review after Tasks 1-7 are integrated. Required verdict format: `PASS`, `CONDITIONAL PASS`, or `FAIL`, with concrete blocking findings.

Gemini must verify:

1. **Task detail scope safety**
   - `project.task` cannot reveal tasks from another project.
   - unknown and foreign tasks produce the same safe error shape.

2. **Task visibility**
   - `task.created` includes enough fields to understand the created work.
   - `/tasks` and `/task <id>` expose goal, verification commands, latest failure reason, worktree, and log path.

3. **Help**
   - `/help` never calls unsupported `system.help`.
   - `/help` works immediately after startup and after reconnect.

4. **Duplicate chat**
   - one submitted user message renders once, not twice, including after reconnect/replay.

5. **Prompt locality**
   - worker command references a prompt path inside the task worktree.
   - `.coordinator/` prompt artifacts do not appear in changed files or commits.

6. **Report-only semantics**
   - report-only baseline tasks run verification commands and can finish `done` with no changed files.
   - ordinary code-edit tasks still fail or block when they produce no changed files.

7. **Regression gates**
   - `npm run typecheck --prefix ui-tui`
   - `npm run lint --prefix ui-tui`
   - `npm test --prefix ui-tui -- --run`
   - `npm run build --prefix ui-tui`
   - isolated-XDG full Python suite with `PYTHONWARNINGS=error::ResourceWarning`
   - `tests.test_tui_pty`
   - `tests.test_global_tui_e2e`
   - `git diff --check`

Blocking if any of these fail:

- `/help` still produces `unsupported method`.
- `chat.send` renders a single user message twice.
- `project.task` leaks foreign project tasks.
- report-only task succeeds without executing every configured verification command.
- code-edit task with no changed files is incorrectly marked done.
- TUI bundle is stale relative to `ui-tui/src`.

---

## Codex Final Acceptance Gate

Codex should not sign Phase 5.1 until:

1. Grok provides commit list and gate output.
2. Gemini provides adversarial review PASS.
3. Codex reruns at minimum:

```bash
npm run typecheck --prefix ui-tui
npm run lint --prefix ui-tui
npm test --prefix ui-tui -- --run
npm run build --prefix ui-tui
XDG_CONFIG_HOME=/private/tmp/coordinator-phase5-1-codex/config \
XDG_DATA_HOME=/private/tmp/coordinator-phase5-1-codex/data \
XDG_STATE_HOME=/private/tmp/coordinator-phase5-1-codex/state \
PYTHONWARNINGS=error::ResourceWarning \
PYTHONPATH=src python3 -m unittest discover -s tests -q
PYTHONPATH=src python3 -m unittest tests.test_tui_pty tests.test_global_tui_e2e -v
git diff --check
```

4. User or Codex runs one real-project smoke in `/Users/xiafan/polymarket-crypto-threshold`.

---

## Self-Review

- Spec coverage: all observed real-smoke issues are mapped to tasks: unknown task visibility, `/help`, duplicate chat, prompt permission failure, report-only no-change failure, failure reason visibility.
- Placeholder scan: no `TBD` or unspecified task remains. Every task has concrete files, expected behavior, commands, and commit message.
- Type consistency: planned method names are consistent: `project.task`, existing `project.tasks`, local `/help`, `task.created`, `task.done`.
- Scope check: this is a focused Phase 5.1 patch, not Phase 6. It does not introduce streaming Commander, multi-project switching UX, or Pi-inspired command modes.

